from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.clients import MqttRuntimeConfig

MAX_TELEGRAMS_PER_METER = 20


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def init(self) -> None:
        await asyncio.to_thread(self._init_sync)

    async def list_keys(self) -> dict[str, str]:
        return await asyncio.to_thread(self._list_keys_sync)

    async def list_known_meters(self) -> list[dict]:
        return await asyncio.to_thread(self._list_known_meters_sync)

    async def list_pending_meters(self) -> list[dict]:
        return await asyncio.to_thread(self._list_pending_meters_sync)

    async def get_key(self, meter_id: str) -> str | None:
        return await asyncio.to_thread(self._get_key_sync, meter_id)

    async def set_key(self, meter_id: str, key_hex: str) -> None:
        await asyncio.to_thread(self._set_key_sync, meter_id, key_hex)

    async def delete_key(self, meter_id: str) -> None:
        await asyncio.to_thread(self._delete_key_sync, meter_id)

    async def add_telegram(self, meter_id: str, gateway: str, status: str, payload: dict, parsed: dict | None) -> None:
        await asyncio.to_thread(self._add_telegram_sync, meter_id, gateway, status, payload, parsed)

    async def list_telegrams(self, meter_id: str, limit: int = MAX_TELEGRAMS_PER_METER) -> list[dict]:
        return await asyncio.to_thread(self._list_telegrams_sync, meter_id, limit)

    async def get_telegram_detail(self, meter_id: str, telegram_id: int) -> dict | None:
        return await asyncio.to_thread(self._get_telegram_detail_sync, meter_id, telegram_id)

    async def mark_pending_meter(
        self,
        meter_id: str,
        manuf: int | None,
        dev_type: int | None,
        version: int | None,
        ci: int | None,
    ) -> None:
        await asyncio.to_thread(self._mark_pending_meter_sync, meter_id, manuf, dev_type, version, ci)

    async def clear_pending_meter(self, meter_id: str) -> None:
        await asyncio.to_thread(self._clear_pending_meter_sync, meter_id)

    async def get_mqtt_config(self) -> Optional[MqttRuntimeConfig]:
        return await asyncio.to_thread(self._get_mqtt_config_sync)

    async def set_mqtt_config(self, config: MqttRuntimeConfig) -> None:
        await asyncio.to_thread(self._set_mqtt_config_sync, config)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _init_sync(self) -> None:
        path = Path(self._db_path)
        if path.parent != Path("."):
            path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                ""
                "CREATE TABLE IF NOT EXISTS meter_keys ("
                "meter_id TEXT PRIMARY KEY,"
                "key_hex TEXT NOT NULL,"
                "updated_at TEXT NOT NULL"
                ")"
            )
            conn.execute(
                ""
                "CREATE TABLE IF NOT EXISTS mqtt_config ("
                "id INTEGER PRIMARY KEY CHECK (id = 1),"
                "url TEXT NOT NULL,"
                "username TEXT,"
                "password TEXT,"
                "topic_template TEXT NOT NULL,"
                "qos INTEGER NOT NULL,"
                "retain INTEGER NOT NULL,"
                "updated_at TEXT NOT NULL"
                ")"
            )
            conn.execute(
                ""
                "CREATE TABLE IF NOT EXISTS pending_meters ("
                "meter_id TEXT PRIMARY KEY,"
                "manuf INTEGER,"
                "dev_type INTEGER,"
                "version INTEGER,"
                "ci INTEGER,"
                "first_seen TEXT NOT NULL,"
                "last_seen TEXT NOT NULL"
                ")"
            )
            conn.execute(
                ""
                "CREATE TABLE IF NOT EXISTS telegrams ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "meter_id TEXT NOT NULL,"
                "received_at TEXT NOT NULL,"
                "status TEXT NOT NULL,"
                "gateway TEXT,"
                "payload_json TEXT NOT NULL,"
                "parsed_json TEXT"
                ")"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_telegrams_meter ON telegrams (meter_id, received_at DESC)"
            )
            conn.commit()

    def _list_keys_sync(self) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT meter_id, key_hex FROM meter_keys ORDER BY meter_id").fetchall()
        return {row[0]: row[1] for row in rows}

    def _list_known_meters_sync(self) -> list[dict]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    ""
                    "SELECT k.meter_id, k.updated_at, "
                    "COALESCE(SUM(CASE WHEN t.status = 'published' THEN 1 ELSE 0 END), 0) AS forwarded_count, "
                    "MAX(t.received_at) AS last_seen "
                    "FROM meter_keys k "
                    "LEFT JOIN telegrams t ON t.meter_id = k.meter_id "
                    "GROUP BY k.meter_id, k.updated_at "
                    "ORDER BY k.meter_id"
                    ""
                ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                self._init_sync()
                with self._connect() as conn:
                    rows = conn.execute(
                        "SELECT meter_id, updated_at, 0 AS forwarded_count, NULL AS last_seen "
                        "FROM meter_keys ORDER BY meter_id"
                    ).fetchall()
            else:
                raise
        return [
            {
                "meter_id": row[0],
                "updated_at": row[1],
                "forwarded_count": row[2],
                "last_seen": row[3],
            }
            for row in rows
        ]

    def _list_pending_meters_sync(self) -> list[dict]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    ""
                    "SELECT meter_id, manuf, dev_type, version, ci, last_seen "
                    "FROM pending_meters ORDER BY last_seen DESC"
                    ""
                ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                self._init_sync()
                rows = []
            else:
                raise
        return [
            {
                "meter_id": row[0],
                "manuf": row[1],
                "dev_type": row[2],
                "version": row[3],
                "ci": row[4],
                "last_seen": row[5],
            }
            for row in rows
        ]

    def _get_key_sync(self, meter_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT key_hex FROM meter_keys WHERE meter_id = ?",
                (meter_id,),
            ).fetchone()
        if row:
            return row[0]
        return None

    def _set_key_sync(self, meter_id: str, key_hex: str) -> None:
        with self._connect() as conn:
            conn.execute(
                ""
                "INSERT INTO meter_keys (meter_id, key_hex, updated_at)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(meter_id) DO UPDATE SET"
                " key_hex = excluded.key_hex,"
                " updated_at = excluded.updated_at"
                "",
                (meter_id, key_hex, _utc_iso()),
            )
            conn.execute("DELETE FROM pending_meters WHERE meter_id = ?", (meter_id,))
            conn.commit()

    def _delete_key_sync(self, meter_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM meter_keys WHERE meter_id = ?", (meter_id,))
            conn.execute("DELETE FROM pending_meters WHERE meter_id = ?", (meter_id,))
            conn.execute("DELETE FROM telegrams WHERE meter_id = ?", (meter_id,))
            conn.commit()

    def _mark_pending_meter_sync(
        self,
        meter_id: str,
        manuf: int | None,
        dev_type: int | None,
        version: int | None,
        ci: int | None,
    ) -> None:
        now = _utc_iso()
        with self._connect() as conn:
            conn.execute(
                ""
                "INSERT INTO pending_meters "
                "(meter_id, manuf, dev_type, version, ci, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(meter_id) DO UPDATE SET "
                "manuf = excluded.manuf, "
                "dev_type = excluded.dev_type, "
                "version = excluded.version, "
                "ci = excluded.ci, "
                "last_seen = excluded.last_seen"
                "",
                (meter_id, manuf, dev_type, version, ci, now, now),
            )
            conn.commit()

    def _clear_pending_meter_sync(self, meter_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM pending_meters WHERE meter_id = ?", (meter_id,))
            conn.commit()

    def _add_telegram_sync(self, meter_id: str, gateway: str, status: str, payload: dict, parsed: dict | None) -> None:
        payload_json = json.dumps(payload, separators=(",", ":"), default=str)
        parsed_json = json.dumps(parsed, separators=(",", ":"), default=str) if parsed is not None else None
        received_at = _utc_iso()
        with self._connect() as conn:
            conn.execute(
                ""
                "INSERT INTO telegrams (meter_id, received_at, status, gateway, payload_json, parsed_json) "
                "VALUES (?, ?, ?, ?, ?, ?)"
                "",
                (meter_id, received_at, status, gateway, payload_json, parsed_json),
            )
            conn.execute(
                ""
                "DELETE FROM telegrams "
                "WHERE meter_id = ? AND id NOT IN ("
                "  SELECT id FROM telegrams WHERE meter_id = ? "
                "  ORDER BY received_at DESC LIMIT ?"
                ")"
                "",
                (meter_id, meter_id, MAX_TELEGRAMS_PER_METER),
            )
            conn.commit()

    def _list_telegrams_sync(self, meter_id: str, limit: int) -> list[dict]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    ""
                    "SELECT id, received_at, status "
                    "FROM telegrams WHERE meter_id = ? "
                    "ORDER BY received_at DESC LIMIT ?"
                    "",
                    (meter_id, limit),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                self._init_sync()
                rows = []
            else:
                raise
        return [{"id": row[0], "received_at": row[1], "status": row[2]} for row in rows]

    def _get_telegram_detail_sync(self, meter_id: str, telegram_id: int) -> dict | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    ""
                    "SELECT payload_json, parsed_json, received_at, status "
                    "FROM telegrams WHERE meter_id = ? AND id = ?"
                    "",
                    (meter_id, telegram_id),
                ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                self._init_sync()
                row = None
            else:
                raise
        if not row:
            return None
        payload = json.loads(row[0])
        parsed = json.loads(row[1]) if row[1] else None
        return {
            "id": telegram_id,
            "received_at": row[2],
            "status": row[3],
            "payload": payload,
            "parsed": parsed,
        }

    def _get_mqtt_config_sync(self) -> Optional[MqttRuntimeConfig]:
        with self._connect() as conn:
            row = conn.execute(
                ""
                "SELECT url, username, password, topic_template, qos, retain"
                " FROM mqtt_config WHERE id = 1"
                ""
            ).fetchone()
        if not row:
            return None
        return MqttRuntimeConfig(
            url=row[0],
            username=row[1],
            password=row[2],
            topic_template=row[3],
            qos=int(row[4]),
            retain=bool(row[5]),
        )

    def _set_mqtt_config_sync(self, config: MqttRuntimeConfig) -> None:
        with self._connect() as conn:
            conn.execute(
                ""
                "INSERT INTO mqtt_config (id, url, username, password, topic_template, qos, retain, updated_at)"
                " VALUES (1, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET"
                " url = excluded.url,"
                " username = excluded.username,"
                " password = excluded.password,"
                " topic_template = excluded.topic_template,"
                " qos = excluded.qos,"
                " retain = excluded.retain,"
                " updated_at = excluded.updated_at"
                "",
                (
                    config.url,
                    config.username,
                    config.password,
                    config.topic_template,
                    config.qos,
                    1 if config.retain else 0,
                    _utc_iso(),
                ),
            )
            conn.commit()
