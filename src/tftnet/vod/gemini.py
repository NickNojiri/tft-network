"""Thin Gemini wrapper for YouTube video extraction.

Two entry points:
- ask_video: one clip per request (the original pipeline). Tries the Interactions API, then generate_content.
- ask_parts: several clips of the same video in ONE request, each with its own offsets, fps and resolution,
  used by the event-sampling pipeline (vod/events.py). Sent through the Interactions API with
  processing.content_layers=["frames"] so no audio is tokenized. generate_content's v1beta endpoint rejects
  content_layers, and its global media_resolution did not change tokens per frame in the first live run, so
  it is only the last fallback (with audio). The mode that worked is saved in caps_path.

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
from pathlib import Path
from typing import Callable, Type

from pydantic import BaseModel, ValidationError

from .budget import Budget, DailyLimitReached, UsageLog

log = logging.getLogger(__name__)

# Estimates, only used to split requests under the tokens/minute cap and to pace them. Measured on
# gemini-3.6-flash via Interactions, frames only, 2026-11-16: low 65.8, medium 44.2, high 252.9 video
# tokens per frame. Rounded up here.
TOKENS_PER_FRAME = {"low": 70, "medium": 70, "high": 260}
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
    code = _status(e)
    if code == 429 or "RESOURCE_EXHAUSTED" in msg:
        return "daily" if re.search(r"(?i)per.?day|daily", msg) else "rate"
    if code in (500, 502, 503, 504) or re.search(r"UNAVAILABLE|DEADLINE_EXCEEDED|INTERNAL|(?i:overloaded|high demand)", msg):
        return "retry"
    if isinstance(e, (ConnectionError, TimeoutError)):
        return "retry"
    return "fatal"


class PartsLimitError(RuntimeError):
    """The API refused the number of video parts in one request."""


class SchemaTooComplexError(RuntimeError):
    """Structured output refused the response schema as too complex ("too many states for serving"). Every
    mode would refuse it, so no other mode is tried. Seen 2026-11-16 with maxItems 400 on the zoom action list."""


_TOO_COMPLEX = re.compile(r"(?i)too many states|constraint that has too many")

_PARTS_LIMIT = re.compile(r"(?i)(too many|at most|maximum|exceed\w*|limit)\W+(\w+\W+){0,6}(video|part|file)s?"
                          r"|(video|part|file)s?\W+(\w+\W+){0,6}(too many|at most|maximum|exceed\w*|limit)")


def _status(e: Exception) -> int | None:
    """HTTP status: generate_content errors carry .code, Interactions errors carry .status_code."""
    return next((v for v in (getattr(e, "code", None), getattr(e, "status_code", None)) if isinstance(v, int)), None)


def _is_400(e: Exception) -> bool:
    return _status(e) == 400 or bool(re.search(r"INVALID_ARGUMENT|invalid_request", str(e)))


def is_unsupported(e: Exception) -> bool:
    """A 400 about the request's shape (an unknown field, an unsupported option)."""
    return _is_400(e) and not _PARTS_LIMIT.search(str(e))


# Multi-part request modes in order of preference: (api, frames only). generate_content without audio is
# left out: its v1beta endpoint rejects VideoMetadata.content_layers ("Unknown name"), measured 2026-11-16.
MODES = [("interactions", True), ("interactions", False), ("generate_content", False)]


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


