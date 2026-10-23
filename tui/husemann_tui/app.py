"""Textual-Oberfläche: Übersicht (live), Verlauf (Diagramme aus SQLite), Ereignisse."""

from __future__ import annotations

import time
from collections import deque
from datetime import datetime
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Static, TabbedContent, TabPane, Tree
from textual_plotext import PlotextPlot

from .client import GatewayConfig, PollResult
from .mapping import meta_for, pretty_name
from .poller import Poller
from .store import HEARTBEAT_S, Series, Store, to_float

RANGES = {"1": ("5 Min", 300), "2": ("1 Std", 3600), "3": ("6 Std", 6 * 3600),
          "4": ("24 Std", 86400), "5": ("7 Tage", 7 * 86400), "6": ("30 Tage", 30 * 86400)}
BLOCKS = "▁▂▃▄▅▆▇█"
MAX_PINNED = 4
LIVE_HISTORY = 40
# Farben je angehefteter Reihe: (plotext-Farbcode, Textual-Markup) – identisch in Diagramm und Legende.
PALETTE = [(45, "#00d7ff"), (214, "#ffaf00"), (84, "#5fff87"), (207, "#ff5fff")]
X_TICKS = 6


def spark(values: list[float]) -> str:
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    if hi == lo:
        return BLOCKS[0] * len(values)
    return "".join(BLOCKS[int((v - lo) / (hi - lo) * 7)] for v in values)


def fmt(raw: str, point: str) -> str:
    meta = meta_for(point)
    if meta.component == "binary_sensor":
        return "● an" if raw not in ("0", "0.0", "") else "○ aus"
    val = to_float(raw)
    if val is None:
        return raw
    text = f"{val:,.3f}".rstrip("0").rstrip(".") if abs(val) < 1000 else f"{val:,.1f}"
    return f"{text} {meta.unit}" if meta.unit else text


def num(val: float) -> str:
    return f"{val:,.2f}".rstrip("0").rstrip(".") if abs(val) < 1000 else f"{val:,.0f}"


def segments(data: list[tuple[float, float]], max_gap: float) -> list[list[tuple[float, float]]]:
    """Zerlegt eine Reihe an Lücken > ``max_gap`` (Gateway offline) in Teilstücke."""
    out: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    for t, v in data:
        if cur and t - cur[-1][0] > max_gap:
            out.append(cur)
            cur = []
        cur.append((t, v))
    if cur:
        out.append(cur)
    return out


