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


# ---------------------------------------------------------------- event-sampling pipeline (vod/events.py)
HIDDEN = ("Use null for any number that is hidden or unreadable (menu open, cursor over it, transition). "
          "Never guess and never write 0 for a hidden number.")


def segmentation_prompt(streamer_name: str) -> str:
    return (
        "The video parts above are consecutive pieces of ONE Teamfight Tactics (Set 18) game from streamer "
        f"'{streamer_name}'. Each part's label gives its range in the original video. Frames are sparse and low "
        "resolution: read only the stage-round indicator at the top center and whether units are fighting.\n"
        "List EVERY round in order, including stage 1 and the carousel rounds (x-4): the stage-round, the part "
        "number, the video time the planning phase starts (the stage-round changes and the shop refreshes) and "
        "the video time combat starts (null for carousels). Give times on the original video's clock, inside "
        "that part's range. Report a round once even if it crosses two parts. Ignore intros, ads, menus and "
        "anything that is not this game. Report when the game ends and the final placement if shown."
    )


def snapshot_prompt(streamer_name: str, wisp_names: list[str], end_part: bool = False) -> str:
    n = allowed_names()
    end = ("The last part, labelled 'End of the game', shows how the game ends: from the end-of-game screen or "
           "the player list, report final_placement, the streamer's final placement 1-8 (1 if they won the "
           "game), or null if it is not shown. It has no rounds to report.\n") if end_part else ""
    return (
        f"The video parts above are clips from one Teamfight Tactics (Set 18) game from streamer '{streamer_name}'. "
        "Each part's label lists the rounds it covers with their planning and combat start times.\n"
        f"{end}"
        f"{HUD_LAYOUT} The XP numbers 'xp/max' are next to the level.\n"
        "For every listed round, read the frames at the start of planning and report gold_start, the streamer's "
        "HP, level_start, xp_start and xp_max_start, taking the value most frames show; frames_agree is true if "
        "every readable start frame showed the same gold. From the frames just before combat report gold_end, "
        f"level_end and xp_end. {HIDDEN}\n"
        "wisp_slot: what the rightmost shop slot shows at the start of planning, 'wisp' or 'champion', null if "
        "the shop is not visible. If it is a Wisp, give its name and whether the streamer bought it. On augment "
        "rounds give the augment picked. Use only names from the lists.\n"
        f"Augments: {', '.join(n['augments'])}\n"
        f"Wisps: {', '.join(wisp_names) if wisp_names else '(no list; copy the name shown)'}"
    )


def zoom_prompt(streamer_name: str, wisp_names: list[str], fps: float | None = None) -> str:
    n = allowed_names()
    rate = f"at high resolution, {fps:g} frames per second" if fps else "at high resolution"
    return (
        f"The video parts above are planning phases from one Teamfight Tactics (Set 18) game from streamer "
        f"'{streamer_name}', {rate}. Each part's label names the round and its planning and combat times.\n"
        f"{HUD_LAYOUT} The XP numbers 'xp/max' are next to the level. The shop is the row of five cards at the "
        "bottom, with the Buy XP and Reroll buttons on its left; the gold number is just above it.\n"
        "Watch the gold number and the shop frame by frame through each listed planning phase.\n"
        "actions: every shop action the streamer takes, in time order, one entry per action, with the round, "
        "the video time and the gold shown right after it:\n"
        "- reroll: all five shop cards change at once and gold drops by 2\n"
        "- buy_xp: gold drops by 4 and the XP number rises by 4\n"
        "- buy: one shop card empties and gold drops by that champion's cost; give the champion\n"
        "- sell: a unit is dragged off the board or bench and gold rises; give the unit and its star level\n"
        "- buy_wisp: the Wisp in the rightmost shop slot is bought\n"
        "If gold drops by more than one action's cost between two frames, write one entry per action and use the "
        "size of the drop to count them (for example 6 gold and three new shops = three rerolls).\n"
        "rounds: for every listed round, gold at the start and right before combat; HP; level, xp and xp_max at "
        "the start; level and xp at the end; shop_followed: true if the shop and the gold stayed visible for the "
        "whole planning phase (so your action list for it is complete, even if it is empty), false if a menu, the "
        "augment screen or the camera hid them for part of it; xp_buys and rerolls: the counts of those actions; "
        "units_bought and units_sold, matching the actions; what the rightmost shop slot held at the start "
        f"('wisp' or 'champion'), the Wisp's name and whether it was bought; the augment picked if any. {HIDDEN}\n"
        "Use only names from the lists.\n"
        f"Champions: {', '.join(n['champions'])}\n"
        f"Augments: {', '.join(n['augments'])}\n"
        f"Wisps: {', '.join(wisp_names) if wisp_names else '(no list; copy the name shown)'}"
    )
