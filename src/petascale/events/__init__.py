"""Event models and state machines for petascale."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SensorType(str, Enum):
    """Types of sensors we monitor."""
    WEIGHT = "weight"
    MOTION = "motion"
    TEMPERATURE = "temperature"


class EventType(str, Enum):
    """Types of events we detect."""
    CAT_PRESENT = "cat_present"
    CAT_LEFT = "cat_left"
    EATING = "eating"
    DRINKING = "drinking"
    LITTERBOX_USE = "litterbox_use"


class SensorReading(BaseModel):
    """Raw sensor reading from Home Assistant."""
    sensor_id: str = Field(..., description="HA entity ID")
    sensor_type: SensorType
    value: float = Field(..., description="Sensor value (weight in grams, etc.)")
    timestamp: int = Field(..., description="Unix timestamp in milliseconds")
    unit: Optional[str] = Field(None, description="Unit of measurement")


class DetectedEvent(BaseModel):
    """Detected pet event."""
    event_type: EventType
    sensor_id: str
    timestamp: int = Field(..., description="Unix timestamp in milliseconds")
    duration_ms: Optional[int] = Field(None, description="Duration in milliseconds")
    metadata: dict = Field(default_factory=dict, description="Additional event data")


class PresenceState(str, Enum):
    """States for the presence detection state machine."""
    IDLE = "idle"
    CAT_ENTERING = "cat_entering"
    CAT_PRESENT = "cat_present"
    CAT_LEAVING = "cat_leaving"


class PresenceStateMachine:
    """State machine for detecting presence based on weight sensor.

    Flow: IDLE → CAT_ENTERING → CAT_PRESENT → CAT_LEAVING → IDLE
    Events fire on: IDLE→CAT_ENTERING (entering), CAT_ENTERING→CAT_PRESENT (present),
    CAT_LEAVING→IDLE (left with session summary).
    """

    STABILITY_STDDEV_G = 20.0  # max std dev (grams) to consider weight "settled"

    def __init__(
        self,
        sensor_id: str,
        weight_threshold_enter: float = 300.0,
        weight_threshold_exit: float = 100.0,
        stability_window_s: int = 5,
        min_samples: int = 3,
    ):
        self.sensor_id = sensor_id
        self.weight_threshold_enter = weight_threshold_enter
        self.weight_threshold_exit = weight_threshold_exit
        self.stability_window_ms = stability_window_s * 1000
        self.min_samples = min_samples
        self.state = PresenceState.IDLE
        self._readings: list[tuple[int, float]] = []  # (timestamp_ms, value)
        self._session_start_ts: int = 0

    def process_reading(self, reading: SensorReading) -> Optional[DetectedEvent]:
        """Process a weight reading and return a DetectedEvent if a transition fires."""
        if reading.sensor_type != SensorType.WEIGHT:
            return None

        self._readings.append((reading.timestamp, reading.value))
        cutoff = reading.timestamp - self.stability_window_ms
        self._readings = [(t, v) for t, v in self._readings if t >= cutoff]

        if self.state == PresenceState.IDLE:
            if self._all_above(self.weight_threshold_enter):
                self.state = PresenceState.CAT_ENTERING

        elif self.state == PresenceState.CAT_ENTERING:
            if self._all_below(self.weight_threshold_exit):
                # Weight dropped before settling — false alarm
                self.state = PresenceState.IDLE
            elif self._is_settled():
                self.state = PresenceState.CAT_PRESENT
                self._session_start_ts = reading.timestamp
                return DetectedEvent(
                    event_type=EventType.CAT_PRESENT,
                    sensor_id=self.sensor_id,
                    timestamp=reading.timestamp,
                    metadata={"weight_g": reading.value},
                )

        elif self.state == PresenceState.CAT_PRESENT:
            if self._all_below(self.weight_threshold_exit):
                self.state = PresenceState.CAT_LEAVING

        elif self.state == PresenceState.CAT_LEAVING:
            if self._all_above(self.weight_threshold_enter):
                # Weight returned — still present
                self.state = PresenceState.CAT_PRESENT
            elif self._all_below(self.weight_threshold_exit):
                duration_ms = reading.timestamp - self._session_start_ts
                median_w = self._median_weight()
                self.state = PresenceState.IDLE
                return DetectedEvent(
                    event_type=EventType.CAT_LEFT,
                    sensor_id=self.sensor_id,
                    timestamp=reading.timestamp,
                    duration_ms=duration_ms,
                    metadata={"median_weight_g": median_w, "session_duration_ms": duration_ms},
                )

        return None

    def _all_above(self, threshold: float) -> bool:
        if len(self._readings) < self.min_samples:
            return False
        return all(v >= threshold for _, v in self._readings)

    def _all_below(self, threshold: float) -> bool:
        if len(self._readings) < self.min_samples:
            return False
        return all(v < threshold for _, v in self._readings)

    def _is_settled(self) -> bool:
        """True when weight has been stable (low stddev) across the window."""
        if len(self._readings) < self.min_samples:
            return False
        values = [v for _, v in self._readings]
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        return variance ** 0.5 < self.STABILITY_STDDEV_G

    def _median_weight(self) -> float:
        values = sorted(v for _, v in self._readings)
        mid = len(values) // 2
        return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2