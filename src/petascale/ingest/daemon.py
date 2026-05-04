"""MQTT ingestion daemon for petascale.

Subscribes to sensor topics and stores raw readings. Event detection runs
in the warm-path daemon (`petascale.warm.litterbox`); this daemon only
captures and persists raw data.

Topic convention (input): sensors/<device>/sensor/<name>/state
"""

import asyncio
import os
import signal
import time
from typing import Optional

import aiomqtt
import structlog
from dotenv import load_dotenv

from petascale.config import AppConfig, load_config
from petascale.events import SensorReading, SensorType
from petascale.ingest.ha_backfill import run_backfill
from petascale.store import Store

logger = structlog.get_logger()

SUBSCRIBE_TOPIC = "sensors/#"


def _sensor_type_from_name(sensor_name: str) -> Optional[SensorType]:
    name = sensor_name.lower()
    if "weight" in name:
        return SensorType.WEIGHT
    if "motion" in name or "pir" in name:
        return SensorType.MOTION
    return None


def _parse_topic(topic: str) -> Optional[tuple[str, SensorType]]:
    """Parse a sensor topic into (sensor_id, SensorType).

    Expects: sensors/<device>/sensor/<name>/state
    Returns None for debug, non-state, or unknown sensor types.
    """
    parts = topic.split("/")
    # Must end with /state and have no 'debug' segment
    if parts[-1] != "state" or "debug" in parts:
        return None
    # Minimum shape: sensors / device / sensor / name / state  (5 parts)
    if len(parts) < 5:
        return None

    device = parts[1]
    sensor_name = parts[3]
    sensor_type = _sensor_type_from_name(sensor_name)
    if sensor_type is None:
        return None

    sensor_id = f"{device}/{sensor_name}"
    return sensor_id, sensor_type


def _parse_payload(raw: bytes | str) -> Optional[float]:
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


class IngestionDaemon:
    """Subscribes to MQTT sensor topics, detects events, publishes them back."""

    def __init__(self) -> None:
        load_dotenv()

        self.mqtt_host: str = os.environ["MQTT_HOST"]
        self.mqtt_port: int = int(os.getenv("MQTT_PORT", "1883"))
        self.mqtt_username: Optional[str] = os.getenv("MQTT_USERNAME") or None
        self.mqtt_password: Optional[str] = os.getenv("MQTT_PASSWORD") or None
        self.db_path: str = os.getenv("DB_PATH", "petascale.db")
        self.ha_url: str = os.getenv("HA_URL", "")
        self.ha_token: str = os.getenv("HA_TOKEN", "")

        self.cfg: AppConfig = load_config()
        self.store: Store = Store(self.db_path)
        self._shutdown = asyncio.Event()

    async def run(self) -> None:
        await self.store.initialize()
        await self.store.upsert_sensors(self.cfg.sensors)
        logger.info("Store ready", db=self.db_path, sensors=len(self.cfg.sensors))

        if self.ha_url and self.ha_token:
            sensor_map = {s.ha_entity: s.id for s in self.cfg.sensors}
            await run_backfill(self.store, self.ha_url, self.ha_token, sensor_map)
        elif self.cfg.sensors:
            logger.warning("Sensors configured but HA_URL or HA_TOKEN missing — skipping backfill")

        while not self._shutdown.is_set():
            try:
                await self._connect_and_loop()
            except Exception as exc:
                logger.error("Connection error, reconnecting in 5s", error=str(exc), exc_info=True)
                await asyncio.sleep(5)

    async def _connect_and_loop(self) -> None:
        async with aiomqtt.Client(
            hostname=self.mqtt_host,
            port=self.mqtt_port,
            username=self.mqtt_username,
            password=self.mqtt_password,
            identifier="petascale-ingest",
        ) as client:
            logger.info("Connected to MQTT broker", host=self.mqtt_host, port=self.mqtt_port)
            await client.subscribe(SUBSCRIBE_TOPIC)
            logger.info("Subscribed", topic=SUBSCRIBE_TOPIC)

            async for message in client.messages:
                if self._shutdown.is_set():
                    break
                try:
                    await self._handle_message(str(message.topic), message.payload)
                except Exception as exc:
                    logger.error("Failed to handle message", topic=str(message.topic), error=str(exc), exc_info=True)

    async def _handle_message(self, topic: str, payload: bytes) -> None:
        parsed = _parse_topic(topic)
        if parsed is None:
            return

        sensor_id, sensor_type = parsed
        value = _parse_payload(payload)
        if value is None:
            logger.warning("Unparseable payload", topic=topic, payload=payload[:100])
            return

        reading = SensorReading(
            sensor_id=sensor_id,
            sensor_type=sensor_type,
            value=value,
            timestamp=int(time.time() * 1000),
        )

        logger.debug("Reading", sensor_id=sensor_id, value=value, type=sensor_type.value)
        await self.store.store_reading(reading)
        await self.store.update_checkpoint(sensor_id, reading.timestamp)

    async def shutdown(self) -> None:
        self._shutdown.set()
        await self.store.close()

    def request_shutdown(self) -> None:
        logger.info("Shutdown requested")
        self._shutdown.set()


async def main() -> None:
    import logging
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=getattr(logging, log_level, logging.INFO))

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    daemon = IngestionDaemon()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, daemon.request_shutdown)

    await daemon.run()
    logger.info("Daemon stopped")


if __name__ == "__main__":
    asyncio.run(main())
