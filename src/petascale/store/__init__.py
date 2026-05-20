"""SQLite store layer for petascale."""

from datetime import datetime, timezone
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
        # TODO(stack-decisions): events lives in SQLite for now. algo.md D18
        # proposes DuckDB. Revisit when events grow > 1M rows or when
        # analytical queries want to live next to the source-of-truth.
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                timestamp        INTEGER NOT NULL,
                type             TEXT    NOT NULL,
                sensor_id        TEXT    NOT NULL,
                algo             TEXT    NOT NULL DEFAULT 'v1',
                cat              TEXT,
                weight_g         INTEGER,
                cat_distance_g   INTEGER,
                segment_start_ts INTEGER NOT NULL,
                segment_end_ts   INTEGER NOT NULL,
                created_at       INTEGER DEFAULT (strftime('%s', 'now') * 1000),
                PRIMARY KEY (sensor_id, timestamp, type, algo)
            )
        """)
        await self._migrate_events_algo_column()
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp)"
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_cat ON events(cat, timestamp)"
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_algo ON events(algo, timestamp)"
        )
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

    async def _migrate_events_algo_column(self) -> None:
        """Add algo column + rebuild PK if upgrading from a pre-registry schema."""
        cursor = await self._conn.execute("PRAGMA table_info(events)")
        cols = [row[1] for row in await cursor.fetchall()]
        if "algo" in cols:
            return
        logger.info("Migrating events table: adding algo column")
        await self._conn.executescript("""
            BEGIN;
            ALTER TABLE events RENAME TO _events_pre_algo;
            CREATE TABLE events (
                timestamp        INTEGER NOT NULL,
                type             TEXT    NOT NULL,
                sensor_id        TEXT    NOT NULL,
                algo             TEXT    NOT NULL DEFAULT 'v1',
                cat              TEXT,
                weight_g         INTEGER,
                cat_distance_g   INTEGER,
                segment_start_ts INTEGER NOT NULL,
                segment_end_ts   INTEGER NOT NULL,
                created_at       INTEGER DEFAULT (strftime('%s', 'now') * 1000),
                PRIMARY KEY (sensor_id, timestamp, type, algo)
            );
            INSERT INTO events
                SELECT timestamp, type, sensor_id, 'v1', cat, weight_g,
                       cat_distance_g, segment_start_ts, segment_end_ts, created_at
                FROM _events_pre_algo;
            DROP TABLE _events_pre_algo;
            COMMIT;
        """)
        logger.info("Events table migration complete")

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

    async def upsert_events(self, events: list[DetectedEvent], algo: str = "v1") -> int:
        """Insert events idempotently. Returns count attempted (DO NOTHING on conflict)."""
        if not events:
            return 0
        await self._conn.executemany(
            """
            INSERT INTO events
                (timestamp, type, sensor_id, algo, cat, weight_g, cat_distance_g,
                 segment_start_ts, segment_end_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (sensor_id, timestamp, type, algo) DO NOTHING
            """,
            [
                (e.timestamp, e.event_type.value, e.sensor_id, algo, e.cat,
                 e.weight_g, e.cat_distance_g,
                 e.segment_start_ts, e.segment_end_ts)
                for e in events
            ],
        )
        await self._conn.commit()
        return len(events)

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

    async def fetch_readings_window(
        self, sensor_id: str, since_ms: int, until_ms: int,
    ) -> list[tuple[int, float]]:
        """Return (timestamp_ms, value) tuples for a sensor within [since, until)."""
        cursor = await self._conn.execute(
            """
            SELECT timestamp, value FROM raw_measurements
            WHERE sensor_id = ? AND timestamp >= ? AND timestamp < ?
            ORDER BY timestamp
            """,
            (sensor_id, since_ms, until_ms),
        )
        return [(r[0], r[1]) for r in await cursor.fetchall()]


def datetime_to_ms(dt: datetime) -> int:
    """Convert a UTC datetime to Unix epoch milliseconds."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
