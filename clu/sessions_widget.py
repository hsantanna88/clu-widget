"""
clu sessions — terminal widget showing local Claude Code sessions.
"""

import sys
import time
import argparse
from datetime import datetime

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich import box

from clu.common import (
    AMBER, VIOLET, CYAN, MUTED, DIM, WHITE, GREEN, RED,
    WIDGET_COLS, get_creature_lines, _cleanup, _setup_terminal, fmt_time_ago,
)
from clu.sessions import scan_sessions


SESSIONS_ROWS = 26


def make_sessions_widget(sessions, tick=0, refresh_secs=10):
    """Build the sessions view renderable."""

    now_str = datetime.now().strftime("%H:%M")
    creature_lines = get_creature_lines(tick)

    rows = []

    for line in creature_lines:
        rows.append(Text.from_markup(line) if line else Text())

    # Header
    header = Text()
    header.append(f"  ◆ ", style=f"bold {AMBER}")
    header.append("claude", style=f"bold {WHITE}")
    header.append("·", style=MUTED)
    header.append("sessions", style=f"bold {VIOLET}")
    header.append(f"    ", style="")
    header.append("●", style=f"bold {GREEN}")
    header.append(f" {now_str}", style=MUTED)
    rows.append(header)
    rows.append(Text())

    if not sessions:
        rows.append(Text("  no sessions found", style=f"italic {MUTED}"))
    else:
        total_turns = sum(s["turn_count"] for s in sessions)
        summary = Text()
        summary.append(f"  {len(sessions)} sessions", style=f"bold {WHITE}")
        summary.append(f" · ", style=MUTED)
        summary.append(f"{total_turns} turns", style=f"{CYAN}")
        rows.append(summary)

        # Show up to 5 sessions
        for s in sessions[:5]:
            slug_display = s["slug"]
            if len(slug_display) > 28:
                slug_display = slug_display[:27] + "…"

            # Session slug line
            slug_line = Text()
            slug_line.append("  ▸ ", style=f"bold {AMBER}")
            slug_line.append(slug_display, style=f"bold {WHITE}")
            if s["is_active"]:
                slug_line.append("   ●", style=f"bold {GREEN}")
            rows.append(slug_line)

            # Details line: project · model · turns
            detail = Text()
            detail.append(f"    /{s['project']}", style=MUTED)
            detail.append(f" · ", style=DIM)
            detail.append(s["model"], style=VIOLET)
            detail.append(f" · ", style=DIM)
            detail.append(f"{s['turn_count']}t", style=CYAN)
            rows.append(detail)

            # Time ago line
            time_line = Text()
            time_line.append(f"    {fmt_time_ago(s['last_ts'])}", style=MUTED)
            rows.append(time_line)
            rows.append(Text())

    footer = Text()
    footer.append(f"  refreshes every {refresh_secs}s", style=MUTED)
    rows.append(footer)

    combined = Text("\n").join(rows)

    panel = Panel(
        combined,
        border_style=DIM,
        padding=(0, 0),
        box=box.SIMPLE,
    )
    return panel


def main():
    parser = argparse.ArgumentParser(description="Claude Code sessions widget")
    parser.add_argument("--refresh", type=int, default=10,
                        help="Refresh interval in seconds (default: 10)")
    parser.add_argument("--no-resize", action="store_true",
                        help="Don't resize the terminal window")
    parser.add_argument("--limit", type=int, default=10,
                        help="Max sessions to scan (default: 10)")
    args = parser.parse_args()

    refresh_secs = args.refresh
    console = Console(width=WIDGET_COLS, highlight=False)

    _setup_terminal(
        resize=not args.no_resize,
        title="claude·sessions",
        rows=SESSIONS_ROWS,
    )

    sessions = []
    tick = 0
    next_fetch = 0

    with Live(make_sessions_widget(sessions, tick, refresh_secs),
              console=console,
              refresh_per_second=2,
              transient=False) as live:
        while True:
            now_ts = time.time()

            if now_ts >= next_fetch:
                sessions = scan_sessions(limit=args.limit)
                next_fetch = now_ts + refresh_secs

            live.update(make_sessions_widget(sessions, tick, refresh_secs))
            tick += 1
            time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _cleanup()
        print()
