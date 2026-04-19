"""Tests for petascale store module."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from petascale.events import SensorReading, SensorType
from petascale.store import Store


@pytest.fixture
async def temp_store():
    """Create a temporary store for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = Store(str(db_path))
        await store.initialize()
        yield store


@pytest.mark.asyncio
async def test_store_reading(temp_store):
    """Test storing and retrieving sensor readings."""
    reading = SensorReading(
        sensor_id="sensor.test_weight",
        sensor_type=SensorType.WEIGHT,
        value=500.0,
        timestamp=1640995200000,  # 2022-01-01 00:00:00 UTC
        unit="g"
    )

    # Store reading
    await temp_store.store_reading(reading)

    # Retrieve recent readings
    readings = await temp_store.get_recent_readings("sensor.test_weight", limit=10)

    assert len(readings) == 1
    assert readings[0].sensor_id == "sensor.test_weight"
    assert readings[0].value == 500.0
    assert readings[0].unit == "g"


@pytest.mark.asyncio
async def test_checkpoints(temp_store):
    """Test ingestion checkpoints."""
    sensor_id = "sensor.test"

    # Initially no checkpoint
    checkpoint = await temp_store.get_checkpoint(sensor_id)
    assert checkpoint is None

    # Set checkpoint
    await temp_store.update_checkpoint(sensor_id, 1640995200000)

    # Retrieve checkpoint
    checkpoint = await temp_store.get_checkpoint(sensor_id)
    assert checkpoint == 1640995200000