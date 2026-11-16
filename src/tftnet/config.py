"""Paths and settings. Everything is overridable with environment variables or a .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so python-dotenv is not required."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(REPO_ROOT / ".env")


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("TFTNET_DATA", REPO_ROOT / "data")))
    out_dir: Path = field(default_factory=lambda: Path(os.environ.get("TFTNET_OUT", REPO_ROOT / "out")))
    static_dir: Path = field(default_factory=lambda: Path(os.environ.get("TFTNET_STATIC", REPO_ROOT / "data" / "static")))

    riot_api_key: str = field(default_factory=lambda: os.environ.get("RIOT_API_KEY", ""))
    platform: str = field(default_factory=lambda: os.environ.get("RIOT_PLATFORM", "na1"))
    region: str = field(default_factory=lambda: os.environ.get("RIOT_REGION", "americas"))

    gemini_api_key: str = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", ""))
    gemini_model: str = field(default_factory=lambda: os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"))

    tft_set: int = field(default_factory=lambda: int(os.environ.get("TFT_SET", "18")))
    # Ranked queue id is UNVERIFIED (expected 1100). Empty string means accept any queue and report counts.
    ranked_queue_ids: tuple[int, ...] = field(
        default_factory=lambda: tuple(int(x) for x in os.environ.get("TFT_QUEUE_IDS", "1100").split(",") if x.strip())
    )
    set_start_epoch: int = field(default_factory=lambda: int(os.environ.get("SET_START_EPOCH", "1787727600")))  # 2026-08-26 00:00 PDT

    # Paths derived from data_dir
    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def matches_dir(self) -> Path:
        return self.raw_dir / "matches"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def vod_dir(self) -> Path:
        return self.data_dir / "vods"

    @property
    def figures_dir(self) -> Path:
        return self.out_dir / "figures"

    def ensure_dirs(self) -> None:
        for p in [self.raw_dir, self.matches_dir, self.processed_dir, self.vod_dir, self.vod_dir / "raw",
                  self.vod_dir / "hand_labels", self.out_dir, self.figures_dir]:
            p.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
