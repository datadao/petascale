"""App configuration loaded from config/sensors.toml."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "sensors.toml"


@dataclass
class SensorConfig:
    id: str
    ha_entity: str
    name: str
    role: str  # litterbox | food | water | scale


@dataclass
class ThresholdConfig:
    enter_g: float = 300.0
    exit_g: float = 100.0
    stability_window_s: int = 5


@dataclass
class AppConfig:
    sensors: list[SensorConfig] = field(default_factory=list)
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    thresholds = ThresholdConfig(**raw.get("thresholds", {}))
    sensors = [SensorConfig(**s) for s in raw.get("sensors", [])]

    return AppConfig(sensors=sensors, thresholds=thresholds)
