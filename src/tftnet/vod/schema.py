"""Pydantic schemas for what Gemini returns. Used both as the structured-output schema and to validate."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class RoundRow(BaseModel):
    stage: str = Field(description="Stage-round shown at top center of the HUD, like '3-2'")
    timestamp: str = Field(description="Video time when this planning phase starts, MM:SS or HH:MM:SS")
    gold: Optional[int] = Field(None, description="Streamer's gold, bottom center above the shop. null if unreadable")
    hp: Optional[int] = Field(None, description="Streamer's HP from the player list on the right edge. null if unreadable")
    level: Optional[int] = Field(None, description="Streamer's tactician level, bottom left. null if unreadable")
    augment_picked: Optional[str] = Field(None, description="Augment name chosen this round, only on augment-selection rounds")


class EconomyTable(BaseModel):
    rounds: list[RoundRow]
    final_placement: Optional[int] = Field(None, description="Streamer's final placement 1-8 if shown")


class TraitCount(BaseModel):
    name: str
    count: int


class Unit(BaseModel):
    name: str
    stars: Optional[int] = None
    items: list[str] = Field(default_factory=list)


class BoardState(BaseModel):
    stage: str
    active_traits: list[TraitCount] = Field(default_factory=list)
    board_units: list[Unit] = Field(default_factory=list)
    bench_units: list[Unit] = Field(default_factory=list)
    bench_items: list[str] = Field(default_factory=list)
    wisp_offered: Optional[str] = None
    wisp_bought: Optional[bool] = None


class RoundCheck(BaseModel):
    """Used by the Gemini self-review pass on a short clip around one flagged round."""
    stage: str
    gold: Optional[int] = None
    hp: Optional[int] = None
    level: Optional[int] = None
    confidence: Optional[str] = Field(None, description="high, medium, or low")
