"""Pydantic schemas for what Gemini returns. Used both as the structured-output schema and to validate."""
from __future__ import annotations

from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema


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

def _bounded(lo: int, hi: int):
    """An int whose schema gives Gemini the range, so constrained decoding can't run away into long digit
    strings (seen in live runs), without rejecting an out-of-range value when parsing."""
    return Annotated[int, WithJsonSchema({"type": "integer", "minimum": lo, "maximum": hi})]


Gold = _bounded(0, 999)
HP = _bounded(0, 200)
Level = _bounded(1, 10)
XP = _bounded(0, 200)
Count = _bounded(0, 99)
Stars = _bounded(1, 4)
Place = _bounded(1, 8)
SHOP_ACTIONS = ["reroll", "buy_xp", "buy", "sell", "buy_wisp"]
Action = Annotated[str, WithJsonSchema({"type": "string", "enum": SHOP_ACTIONS})]


def _all_required(schema: dict, cls) -> None:
    schema["required"] = list(schema.get("properties", {}))


class Answer(BaseModel):
    """Every field is required in the schema Gemini gets (nullable ones may be null), but parsing keeps the
    defaults. With optional fields Gemini left out every shop field in the 2026-11-16 zoom answer."""
    model_config = ConfigDict(json_schema_extra=_all_required)


# ---------------------------------------------------------------- event-sampling pipeline (vod/events.py)
class SegRound(BaseModel):
    stage: str = Field(description="Stage-round at top center, like '3-2'")
    part: Optional[Count] = Field(None, description="Number of the video part where this round's planning starts")
    planning: str = Field(description="Video time the planning phase starts, MM:SS or HH:MM:SS")
    combat: Optional[str] = Field(None, description="Video time combat starts, MM:SS or HH:MM:SS")


class Segmentation(BaseModel):
    """Pass A: round boundaries only, from low-resolution frames of the whole game."""
    rounds: list[SegRound]
    game_end: Optional[str] = Field(None, description="Video time the streamer's game ends")
    final_placement: Optional[Place] = Field(None, description="Streamer's final placement 1-8 if shown")


class FrameRead(BaseModel):
    t: Optional[str] = Field(None, description="Video time of this frame, MM:SS")
    gold: Optional[Gold] = None
    hp: Optional[HP] = None
    level: Optional[Level] = None
    xp: Optional[XP] = Field(None, description="XP toward next level, left number of 'xp/max'")
    xp_max: Optional[XP] = Field(None, description="Right number of 'xp/max'")


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


class SnapRoundFlat(Answer):
    """Pass B, flat form (same shape as the zooms, which read well): Gemini takes the majority of the frames."""
    stage: str
    gold_start: Optional[Gold] = None
    hp: Optional[HP] = None
    level_start: Optional[Level] = None
    xp_start: Optional[XP] = None
    xp_max_start: Optional[XP] = None
    gold_end: Optional[Gold] = None
    level_end: Optional[Level] = None
    xp_end: Optional[XP] = None
    frames_agree: Optional[bool] = Field(None, description="true if every readable start frame showed the same gold")
    wisp_slot: Optional[str] = Field(None, description="'wisp', 'champion', or null if the shop is not visible")
    wisp_name: Optional[str] = None
    wisp_bought: Optional[bool] = None
    augment: Optional[str] = None


class SnapshotsFlat(Answer):
    rounds: list[SnapRoundFlat]
    final_placement: Optional[Place] = Field(None, description="From the end-of-game part: the streamer's final "
                                                               "placement 1-8 (1 if they won), null if not shown")


class SoldUnit(Answer):
    name: str
    stars: Optional[Stars] = 1


class ShopAction(Answer):
    """One shop action in a zoomed planning phase. A flat list beside the rounds, not nested in them: nested
    per-frame lists came back near-empty in pass B."""
    stage: str
    t: Optional[str] = Field(None, description="Video time, MM:SS")
    action: Action = Field(description="reroll, buy_xp, buy (a champion), sell (a unit), buy_wisp")
    unit: Optional[str] = Field(None, description="Champion bought or unit sold; null otherwise")
    stars: Optional[Stars] = Field(None, description="Star level of a sold unit; null otherwise")
    gold_after: Optional[Gold] = Field(None, description="Gold shown right after the action")


class ZoomRound(Answer):
    """Pass C: the whole planning phase at high resolution, for the gold accounting."""
    stage: str
    shop_followed: Optional[bool] = Field(None, description="true if the shop and gold stayed visible for the "
                                                            "whole planning phase, so the action list is complete")
    gold_start: Optional[Gold] = None
    gold_end: Optional[Gold] = None
    hp: Optional[HP] = None
    level_start: Optional[Level] = None
    xp_start: Optional[XP] = None
    xp_max_start: Optional[XP] = None
    level_end: Optional[Level] = None
    xp_end: Optional[XP] = None
    xp_buys: Optional[Count] = Field(None, description="Times the Buy XP button was pressed")
    rerolls: Optional[Count] = Field(None, description="Times the shop was rerolled")
    units_bought: list[str] = Field(default_factory=list, description="Champion names bought from the shop")
    units_sold: list[SoldUnit] = Field(default_factory=list)
    wisp_slot: Optional[str] = Field(None, description="'wisp', 'champion', or null if not visible")
    wisp_name: Optional[str] = None
    wisp_bought: Optional[bool] = None
    augment: Optional[str] = None


class Zooms(Answer):
    rounds: list[ZoomRound]
    actions: list[ShopAction] = Field(default_factory=list, json_schema_extra={"maxItems": 400},
                                      description="Every shop action in every listed round, in time order")
