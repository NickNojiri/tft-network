"""Thin Gemini wrapper for YouTube video extraction.

Two entry points:
- ask_video: one clip per request (the original pipeline). Tries the Interactions API, then generate_content.
- ask_parts: several clips of the same video in ONE request, each with its own offsets and fps, used by the
  event-sampling pipeline (vod/events.py). Audio is excluded with content_layers=[FRAMES]; if the API
  rejects that, the request is resent with audio and the token log shows the audio tokens.

Every request goes through an optional Budget (daily counter + per-minute limits) and is retried on
503/500 with exponential backoff and jitter, and on 429 after a minute. Real token counts from
usage_metadata are written to a UsageLog. Field names checked against google-genai 3.4.0.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Callable, Type

from pydantic import BaseModel, ValidationError

from .budget import Budget, DailyLimitReached, UsageLog

log = logging.getLogger(__name__)

# Estimates, only used to split requests under the tokens/minute cap and to pace them. The real counts
# are in the token log; update these from it. Gemini 3 docs list ~70 tokens/frame at low and ~280 at
# high; medium is set high on purpose until measured.
TOKENS_PER_FRAME = {"low": 70, "medium": 258, "high": 280}
AUDIO_TOKENS_PER_S = 32
_GC_RES = {"low": "MEDIA_RESOLUTION_LOW", "medium": "MEDIA_RESOLUTION_MEDIUM", "high": "MEDIA_RESOLUTION_HIGH"}


@dataclass
class VideoPart:
    url: str
    start_s: float
    end_s: float
    fps: float
    label: str = ""

    @property
    def frames(self) -> int:
        return max(1, int((self.end_s - self.start_s) * self.fps) + 1)


def estimate_tokens(parts: list[VideoPart], resolution: str, audio: bool = False, prompt: str = "") -> int:
    per = TOKENS_PER_FRAME.get(resolution, TOKENS_PER_FRAME["high"])
    video = sum(p.frames * per for p in parts)
    sound = sum(AUDIO_TOKENS_PER_S * (p.end_s - p.start_s) for p in parts) if audio else 0
    return int(video + sound + len(prompt) / 4 + 300)


def _offset(s: float) -> str:
    return f"{max(0, int(round(s)))}s"


def classify_error(e: Exception) -> str:
    """'daily' (stop for today), 'rate' (wait a minute), 'retry' (backoff) or 'fatal'."""
    msg = str(e)
    code = next((v for v in (getattr(e, "code", None), getattr(e, "status_code", None)) if isinstance(v, int)), None)
    if code == 429 or "RESOURCE_EXHAUSTED" in msg:
        return "daily" if re.search(r"(?i)per.?day|daily", msg) else "rate"
    if code in (500, 502, 503, 504) or re.search(r"UNAVAILABLE|DEADLINE_EXCEEDED|INTERNAL|(?i:overloaded|high demand)", msg):
        return "retry"
    if isinstance(e, (ConnectionError, TimeoutError)):
        return "retry"
    return "fatal"


def _extract_json(text: str) -> str:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        return fence.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=0)
    return text[start:]


def _modality(m) -> str:
    return str(getattr(m, "name", m) or "unknown").lower().split(".")[-1]


def _gc_usage(resp) -> dict:
    u = getattr(resp, "usage_metadata", None)
    if u is None:
        return {}
    d = {"prompt_tokens": u.prompt_token_count, "output_tokens": u.candidates_token_count,
         "thoughts_tokens": u.thoughts_token_count, "cached_tokens": u.cached_content_token_count,
         "total_tokens": u.total_token_count}
    for m in u.prompt_tokens_details or []:
        d[f"in_{_modality(m.modality)}_tokens"] = m.token_count
    return {k: v for k, v in d.items() if v is not None}


def _ia_usage(resp) -> dict:
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    d = {"prompt_tokens": u.total_input_tokens, "output_tokens": u.total_output_tokens,
         "thoughts_tokens": u.total_thought_tokens, "cached_tokens": u.total_cached_tokens,
         "total_tokens": u.total_tokens}
    for m in u.input_tokens_by_modality or []:
        d[f"in_{_modality(m.modality)}_tokens"] = m.tokens
    return {k: v for k, v in d.items() if v is not None}


class GeminiVideo:
    def __init__(self, api_key: str, model: str, resolution: str = "high", budget: Budget | None = None,
                 usage_log: UsageLog | None = None, api: str = "generate_content",
                 sleep: Callable[[float], None] = time.sleep, max_attempts: int = 5, backoff_s: float = 8.0):
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is empty. Get a free key in Google AI Studio and add it to .env.")
        from google import genai  # imported lazily so the rest of the repo works without it

        self.genai = genai
        self.client = genai.Client(api_key=api_key)
        self._scrub = api_key
        self.model = model
        self.resolution = resolution
        self.budget, self.usage_log, self.api = budget, usage_log, api
        self.sleep, self.max_attempts, self.backoff_s = sleep, max_attempts, backoff_s
        self.exclude_audio = True

    # ------------------------------------------------------------------ budget + retry
    def _clean(self, e: Exception) -> str:
        s = str(e)
        return s.replace(self._scrub, "***") if self._scrub else s

    def _send(self, fn: Callable[[], tuple[str, dict, str]], est: int, tag: dict) -> tuple[str, dict, str]:
        tag = {"model": self.model, **tag}
        for attempt in range(1, self.max_attempts + 1):
            if self.budget:
                self.budget.acquire(est)
            t0 = time.time()
            try:
                text, usage, api = fn()
            except DailyLimitReached:
                raise
            except Exception as e:
                kind = classify_error(e)
                self._log({**tag, "status": f"error:{kind}", "attempt": attempt, "est_tokens": est,
                           "error": self._clean(e)[:300]})
                if kind == "daily":
                    raise DailyLimitReached(f"Gemini daily quota exhausted: {self._clean(e)[:200]}") from None
                if kind == "fatal" or attempt == self.max_attempts:
                    raise
                delay = 65.0 if kind == "rate" else min(120.0, self.backoff_s * 2 ** (attempt - 1))
                delay += random.uniform(0, self.backoff_s)
                log.warning("Gemini %s (%s); retry %d/%d in %.0f s", kind, self._clean(e)[:120], attempt,
                            self.max_attempts - 1, delay)
                self.sleep(delay)
                continue
            if self.budget:
                self.budget.settle(usage.get("prompt_tokens"))
            self._log({**tag, "status": "ok", "attempt": attempt, "api": api, "est_tokens": est,
                       "seconds": round(time.time() - t0, 1), **usage})
            return text, usage, api
        raise RuntimeError("unreachable")

    def _log(self, rec: dict) -> None:
        if self.usage_log:
            self.usage_log.write({"ts": time.time(), **rec})

    # ------------------------------------------------------------------ multi-part (event sampling)
    def ask_parts(self, parts: list[VideoPart], prompt: str, schema: Type[BaseModel], resolution: str = "medium",
                  audio: bool = False, tag: dict | None = None) -> tuple[BaseModel | None, str, str, dict]:
        """All parts go in one request. Returns (parsed or None, raw text, api used, usage)."""
        est = estimate_tokens(parts, resolution, audio or not self.exclude_audio, prompt)
        tag = {**(tag or {}), "n_parts": len(parts), "frames": sum(p.frames for p in parts),
               "resolution": resolution}
        call = self._gc_parts if self.api == "generate_content" else self._ia_parts
        try:
            text, usage, api = self._send(lambda: call(parts, prompt, schema, resolution, audio), est,
                                          {**tag, "audio": audio or not self.exclude_audio})
        except DailyLimitReached:
            raise
        except Exception as e:
            if audio or not self.exclude_audio or "content_layer" not in str(e).lower():
                raise
            log.warning("API rejected content_layers=[FRAMES]; resending with audio included (%s)", self._clean(e)[:120])
            self.exclude_audio = False
            text, usage, api = self._send(lambda: call(parts, prompt, schema, resolution, audio), est,
                                          {**tag, "audio": True})
        usage = {**usage, "audio_excluded": not (audio or not self.exclude_audio)}
        return self.parse(text, schema), text, api, usage

    def _gc_parts(self, parts, prompt, schema, resolution, audio) -> tuple[str, dict, str]:
        types = self.genai.types
        layers = None if (audio or not self.exclude_audio) else [types.VideoMetadataContentLayer.FRAMES]
        content = []
        for p in parts:
            if p.label:
                content.append(types.Part(text=p.label))
            meta = types.VideoMetadata(start_offset=_offset(p.start_s), end_offset=_offset(p.end_s), fps=p.fps,
                                       content_layers=layers)
            content.append(types.Part(file_data=types.FileData(file_uri=p.url), video_metadata=meta))
        content.append(types.Part(text=prompt))
        config = types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=schema,
            media_resolution=getattr(types.MediaResolution, _GC_RES.get(resolution, _GC_RES["high"])))
        resp = self.client.models.generate_content(
            model=self.model, contents=[types.Content(role="user", parts=content)], config=config)
        return resp.text, _gc_usage(resp), "generate_content"

    def _ia_parts(self, parts, prompt, schema, resolution, audio) -> tuple[str, dict, str]:
        items: list[dict] = []
        for p in parts:
            if p.label:
                items.append({"type": "text", "text": p.label})
            proc = {"type": "static", "fps": p.fps, "start_offset": _offset(p.start_s), "end_offset": _offset(p.end_s)}
            if not (audio or not self.exclude_audio):
                proc["content_layers"] = ["frames"]
            items.append({"type": "video", "uri": p.url, "processing": proc, "resolution": resolution})
        items.append({"type": "text", "text": prompt})
        resp = self.client.interactions.create(
            model=self.model, input=items,
            response_format={"type": "text", "mime_type": "application/json", "schema": schema.model_json_schema()})
        return resp.output_text, _ia_usage(resp), "interactions"

    # ------------------------------------------------------------------ single clip (original pipeline)
    def ask_video(self, url: str, prompt: str, schema: Type[BaseModel], start_s: float | None = None,
                  end_s: float | None = None, fps: float = 0.25) -> tuple[BaseModel | None, str, str]:
        """Returns (parsed model or None, raw text, api_used)."""
        dur = (end_s or 0) - (start_s or 0)
        est = estimate_tokens([VideoPart(url, start_s or 0, (start_s or 0) + max(dur, 60), fps)], self.resolution,
                              True, prompt)
        tag = {"pass": "single", "frames": int(max(dur, 0) * fps), "resolution": self.resolution}
        try:
            text, _, api = self._send(lambda: self._interactions(url, prompt, schema, start_s, end_s, fps), est, tag)
        except DailyLimitReached:
            raise
        except Exception as e:
            log.warning("Interactions API call failed (%s: %s); trying generate_content", type(e).__name__,
                        self._clean(e)[:200])
            try:
                text, _, api = self._send(lambda: self._generate_content(url, prompt, schema, start_s, end_s, fps),
                                          est, tag)
            except DailyLimitReached:
                raise
            except Exception as e2:
                raise RuntimeError(f"Both Gemini paths failed.\n  interactions: {self._clean(e)}\n  generate_content: "
                                   f"{self._clean(e2)}\nCheck GEMINI_MODEL in .env (list models in AI Studio) and your "
                                   "quota.") from None
        return self.parse(text, schema), text, api

    def ask_text(self, prompt: str) -> str:
        try:
            return self.client.interactions.create(model=self.model, input=prompt).output_text
        except (AttributeError, TypeError):
            return self.client.models.generate_content(model=self.model, contents=prompt).text

    @staticmethod
    def parse(text: str, schema: Type[BaseModel]) -> BaseModel | None:
        try:
            return schema.model_validate_json(_extract_json(text))
        except (ValidationError, ValueError):
            try:
                return schema.model_validate(json.loads(_extract_json(text)))
            except Exception:
                return None

    # ------------------------------------------------------------------
    def _interactions(self, url, prompt, schema, start_s, end_s, fps) -> tuple[str, dict, str]:
        processing: dict = {"type": "static", "fps": fps}
        # SDK 3.3 types these as strings ("1200s"), checked against google.genai.interactions.StaticMediaProcessing.
        if start_s is not None:
            processing["start_offset"] = f"{int(start_s)}s"
        if end_s is not None:
            processing["end_offset"] = f"{int(end_s)}s"
        video = {"type": "video", "uri": url, "processing": processing}
        if self.resolution:
            video["resolution"] = self.resolution  # field name confirmed in SDK 3.3 VideoContentParam
        kwargs = dict(model=self.model, input=[{"type": "text", "text": prompt}, video],
                      response_format={"type": "text", "mime_type": "application/json",
                                       "schema": schema.model_json_schema()})
        try:
            resp = self.client.interactions.create(**kwargs)
        except Exception as e:  # structured output + video untested together; retry without schema
            if "schema" in str(e).lower() or "response_format" in str(e).lower():
                log.warning("Structured output rejected (%s); retrying with JSON-in-prompt", self._clean(e)[:200])
                kwargs.pop("response_format")
                kwargs["input"][0]["text"] += "\nReturn ONLY JSON matching this schema:\n" + json.dumps(schema.model_json_schema())
                resp = self.client.interactions.create(**kwargs)
            else:
                raise
        return resp.output_text, _ia_usage(resp), "interactions"

    def _generate_content(self, url, prompt, schema, start_s, end_s, fps) -> tuple[str, dict, str]:
        types = self.genai.types
        meta = {"fps": fps}
        if start_s is not None:
            meta["start_offset"] = f"{int(start_s)}s"
        if end_s is not None:
            meta["end_offset"] = f"{int(end_s)}s"
        part = types.Part(file_data=types.FileData(file_uri=url), video_metadata=types.VideoMetadata(**meta))
        config = types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=schema,
            media_resolution=getattr(types.MediaResolution, "MEDIA_RESOLUTION_HIGH", None))
        resp = self.client.models.generate_content(model=self.model, contents=[part, prompt], config=config)
        return resp.text, _gc_usage(resp), "generate_content"
