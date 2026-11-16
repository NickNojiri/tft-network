"""Prompt builders. HUD positions are from pre-Set-18 footage: Set 18 moved to a new engine, so check
one current VOD by eye and edit HUD_LAYOUT if the layout changed."""
from __future__ import annotations

from ..static import allowed_names

HUD_LAYOUT = (
    "HUD layout: the stage-round indicator (like 3-2) is at the top center. The streamer's gold is at the "
    "bottom center just above the shop. The tactician level is at the bottom left. Every player's HP is in "
    "the list on the right edge of the screen. Active traits are listed on the left edge. Unequipped item "
    "components sit on the left side of the board."
)

RULES = (
    "Rules: report only values that are visible on screen. If a value is not readable, use null. Do not "
    "infer, interpolate or estimate missing values. Skip carousel rounds."
)


def economy_prompt(streamer_name: str) -> str:
    return (
        "This is a full Teamfight Tactics (Set 18) game recorded from the streamer's point of view. "
        f"The streamer's in-game name is '{streamer_name}'; read HP from that row of the player list.\n"
        f"{HUD_LAYOUT}\n"
        "For EVERY round, at the start of the planning phase, report: stage-round, video timestamp, the "
        "streamer's gold, HP and level. On augment-selection rounds, also report the augment the streamer picked "
        "(use the exact name from the list below). Report the streamer's final placement if it is shown.\n"
        f"{RULES}\n"
        f"Augment names: {', '.join(allowed_names()['augments'])}"
    )


def board_prompt(stage: str, streamer_name: str) -> str:
    n = allowed_names()
    return (
        f"This clip shows round {stage} of a Teamfight Tactics (Set 18) game from streamer '{streamer_name}'. "
        "Look at the streamer's own board during the planning phase.\n"
        f"{HUD_LAYOUT}\n"
        "Report: active traits with their unit counts (from the trait panel), every champion on the board with "
        "its star level and the items it holds, every champion on the bench with star level, unequipped items "
        "on the item bench, the Wisp offered in the rightmost shop slot (if any) and whether it was bought.\n"
        f"{RULES} Only use names from these lists.\n"
        f"Champions: {', '.join(n['champions'])}\n"
        f"Traits: {', '.join(n['traits'])}\n"
        f"Items: {', '.join(n['items'])}"
    )


def round_check_prompt(stage: str, streamer_name: str) -> str:
    return (
        f"This short clip is around round {stage} of a Teamfight Tactics game from streamer '{streamer_name}'. "
        "At the start of the planning phase, read the streamer's gold, HP and level.\n"
        f"{HUD_LAYOUT}\n{RULES} Also rate your confidence as high, medium or low."
    )
