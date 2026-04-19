"""HA REST API backfill for petascale.

Fetches historical state from HA's recorder since the last checkpoint and
stores readings into SQLite. Run once on daemon startup to close any gaps
caused by MQTT downtime or daemon restarts.

Sensor map format (HA_BACKFILL_SENSORS env var):
    sensor.cat_weight_sensor:sensor14/cat_weight_sensor,sensor.dog_weight:sensor12/dog_weight
"""

import time
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import structlog

from petascale.events import SensorReading, SensorType
from petascale.store import Store

logger = structlog.get_logger()

# How far back to look when there is no checkpoint (seconds)
_DEFAULT_LOOKBACK_S = 10 * 24 * 3600


def _sensor_type_from_entity(entity_id: str) -> Optional[SensorType]:
    name = entity_id.lower()
    if "weight" in name:
        return SensorType.WEIGHT
    if "motion" in name or "pir" in name:
        return SensorType.MOTION
    return None


def _ts_ms_to_iso(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return dt.isoformat()


async def run_backfill(
    store: Store,
    ha_url: str,
    ha_token: str,
    sensor_map: dict[str, str],
) -> None:
    """Fetch HA history for each sensor since last checkpoint.

    Args:
        store: Initialized Store instance.
        ha_url: Base URL of HA instance, e.g. http://homeassistant.local:8123
        ha_token: Long-lived access token.
        sensor_map: Mapping of HA entity_id → our sensor_id.
    """
    if not sensor_map:
        logger.info("No sensors configured for backfill, skipping")
        return

    headers = {"Authorization": f"Bearer {ha_token}"}

    async with aiohttp.ClientSession(headers=headers) as session:
        for ha_entity_id, sensor_id in sensor_map.items():
            await _backfill_sensor(session, store, ha_url, ha_entity_id, sensor_id)


async def _backfill_sensor(
    session: aiohttp.ClientSession,
    store: Store,
    ha_url: str,
    ha_entity_id: str,
    sensor_id: str,
) -> None:
    sensor_type = _sensor_type_from_entity(ha_entity_id)
    if sensor_type is None:
        logger.warning("Unknown sensor type, skipping backfill", entity_id=ha_entity_id)
        return

    checkpoint = await store.get_checkpoint(sensor_id)
    since_ts_ms = checkpoint if checkpoint else int((time.time() - _DEFAULT_LOOKBACK_S) * 1000)

    # Fetch day by day to avoid HA truncating large responses
    now_ts_ms = int(time.time() * 1000)
    window_start = since_ts_ms
    total_count = 0

    while window_start < now_ts_ms:
        window_end = min(window_start + 86_400_000, now_ts_ms)  # 1-day window
        count, latest_ts = await _fetch_window(
            session, store, ha_url, ha_entity_id, sensor_id, sensor_type,
            window_start, window_end,
        )
        total_count += count
        if latest_ts > window_start:
            await store.update_checkpoint(sensor_id, latest_ts)
        window_start = window_end

    logger.info("Backfill complete", entity_id=ha_entity_id, readings_stored=total_count)


async def _fetch_window(
    session: aiohttp.ClientSession,
    store: Store,
    ha_url: str,
    ha_entity_id: str,
    sensor_id: str,
    sensor_type: SensorType,
    since_ts_ms: int,
    end_ts_ms: int,
) -> tuple[int, int]:
    """Fetch one time window from HA history. Returns (count_stored, latest_ts)."""
    since_iso = _ts_ms_to_iso(since_ts_ms)
    end_iso   = _ts_ms_to_iso(end_ts_ms)

    logger.info("Fetching window", entity_id=ha_entity_id, since=since_iso, end=end_iso)

    params = {"filter_entity_id": ha_entity_id, "minimal_response": "true", "end_time": end_iso}
    url = f"{ha_url}/api/history/period/{since_iso}"

    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            if resp.status != 200:
                logger.error("HA history request failed", status=resp.status, entity_id=ha_entity_id)
                return 0, since_ts_ms
            data = await resp.json()
    except Exception as exc:
        logger.error("HA history request error", entity_id=ha_entity_id, error=str(exc))
        return 0, since_ts_ms

    count = 0
    latest_ts = since_ts_ms

    batch: list[SensorReading] = []
    for entity_states in data:
        for state in entity_states:
            reading = _parse_ha_state(state, sensor_id, sensor_type)
            if reading is None or reading.timestamp <= since_ts_ms:
                continue
            batch.append(reading)
            if reading.timestamp > latest_ts:
                latest_ts = reading.timestamp

    if batch:
        count = await store.store_readings_batch(batch)

    return count, latest_ts


def _parse_ha_state(
    state: dict,
    sensor_id: str,
    sensor_type: SensorType,
) -> Optional[SensorReading]:
    try:
        value = float(state["state"])
    except (KeyError, ValueError, TypeError):
        return None

    try:
        # HA uses ISO 8601 with timezone; minimal_response uses last_changed
        raw_ts = state.get("last_changed") or state.get("lu")  # lu = last_updated in minimal
        if raw_ts is None:
            return None
        # Numeric epoch (minimal_response) or ISO string
        if isinstance(raw_ts, (int, float)):
            ts_ms = int(raw_ts * 1000)
        else:
            ts_ms = int(datetime.fromisoformat(raw_ts).timestamp() * 1000)
    except Exception:
        return None

    return SensorReading(
        sensor_id=sensor_id,
        sensor_type=sensor_type,
        value=value,
        timestamp=ts_ms,
    )


