"""Static Set 18 game data (from Mobalytics) and name normalization helpers.

Riot match JSON uses ids like ``TFT18_Ahri`` / ``TFT18_Blossom`` / ``DA_InfinityEdge``.
Static files use display names. ``norm()`` maps both to a common key.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

from .config import get_settings

# Riot ids whose name differs from the static display name, so no format rule can map them.
# Verified against Community Dragon Set 18 data (apiName -> name) and cached Set 18 matches.
ALIASES = {
    # units
    "sentry": "pebbles",  # DA_18_Sentry
    "crimsonraptor": "mamabeak",  # DA_CrimsonRaptor18
    "raptor": "mamabeak",
    "elderdragon": "theelderdragon",  # DA_18_ElderDragon
    # traits
    "eldritch": "blackthorn",
    "battlemage": "monolith",  # DA_18_Battlemage
    "slayer": "ravager",  # DA_18_Slayer, also DA_18_EmblemSlayer
    "emerald": "emeraldaspect",  # DA_Emerald18
    "luxuniquetrait": "avatar",  # DA_18_LuxUniqueTrait
    "aluneuniquetrait": "attuned",  # DA_AluneUniqueTrait18
    "dravenuniquetrait": "bountyseeker",  # DA_DravenUniqueTrait18
    "maokaiuniquetrait": "oldgrowth",  # DA_18_Maokai_UniqueTrait
    "zyrauniquetrait": "thornmaiden",  # DA_18_ZyraUniqueTrait
}

_ID_PREFIX = re.compile(r"^(TFT\d*(_\d+)?|DA(_\d+)?)_")  # TFT18_Ahri, TFT18_5_x, DA_18_Ahri, DA_Amumu18
# Variant suffixes on unit ids: DA_KogMaw18_AD, DA_Nidalee18_AP, DA_Lux18_Base, DA_18_GnarSmall.
_VARIANT_SUFFIX = re.compile(r"(_AD|_AP|_Base|Small)$")
# Set number after the name (DA_Amumu18, DA_KogMaw18_AD, DA_BlastPotion18_Radiant). Two digits only,
# so single-digit tiers such as Augment_CyberneticImplants2 are kept.
_SET_SUFFIX = re.compile(r"(?<=[A-Za-z])\d{2}(?=(_[A-Za-z]+)?$)")


def norm(name: str | None) -> str:
    """Normalize a display name or Riot id to a lowercase alphanumeric key.

    Handles both Set 18 id formats, ``DA_18_Name`` and ``DA_Name18``, plus variant suffixes
    (``_AD``, ``_AP``, ``_Base``, ``Small``). Suffix rules only apply to prefixed Riot ids, never
    to display names.
    """
    if not name:
        return ""
    s = str(name)
    s, n_prefix = _ID_PREFIX.subn("", s)
    if n_prefix:
        s = _VARIANT_SUFFIX.sub("", s)
        s = _SET_SUFFIX.sub("", s)
        s = _VARIANT_SUFFIX.sub("", s)
    s = re.sub(r"^(Item_|Augment_|Trait_|Emblem)", "", s)
    s = re.sub(r"[^a-z0-9]", "", s.lower())
    return ALIASES.get(s, s)


def is_lux(unit_id: str) -> bool:
    return norm(unit_id).startswith("lux")


def _static_dir() -> Path:
    return get_settings().static_dir


@lru_cache(maxsize=None)
def champions() -> pd.DataFrame:
    df = pd.read_csv(_static_dir() / "champions.csv")
    df["key"] = df["name"].map(lambda n: "lux" if str(n).startswith("Lux") else norm(n))
    df["slug_key"] = df["slug"].map(norm)
    df["trait_list"] = df["traits"].fillna("").str.split(";")
    return df


@lru_cache(maxsize=None)
def traits() -> pd.DataFrame:
    df = pd.read_csv(_static_dir() / "traits.csv")
    df["key"] = df["trait"].map(norm)
    return df


@lru_cache(maxsize=None)
def items() -> pd.DataFrame:
    df = pd.read_csv(_static_dir() / "items.csv")
    df["key"] = df["item"].map(norm)
    df["api_key"] = df["riot_api_id"].map(norm)
    return df


@lru_cache(maxsize=None)
def augments() -> pd.DataFrame:
    df = pd.read_csv(_static_dir() / "augments.csv")
    df["key"] = df["name"].map(norm)
    return df


@lru_cache(maxsize=None)
def cost_lookup() -> dict[str, int]:
    """Normalized champion key (also slug key) -> cost."""
    df = champions()
    out: dict[str, int] = {}
    for _, r in df.iterrows():
        out[r["key"]] = int(r["cost"])
        out[r["slug_key"]] = int(r["cost"])
    return out


def unit_cost(unit_id: str, rarity: int | None = None) -> int | None:
    """Cost from static data; falls back to Riot rarity mapping (UNVERIFIED) if unknown."""
    if is_lux(unit_id):
        return 5
    c = cost_lookup().get(norm(unit_id))
    if c is not None:
        return c
    if rarity is not None:
        # Historical mapping: rarity 0,1,2,4,6 -> cost 1..5. Verify on real data.
        return {0: 1, 1: 2, 2: 3, 4: 4, 6: 5}.get(int(rarity))
    return None


@lru_cache(maxsize=None)
def unique_traits() -> frozenset[str]:
    """Traits that only one champion can ever have (always 'active', useless for labeling)."""
    df = traits()
    return frozenset(df.loc[(df["type"].isin(["unique", "special"])) | (df["num_champions"] <= 1), "key"])


def allowed_names() -> dict[str, list[str]]:
    """Allowed-answer lists for Gemini prompts."""
    ch = champions()
    champ_names = sorted({"Lux" if n.startswith("Lux") else n for n in ch["name"]})
    it = items()
    aug = augments()
    return {
        "champions": champ_names,
        "traits": sorted(traits()["trait"].tolist()),
        "items": sorted(it.loc[~it["category"].isin(["Consumable"]), "item"].tolist()),
        "augments": sorted(aug.loc[aug["shown_on_site"] == "yes", "name"].tolist()),
    }
