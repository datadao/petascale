"""App configuration.

Loads sensors + detection thresholds from `config/sensors.toml` (committed)
and cat profiles from `config/cats.local.toml` (gitignored — names and
avatar paths are personal). Both files are merged into a single AppConfig.
"""

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


@dataclass
class AppConfig:
    sensors: list[SensorConfig] = field(default_factory=list)
    detection: dict[str, DetectionConfig] = field(default_factory=dict)
    cats: list[CatProfile] = field(default_factory=list)


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
    if cats_path.is_file():
        with open(cats_path, "rb") as f:
            cats_raw = tomllib.load(f)
        cats = [CatProfile(**c) for c in cats_raw.get("cats", [])]
    else:
        log.warning(
            "cat profile file not found: %s — events will not be attributed "
            "to a cat. Copy config/cats.local.toml.example and edit.",
            cats_path,
        )

    return AppConfig(sensors=sensors, detection=detection, cats=cats)
