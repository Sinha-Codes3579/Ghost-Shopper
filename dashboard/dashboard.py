"""Live Rich terminal dashboard. Replays events.jsonl in simulated real-time and polls the API for metrics.

Usage:
    python dashboard/dashboard.py --events data/events.jsonl --api http://localhost:8000
"""

import argparse
import json
import time
import sys
from datetime import datetime

import httpx
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich import box

STORE_ID = "ST1008"
POLL_INTERVAL = 2.0   # seconds between API polls
REPLAY_SPEED  = 10.0  # replay N seconds of events per real second


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--events", default="data/events.jsonl")
    p.add_argument("--api",    default="http://localhost:8000")
    p.add_argument("--store",  default=STORE_ID)
    return p.parse_args()


def fetch_metrics(api_url: str, store_id: str) -> dict:
    try:
        r = httpx.get(f"{api_url}/stores/{store_id}/metrics", timeout=3)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


def fetch_anomalies(api_url: str, store_id: str) -> list:
    try:
        r = httpx.get(f"{api_url}/stores/{store_id}/anomalies", timeout=3)
        return r.json().get("anomalies", []) if r.status_code == 200 else []
    except Exception:
        return []


def fetch_funnel(api_url: str, store_id: str) -> dict:
    try:
        r = httpx.get(f"{api_url}/stores/{store_id}/funnel", timeout=3)
        return r.json().get("funnel", {}) if r.status_code == 200 else {}
    except Exception:
        return {}


def post_event(api_url: str, event: dict):
    try:
        httpx.post(f"{api_url}/events/ingest",
                   json={"events": [event]}, timeout=3)
    except Exception:
        pass


def build_layout(metrics: dict, anomalies: list, funnel: dict,
                 last_event: str, event_count: int) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=3),
    )
    layout["body"].split_row(
        Layout(name="left"),
        Layout(name="right"),
    )

    # Header
    layout["header"].update(Panel(
        f"[bold cyan]Store Intelligence — Brigade Bangalore (ST1008)[/]  "
        f"[dim]{datetime.now().strftime('%H:%M:%S')}[/]",
        box=box.HORIZONTALS,
    ))

    # Metrics panel
    m_table = Table(box=box.SIMPLE, show_header=False, padding=(0,1))
    m_table.add_column("Metric", style="dim", width=22)
    m_table.add_column("Value",  style="bold white")
    m_table.add_row("Unique Visitors",   str(metrics.get("unique_visitors", "-")))
    m_table.add_row("Conversion Rate",   f"{metrics.get('conversion_rate', 0)*100:.1f}%")
    m_table.add_row("Queue Depth",       str(metrics.get("queue_depth", 0)))
    m_table.add_row("Abandonment Rate",  f"{metrics.get('abandonment_rate', 0)*100:.1f}%")
    m_table.add_row("Events ingested",   str(event_count))
    layout["left"].update(Panel(m_table, title="[bold]Live Metrics[/]", border_style="cyan"))

    # Right side: funnel + anomalies
    layout["right"].split_column(
        Layout(name="funnel", ratio=1),
        Layout(name="alerts", ratio=1),
    )

    # Funnel
    f_table = Table(box=box.SIMPLE, show_header=False, padding=(0,1))
    f_table.add_column("Stage", style="dim", width=14)
    f_table.add_column("Count", style="white")
    f_table.add_column("Drop-off", style="red")
    for stage in ["entry", "zone_visit", "billing", "purchase"]:
        s = funnel.get(stage, {})
        f_table.add_row(
            stage.replace("_", " ").title(),
            str(s.get("count", "-")),
            f"{s.get('drop_off_pct', 0):.0f}%" if s else "-",
        )
    layout["funnel"].update(Panel(f_table, title="[bold]Conversion Funnel[/]", border_style="blue"))

    # Anomalies
    if anomalies:
        a_table = Table(box=box.SIMPLE, show_header=False, padding=(0,1))
        a_table.add_column("Sev", width=8)
        a_table.add_column("Type")
        SEV_STYLE = {"CRITICAL": "bold red", "WARN": "yellow", "INFO": "dim"}
        for a in anomalies[:4]:
            sev = a.get("severity", "INFO")
            a_table.add_row(
                f"[{SEV_STYLE.get(sev,'white')}]{sev}[/]",
                a.get("type", ""),
            )
        content = a_table
    else:
        content = Panel("[green]No active anomalies[/]", border_style="green")

    layout["alerts"].update(Panel(content, title="[bold]Anomalies[/]", border_style="red" if anomalies else "green"))

    # Footer
    layout["footer"].update(Panel(
        f"[dim]Last event: {last_event or 'none yet'}  |  "
        f"API: {STORE_ID}  |  Press Ctrl+C to stop[/]",
        box=box.HORIZONTALS,
    ))
    return layout


def replay_events(events_path: str, api_url: str, store_id: str):
    console = Console()

    # Load events
    events = []
    try:
        with open(events_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    except FileNotFoundError:
        console.print(f"[yellow]events.jsonl not found at {events_path} — waiting for pipeline...[/]")
        events = []

    metrics    = {}
    anomalies  = []
    funnel     = {}
    last_event = ""
    event_count = 0

    with Live(console=console, refresh_per_second=2) as live:
        for i, event in enumerate(events):
            post_event(api_url, event)
            event_count += 1
            last_event = event.get("timestamp", "")

            if i % 10 == 0:
                metrics   = fetch_metrics(api_url, store_id)
                anomalies = fetch_anomalies(api_url, store_id)
                funnel    = fetch_funnel(api_url, store_id)

            live.update(build_layout(metrics, anomalies, funnel, last_event, event_count))
            time.sleep(1.0 / REPLAY_SPEED)

        # Keep dashboard live after replay
        console.print("[green]Replay complete. Dashboard live. Press Ctrl+C to stop.[/]")
        while True:
            metrics   = fetch_metrics(api_url, store_id)
            anomalies = fetch_anomalies(api_url, store_id)
            funnel    = fetch_funnel(api_url, store_id)
            live.update(build_layout(metrics, anomalies, funnel, last_event, event_count))
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    args = parse_args()
    try:
        replay_events(args.events, args.api, args.store)
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
