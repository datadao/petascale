"""Event models for petascale.

Hot-path readings flow through `SensorReading`. Detected events from the
warm-path detector flow through `DetectedEvent` (potty / cleaning).
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SensorType(str, Enum):
    """Types of sensors we monitor."""
    WEIGHT = "weight"
    MOTION = "motion"
    TEMPERATURE = "temperature"


class EventType(str, Enum):
    """Types of detected events."""
    POTTY = "potty"
    CLEANING = "cleaning"


class SensorReading(BaseModel):
    """Raw sensor reading from Home Assistant."""
    sensor_id: str = Field(..., description="HA entity ID")
    sensor_type: SensorType
    value: float = Field(..., description="Sensor value (weight in grams, etc.)")
    timestamp: int = Field(..., description="Unix timestamp in milliseconds")
    unit: Optional[str] = Field(None, description="Unit of measurement")


class DetectedEvent(BaseModel):
    """Detected pet event from the warm-path detector."""
    event_type: EventType
    sensor_id: str
    timestamp: int = Field(..., description="Unix timestamp in milliseconds (segment end)")
    cat: Optional[str] = Field(None, description="Identified cat name; null for cleaning or unknown")
    weight_g: Optional[int] = Field(None, description="Cat weight (potty) or baseline drop in grams (cleaning, negative)")
    cat_distance_g: Optional[int] = Field(None, description="|weight_g - cat_profile_weight| in grams")
    segment_start_ts: int = Field(..., description="Segment start timestamp (ms)")
    segment_end_ts: int = Field(..., description="Segment end timestamp (ms)")
