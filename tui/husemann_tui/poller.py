"""Hintergrund-Poller: ein Thread je Gateway, schreibt in den Verlauf und meldet per Callback."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from .client import GatewayConfig, PollResult, TixiClient
from .store import Store


class Poller:
    def __init__(
        self,
        gateways: list[GatewayConfig],
        interval: float,
        db_path: Path | None,
        retention_days: float,
        on_result: Callable[[PollResult], None] | None = None,
    ):
        self.gateways = gateways
        self.interval = interval
        self.db_path = db_path
        self.retention_days = retention_days
        self.on_result = on_result
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._store_lock = threading.Lock()
        self._store: Store | None = None

    def start(self) -> None:
        if self.db_path:
            # Ein Schreiber für alle Gateway-Threads (Zugriff über Lock).
            self._store = Store(self.db_path, self.retention_days)
            self._store.prune()
        for gw in self.gateways:
            t = threading.Thread(target=self._run, args=(gw,), name=f"poll-{gw.name}", daemon=True)
            t.start()
            self._threads.append(t)

    def _run(self, gw: GatewayConfig) -> None:
        client = TixiClient(gw)
        while not self._stop.is_set():
            res = client.poll()
            if self._store:
                with self._store_lock:
                    self._store.record(res)
            if self.on_result:
                self.on_result(res)
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=3)
        if self._store:
            with self._store_lock:
                self._store.close()
