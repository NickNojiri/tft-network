"""Thin Gemini wrapper for YouTube video extraction.

Google's API changed in 2026 (Interactions API). This tries the Interactions API first and falls back
to the older generate_content API. Field names and types were checked against google-genai 3.3.0
(video part: uri/processing/resolution; processing: type/fps/start_offset/end_offset as "Ns" strings).
Not yet run against a live key: run `python -m tftnet vod test-one` first and record what works in
docs/findings.md.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Type

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)


def _extract_json(text: str) -> str:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        return fence.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=0)
    return text[start:]


class GeminiVideo:
    def __init__(self, api_key: str, model: str, resolution: str = "high"):
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is empty. Get a free key in Google AI Studio and add it to .env.")
        from google import genai  # imported lazily so the rest of the repo works without it

        self.genai = genai
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.resolution = resolution

    # ------------------------------------------------------------------
    def ask_video(self, url: str, prompt: str, schema: Type[BaseModel], start_s: float | None = None,
                  end_s: float | None = None, fps: float = 0.25) -> tuple[BaseModel | None, str, str]:
        """Returns (parsed model or None, raw text, api_used)."""
        try:
            text = self._interactions(url, prompt, schema, start_s, end_s, fps)
            api = "interactions"
        except Exception as e:
            log.warning("Interactions API call failed (%s: %s); trying generate_content", type(e).__name__, str(e)[:200])
            try:
                text = self._generate_content(url, prompt, schema, start_s, end_s, fps)
            except Exception as e2:
                raise RuntimeError(f"Both Gemini paths failed.\n  interactions: {e}\n  generate_content: {e2}\n"
                                   "Check GEMINI_MODEL in .env (list models in AI Studio) and your quota.") from e2
            api = "generate_content"
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
    def _interactions(self, url, prompt, schema, start_s, end_s, fps) -> str:
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
            return self.client.interactions.create(**kwargs).output_text
        except Exception as e:  # structured output + video untested together; retry without schema
            if "schema" in str(e).lower() or "response_format" in str(e).lower():
                log.warning("Structured output rejected (%s); retrying with JSON-in-prompt", e)
                kwargs.pop("response_format")
                kwargs["input"][0]["text"] += "\nReturn ONLY JSON matching this schema:\n" + json.dumps(schema.model_json_schema())
                return self.client.interactions.create(**kwargs).output_text
            raise

    def _generate_content(self, url, prompt, schema, start_s, end_s, fps) -> str:
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
        return resp.text
