"""SQLite schema and queries for item metadata and stored price windows."""

import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from flipwatch.api import TIMESTEP_SECONDS, check_timestep
from flipwatch.models import Item, PriceWindow

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE items (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    members INTEGER NOT NULL,
    buy_limit INTEGER,
    value INTEGER,
    high_alch INTEGER,
    low_alch INTEGER,
    icon TEXT NOT NULL
);

CREATE TABLE price_windows (
    timestep TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    avg_high_price INTEGER,
    high_volume INTEGER NOT NULL,
    avg_low_price INTEGER,
    low_volume INTEGER NOT NULL,
    PRIMARY KEY (timestep, timestamp, item_id)
) WITHOUT ROWID;

CREATE INDEX price_windows_by_item ON price_windows (item_id, timestep, timestamp);

-- One row per complete market snapshot, so a window with no rows for an item can be
-- told apart from a window the collector missed.
CREATE TABLE collected_windows (
    timestep TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    item_count INTEGER NOT NULL,
    collected_at INTEGER NOT NULL,
    PRIMARY KEY (timestep, timestamp)
) WITHOUT ROWID;
"""


class StoreError(Exception):
    """Raised when the database cannot be used, for example because it is too new."""


@dataclass(frozen=True)
class StoreSummary:
    timestep: str
    snapshot_count: int
    first_timestamp: int | None
    last_timestamp: int | None
    missing_count: int
    row_count: int


class Store:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._migrate()

    @classmethod
    def open(cls, path: str | Path) -> Self:
        connection = sqlite3.connect(path)
        # WAL lets the dashboard read while the collector writes.
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return cls(connection)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def save_items(self, items: Iterable[Item]) -> None:
        rows = [
            (i.id, i.name, i.members, i.buy_limit, i.value, i.high_alch, i.low_alch, i.icon)
            for i in items
        ]
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO items (id, name, members, buy_limit, value, high_alch, low_alch, icon)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    name = excluded.name,
                    members = excluded.members,
                    buy_limit = excluded.buy_limit,
                    value = excluded.value,
                    high_alch = excluded.high_alch,
                    low_alch = excluded.low_alch,
                    icon = excluded.icon
                """,
                rows,
            )

    def items(self) -> dict[int, Item]:
        cursor = self._conn.execute(
            "SELECT id, name, members, buy_limit, value, high_alch, low_alch, icon FROM items"
        )
        return {
            row[0]: Item(
                id=row[0],
                name=row[1],
                members=bool(row[2]),
                buy_limit=row[3],
                value=row[4],
                high_alch=row[5],
                low_alch=row[6],
                icon=row[7],
            )
            for row in cursor
        }

    def save_snapshot(
        self,
        timestep: str,
        timestamp: int,
        windows: Mapping[int, PriceWindow],
        collected_at: int,
    ) -> bool:
        """Store a complete market snapshot. Returns False if it was already stored."""
        check_timestep(timestep)
        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO collected_windows (timestep, timestamp, item_count, collected_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                (timestep, timestamp, len(windows), collected_at),
            )
            if cursor.rowcount == 0:
                return False
            self._insert_windows(timestep, windows.values())
        return True

    def save_windows(self, timestep: str, windows: Iterable[PriceWindow]) -> int:
        """Store windows for individual items, keeping any rows already present.

        Returns the number of new rows.
        """
        check_timestep(timestep)
        with self._conn:
            return self._insert_windows(timestep, windows)

    def collected_timestamps(self, timestep: str, since: int) -> set[int]:
        cursor = self._conn.execute(
            "SELECT timestamp FROM collected_windows WHERE timestep = ? AND timestamp >= ?",
            (timestep, since),
        )
        return {row[0] for row in cursor}

    def windows_for_item(
        self,
        item_id: int,
        timestep: str,
        start: int | None = None,
        end: int | None = None,
    ) -> list[PriceWindow]:
        """Return an item's windows from `start` up to but not including `end`, oldest first."""
        conditions = ["item_id = ?", "timestep = ?"]
        params: list[object] = [item_id, timestep]
        if start is not None:
            conditions.append("timestamp >= ?")
            params.append(start)
        if end is not None:
            conditions.append("timestamp < ?")
            params.append(end)
        cursor = self._conn.execute(
            "SELECT timestamp, avg_high_price, high_volume, avg_low_price, low_volume "
            f"FROM price_windows WHERE {' AND '.join(conditions)} ORDER BY timestamp",
            params,
        )
        return [
            PriceWindow(
                item_id=item_id,
                timestamp=row[0],
                avg_high_price=row[1],
                high_volume=row[2],
                avg_low_price=row[3],
                low_volume=row[4],
            )
            for row in cursor
        ]

    def summary(self, timestep: str) -> StoreSummary:
        check_timestep(timestep)
        count, first, last = self._conn.execute(
            """
            SELECT COUNT(*), MIN(timestamp), MAX(timestamp)
            FROM collected_windows WHERE timestep = ?
            """,
            (timestep,),
        ).fetchone()
        (row_count,) = self._conn.execute(
            "SELECT COUNT(*) FROM price_windows WHERE timestep = ?", (timestep,)
        ).fetchone()
        expected = 0 if first is None else (last - first) // TIMESTEP_SECONDS[timestep] + 1
        return StoreSummary(
            timestep=timestep,
            snapshot_count=count,
            first_timestamp=first,
            last_timestamp=last,
            missing_count=expected - count,
            row_count=row_count,
        )

    def delete_before(self, timestamp: int) -> int:
        """Remove every window that started before `timestamp`. Returns rows removed."""
        with self._conn:
            removed = self._conn.execute(
                "DELETE FROM price_windows WHERE timestamp < ?", (timestamp,)
            ).rowcount
            self._conn.execute("DELETE FROM collected_windows WHERE timestamp < ?", (timestamp,))
        return removed

    def _insert_windows(self, timestep: str, windows: Iterable[PriceWindow]) -> int:
        rows = [
            (
                timestep,
                w.timestamp,
                w.item_id,
                w.avg_high_price,
                w.high_volume,
                w.avg_low_price,
                w.low_volume,
            )
            for w in windows
        ]
        before = self._conn.total_changes
        self._conn.executemany(
            """
            INSERT INTO price_windows (
                timestep, timestamp, item_id,
                avg_high_price, high_volume, avg_low_price, low_volume
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            rows,
        )
        return self._conn.total_changes - before

    def _migrate(self) -> None:
        (version,) = self._conn.execute("PRAGMA user_version").fetchone()
        if version > SCHEMA_VERSION:
            raise StoreError(
                f"database schema version {version} is newer than this code supports "
                f"({SCHEMA_VERSION}). Update flipwatch or use a different database."
            )
        if version == 0:
            # executescript runs outside the module's transaction handling, so the
            # transaction is spelt out to make a half created schema impossible.
            try:
                self._conn.executescript(
                    f"BEGIN; {_SCHEMA} PRAGMA user_version = {SCHEMA_VERSION}; COMMIT;"
                )
            except sqlite3.Error:
                self._conn.rollback()
                raise
