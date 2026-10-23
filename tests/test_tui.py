"""TUI: Verlaufsdiagramm (Segmente, Treppen, Achsen) und Start der App ohne Netz."""

from __future__ import annotations

import io
import math
import re
import time

import plotext as plt
import pytest
from rich.console import Console

from husemann_tui.app import HusemannApp, render_history, segments, spark, stepped
from husemann_tui.client import Device, GatewayConfig, PollResult
from husemann_tui.store import Store

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    return ANSI.sub("", text)


def test_helpers():
    assert segments([(0, 1), (10, 2), (500, 3), (510, 4)], max_gap=100) == [[(0, 1), (10, 2)], [(500, 3), (510, 4)]]
    assert stepped([(0, 0.0), (10, 1.0), (20, 0.0)]) == [(0, 0.0), (10, 0.0), (10, 1.0), (20, 1.0), (20, 0.0)]
    assert spark([1, 2, 3]) == "▁▄█" and spark([5]) == ""


@pytest.fixture
def store(tmp_path):
    st = Store(tmp_path / "history.db")
    now = time.time()
    rows = []
    for i in range(0, 3600, 10):
        if 1500 < i < 2100:                      # Gateway offline -> Lücke
            continue
        t = now - 3600 + i
        rows.append((t, "Gateway 1", "D11", "RealPower_L1_N", -30 + 8 * math.sin(i / 400)))
        rows.append((t, "Gateway 1", "D11", "Voltage_L1_N", 232 + 3 * math.sin(i / 700)))
        rows.append((t, "Gateway 1", "D99", "Input01", 1.0 if (i // 600) % 2 else 0.0))
    st.db.executemany("INSERT INTO samples VALUES (?,?,?,?,?)", rows)
    st.db.commit()
    yield st
    st.close()


def test_render_history_two_units(store):
    plt.clear_figure()
    pinned = [("Gateway 1", "D11", "RealPower_L1_N"), ("Gateway 1", "D11", "Voltage_L1_N")]
    lines = render_history(plt, store, pinned, {pinned[0]: "-31.2"}, 3600, "1 Std")
    assert len(lines) == 2
    assert "jetzt -31.2 kW" in lines[0] and "min" in lines[0] and "Werte" in lines[0]
    assert "Gateway 1" in lines[1] and "V ·" in lines[1]
    out = plain(plt.build())
    assert "kW" in out and out.rstrip().endswith("V")        # linke und rechte Achsenbeschriftung
    assert "Letzte 1 Std" in out


def test_render_history_binary_and_empty(store):
    plt.clear_figure()
    lines = render_history(plt, store, [("Gateway 1", "D99", "Input01")], {}, 3600, "1 Std")
    out = plain(plt.build())
    assert " an┤" in out and "aus┤" in out and "Werte" in lines[0]

    plt.clear_figure()
    lines = render_history(plt, store, [("Gateway 1", "D12", "Frequency")], {}, 300, "5 Min")
    assert "keine Daten" in lines[0]


async def test_app_renders_history_tab(tmp_path, store):
    app = HusemannApp([GatewayConfig("Gateway 1", "192.0.2.10")], 9999, tmp_path / "history.db", 30)
    app.poller.start = lambda: None            # kein echtes Netz
    app.poller.stop = lambda: None
    async with app.run_test(size=(120, 40)) as pilot:
        res = PollResult("Gateway 1", time.time(), {"D11": Device("D11", "EM330",
                         {"RealPower_L1_N": "-31.2", "Voltage_L1_N": "233.4", "DeviceState": "1"})})
        app._apply_result(res)
        assert app.selected == ("Gateway 1", "D11")
        assert app.query_one("#points").row_count == 3
        app.pinned = [("Gateway 1", "D11", "RealPower_L1_N"), ("Gateway 1", "D11", "Voltage_L1_N")]
        app.query_one("TabbedContent").active = "history"
        app._refresh_history()
        await pilot.pause()
        console = Console(record=True, width=120, force_terminal=True, file=io.StringIO())
        console.print(app.screen._compositor.render_full_update())
        text = console.export_text()
        assert "Letzte 1 Std" in text and "jetzt -31.2 kW" in text and "kW" in text
