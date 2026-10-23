"""SQLite-Verlauf: speichert Messwerte bei Änderung (+ Heartbeat) und Ereignisse."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .client import PollResult
from .mapping import meta_for

HEARTBEAT_S = 60          # spätestens alle 60 s ein Wert je Reihe, auch ohne Änderung
PRUNE_EVERY_S = 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts REAL NOT NULL, gw TEXT NOT NULL, node TEXT NOT NULL,
    point TEXT NOT NULL, value REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_samples ON samples (gw, node, point, ts);
CREATE TABLE IF NOT EXISTS events (
    ts REAL NOT NULL, gw TEXT NOT NULL, node TEXT NOT NULL,
    point TEXT NOT NULL, old TEXT, new TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events ON events (ts);
"""

Series = tuple[str, str, str]   # (gateway, node, point)


def default_db_path() -> Path:
    return Path.home() / ".local" / "share" / "husemann-tui" / "history.db"


def to_float(raw: str) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


class Store:
    """Eine Instanz je Nutzer (Poller-Schreiber bzw. UI-Leser); WAL erlaubt parallele Zugriffe.

    Wird eine Instanz von mehreren Threads genutzt, muss der Aufrufer sperren (siehe Poller).
    """

    def __init__(self, path: Path, retention_days: float = 30):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.retention_s = retention_days * 86400
        self._last: dict[Series, tuple[float, float]] = {}      # series -> (ts, value)
        self._last_raw: dict[Series, str] = {}                  # nur binäre Punkte
        self._gw_ok: dict[str, bool] = {}
        self._last_prune = 0.0

    # ---------------------------------------------------------------- schreiben
    def record(self, res: PollResult) -> None:
        ok = res.error is None
        prev_ok = self._gw_ok.get(res.gateway)
        if prev_ok is not None and prev_ok != ok:
            self.db.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?)",
                (res.ts, res.gateway, "", "Gateway", "online" if prev_ok else "offline",
                 "online" if ok else f"offline ({res.error})"),
            )
        self._gw_ok[res.gateway] = ok

        rows, events = [], []
        for node, dev in res.devices.items():
            for point, raw in dev.values.items():
                val = to_float(raw)
                if val is None:
                    continue
                key = (res.gateway, node, point)
                last = self._last.get(key)
                if last is None or last[1] != val or res.ts - last[0] >= HEARTBEAT_S:
                    rows.append((res.ts, *key, val))
                    self._last[key] = (res.ts, val)
                if meta_for(point).component == "binary_sensor":
                    old = self._last_raw.get(key)
                    if old is not None and old != raw:
                        events.append((res.ts, *key, old, raw))
                    self._last_raw[key] = raw
        if rows:
            self.db.executemany("INSERT INTO samples VALUES (?,?,?,?,?)", rows)
        if events:
            self.db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?)", events)
        if rows or events or prev_ok != ok:
            self.db.commit()
        if res.ts - self._last_prune > PRUNE_EVERY_S:
            self.prune(res.ts)

    def prune(self, now: float | None = None) -> None:
        now = now or time.time()
        cutoff = now - self.retention_s
        self.db.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
        self.db.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        self.db.commit()
        self._last_prune = now

    # ------------------------------------------------------------------ lesen
    def series(self, s: Series, since: float, max_points: int = 400) -> list[tuple[float, float]]:
        """Reihe ab ``since``, auf ~``max_points`` Buckets gemittelt."""
        bucket = max((time.time() - since) / max_points, 1.0)
        cur = self.db.execute(
            "SELECT CAST(ts/? AS INTEGER)*? AS t, AVG(value) FROM samples "
            "WHERE gw=? AND node=? AND point=? AND ts>=? GROUP BY t ORDER BY t",
            (bucket, bucket, *s, since),
        )
        return [(t, v) for t, v in cur.fetchall()]

    def stats(self, s: Series, since: float) -> tuple[float, float, float, int] | None:
        row = self.db.execute(
            "SELECT MIN(value), MAX(value), AVG(value), COUNT(*) FROM samples "
            "WHERE gw=? AND node=? AND point=? AND ts>=?", (*s, since)
        ).fetchone()
        return row if row and row[3] else None

    def events(self, limit: int = 500) -> list[tuple]:
        return self.db.execute(
            "SELECT ts, gw, node, point, old, new FROM events ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()

    def db_info(self) -> tuple[int, float | None]:
        n, first = self.db.execute("SELECT COUNT(*), MIN(ts) FROM samples").fetchone()
        return n, first

    def close(self) -> None:
        self.db.commit()
        self.db.close()
