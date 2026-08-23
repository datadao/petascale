"""App configuration.

Loads sensors + detection thresholds from `config/sensors.toml` (committed)
and cat profiles from `config/cats.local.toml` (gitignored — names and
avatar paths are personal). Both files are merged into a single AppConfig.
"""

import itertools
import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
CONFIG_PATH = CONFIG_DIR / "sensors.toml"
CATS_LOCAL_PATH = CONFIG_DIR / "cats.local.toml"


@dataclass
class SensorConfig:
    id: str
    ha_entity: str
    name: str
    role: str  # litterbox | food | water | scale


@dataclass
class DetectionConfig:
    resample_freq_s: int = 1
    ffill_cap_s: int = 300
    baseline_window_s: int = 5
    noise_threshold_g: float = 10.0
    segment_buffer_s: int = 60
    event_delta_g: float = 10.0
    min_potty_peak_delta_g: float = 500.0
    min_cleaning_drop_g: float = 100.0
    min_event_duration_s: float = 3.0
    max_event_duration_s: float = 600.0
    plateau_round_g: float = 0.5
    iqr_multiplier: float = 1.5
    warm_window_minutes: int = 15
    warm_interval_seconds: int = 60


@dataclass
class CatProfile:
    name: str
    weight_g: int
    slop_g: int
    avatar_path: str | None = None  # filesystem path to avatar image (jpg/png)
    weight_alert_g: int = 300  # flag if last visit differs from 30d avg by more than this

    @property
    def window_g(self) -> tuple[int, int]:
        """Inclusive weight range this profile accepts, per `identify_cat`."""
        return self.weight_g - self.slop_g, self.weight_g + self.slop_g


def cat_profile_overlaps(cats: list[CatProfile]) -> list[str]:
    """Return one description per pair of cat profiles with intersecting windows.

    Windows are inclusive on both ends (`identify_cat` matches on `<=`), so
    profiles that merely touch at a single gram still count as overlapping.

    Overlaps are costly in both directions: a reading in the shared range is
    left unattributed, and the range that *should* separate the cats is the
    first thing to disappear as one of them gains weight.
    """
    problems: list[str] = []
    for a, b in itertools.combinations(cats, 2):
        (a_lo, a_hi), (b_lo, b_hi) = a.window_g, b.window_g
        if a_lo <= b_hi and b_lo <= a_hi:
            problems.append(
                f"{a.name} [{a_lo}, {a_hi}] overlaps {b.name} [{b_lo}, {b_hi}]"
            )
    return problems


@dataclass
class AppConfig:
    sensors: list[SensorConfig] = field(default_factory=list)
    detection: dict[str, DetectionConfig] = field(default_factory=dict)
    cats: list[CatProfile] = field(default_factory=list)
    timezone: str = "UTC"
    active_algos: list[str] = field(default_factory=lambda: ["v1"])


def load_config(
    path: Path = CONFIG_PATH,
    cats_path: Path = CATS_LOCAL_PATH,
) -> AppConfig:
    """Load and merge sensor config and (optionally) local cat profiles."""
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    sensors = [SensorConfig(**s) for s in raw.get("sensors", [])]
    detection = {
        role: DetectionConfig(**params)
        for role, params in raw.get("detection", {}).items()
    }

    cats: list[CatProfile] = []
    timezone = "UTC"
    if cats_path.is_file():
        with open(cats_path, "rb") as f:
            cats_raw = tomllib.load(f)
        cats = [CatProfile(**c) for c in cats_raw.get("cats", [])]
        timezone = cats_raw.get("timezone", "UTC")
    else:
        log.warning(
            "cat profile file not found: %s — events will not be attributed "
            "to a cat. Copy config/cats.local.toml.example and edit.",
            cats_path,
        )

    # Log the acceptance windows so the deployed state is visible in the
    # journal, and shout about overlaps. Deliberately not fatal: the warm
    # daemon restarts unless-stopped, so raising here would crash-loop and
    # stop detection entirely — strictly worse than attributing nothing.
    for c in cats:
        lo, hi = c.window_g
        log.info("Cat profile %s: accepts %d-%d g", c.name, lo, hi)
    for problem in cat_profile_overlaps(cats):
        log.error(
            "Cat profile overlap — readings in the shared range are left "
            "unattributed: %s", problem,
        )

    active_algos = raw.get("warm", {}).get("active_algos", ["v1"])
    return AppConfig(
        sensors=sensors, detection=detection, cats=cats,
        timezone=timezone, active_algos=active_algos,
    )
