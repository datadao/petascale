"""SQLite store layer for petascale."""

import json
from pathlib import Path
from typing import Optional

import aiosqlite
import structlog

from petascale.config import SensorConfig
from petascale.events import DetectedEvent, SensorReading, SensorType

logger = structlog.get_logger()


class Store:
    """SQLite store with a single persistent connection in WAL mode."""

    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA cache_size=-64000")  # 64 MB
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS raw_measurements (
                sensor_id   TEXT    NOT NULL,
                sensor_type TEXT    NOT NULL,
                value       REAL    NOT NULL,
                timestamp   INTEGER NOT NULL,
                unit        TEXT,
                created_at  INTEGER DEFAULT (strftime('%s', 'now') * 1000),
                PRIMARY KEY (sensor_id, timestamp)
            )
        """)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS hot_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type  TEXT    NOT NULL,
                sensor_id   TEXT    NOT NULL,
                timestamp   INTEGER NOT NULL,
                duration_ms INTEGER,
                metadata    TEXT,
                created_at  INTEGER DEFAULT (strftime('%s', 'now') * 1000)
            )
        """)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS ingestion_checkpoints (
                sensor_id        TEXT    PRIMARY KEY,
                last_captured_ts INTEGER NOT NULL,
                last_updated_at  INTEGER DEFAULT (strftime('%s', 'now') * 1000)
            )
        """)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS sensors (
                id         TEXT PRIMARY KEY,
                ha_entity  TEXT NOT NULL,
                name       TEXT NOT NULL,
                role       TEXT NOT NULL,
                created_at INTEGER DEFAULT (strftime('%s', 'now') * 1000)
            )
        """)
        await self._conn.commit()
        logger.info("Database initialized", db_path=str(self.db_path))

    async def upsert_sensors(self, sensors: list[SensorConfig]) -> None:
        """Sync sensor config into the sensors table (upsert, never delete)."""
        await self._conn.executemany(
            """
            INSERT INTO sensors (id, ha_entity, name, role)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                ha_entity = excluded.ha_entity,
                name      = excluded.name,
                role      = excluded.role
            """,
            [(s.id, s.ha_entity, s.name, s.role) for s in sensors],
        )
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def store_reading(self, reading: SensorReading) -> None:
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO raw_measurements
                (sensor_id, sensor_type, value, timestamp, unit)
            VALUES (?, ?, ?, ?, ?)
            """,
            (reading.sensor_id, reading.sensor_type.value, reading.value,
             reading.timestamp, reading.unit),
        )
        await self._conn.commit()

    async def store_readings_batch(self, readings: list[SensorReading]) -> int:
        """Insert many readings in a single transaction. Returns count stored."""
        await self._conn.executemany(
            """
            INSERT OR REPLACE INTO raw_measurements
                (sensor_id, sensor_type, value, timestamp, unit)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (r.sensor_id, r.sensor_type.value, r.value, r.timestamp, r.unit)
                for r in readings
            ],
        )
        await self._conn.commit()
        return len(readings)

    async def store_event(self, event: DetectedEvent) -> int:
        cursor = await self._conn.execute(
            """
            INSERT INTO hot_events
                (event_type, sensor_id, timestamp, duration_ms, metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event.event_type.value, event.sensor_id, event.timestamp,
             event.duration_ms, json.dumps(event.metadata)),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_checkpoint(self, sensor_id: str) -> Optional[int]:
        cursor = await self._conn.execute(
            "SELECT last_captured_ts FROM ingestion_checkpoints WHERE sensor_id = ?",
            (sensor_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def update_checkpoint(self, sensor_id: str, timestamp: int) -> None:
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO ingestion_checkpoints
                (sensor_id, last_captured_ts, last_updated_at)
            VALUES (?, ?, strftime('%s', 'now') * 1000)
            """,
            (sensor_id, timestamp),
        )
        await self._conn.commit()

    async def get_recent_readings(self, sensor_id: str, limit: int = 100) -> list[SensorReading]:
        cursor = await self._conn.execute(
            """
            SELECT sensor_id, sensor_type, value, timestamp, unit
            FROM raw_measurements
            WHERE sensor_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (sensor_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            SensorReading(
                sensor_id=row[0], sensor_type=SensorType(row[1]),
                value=row[2], timestamp=row[3], unit=row[4],
            )
            for row in rows
        ]