def _continuation(resp) -> dict:
    """{'continuation': (interaction id, token)} when the answer stopped at the decode limit, else {}."""
    status = str(getattr(resp, "status", "") or "").lower()
    token = getattr(resp, "continuation_token", None)
    if token and status.endswith("requires_continuation"):
        return {"continuation": (getattr(resp, "id", None), token)}
    return {}


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
                 usage_log: UsageLog | None = None, api: str = "interactions",
                 sleep: Callable[[float], None] = time.sleep, max_attempts: int = 5, backoff_s: float = 8.0,
                 caps_path: Path | None = None, max_output_tokens: int = 40960, request_timeout_s: float = 900.0,
                 max_hops: int = 3):
        # The cap includes thinking (13K in the 2026-11-16 zoom answer). Bounded ints keep answers from running
        # away, so it only needs to stop a runaway, not fit a tight answer.
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is empty. Get a free key in Google AI Studio and add it to .env.")
        from google import genai  # imported lazily so the rest of the repo works without it

        self.genai = genai
        # Every attempt must go through _send, so the budget counts it: the SDK's own retries (4 by default, on
        # 408/409/429/5xx) and its automatic continuation hops (each re-sends the whole input) are turned off.
        # A zoom request hung for 15+ minutes with them on (2026-11-16).
        self.http_options = genai.types.HttpOptions(timeout=int(request_timeout_s * 1000),
                                                    retry_options=genai.types.HttpRetryOptions(attempts=1))
        self.client = genai.Client(api_key=api_key, http_options=self.http_options)
        self.max_hops = max_hops
        self._scrub = api_key
        self.model = model
        self.resolution = resolution
        self.budget, self.usage_log, self.api = budget, usage_log, api
        self.sleep, self.max_attempts, self.backoff_s = sleep, max_attempts, backoff_s
        self.exclude_audio = True
        # A runaway answer (long digit loops, 58K tokens in one live run) stops here instead.
        self.max_output_tokens = max_output_tokens
        # Which multi-part mode works is remembered per model, so a rejected option costs one request once.
        self.caps_path = Path(caps_path) if caps_path else None
        caps = self._caps().get(model)
        if caps:
            self.api, self.exclude_audio = caps["api"], caps["frames_only"]

    def _caps(self) -> dict:
        if self.caps_path and self.caps_path.exists():
            try:
                return json.loads(self.caps_path.read_text())
            except ValueError:
                return {}
        return {}

    def _save_caps(self) -> None:
        if self.caps_path:
            caps = self._caps()
            caps[self.model] = {"api": self.api, "frames_only": self.exclude_audio}
            self.caps_path.parent.mkdir(parents=True, exist_ok=True)
            self.caps_path.write_text(json.dumps(caps, indent=1))

    def mode(self) -> dict:
        return {"api": self.api, "frames_only": self.exclude_audio}

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
        """All parts go in one request. Returns (parsed or None, raw text, api used, usage).

        Tries the remembered mode first. A 400 about the request's shape moves to the next mode in MODES
        (frames only via Interactions, then with audio, then generate_content with audio); a structured-output
        rejection skips straight to generate_content. A refused part count raises PartsLimitError."""
        tag = {**(tag or {}), "n_parts": len(parts), "frames": sum(p.frames for p in parts),
               "resolution": resolution}
        start = (self.api, self.exclude_audio and not audio)
        modes = [m for m in MODES if not (audio and m[1])]
        modes = modes[modes.index(start):] if start in modes else modes
        last: Exception | None = None
        for api, frames_only in modes:
            if last is not None and api == "interactions" and re.search(r"(?i)schema|response_format", str(last)):
                continue
            call = self._gc_parts if api == "generate_content" else self._ia_parts
            est = estimate_tokens(parts, resolution, not frames_only, prompt)
            try:
                text, usage, used = self._send(lambda: call(parts, prompt, schema, resolution, frames_only), est,
                                               {**tag, "api": api, "audio": not frames_only})
                text, usage = self._continue(text, usage, schema, frames_only, est,
                                             {**tag, "api": api, "audio": not frames_only})
            except DailyLimitReached:
                raise
            except Exception as e:
                if _TOO_COMPLEX.search(str(e)) and _is_400(e):
                    raise SchemaTooComplexError(f"{schema.__name__} refused: {self._clean(e)[:300]}") from None
                if _PARTS_LIMIT.search(str(e)) and _is_400(e):
                    raise PartsLimitError(f"{len(parts)} video parts refused: {self._clean(e)[:200]}") from None
                if not is_unsupported(e):
                    raise
                log.warning("%s with frames_only=%s rejected (%s); trying the next mode", api, frames_only,
                            self._clean(e)[:160])
                last = e
                continue
            if not audio:
                self.api, self.exclude_audio = api, frames_only
                if self._caps().get(self.model) != self.mode():
                    self._save_caps()
            return self.parse(text, schema), text, used, {**usage, "audio_excluded": frames_only}
        raise last  # every mode was rejected

    def _gc_parts(self, parts, prompt, schema, resolution, frames_only) -> tuple[str, dict, str]:
        types = self.genai.types
        layers = [types.VideoMetadataContentLayer.FRAMES] if frames_only else None
        content = []
        for p in parts:
            if p.label:
                content.append(types.Part(text=p.label))
            meta = types.VideoMetadata(start_offset=_offset(p.start_s), end_offset=_offset(p.end_s), fps=p.fps,
                                       content_layers=layers)
            content.append(types.Part(file_data=types.FileData(file_uri=p.url), video_metadata=meta))
        content.append(types.Part(text=prompt))
        config = types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=schema, max_output_tokens=self.max_output_tokens,
            media_resolution=getattr(types.MediaResolution, _GC_RES.get(resolution, _GC_RES["high"])))
        resp = self.client.models.generate_content(
            model=self.model, contents=[types.Content(role="user", parts=content)], config=config)
        return resp.text, _gc_usage(resp), "generate_content"

    def _ia_parts(self, parts, prompt, schema, resolution, frames_only) -> tuple[str, dict, str]:
        items: list[dict] = []
        for p in parts:
            if p.label:
                items.append({"type": "text", "text": p.label})
            proc = {"type": "static", "fps": p.fps, "start_offset": _offset(p.start_s), "end_offset": _offset(p.end_s)}
            if frames_only:
                proc["content_layers"] = ["frames"]
            items.append({"type": "video", "uri": p.url, "processing": proc, "resolution": resolution})
        items.append({"type": "text", "text": prompt})
        kw = dict(model=self.model, input=items,
                  response_format={"type": "text", "mime_type": "application/json", "schema": schema.model_json_schema()},
                  generation_config={"max_output_tokens": self.max_output_tokens})
        if frames_only:
            # content_layers exists only in v1beta; the client's default Interactions route is v1 (live run 2026-11-16)
            kw["api_version"] = "v1beta"
        resp = self.client.interactions.create(**kw, automatic_continuation=False)
        return resp.output_text, {**_ia_usage(resp), **_continuation(resp)}, "interactions"

    def _continue(self, text: str, usage: dict, schema, frames_only: bool, est: int, tag: dict) -> tuple[str, dict]:
        """An Interactions answer that stopped at the per-request decode limit resumes here, one counted request
        per hop (each re-bills the input), up to max_hops. A still-unfinished answer is returned as is; it
        won't parse and is saved as .failed.json."""
        hops = 0
        while usage.get("continuation") and hops < self.max_hops:
            iid, token = usage.pop("continuation")
            hops += 1
            log.warning("Gemini answer needs continuation; hop %d/%d", hops, self.max_hops)
            kw = dict(model=self.model, previous_interaction_id=iid, continuation_token=token,
                      response_format={"type": "text", "mime_type": "application/json",
                                       "schema": schema.model_json_schema()},
                      generation_config={"max_output_tokens": self.max_output_tokens})
            if frames_only:
                kw["api_version"] = "v1beta"

            def hop(kw=kw):
                resp = self.client.interactions.create(**kw, automatic_continuation=False)
                return resp.output_text, {**_ia_usage(resp), **_continuation(resp)}, "interactions"

            more, u, _ = self._send(hop, est, {**tag, "hop": hops})
            text = (text or "") + (more or "")
            usage = {**{k: (usage.get(k) or 0) + (u.get(k) or 0) for k in set(usage) | set(u)
                        if k != "continuation" and isinstance(usage.get(k, u.get(k)), (int, float))},
                     **({"continuation": u["continuation"]} if u.get("continuation") else {})}
        if usage.pop("continuation", None):
            usage["unfinished"] = True
        if hops:
            usage["hops"] = hops + 1
        return text, usage

    # ------------------------------------------------------------------ single clip (original pipeline)
    def ask_video(self, url: str, prompt: str, schema: Type[BaseModel], start_s: float | None = None,
                  end_s: float | None = None, fps: float = 0.25) -> tuple[BaseModel | None, str, str]:
        """Returns (parsed model or None, raw text, api_used)."""
        dur = (end_s or 0) - (start_s or 0)
        est = estimate_tokens([VideoPart(url, start_s or 0, (start_s or 0) + max(dur, 60), fps)], self.resolution,
                              True, prompt)
        tag = {"pass": "single", "frames": int(max(dur, 0) * fps), "resolution": self.resolution}
        try:
            text, usage, api = self._send(lambda: self._interactions(url, prompt, schema, start_s, end_s, fps), est, tag)
            text, _ = self._continue(text, usage, schema, False, est, tag)
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
            resp = self.client.interactions.create(**kwargs, automatic_continuation=False)
        except Exception as e:  # structured output + video untested together; retry without schema
            if "schema" in str(e).lower() or "response_format" in str(e).lower():
                log.warning("Structured output rejected (%s); retrying with JSON-in-prompt", self._clean(e)[:200])
                kwargs.pop("response_format")
                kwargs["input"][0]["text"] += "\nReturn ONLY JSON matching this schema:\n" + json.dumps(schema.model_json_schema())
                resp = self.client.interactions.create(**kwargs, automatic_continuation=False)
            else:
                raise
        return resp.output_text, {**_ia_usage(resp), **_continuation(resp)}, "interactions"

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
