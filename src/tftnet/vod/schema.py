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


# ---------------------------------------------------------------- event-sampling pipeline (vod/events.py)
class SegRound(BaseModel):
    stage: str = Field(description="Stage-round at top center, like '3-2'")
    part: Optional[int] = Field(None, description="Number of the video part where this round's planning starts")
    planning: str = Field(description="Video time the planning phase starts, MM:SS or HH:MM:SS")
    combat: Optional[str] = Field(None, description="Video time combat starts, MM:SS or HH:MM:SS")


class Segmentation(BaseModel):
    """Pass A: round boundaries only, from low-resolution frames of the whole game."""
    rounds: list[SegRound]
    game_end: Optional[str] = Field(None, description="Video time the streamer's game ends")
    final_placement: Optional[int] = Field(None, description="Streamer's final placement 1-8 if shown")


class FrameRead(BaseModel):
    t: Optional[str] = Field(None, description="Video time of this frame, MM:SS")
    gold: Optional[int] = None
    hp: Optional[int] = None
    level: Optional[int] = None
    xp: Optional[int] = Field(None, description="XP toward next level, left number of 'xp/max'")
    xp_max: Optional[int] = Field(None, description="Right number of 'xp/max'")


class SnapRound(BaseModel):
    """Pass B, per-frame form: a few frames at planning start and a few right before combat. Gemini gave
    near-empty answers with these nested lists through Interactions (2026-11-16); kept to read old caches."""
    stage: str
    start: list[FrameRead] = Field(default_factory=list, description="Frames from the start of planning")
    end: list[FrameRead] = Field(default_factory=list, description="Frames from just before combat")
    wisp_slot: Optional[str] = Field(None, description="'wisp', 'champion', or null if the shop is not visible")
    wisp_name: Optional[str] = None
    wisp_bought: Optional[bool] = None
    augment: Optional[str] = None


class Snapshots(BaseModel):
    rounds: list[SnapRound]


class SnapRoundFlat(BaseModel):
    """Pass B, flat form (same shape as the zooms, which read well): Gemini takes the majority of the frames."""
    stage: str
    gold_start: Optional[int] = None
    hp: Optional[int] = None
    level_start: Optional[int] = None
    xp_start: Optional[int] = None
    xp_max_start: Optional[int] = None
    gold_end: Optional[int] = None
    level_end: Optional[int] = None
    xp_end: Optional[int] = None
    frames_agree: Optional[bool] = Field(None, description="true if every readable start frame showed the same gold")
    wisp_slot: Optional[str] = Field(None, description="'wisp', 'champion', or null if the shop is not visible")
    wisp_name: Optional[str] = None
    wisp_bought: Optional[bool] = None
    augment: Optional[str] = None


class SnapshotsFlat(BaseModel):
    rounds: list[SnapRoundFlat]


class SoldUnit(BaseModel):
    name: str
    stars: Optional[int] = 1


class ZoomRound(BaseModel):
    """Pass C: every frame of one planning phase at high resolution, for the gold accounting."""
    stage: str
    gold_start: Optional[int] = None
    gold_end: Optional[int] = None
    hp: Optional[int] = None
    level_start: Optional[int] = None
    xp_start: Optional[int] = None
    xp_max_start: Optional[int] = None
    level_end: Optional[int] = None
    xp_end: Optional[int] = None
    xp_buys: Optional[int] = Field(None, description="Times the Buy XP button was pressed")
    rerolls: Optional[int] = Field(None, description="Times the shop was rerolled")
    units_bought: list[str] = Field(default_factory=list, description="Champion names bought from the shop")
    units_sold: list[SoldUnit] = Field(default_factory=list)
    wisp_slot: Optional[str] = Field(None, description="'wisp', 'champion', or null if not visible")
    wisp_name: Optional[str] = None
    wisp_bought: Optional[bool] = None
    augment: Optional[str] = None


class Zooms(BaseModel):
    rounds: list[ZoomRound]