def stepped(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Treppenkurve für Schaltzustände: Wert bleibt bis zur nächsten Änderung stehen."""
    out: list[tuple[float, float]] = []
    for i, (t, v) in enumerate(points):
        if i:
            out.append((t, points[i - 1][1]))
        out.append((t, v))
    return out


def render_history(plt, reader: Store, pinned: list[Series], current: dict[Series, str],
                   span: float, label: str, now: float | None = None) -> list[str]:
    """Zeichnet die angehefteten Reihen in ``plt`` und liefert die Legenden-/Statistikzeilen.

    - Zeitachse numerisch (Epoch-Sekunden) mit eigenen Ticks → kein Umbruch über Mitternacht.
    - Bis zu zwei Einheiten: die zweite wandert auf die rechte Achse.
    - Lücken (Gateway offline) werden nicht überbrückt, Schaltzustände als Treppe gezeichnet.
    """
    now = now or time.time()
    since = now - span
    bucket = max(span / 400, 1.0)
    max_gap = max(3 * HEARTBEAT_S, 4 * bucket)

    units: list[str] = []
    for s in pinned:
        u = meta_for(s[2]).unit or ""
        if u not in units:
            units.append(u)
    right_unit = units[1] if len(units) == 2 else None

    lines: list[str] = []
    crosses_zero = False
    all_binary = True
    for idx, s in enumerate(pinned):
        color, rich = PALETTE[idx % len(PALETTE)]
        meta = meta_for(s[2])
        unit = meta.unit or ""
        binary = meta.component == "binary_sensor"
        all_binary &= binary
        side = "right" if right_unit is not None and unit == right_unit else "left"
        name = f"{s[1]} {pretty_name(s[2])}"
        data = reader.series(s, since)
        if data and now - data[-1][0] < max_gap:      # letzter Wert gilt bis jetzt
            data = data + [(now, data[-1][1])]
        for seg in segments(data, max_gap):
            pts = stepped(seg) if binary else seg
            if len(pts) == 1:
                pts = pts + [(pts[0][0] + bucket, pts[0][1])]
            plt.plot([t for t, _ in pts], [v for _, v in pts], marker="braille", color=color, yside=side)
        st = reader.stats(s, since)
        if st and st[0] < 0 < st[1]:
            crosses_zero = True
        head = f"[{rich}]━━[/] [b]{name}[/] [dim]{s[0]}[/]"
        raw_now = current.get(s)
        now_txt = f"jetzt {fmt(raw_now, s[2])}" if raw_now is not None else ""
        if st and binary:
            body = f"{now_txt} · {st[3]} Werte"
        elif st:
            body = f"{now_txt} · min {num(st[0])} · Ø {num(st[2])} · max {num(st[1])} {unit} · {st[3]} Werte"
        else:
            body = f"{now_txt} · keine Daten in den letzten {label}"
        lines.append(f"{head}  {body.strip(' ·')}")

    ticks = [since + span * i / X_TICKS for i in range(X_TICKS + 1)]
    form = "%H:%M" if span <= 86400 else "%d.%m. %H:%M"
    plt.xlim(since, now)
    plt.xticks(ticks, [datetime.fromtimestamp(t).strftime(form) for t in ticks])
    if all_binary and pinned:
        plt.ylim(-0.15, 1.15)
        plt.yticks([0, 1], ["aus", "an"])
    elif crosses_zero:
        plt.hline(0, color="gray")
    plt.ylabel(units[0] if units else "", yside="left")
    if right_unit is not None:
        plt.ylabel(right_unit, yside="right")
    plt.title(f"Letzte {label}")
    return lines


class HusemannApp(App):
    TITLE = "Husemann Smart Panel"
    CSS = """
    #side { width: 34; border-right: solid $primary-darken-2; }
    #status { height: auto; padding: 0 1; color: $text-muted; }
    #plot { height: 1fr; }
    #pinned, #stats { height: auto; padding: 0 1; }
    DataTable { height: 1fr; }
    """
    BINDINGS = [
        Binding("q", "quit", "Beenden"),
        Binding("p", "pin", "Anheften"),
        Binding("c", "clear_pins", "Leeren"),
        *[Binding(k, f"range('{k}')", f"{v[0]}", show=True) for k, v in RANGES.items()],
    ]

    def __init__(self, gateways: list[GatewayConfig], interval: float, db: Path | None, retention: float):
        super().__init__()
        self.gateways = gateways
        self.db_path = db
        self.poller = Poller(gateways, interval, db, retention, self._on_result_thread)
        self.reader = Store(db, retention) if db else None
        self.latest: dict[str, PollResult] = {}
        self.last_ok: dict[str, float] = {}
        self.live: dict[Series, deque[float]] = {}
        self.minmax: dict[Series, list[float]] = {}
        self.selected: tuple[str, str] | None = None        # (gw, node)
        self.pinned: list[Series] = []
        self.range_key = "2"
        self._tree_nodes: dict[tuple[str, str], object] = {}
        self._table_dev: tuple[str, str] | None = None

    # ------------------------------------------------------------------ Aufbau
    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="live"):
            with TabPane("Übersicht", id="live"):
                with Horizontal():
                    with Vertical(id="side"):
                        yield Tree("Gateways", id="tree")
                        yield Static(id="status")
                    yield DataTable(id="points", cursor_type="row", zebra_stripes=True)
            with TabPane("Verlauf", id="history"):
                yield Static(id="pinned")
                yield PlotextPlot(id="plot")
                yield Static(id="stats")
            with TabPane("Ereignisse", id="events"):
                yield DataTable(id="evtable", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        tree = self.query_one("#tree", Tree)
        tree.root.expand()
        tree.show_root = False
        self.query_one("#points", DataTable).add_columns("Datenpunkt", "Wert", "Min", "Max", "Trend")
        self.query_one("#evtable", DataTable).add_columns("Zeit", "Gateway", "Gerät", "Punkt", "Von", "Nach")
        self.sub_title = (f"Aufzeichnung → {self.db_path.name}" if self.reader else "ohne Aufzeichnung")
        self.poller.start()
        self.set_interval(1.0, self._refresh_status)
        self.set_interval(5.0, self._refresh_history)
        self.set_interval(3.0, self._refresh_events)
        self._refresh_events()

    def on_unmount(self) -> None:
        self.poller.stop()
        if self.reader:
            self.reader.close()

    # ------------------------------------------------------------------ Daten
    def _on_result_thread(self, res: PollResult) -> None:
        try:
            self.call_from_thread(self._apply_result, res)
        except RuntimeError:     # App beendet sich bereits
            pass

    def _apply_result(self, res: PollResult) -> None:
        self.latest[res.gateway] = res
        if res.error is None:
            self.last_ok[res.gateway] = res.ts
            for node, dev in res.devices.items():
                for point, raw in dev.values.items():
                    val = to_float(raw)
                    if val is None:
                        continue
                    key = (res.gateway, node, point)
                    self.live.setdefault(key, deque(maxlen=LIVE_HISTORY)).append(val)
                    mm = self.minmax.setdefault(key, [val, val])
                    mm[0], mm[1] = min(mm[0], val), max(mm[1], val)
        self._update_tree(res)
        self._update_points()
        self._refresh_status()

    def _update_tree(self, res: PollResult) -> None:
        tree = self.query_one("#tree", Tree)
        gw_key = (res.gateway, "")
        mark = "[green]●[/]" if res.error is None else "[red]○[/]"
        if gw_key not in self._tree_nodes:
            self._tree_nodes[gw_key] = tree.root.add(res.gateway, data=gw_key, expand=True)
        self._tree_nodes[gw_key].set_label(f"{mark} {res.gateway}")
        gw_node = self._tree_nodes[gw_key]
        for node, dev in sorted(res.devices.items(), key=lambda kv: int(kv[0][1:])):
            key = (res.gateway, node)
            label = f"{node}  {dev.model}".rstrip()
            if key not in self._tree_nodes:
                self._tree_nodes[key] = gw_node.add_leaf(label, data=key)
                if self.selected is None:
                    self.selected = key
                    self._table_dev = None
            else:
                self._tree_nodes[key].set_label(label)

    def _update_points(self) -> None:
        if not self.selected:
            return
        gw, node = self.selected
        res = self.latest.get(gw)
        dev = res.devices.get(node) if res else None
        table = self.query_one("#points", DataTable)
        if not dev:
            return
        if self._table_dev != self.selected:
            table.clear()
            self._table_dev = self.selected
        for point, raw in dev.values.items():
            key = (gw, node, point)
            mm = self.minmax.get(key)
            hist = list(self.live.get(key, []))
            binary = meta_for(point).component == "binary_sensor"
            mn = fmt(str(mm[0]), point) if mm and not binary else ""
            mx = fmt(str(mm[1]), point) if mm and not binary else ""
            cells = (pretty_name(point), fmt(raw, point), mn, mx, "" if binary else spark(hist))
            rk = f"{point}"
            if rk in table.rows:
                for col, val in zip(table.columns, cells):
                    table.update_cell(rk, col, val)
            else:
                table.add_row(*cells, key=rk)

    def _refresh_status(self) -> None:
        lines = []
        for gw in self.gateways:
            res = self.latest.get(gw.name)
            if res is None:
                lines.append(f"{gw.name}: verbinde …")
            elif res.error:
                ago = time.time() - self.last_ok[gw.name] if gw.name in self.last_ok else None
                lines.append(f"[red]{gw.name}: {res.error}[/]" + (f"\nzuletzt OK vor {ago:.0f}s" if ago else ""))
            else:
                lines.append(f"{gw.name}: {len(res.devices)} Geräte, {res.latency_ms:.0f} ms, "
                             f"vor {time.time() - res.ts:.0f}s")
        if self.reader:
            n, first = self.reader.db_info()
            span = f"seit {datetime.fromtimestamp(first):%d.%m. %H:%M}" if first else ""
            lines.append(f"Verlauf: {n:,} Werte {span}")
        self.query_one("#status", Static).update("\n".join(lines))

    # ------------------------------------------------------------------ Events
    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data
        if data and data[1]:
            self.selected = data
            self._update_points()

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        self.on_tree_node_selected(event)   # type: ignore[arg-type]

    def _current_series(self) -> Series | None:
        table = self.query_one("#points", DataTable)
        if not self.selected or table.row_count == 0:
            return None
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        except Exception:  # noqa: BLE001
            return None
        return (*self.selected, str(row_key.value))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "points":
            return
        series = self._current_series()
        if series:
            self.pinned = [series]
            self.query_one(TabbedContent).active = "history"
            self._refresh_history()

    def action_pin(self) -> None:
        series = self._current_series()
        if series and series not in self.pinned:
            self.pinned = (self.pinned + [series])[-MAX_PINNED:]
            self.notify(f"Angeheftet: {series[1]} {pretty_name(series[2])}")
            self._refresh_history()

    def action_clear_pins(self) -> None:
        self.pinned = []
        self._refresh_history()

    def action_range(self, key: str) -> None:
        self.range_key = key
        self._refresh_history()

    # ------------------------------------------------------------------ Verlauf
    def _refresh_history(self) -> None:
        plot = self.query_one("#plot", PlotextPlot)
        pinned_w, stats_w = self.query_one("#pinned", Static), self.query_one("#stats", Static)
        label, span = RANGES[self.range_key]
        plt = plot.plt
        plt.clear_figure()
        if not self.reader:
            pinned_w.update("Aufzeichnung ist aus (--no-record) – kein Verlauf verfügbar.")
            plot.refresh()
            return
        if not self.pinned:
            pinned_w.update(f"Zeitraum {label} · Zeile in der Übersicht mit Enter öffnen, mit p weitere anheften")
            stats_w.update("")
            plot.refresh()
            return
        current: dict[Series, str] = {}
        for s in self.pinned:
            res = self.latest.get(s[0])
            dev = res.devices.get(s[1]) if res else None
            if dev and s[2] in dev.values:
                current[s] = dev.values[s[2]]
        lines = render_history(plt, self.reader, self.pinned, current, span, label)
        pinned_w.update(f"Zeitraum {label} (Tasten 1–6) · Enter ersetzt, p heftet an (max. {MAX_PINNED}), c leert")
        stats_w.update("\n".join(lines))
        plot.refresh()

    # ------------------------------------------------------------------ Ereignisse
    def _refresh_events(self) -> None:
        if not self.reader:
            return
        table = self.query_one("#evtable", DataTable)
        table.clear()
        for ts, gw, node, point, old, new in self.reader.events():
            is_gw = point == "Gateway"
            table.add_row(
                f"{datetime.fromtimestamp(ts):%d.%m. %H:%M:%S}", gw, node or "–",
                point if is_gw else pretty_name(point),
                old or "", new if is_gw else ("an" if new not in ("0", "0.0") else "aus"),
            )
