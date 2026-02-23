"""Shared constants, creature frames, terminal helpers, and formatting utilities."""

import sys
import atexit
from datetime import datetime, timezone

from rich.text import Text

# ── Claude Code colour palette ────────────────────────────────────────────────
AMBER   = "#d97706"
AMBER_L = "#fbbf24"
VIOLET  = "#a78bfa"
CYAN    = "#67e8f9"
MUTED   = "#6b7280"
DIM     = "#374151"
WHITE   = "#f3f4f6"
GREEN   = "#34d399"
ORANGE  = "#fb923c"
RED     = "#f87171"
BLUE    = "#60a5fa"
SKIN    = "#c8866b"
SKIN_D  = "#a0674e"

# ── The creature ─────────────────────────────────────────────────────────────
CREATURE_IDLE = [
    [
        f"          [{VIOLET}]*[/]",
        f"          [{VIOLET}]|[/]",
        f"        [{SKIN}]┌────┐[/]",
        f"        [{SKIN}]│[/][{VIOLET}]▪[/] [{VIOLET}]▪[/][{SKIN}]│[/]",
        f"        [{SKIN}]└┬──┬┘[/]",
        f"        [{SKIN}] │  │[/]",
    ],
]

CREATURE_BOUNCE = [
    [
        f"",
        f"          [{VIOLET}]*[/]",
        f"        [{SKIN}]┌─╨──┐[/]",
        f"        [{SKIN}]│[/][{VIOLET}]▪[/] [{VIOLET}]▪[/][{SKIN}]│[/]",
        f"        [{SKIN}]└┬──┬┘[/]",
        f"        [{SKIN}] ╘══╛[/]",
    ],
    [
        f"          [{VIOLET}]*[/]",
        f"          [{VIOLET}]|[/]",
        f"        [{SKIN}]┌────┐[/]",
        f"        [{SKIN}]│[/][{VIOLET}]^[/] [{VIOLET}]^[/][{SKIN}]│[/]",
        f"        [{SKIN}]└────┘[/]",
        f"        [{SKIN}] ╱  ╲[/]",
    ],
    [
        f"          [{VIOLET}]✱[/]",
        f"          [{VIOLET}]|[/]",
        f"        [{SKIN}]┌────┐[/]",
        f"        [{SKIN}]│[/][{VIOLET}]°[/] [{VIOLET}]°[/][{SKIN}]│[/]",
        f"        [{SKIN}]└────┘[/]",
        f"",
    ],
    [
        f"          [{VIOLET}]✱[/]",
        f"          [{VIOLET}]![/]",
        f"        [{SKIN}]┌────┐[/]",
        f"        [{SKIN}]│[/][{VIOLET}]⌒[/] [{VIOLET}]⌒[/][{SKIN}]│[/]",
        f"        [{SKIN}]└────┘[/]",
        f"",
    ],
    [
        f"          [{VIOLET}]✱[/]",
        f"          [{VIOLET}]|[/]",
        f"        [{SKIN}]┌────┐[/]",
        f"        [{SKIN}]│[/][{VIOLET}]°[/] [{VIOLET}]°[/][{SKIN}]│[/]",
        f"        [{SKIN}]└────┘[/]",
        f"",
    ],
    [
        f"",
        f"          [{VIOLET}]*[/]",
        f"        [{SKIN}]┌─╨──┐[/]",
        f"        [{SKIN}]│[/][{VIOLET}]▪[/] [{VIOLET}]▪[/][{SKIN}]│[/]",
        f"        [{SKIN}]└┬──┬┘[/]",
        f"        [{SKIN}] ╘══╛[/]",
    ],
]

BOUNCE_INTERVAL = 120
BOUNCE_FRAME_HOLD = 3


def get_creature_lines(tick):
    """Return the creature lines for the current tick."""
    cycle_pos = tick % BOUNCE_INTERVAL
    bounce_total_ticks = len(CREATURE_BOUNCE) * BOUNCE_FRAME_HOLD

    if cycle_pos < bounce_total_ticks:
        frame_idx = cycle_pos // BOUNCE_FRAME_HOLD
        return CREATURE_BOUNCE[frame_idx]
    else:
        return CREATURE_IDLE[0]


# ── Terminal helpers ──────────────────────────────────────────────────────────

WIDGET_COLS = 46
WIDGET_ROWS = 21


def _cleanup():
    """Restore terminal state on exit."""
    sys.stdout.write("\033[?25h")   # show cursor
    sys.stdout.write("\033[0m")     # reset colors
    sys.stdout.flush()


def _setup_terminal(resize=True, title="claude·usage", rows=None):
    """Clear screen, resize window, hide cursor, set title."""
    atexit.register(_cleanup)
    sys.stdout.write(f"\033]0;{title}\007")

    if resize:
        r = rows or WIDGET_ROWS
        sys.stdout.write(f"\033[8;{r};{WIDGET_COLS}t")

    sys.stdout.write("\033[2J\033[H")
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()


# ── Formatting helpers ────────────────────────────────────────────────────────

def fmt_pct(v):
    if v is None: return "—"
    return f"{round(v)}%"


def fmt_time_until(iso_str):
    """Convert an ISO timestamp to a human-readable 'time until' string."""
    if iso_str is None: return "—"
    try:
        target = datetime.fromisoformat(iso_str)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        secs = max(0, int((target - now).total_seconds()))
        d, remainder = divmod(secs, 86400)
        h, remainder = divmod(remainder, 3600)
        m, s = divmod(remainder, 60)
        if d > 0:   return f"{d}d {h:02d}h"
        if h > 0:   return f"{h}h {m:02d}m"
        if m > 0:   return f"{m}m {s:02d}s"
        return f"{s}s"
    except Exception:
        return "—"


def fmt_tokens(n):
    if n is None: return "—"
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000:     return f"{n/1_000:.1f}K"
    return str(n)


def bar(pct, width=18):
    """Render a progress bar from a percentage (0-100). Returns a rich Text."""
    if pct is None: pct = 0.0
    ratio = min(max(pct / 100.0, 0.0), 1.0)
    filled = round(ratio * width)
    empty  = width - filled

    if   ratio >= 0.90: color = RED
    elif ratio >= 0.70: color = ORANGE
    elif ratio >= 0.40: color = AMBER_L
    else:               color = GREEN

    t = Text()
    t.append("▓" * filled, style=color)
    t.append("░" * empty,  style=DIM)
    return t


def time_bar(iso_reset, window_secs, width=18):
    """Render a time-elapsed bar for a reset window. Returns a rich Text."""
    if iso_reset is None:
        return Text()
    try:
        target = datetime.fromisoformat(iso_reset)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        secs_remaining = max(0, (target - now).total_seconds())
        ratio = min(max(1.0 - secs_remaining / window_secs, 0.0), 1.0)
    except Exception:
        return Text()

    filled = round(ratio * width)
    empty = width - filled

    t = Text()
    t.append("      ")
    t.append("▓" * filled, style=BLUE)
    t.append("░" * empty, style=DIM)
    t.append("  🕐", style=MUTED)
    return t


def fmt_time_ago(iso_str):
    """Convert an ISO timestamp to a human-readable 'time ago' string."""
    if iso_str is None: return "—"
    try:
        target = datetime.fromisoformat(iso_str)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        secs = max(0, int((now - target).total_seconds()))
        if secs < 60:      return f"{secs}s ago"
        mins = secs // 60
        if mins < 60:      return f"{mins}m ago"
        hours = mins // 60
        if hours < 24:     return f"{hours}h ago"
        days = hours // 24
        return f"{days}d ago"
    except Exception:
        return "—"
