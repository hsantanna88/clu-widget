#!/usr/bin/env python3
"""
clu — Claude Usage Monitor

Widget mode (default):
    clu                          # cute animated widget
    clu --refresh 90             # custom refresh interval

Dashboard mode:
    clu --dash                   # full-terminal dashboard
    clu --dash --data-dir /mnt/hpc/.claude   # include HPC data

Requirements: pip install rich requests
"""

import sys
import os
import json
import time
import random
import argparse
import subprocess
import atexit
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("clu-widget")
except Exception:
    __version__ = "dev"

try:
    import requests
except ImportError:
    print("Missing: pip install rich requests --break-system-packages")
    sys.exit(1)

try:
    import cloudscraper
    import logging
    logging.getLogger("cloudscraper").setLevel(logging.ERROR)
    logging.getLogger("urllib3").setLevel(logging.ERROR)
except ImportError:
    cloudscraper = None

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.columns import Columns
from rich import box
from rich.style import Style
from rich.align import Align
from rich.padding import Padding
from rich.rule import Rule
from rich.layout import Layout

# ── Claude Code colour palette ────────────────────────────────────────────────
AMBER   = "#d97706"
AMBER_L = "#fbbf24"
VIOLET  = "#a78bfa"
VIOLET_D = "#7c3aed"
CYAN    = "#67e8f9"
MUTED   = "#6b7280"
DIM     = "#374151"
DIM_D   = "#1f2937"
WHITE   = "#f3f4f6"
GREEN   = "#34d399"
ORANGE  = "#fb923c"
RED     = "#f87171"
RED_D   = "#dc2626"
SKIN    = "#c8866b"
SKIN_D  = "#a0674e"
BLUE    = "#60a5fa"
PINK    = "#f472b6"
YELLOW  = "#fde047"
LIME    = "#a3e635"

# ── Mood-reactive creature for dashboard ─────────────────────────────────────
# Moods: chill (0-30%), cozy (30-50%), warm (50-70%), hot (70-90%), fire (90%+)

_EYE_STYLES = ["⌒ ⌒", "◕ ◕", "● ●", "◠ ◠", "◉ ◉", "◦ ◦", "• •", "○ ○"]

def _get_eyes_pair(tick):
    """Return (left_eye, right_eye) rotating through styles."""
    style_idx = (tick // 40) % len(_EYE_STYLES)
    pair = _EYE_STYLES[style_idx]
    left, right = pair.split(" ")
    return left, right

def get_dash_creature(utilization, tick):
    """Get a happy bouncy creature — line-art style with rotating eyes."""
    bounce = (tick % 80) < 12
    frame = (tick % 80) // 3 if bounce else -1

    left, right = _get_eyes_pair(tick)
    ant   = f"   [{VIOLET}]*[/]"
    stk   = f"   [{VIOLET}]|[/]"
    top   = f" [{SKIN}]┌────┐[/]"
    face  = f" [{SKIN}]│[/][{VIOLET}]{left}[/] [{VIOLET}]{right}[/][{SKIN}]│[/]"
    blink = f" [{SKIN}]│[/][{VIOLET}]^[/] [{VIOLET}]^[/][{SKIN}]│[/]"
    chin  = f" [{SKIN}]└┬──┬┘[/]"
    legs  = f" [{SKIN}] │  │[/]"

    is_blink = (tick % 20) in (0, 1)
    eyes = blink if is_blink else face

    if bounce and 0 <= frame < 4:
        frames = [
            [ant, stk, top, eyes, chin, legs],
            [f"", ant, top, blink, f" [{SKIN}]└────┘[/]", f""],
            [ant, stk, top, blink, f" [{SKIN}]└────┘[/]", f""],
            [f"", ant, top, eyes, chin, legs],
        ]
        return frames[frame]
    return [ant, stk, top, eyes, chin, legs]


def get_creature_speech(utilization, tick, error_msg=None):
    """Get a speech bubble — cheerful normally, concerned on errors."""
    if error_msg:
        sad_phrases = [
            "ugh, hold on...", "not again~", "waiting...", "brb~",
            "oops!", "one sec...", "hmm...", "hang tight~",
        ]
        idx = (tick // 20) % len(sad_phrases)
        return sad_phrases[idx]
    phrases = [
        "let's go!", "vibing~", "all good!", "smooth sailing~",
        "doing great!", "feeling good!", "cruising along~", "no worries!",
        "looking good!", "keep going!", "nice work!", "on a roll!",
    ]
    idx = (tick // 20) % len(phrases)
    return phrases[idx]


# ── Original widget creature (wider spacing for 46-col widget) ───────────────
_W_ANT   = f"          [{VIOLET}]*[/]"
_W_STK   = f"          [{VIOLET}]|[/]"
_W_TOP   = f"        [{SKIN}]┌────┐[/]"
_W_BLINK = f"        [{SKIN}]│[/][{VIOLET}]^[/] [{VIOLET}]^[/][{SKIN}]│[/]"
_W_CHIN  = f"        [{SKIN}]└┬──┬┘[/]"
_W_LEGS  = f"        [{SKIN}] │  │[/]"

BOUNCE_INTERVAL = 120
BOUNCE_FRAME_HOLD = 3

def _w_face(tick):
    left, right = _get_eyes_pair(tick)
    return f"        [{SKIN}]│[/][{VIOLET}]{left}[/] [{VIOLET}]{right}[/][{SKIN}]│[/]"

def get_creature_lines_widget(tick):
    """Return the creature lines for widget mode — line-art with rotating eyes."""
    face = _w_face(tick)
    cycle_pos = tick % BOUNCE_INTERVAL
    bounce_frames = [
        [_W_ANT, _W_STK, _W_TOP, face,     _W_CHIN, _W_LEGS],
        [f"",    _W_ANT, _W_TOP, _W_BLINK, f"        [{SKIN}]└────┘[/]", f""],
        [_W_ANT, _W_STK, _W_TOP, _W_BLINK, f"        [{SKIN}]└────┘[/]", f""],
        [f"",    _W_ANT, _W_TOP, face,      _W_CHIN, _W_LEGS],
    ]
    bounce_total_ticks = len(bounce_frames) * BOUNCE_FRAME_HOLD
    if cycle_pos < bounce_total_ticks:
        frame_idx = cycle_pos // BOUNCE_FRAME_HOLD
        return bounce_frames[frame_idx]
    # Blink every ~10 seconds for 1 second
    elif (tick % 20) in (0, 1):
        return [_W_ANT, _W_STK, _W_TOP, _W_BLINK, _W_CHIN, _W_LEGS]
    else:
        return [_W_ANT, _W_STK, _W_TOP, face, _W_CHIN, _W_LEGS]


# ── Formatting helpers ───────────────────────────────────────────────────────

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
        if d > 0:   return f"{d}d {h}h"
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

def fmt_duration(secs):
    """Format seconds into human readable duration."""
    if secs < 60:       return f"{int(secs)}s"
    if secs < 3600:     return f"{int(secs//60)}m"
    if secs < 86400:
        h = int(secs // 3600)
        m = int((secs % 3600) // 60)
        return f"{h}h {m}m"
    d = int(secs // 86400)
    h = int((secs % 86400) // 3600)
    return f"{d}d {h}h"

def fmt_ago(ts):
    """Format a datetime as 'X ago'."""
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    secs = max(0, (now - ts).total_seconds())
    if secs < 60:       return "just now"
    if secs < 3600:     return f"{int(secs//60)}m ago"
    if secs < 86400:    return f"{int(secs//3600)}h ago"
    return f"{int(secs//86400)}d ago"

def fmt_model(name):
    """Format model name nicely."""
    if not name or name == "—":
        return "—"
    name = name.replace("claude-", "")
    # "opus-4-6" → "Opus 4"
    parts = name.split("-")
    if len(parts) >= 2:
        model = parts[0].capitalize()
        ver = parts[1]
        return f"{model} {ver}"
    return name.capitalize()

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

def big_bar(pct, width=30, label=""):
    """Render a usage progress bar with percentage."""
    if pct is None: pct = 0.0
    ratio = min(max(pct / 100.0, 0.0), 1.0)
    filled = round(ratio * width)
    empty  = width - filled

    if   ratio >= 0.90: color = RED
    elif ratio >= 0.70: color = ORANGE
    elif ratio >= 0.40: color = AMBER_L
    else:               color = GREEN

    t = Text()
    if label:
        t.append(f"{label} ", style=f"bold {color}")

    t.append("█" * filled, style=f"bold {color}")
    t.append("░" * empty, style=DIM_D)
    t.append(f" {round(pct)}%", style=f"bold {color}")

    return t

def time_bar(reset_iso, window_secs, width=30):
    """Render a time-remaining bar (cyan) aligned under big_bar. Same prefix width."""
    # Prefix: "  ◷ " = 5 chars to match "  5h " / "  7d " from big_bar
    prefix_style = f"bold {CYAN}"

    if reset_iso is None:
        t = Text()
        t.append("  ◷  ", style=prefix_style)
        t.append("░" * width, style=DIM_D)
        t.append(" —", style=MUTED)
        return t

    try:
        target = datetime.fromisoformat(reset_iso)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        secs_left = max(0, (target - now).total_seconds())
        elapsed_ratio = 1.0 - (secs_left / window_secs) if window_secs > 0 else 0
        elapsed_ratio = min(max(elapsed_ratio, 0.0), 1.0)
    except Exception:
        elapsed_ratio = 0.0

    filled = round(elapsed_ratio * width)
    empty = width - filled

    t = Text()
    t.append("  ◷  ", style=prefix_style)
    t.append("▓" * filled, style=CYAN)
    t.append("░" * empty, style=DIM_D)

    reset_str = fmt_time_until(reset_iso) if reset_iso else "—"
    t.append(f" {reset_str}", style=f"bold {CYAN}")

    return t

def project_bar(pct, width=20):
    """Mini proportional bar for project rows."""
    if pct is None or pct == 0: return Text("", style=DIM)
    ratio = min(max(pct / 100.0, 0.0), 1.0)
    filled = max(1, round(ratio * width))

    # Color gradient based on position in ranking
    colors = [VIOLET, BLUE, CYAN, GREEN, AMBER_L, ORANGE]
    color = colors[min(int(ratio * 5), 5)]

    t = Text()
    t.append("▓" * filled, style=color)
    return t

def sparkline(values, width=20):
    """Render a sparkline from a list of values."""
    if not values:
        return Text("—", style=MUTED)
    blocks = " ▁▂▃▄▅▆▇█"
    mx = max(values) if max(values) > 0 else 1
    vals = values[-width:]
    t = Text()
    for v in vals:
        idx = min(int((v / mx) * (len(blocks) - 1)), len(blocks) - 1)
        # Color gradient for sparkline
        ratio = v / mx if mx > 0 else 0
        if ratio > 0.7:   c = AMBER_L
        elif ratio > 0.4: c = VIOLET
        else:              c = CYAN
        t.append(blocks[idx], style=c)
    return t

def flame_icon(tick):
    """Animated flame for burn rate."""
    flames = ["◝", "◜", "◝", "◞"]
    colors = [ORANGE, AMBER_L, RED, ORANGE]
    idx = tick % len(flames)
    return Text(flames[idx], style=f"bold {colors[idx]}")

def get_burn_rate(daily_tokens):
    """Calculate tokens per hour from recent activity."""
    if not daily_tokens:
        return 0
    # Use today's tokens and figure out hours elapsed
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_tokens = daily_tokens.get(today, 0)
    hours_elapsed = datetime.now(timezone.utc).hour + datetime.now(timezone.utc).minute / 60
    if hours_elapsed < 0.1:
        hours_elapsed = 0.1
    return today_tokens / hours_elapsed


# ── Promo detection ──────────────────────────────────────────────────────────

def detect_promo(api_data, history_samples=None):
    """Detect 2x usage promo from API response or heuristics.

    Returns (is_promo: bool, label: str).
    """
    if not api_data:
        return False, ""

    # Layer 1: Check known promo fields
    iguana = api_data.get("iguana_necktie")
    if iguana and isinstance(iguana, dict):
        mult = iguana.get("multiplier", iguana.get("factor", 2))
        return True, f"{mult}x PROMO"

    # Layer 2: Scan for any promo-like keys
    promo_keywords = {"promo", "bonus", "multiplier", "double", "boost"}
    for key, val in api_data.items():
        if any(kw in key.lower() for kw in promo_keywords):
            if val is not None and val != False and val != 0:
                return True, "2x PROMO"

    # Layer 3: Capacity heuristic from persistent history
    if history_samples and len(history_samples) > 100:
        recent = history_samples[-10:]
        older = history_samples[-500:-100]

        def avg_tpp(samples):
            valid = [(s["tok"], s["5h"]) for s in samples
                     if s.get("5h", 0) > 5 and s.get("tok", 0) > 0]
            if len(valid) < 3:
                return None
            return sum(t / p for t, p in valid) / len(valid)

        recent_tpp = avg_tpp(recent)
        older_tpp = avg_tpp(older)

        if recent_tpp and older_tpp and recent_tpp > older_tpp * 1.7:
            return True, "2x PROMO"

    return False, ""

PROMO_STYLE = f"bold {YELLOW} on #78350f"

# ── Real-time usage history ───────────────────────────────────────────────────

class UsageHistory:
    """Ring buffer that records 5h utilization samples over time."""

    def __init__(self, max_samples=60):
        self.max_samples = max_samples  # ~30 min at 30s refresh
        self.samples_5h = []
        self.samples_7d = []
        self.timestamps = []

    def load_from_persistent(self, history_samples):
        """Backfill ring buffer from persistent history samples."""
        if not history_samples:
            return
        recent = history_samples[-self.max_samples:]
        for s in recent:
            self.samples_5h.append(s.get("5h", 0))
            self.samples_7d.append(s.get("7d", 0))
            self.timestamps.append(s.get("ts", 0))

    def record(self, api_data):
        """Record a new sample from API data."""
        if not api_data:
            return
        fh = api_data.get("five_hour") or api_data.get("fiveHour") or {}
        sd = api_data.get("seven_day") or api_data.get("sevenDay") or {}
        fh_pct = fh.get("utilization") or 0
        sd_pct = sd.get("utilization") or 0

        self.samples_5h.append(fh_pct)
        self.samples_7d.append(sd_pct)
        self.timestamps.append(time.time())

        # Trim to max
        if len(self.samples_5h) > self.max_samples:
            self.samples_5h = self.samples_5h[-self.max_samples:]
            self.samples_7d = self.samples_7d[-self.max_samples:]
            self.timestamps = self.timestamps[-self.max_samples:]

    def render_chart(self, width=20, height=6, show_7d=False):
        """Render a real-time ASCII chart of utilization over time."""
        samples = self.samples_5h if not show_7d else self.samples_7d
        color = AMBER_L if not show_7d else VIOLET
        label = "5h" if not show_7d else "7d"

        if len(samples) < 1:
            t = Text()
            t.append(f"  {label} ", style=f"bold {color}")
            t.append("waiting for data…", style=DIM)
            return t

        # Take last `width` samples
        vals = samples[-width:]
        # Always scale 0-100% for utilization
        mx = 100

        rows = []

        # Build chart row by row (top to bottom)
        for row_idx in range(height):
            r = Text()
            # Y-axis label
            if row_idx == 0:
                r.append("100", style=DIM)
            elif row_idx == height // 2:
                r.append(" 50", style=DIM)
            elif row_idx == height - 1:
                r.append("  0", style=DIM)
            else:
                r.append("   ", style=DIM)

            r.append("│", style=DIM)

            # Plot each sample (2 chars wide per bar)
            for v in vals:
                v_pos = (v / mx) * (height - 1) if mx > 0 else 0
                filled_row = height - 1 - row_idx  # row 0 is top
                if v_pos >= filled_row + 0.5:
                    if v >= 90:   c = RED
                    elif v >= 70: c = ORANGE
                    elif v >= 40: c = color
                    else:         c = GREEN
                    r.append("██", style=c)
                elif v_pos >= filled_row:
                    if v >= 90:   c = RED
                    elif v >= 70: c = ORANGE
                    elif v >= 40: c = color
                    else:         c = GREEN
                    r.append("▄▄", style=c)
                else:
                    r.append("  ")

            rows.append(r)

        # Bottom axis
        axis = Text()
        axis.append("   └", style=DIM)
        axis.append("─" * (len(vals) * 2), style=DIM)
        rows.append(axis)

        # Label
        if self.timestamps:
            elapsed = time.time() - self.timestamps[0]
            elapsed_str = f"{int(elapsed // 60)}m" if elapsed >= 60 else f"{int(elapsed)}s"
            time_label = Text()
            time_label.append(f"    {label} utilization ", style=f"bold {color}")
            time_label.append(f"(last {elapsed_str})", style=DIM)
            rows.append(time_label)

        return Text("\n").join(rows)


# ── Pace & cumulative charts ─────────────────────────────────────────────────

def compute_pace(api_data):
    """Compute pace % = (actual_pct / expected_pct_at_this_time) × 100.

    Returns (pace_pct, elapsed_ratio) or (None, None) if insufficient data.
    """
    if not api_data:
        return None, None
    fh = api_data.get("five_hour") or api_data.get("fiveHour") or {}
    actual_pct = fh.get("utilization") or 0
    reset_iso = fh.get("resets_at")
    if not reset_iso or not isinstance(reset_iso, str):
        return None, None
    try:
        target = datetime.fromisoformat(reset_iso)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        secs_left = max(0, (target - now).total_seconds())
        window_secs = 5 * 3600
        elapsed_ratio = 1.0 - (secs_left / window_secs)
        elapsed_ratio = min(max(elapsed_ratio, 0.01), 1.0)  # avoid div by zero
        expected_pct = elapsed_ratio * 100  # even distribution target
        pace = (actual_pct / expected_pct) * 100 if expected_pct > 0 else 0
        return round(pace, 1), elapsed_ratio
    except Exception:
        return None, None

def _downsample(values, n):
    """Reduce a list to n points by averaging adjacent groups."""
    if len(values) <= n:
        return values
    chunk = len(values) / n
    result = []
    for i in range(n):
        start = int(i * chunk)
        end = int((i + 1) * chunk)
        group = values[start:end]
        result.append(sum(group) / len(group) if group else 0)
    return result

def render_cumulative_chart(history_samples, api_data, width=36, height=8):
    """Render cumulative usage chart with budget line, actual line, and prediction.

    The budget line shows even distribution (diagonal from 0% to 100%).
    The actual line shows real utilization over the current 5h window.
    """
    if not api_data:
        t = Text()
        t.append("  waiting for data\u2026", style=DIM)
        return t

    fh = api_data.get("five_hour") or api_data.get("fiveHour") or {}
    reset_iso = fh.get("resets_at")
    current_pct = fh.get("utilization") or 0

    # Determine window boundaries
    now = datetime.now(timezone.utc)
    window_secs = 5 * 3600
    try:
        reset_time = datetime.fromisoformat(reset_iso)
        if reset_time.tzinfo is None:
            reset_time = reset_time.replace(tzinfo=timezone.utc)
        window_start = reset_time - timedelta(seconds=window_secs)
    except Exception:
        window_start = now - timedelta(hours=5)
        reset_time = now

    elapsed_secs = (now - window_start).total_seconds()
    elapsed_ratio = min(max(elapsed_secs / window_secs, 0), 1.0)
    total_cols = width  # columns representing the full 5h window

    # Filter history samples to current window
    window_start_ts = window_start.timestamp()
    now_ts = now.timestamp()
    window_samples = []
    if history_samples:
        window_samples = [s for s in history_samples if s.get("ts", 0) >= window_start_ts]

    # Build actual values: map each sample to its position in the window
    # Then interpolate/downsample to fit width
    actual_cols = max(1, int(elapsed_ratio * total_cols))

    if window_samples:
        # Map samples to column positions and take the value at each column
        actual_values = []
        for col in range(actual_cols):
            col_time = window_start_ts + (col / total_cols) * window_secs
            # Find closest sample at or before this time
            best = None
            for s in window_samples:
                if s["ts"] <= col_time + 120:  # 2min tolerance
                    best = s
            actual_values.append(best["5h"] if best else 0)
    else:
        # No history in window — just show current value at the end
        actual_values = [current_pct]
        actual_cols = 1

    # Pad actual_values to actual_cols if needed
    if len(actual_values) < actual_cols:
        actual_values.extend([actual_values[-1] if actual_values else 0] * (actual_cols - len(actual_values)))

    # Compute prediction: extend from current value at current rate
    pace_pct, _ = compute_pace(api_data)
    if pace_pct and pace_pct > 0 and actual_cols < total_cols:
        remaining_cols = total_cols - actual_cols
        # Predict: extrapolate linearly from current utilization at current pace
        current_rate = current_pct / elapsed_ratio if elapsed_ratio > 0 else 0  # rate per 100% of window
        pred_values = []
        for i in range(remaining_cols):
            future_ratio = elapsed_ratio + ((i + 1) / total_cols)
            pred_pct = min(100, current_rate * future_ratio)
            pred_values.append(pred_pct)
    else:
        pred_values = []

    # Build the chart grid
    rows = []
    y_labels = {0: "100", height // 4: " 75", height // 2: " 50", 3 * height // 4: " 25", height - 1: "  0"}

    for row_idx in range(height):
        r = Text()
        # Y-axis label
        r.append(y_labels.get(row_idx, "   "), style=DIM)
        r.append("\u2502", style=DIM)  # │

        # The value this row represents (top = 100, bottom = 0)
        row_value = 100 * (height - 1 - row_idx) / (height - 1)

        for col in range(total_cols):
            col_ratio = (col + 0.5) / total_cols  # position in window (0-1)
            budget_value = col_ratio * 100  # even distribution value at this column

            # Determine what to draw at this cell
            is_actual_zone = col < actual_cols
            is_pred_zone = col >= actual_cols and (col - actual_cols) < len(pred_values)

            if is_actual_zone:
                actual_val = actual_values[min(col, len(actual_values) - 1)]
            elif is_pred_zone:
                actual_val = pred_values[col - actual_cols]
            else:
                actual_val = 0

            # Budget line: draw if this row is the closest to budget_value
            budget_row_pos = (budget_value / 100) * (height - 1)
            is_budget_row = abs((height - 1 - row_idx) - budget_row_pos) < 0.6

            # Actual/pred line: draw if this row is at or below the value
            actual_row_pos = (actual_val / 100) * (height - 1)
            is_at_actual = abs((height - 1 - row_idx) - actual_row_pos) < 0.6
            is_below_actual = (height - 1 - row_idx) < actual_row_pos

            if is_actual_zone and (is_at_actual or is_below_actual):
                # Filled area under actual line
                if actual_val >= 90:     c = RED
                elif actual_val >= 70:   c = ORANGE
                elif actual_val >= 40:   c = AMBER_L
                else:                    c = GREEN
                if is_at_actual:
                    r.append("\u2584", style=c)  # ▄ top of fill
                else:
                    r.append("\u2588", style=c)  # █ solid fill
            elif is_pred_zone and is_at_actual:
                r.append("\u00b7", style=CYAN)  # · prediction dot
            elif is_budget_row:
                r.append("\u2571", style=DIM)   # ╱ budget diagonal
            else:
                # Subtle threshold lines
                if row_idx == height // 5:       # ~80% line
                    r.append("\u00b7", style=DIM_D)
                elif row_idx == height // 2:     # 50% line
                    r.append("\u00b7", style=DIM_D)
                else:
                    r.append(" ")

        rows.append(r)

    # Bottom axis
    axis = Text()
    axis.append("   \u2514", style=DIM)  # └
    axis.append("\u2500" * total_cols, style=DIM)  # ─
    rows.append(axis)

    # Time labels
    time_row = Text()
    time_row.append("    ", style=DIM)
    start_label = window_start.strftime("%H:%M") if window_start else ""
    # Place "now" marker at the elapsed position
    now_col = int(elapsed_ratio * total_cols)
    end_label = reset_time.strftime("%H:%M") if reset_time else ""
    # Build label: start ... now ... end
    label_line = list(" " * total_cols)
    for i, ch in enumerate(start_label):
        if i < total_cols:
            label_line[i] = ch
    now_label = "now"
    now_start = max(len(start_label) + 1, min(now_col - 1, total_cols - len(now_label)))
    for i, ch in enumerate(now_label):
        if now_start + i < total_cols:
            label_line[now_start + i] = ch
    end_start = max(now_start + len(now_label) + 1, total_cols - len(end_label))
    for i, ch in enumerate(end_label):
        if end_start + i < total_cols:
            label_line[end_start + i] = ch
    time_row.append("".join(label_line), style=MUTED)
    rows.append(time_row)

    # Legend
    legend = Text()
    legend.append("    ", style=DIM)
    legend.append("\u2500\u2500", style=AMBER_L)  # ── actual
    legend.append(" actual  ", style=DIM)
    legend.append("\u2571", style=DIM)            # ╱ even
    legend.append(" even  ", style=DIM)
    legend.append("\u00b7\u00b7", style=CYAN)     # ·· prediction
    legend.append(" pred", style=DIM)
    rows.append(legend)

    return Text("\n").join(rows)


def render_rate_chart(history_samples, width=36, height=4):
    """Render consumption rate (derivative) as a sparkline bar chart.

    Shows how fast usage is changing — bursts vs quiet periods.
    """
    if not history_samples or len(history_samples) < 3:
        return None

    # Filter to last 5 hours
    cutoff = time.time() - 5 * 3600
    recent = [s for s in history_samples if s.get("ts", 0) >= cutoff]
    if len(recent) < 3:
        return None

    # Compute deltas (rate of change between consecutive samples)
    deltas = []
    for i in range(1, len(recent)):
        dt = recent[i]["ts"] - recent[i - 1]["ts"]
        if dt > 0:
            d_pct = max(0, recent[i]["5h"] - recent[i - 1]["5h"])
            rate = d_pct / (dt / 60)  # %/min
            deltas.append(rate)

    if not deltas:
        return None

    # Downsample to fit width
    vals = _downsample(deltas, width)
    mx = max(vals) if vals and max(vals) > 0 else 1

    # Compute peak and average
    peak_delta = max(deltas)
    avg_delta = sum(deltas) / len(deltas)

    rows = []

    # Header with metrics
    header = Text()
    header.append("  peak ", style=DIM)
    header.append(f"{peak_delta:.2f}", style=f"bold {ORANGE}")
    header.append("%/min  avg ", style=DIM)
    header.append(f"{avg_delta:.3f}", style=f"bold {MUTED}")
    header.append("%/min", style=DIM)
    rows.append(header)

    # Bar chart (sparkline style, using block chars)
    blocks = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"  # ▁▂▃▄▅▆▇█
    sp = Text()
    sp.append("  ")
    for v in vals:
        ratio = v / mx if mx > 0 else 0
        idx = min(int(ratio * (len(blocks) - 1)), len(blocks) - 1)
        if ratio > 0.7:   c = ORANGE
        elif ratio > 0.3: c = AMBER_L
        else:              c = DIM
        sp.append(blocks[idx], style=c)
    rows.append(sp)

    return Text("\n").join(rows)


def render_heatmap(daily_tokens, weeks=5):
    """Render a GitHub-style contribution heatmap for daily token usage."""
    if not daily_tokens:
        return None

    today = datetime.now(timezone.utc).date()
    # Build grid: rows=weekdays (Mon=0..Sun=6), cols=weeks
    total_days = weeks * 7
    start_date = today - timedelta(days=total_days - 1)
    # Align to Monday
    start_date = start_date - timedelta(days=start_date.weekday())

    # Collect values
    grid = {}
    mx = 0
    for d in range(total_days + 7):  # extra week for alignment
        day = start_date + timedelta(days=d)
        if day > today:
            break
        key = day.strftime("%Y-%m-%d")
        val = daily_tokens.get(key, 0)
        grid[day] = val
        if val > mx:
            mx = val

    if mx == 0:
        return None

    # Intensity levels
    levels = [
        (0, DIM_D, "\u2591"),       # ░ empty
        (0.01, GREEN, "\u25aa"),    # ▪ low
        (0.25, CYAN, "\u25aa"),     # ▪ medium-low
        (0.50, AMBER_L, "\u25aa"), # ▪ medium-high
        (0.75, ORANGE, "\u25aa"),  # ▪ high
    ]
    day_labels = {0: "M", 2: "W", 4: "F"}

    rows = []
    for weekday in range(7):
        r = Text()
        label = day_labels.get(weekday, " ")
        r.append(f"  {label} ", style=MUTED)
        for week in range(weeks):
            day = start_date + timedelta(days=week * 7 + weekday)
            if day > today:
                r.append(" ")
                continue
            val = grid.get(day, 0)
            ratio = val / mx if mx > 0 else 0
            # Find appropriate level
            color, char = levels[0][1], levels[0][2]
            for threshold, c, ch in levels:
                if ratio >= threshold:
                    color, char = c, ch
            r.append(char + " ", style=color)
        rows.append(r)

    return Text("\n").join(rows)


# ── Token resolution ──────────────────────────────────────────────────────────

def get_token():
    """Try every known location Claude Code stores its OAuth token."""

    if os.environ.get("CLAUDE_TOKEN"):
        return os.environ["CLAUDE_TOKEN"].strip()

    if sys.platform == "darwin":
        services = ["Claude Code-credentials", "claude.ai", "Claude Code", "Anthropic Claude", "Claude"]
        for svc in services:
            try:
                out = subprocess.check_output(
                    ["security", "find-generic-password", "-s", svc, "-w"],
                    stderr=subprocess.DEVNULL, timeout=3
                ).decode().strip()
                if out.startswith("{"):
                    try:
                        blob = json.loads(out)
                        for top_key in blob.values():
                            if isinstance(top_key, dict) and top_key.get("accessToken"):
                                return top_key["accessToken"].strip()
                    except json.JSONDecodeError:
                        pass
                if len(out) > 20:
                    return out
            except Exception:
                pass

        try:
            import keyring
            for svc in services:
                t = keyring.get_password(svc, "default") or \
                    keyring.get_password(svc, "oauth_token") or \
                    keyring.get_password(svc, "claude")
                if t: return t.strip()
        except Exception:
            pass

    home = Path.home()
    cred_paths = [
        home / ".claude" / ".credentials.json",
        home / ".config" / "claude" / "credentials.json",
        home / ".claude" / "auth.json",
        home / ".claude" / "session.json",
        home / "Library" / "Application Support" / "Claude" / "credentials.json",
    ]
    token_keys = [
        "access_token", "oauth_token", "token",
        "claudeAiOauthToken", "session_key", "sessionKey",
    ]
    for p in cred_paths:
        if p.exists():
            try:
                data = json.loads(p.read_text())
                for k in token_keys:
                    if data.get(k):
                        return str(data[k]).strip()
            except Exception:
                pass

    return None

# ── API call ──────────────────────────────────────────────────────────────────

class RateLimited(Exception):
    """Raised on 429 with optional Retry-After seconds."""
    def __init__(self, retry_after=None):
        self.retry_after = retry_after
        super().__init__(f"429 rate limited (retry after {retry_after}s)")

_CACHE_FILE = Path.home() / ".claude" / ".clu_cache.json"
_HISTORY_FILE = Path.home() / ".claude" / ".clu_history.json"
_HISTORY_MAX_AGE = 30 * 86400  # 30 days in seconds

def _load_cached_usage():
    """Load last successful API response from disk cache."""
    try:
        if _CACHE_FILE.exists():
            data = json.loads(_CACHE_FILE.read_text())
            cached_at = data.get("_cached_at", 0)
            # Only use cache if less than 5 minutes old
            if time.time() - cached_at < 300:
                return data
    except Exception:
        pass
    return None

def _save_cached_usage(data):
    """Save API response to disk cache."""
    try:
        cache = dict(data)
        cache["_cached_at"] = time.time()
        _CACHE_FILE.write_text(json.dumps(cache))
        _CACHE_FILE.chmod(0o600)
    except Exception:
        pass

def _load_history():
    """Load persistent usage history from disk, pruning old samples."""
    try:
        if not _HISTORY_FILE.exists():
            return []
        data = json.loads(_HISTORY_FILE.read_text())
        if data.get("v") != 1:
            return []
        samples = data.get("samples", [])
        cutoff = time.time() - _HISTORY_MAX_AGE
        pruned = [s for s in samples if s.get("ts", 0) >= cutoff]
        # Write back if we pruned anything
        if len(pruned) < len(samples):
            _save_history(pruned)
        return pruned
    except Exception:
        return []

def _save_history(samples):
    """Write history samples to disk atomically."""
    try:
        tmp = _HISTORY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"v": 1, "samples": samples}))
        tmp.chmod(0o600)
        os.replace(str(tmp), str(_HISTORY_FILE))
    except Exception:
        pass

_last_history_ts = 0  # debounce guard

def _save_history_sample(api_data):
    """Append a single sample to persistent history (debounced to 60s)."""
    global _last_history_ts
    now = time.time()
    if now - _last_history_ts < 60:
        return
    if not api_data:
        return
    fh = api_data.get("five_hour") or api_data.get("fiveHour") or {}
    sd = api_data.get("seven_day") or api_data.get("sevenDay") or {}
    fh_pct = fh.get("utilization") or 0
    sd_pct = sd.get("utilization") or 0
    tok = (fh.get("input_tokens") or 0) + (fh.get("output_tokens") or 0)
    sample = {"ts": int(now), "5h": round(fh_pct, 1), "7d": round(sd_pct, 1), "tok": tok}
    samples = _load_history()
    samples.append(sample)
    _save_history(samples)
    _last_history_ts = now

_SESSION_KEY_FILE = Path.home() / ".claude" / ".clu_session_key"

def _get_session_key():
    """Get sessionKey: file cache → Claude Desktop cookies → None."""
    # Check file cache first
    try:
        if _SESSION_KEY_FILE.exists():
            sk = _SESSION_KEY_FILE.read_text().strip()
            if sk:
                return sk
    except Exception:
        pass

    # Try decrypting from Claude Desktop cookie store
    sk = _decrypt_session_key()
    if sk:
        # Cache it so we don't need keychain access again
        try:
            _SESSION_KEY_FILE.write_text(sk)
            _SESSION_KEY_FILE.chmod(0o600)
        except Exception:
            pass
    return sk

def _decrypt_session_key():
    """Extract sessionKey from Claude Desktop's encrypted cookie store."""
    if sys.platform == "darwin":
        return _decrypt_session_key_macos()
    elif sys.platform == "win32":
        return _decrypt_session_key_windows()
    elif sys.platform.startswith("linux"):
        return _decrypt_session_key_linux()
    return None

def _decrypt_session_key_macos():
    """macOS: Keychain + AES-128-CBC."""
    try:
        cookie_db = Path.home() / "Library" / "Application Support" / "Claude" / "Cookies"
        if not cookie_db.exists():
            return None
        import sqlite3, shutil, tempfile, hashlib
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend

        key_password = subprocess.check_output(
            ["security", "find-generic-password", "-s", "Claude Safe Storage", "-a", "Claude Key", "-w"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()

        derived_key = hashlib.pbkdf2_hmac("sha1", key_password.encode("utf-8"), b"saltysalt", 1003, dklen=16)

        fd, tmp = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            shutil.copy2(str(cookie_db), tmp)
            conn = sqlite3.connect(tmp)
            cur = conn.cursor()
            cur.execute("SELECT encrypted_value FROM cookies WHERE name='sessionKey' AND host_key LIKE '%claude%'")
            row = cur.fetchone()
            conn.close()
        finally:
            os.unlink(tmp)

        if row and row[0][:3] == b"v10":
            enc_data = row[0][3:]
            iv = b" " * 16
            cipher = Cipher(algorithms.AES(derived_key), modes.CBC(iv), backend=default_backend())
            decryptor = cipher.decryptor()
            decrypted = decryptor.update(enc_data) + decryptor.finalize()
            pad_len = decrypted[-1]
            return decrypted[:-pad_len].decode("utf-8")
    except Exception:
        pass
    return None

def _decrypt_session_key_windows():
    """Windows: DPAPI master key + AES-256-GCM."""
    try:
        import ctypes, ctypes.wintypes
        import sqlite3, shutil, tempfile, base64
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        app_data = os.environ.get("APPDATA", "")
        local_app_data = os.environ.get("LOCALAPPDATA", "")

        # Find cookie database
        cookie_db = None
        for p in [
            Path(app_data) / "Claude" / "Cookies",
            Path(local_app_data) / "Claude" / "Cookies",
            Path(app_data) / "Claude" / "User Data" / "Default" / "Cookies",
        ]:
            if p.exists():
                cookie_db = p
                break
        if not cookie_db:
            return None

        # Read master key from Local State
        local_state = None
        for p in [
            Path(app_data) / "Claude" / "Local State",
            Path(local_app_data) / "Claude" / "Local State",
        ]:
            if p.exists():
                local_state = p
                break
        if not local_state:
            return None

        with open(local_state, "r") as f:
            encrypted_key_b64 = json.load(f)["os_crypt"]["encrypted_key"]
        encrypted_key = base64.b64decode(encrypted_key_b64)[5:]  # strip "DPAPI" prefix

        # Decrypt master key with DPAPI
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        blob_in = DATA_BLOB(len(encrypted_key),
                            ctypes.create_string_buffer(encrypted_key, len(encrypted_key)))
        blob_out = DATA_BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            return None
        master_key = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)

        # Read cookie from database
        fd, tmp = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            shutil.copy2(str(cookie_db), tmp)
            conn = sqlite3.connect(tmp)
            cur = conn.cursor()
            cur.execute("SELECT encrypted_value FROM cookies WHERE name='sessionKey' AND host_key LIKE '%claude%'")
            row = cur.fetchone()
            conn.close()
        finally:
            os.unlink(tmp)

        if row and row[0][:3] == b"v10":
            nonce = row[0][3:15]       # 12-byte nonce
            ciphertext = row[0][15:]   # ciphertext + 16-byte GCM tag
            decrypted = AESGCM(master_key).decrypt(nonce, ciphertext, None)
            return decrypted.decode("utf-8")
    except Exception:
        pass
    return None

def _decrypt_session_key_linux():
    """Linux: Secret Service (or 'peanuts' fallback) + AES-128-CBC."""
    try:
        import sqlite3, shutil, tempfile, hashlib
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend

        # Find cookie database
        config = Path.home() / ".config"
        cookie_db = None
        for p in [
            config / "Claude" / "Cookies",
            config / "claude-desktop" / "Cookies",
            config / "Claude" / "Default" / "Cookies",
        ]:
            if p.exists():
                cookie_db = p
                break
        if not cookie_db:
            return None

        # Get encryption password from Secret Service, fall back to Chromium default
        key_password = "peanuts"
        try:
            import secretstorage
            bus = secretstorage.dbus_init()
            collection = secretstorage.get_default_collection(bus)
            if collection.is_locked():
                collection.unlock()
            for item in collection.get_all_items():
                if item.get_label() == "Claude Safe Storage":
                    key_password = item.get_secret().decode("utf-8")
                    break
        except Exception:
            pass

        # Linux uses 1 PBKDF2 iteration (vs 1003 on macOS)
        derived_key = hashlib.pbkdf2_hmac("sha1", key_password.encode("utf-8"), b"saltysalt", 1, dklen=16)

        # Read cookie from database
        fd, tmp = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            shutil.copy2(str(cookie_db), tmp)
            conn = sqlite3.connect(tmp)
            cur = conn.cursor()
            cur.execute("SELECT encrypted_value FROM cookies WHERE name='sessionKey' AND host_key LIKE '%claude%'")
            row = cur.fetchone()
            conn.close()
        finally:
            os.unlink(tmp)

        if row and row[0][:3] in (b"v10", b"v11"):
            enc_data = row[0][3:]
            iv = b" " * 16
            cipher = Cipher(algorithms.AES(derived_key), modes.CBC(iv), backend=default_backend())
            decryptor = cipher.decryptor()
            decrypted = decryptor.update(enc_data) + decryptor.finalize()
            pad_len = decrypted[-1]
            return decrypted[:-pad_len].decode("utf-8")
    except Exception:
        pass
    return None

def _ensure_session_key():
    """Ensure session key is available, prompting interactively if needed."""
    # Already have one cached?
    try:
        if _SESSION_KEY_FILE.exists() and _SESSION_KEY_FILE.read_text().strip():
            return
    except Exception:
        pass

    # Try auto-discovery from Claude Desktop cookies
    sk = _decrypt_session_key()
    if sk:
        try:
            _SESSION_KEY_FILE.write_text(sk)
            _SESSION_KEY_FILE.chmod(0o600)
        except Exception:
            pass
        return

    # Interactive prompt (only if running in a terminal)
    if not sys.stdin.isatty():
        return

    console = Console(highlight=False)
    console.print()
    console.print(Text.from_markup(
        f"  [{AMBER}]◆[/] [{WHITE}]Session key needed[/]\n\n"
        f"  clu needs your claude.ai session cookie to fetch usage data.\n"
        f"  This is a one-time setup — the key is cached locally.\n\n"
        f"  [{CYAN}]How to get it:[/]\n"
        f"  1. Open [{CYAN}]claude.ai[/] in your browser\n"
        f"  2. DevTools (F12) → [{CYAN}]Application[/] → [{CYAN}]Cookies[/] → claude.ai\n"
        f"  3. Copy the [{CYAN}]sessionKey[/] value\n"
    ))
    try:
        sk = input("  Paste session key (or Enter to skip): ").strip()
        if sk and len(sk) > 20:
            try:
                _SESSION_KEY_FILE.write_text(sk)
                _SESSION_KEY_FILE.chmod(0o600)
            except Exception:
                pass
            console.print(Text.from_markup(f"\n  [{GREEN}]✓[/] Saved to ~/.claude/.clu_session_key\n"))
        else:
            console.print(Text.from_markup(
                f"\n  [{MUTED}]Skipped — set CLU_SESSION_KEY env var or use --session-key later[/]\n"
            ))
    except (EOFError, KeyboardInterrupt):
        console.print()

def _get_org_id(token):
    """Get organization UUID from the OAuth profile endpoint."""
    try:
        resp = requests.get(
            "https://api.anthropic.com/api/oauth/profile",
            headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("organization", {}).get("uuid")
    except Exception:
        pass
    return None

# Cached scraper and org_id to avoid re-creating on every fetch
_scraper = None
_org_id = None

def fetch_usage(token):
    """Fetch usage from claude.ai web API via session cookie, with OAuth API fallback."""
    global _scraper, _org_id

    # ── Strategy 1: claude.ai web API (session cookie + cloudscraper) ────
    if cloudscraper:
        if _org_id is None:
            _org_id = _get_org_id(token) or os.environ.get("CLU_ORG_ID")
        if _org_id:
            if _scraper is None:
                _scraper = cloudscraper.create_scraper()
                # Try session key from env, then from Claude Desktop cookies
                sk = os.environ.get("CLU_SESSION_KEY") or _get_session_key()
                if sk:
                    _scraper.cookies.set("sessionKey", sk, domain=".claude.ai")
            if _scraper.cookies.get("sessionKey"):
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    resp = _scraper.get(
                        f"https://claude.ai/api/organizations/{_org_id}/usage",
                        timeout=10,
                    )
                if resp.status_code == 200:
                    data = resp.json()
                    _save_cached_usage(data)
                    return data
                elif resp.status_code == 429:
                    # Don't raise — fall through to OAuth fallback
                    pass
                elif resp.status_code in (401, 403):
                    # Session expired — clear cookie so we re-fetch next time
                    _scraper.cookies.clear()
                    _scraper = None

    # ── Strategy 2: OAuth API (legacy, may be blocked on consumer plans) ─
    resp = requests.get(
        "https://api.anthropic.com/api/oauth/usage",
        headers={
            "Authorization":   f"Bearer {token}",
            "anthropic-beta":  "oauth-2025-04-20",
        },
        timeout=10,
    )
    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
        secs = int(retry_after) if retry_after and retry_after.isdigit() else None
        if secs is not None:
            secs = min(secs, REFRESH_SECS * 2)  # cap to avoid absurd waits
        raise RateLimited(secs)
    resp.raise_for_status()
    data = resp.json()
    _save_cached_usage(data)
    return data


# ── JSONL Project Data Parser ────────────────────────────────────────────────

# Structural path segments to strip when extracting project names
_STRUCTURAL = {
    "users", "work", "desktop", "documents", "downloads",
    "projects", "code", "source", "repos", "git", "github",
    "home", "research", "development", "dev", "src",
}

def _extract_project_name(dir_name):
    """Extract project name from a Claude project directory name.

    Directory names encode full paths with '-' replacing '/', e.g.:
      -Users-hsantanna-Work-Research-sis-employment
    Since '-' replaces both '/' and literal hyphens in folder names,
    we walk the filesystem to reconstruct the real path and extract
    the leaf folder name.
    """
    parts = [p for p in dir_name.split("-") if p]
    if not parts:
        return dir_name

    # Greedily reconstruct the path by checking the filesystem.
    # At each hyphen, check if the accumulated string is a real directory.
    # If so, treat the hyphen as a '/'. Otherwise, it's a literal '-'.
    resolved = "/"
    remaining = parts[:]

    while remaining:
        best = 0
        for i in range(1, len(remaining) + 1):
            candidate = resolved.rstrip("/") + "/" + "-".join(remaining[:i])
            if Path(candidate).is_dir():
                best = i

        if best > 0:
            resolved = resolved.rstrip("/") + "/" + "-".join(remaining[:best])
            remaining = remaining[best:]
        else:
            break

    if remaining:
        # Filesystem resolved the parent but couldn't go deeper (stale path).
        # Strip structural segments from remaining to get the leaf.
        last_structural = -1
        for i, part in enumerate(remaining):
            if part.lower() in _STRUCTURAL:
                last_structural = i
        if last_structural >= 0 and last_structural < len(remaining) - 1:
            remaining = remaining[last_structural + 1:]
        return _prettify_name("-".join(remaining))

    if resolved != "/":
        # Entire path resolved on disk — use the actual leaf folder name
        return _prettify_name(Path(resolved).name)

    # Fallback for paths that no longer exist on disk:
    # Strip all structural segments (from anywhere) + username, keep only
    # the meaningful tail after the LAST structural segment.
    has_users_prefix = parts[0].lower() == "users" and len(parts) >= 2
    last_structural = -1
    for i, part in enumerate(parts):
        if part.lower() in _STRUCTURAL:
            last_structural = i
        elif has_users_prefix and i == 1:
            last_structural = i

    start = last_structural + 1 if last_structural >= 0 else 0
    tail = parts[start:] if start < len(parts) else parts[-1:]
    return _prettify_name("-".join(tail))


def _prettify_name(name):
    """Prettify a folder name: title-case words, uppercase short acronyms."""
    words = name.replace("-", " ").replace("_", " ").split()
    if not words:
        return name

    _COMMON_SHORT = {
        "a", "an", "and", "the", "of", "or", "in", "on", "to", "at",
        "for", "is", "it", "my", "by", "do", "if", "no", "so", "up",
        "us", "we", "bad", "big", "new", "old", "all", "any", "but",
        "can", "did", "get", "has", "her", "him", "his", "how", "its",
        "let", "may", "not", "now", "our", "out", "own", "run", "say",
        "she", "too", "two", "use", "was", "way", "who", "why", "yet",
    }
    pretty = []
    for word in words:
        if len(word) <= 3 and word.lower() not in _COMMON_SHORT:
            pretty.append(word.upper())
        else:
            pretty.append(word.capitalize())

    return " ".join(pretty)


def parse_project_data(data_dirs):
    """Parse all JSONL conversation files from Claude Code project directories."""
    all_jsonl_files = []

    for claude_dir in data_dirs:
        projects_dir = claude_dir / "projects"
        if not projects_dir.exists():
            continue

        source_label = "local"
        default_claude = Path.home() / ".claude"
        if claude_dir.resolve() != default_claude.resolve():
            source_label = claude_dir.parent.name or str(claude_dir)

        for proj_dir in sorted(projects_dir.iterdir()):
            if not proj_dir.is_dir():
                continue
            proj_name = _extract_project_name(proj_dir.name)

            for jsonl_file in proj_dir.glob("*.jsonl"):
                all_jsonl_files.append((proj_name, source_label, jsonl_file))

    projects = defaultdict(lambda: {
        "name": "", "source": set(), "sessions": 0, "messages": 0,
        "input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_create": 0,
        "first_ts": None, "last_ts": None, "session_ids": set(), "models": defaultdict(int),
    })

    sessions = {}
    daily_tokens = defaultdict(int)
    model_totals = defaultdict(int)
    tokens_5h = 0  # tokens from the last 5 hours
    cutoff_5h = datetime.now(timezone.utc) - timedelta(hours=5)

    # Track preferred display name per case-insensitive key (most-used casing wins)
    name_counts = defaultdict(lambda: defaultdict(int))

    for proj_name, source_label, jsonl_file in all_jsonl_files:
        proj_key = proj_name.lower()  # case-insensitive grouping
        name_counts[proj_key][proj_name] += 1

        try:
            with open(jsonl_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    ts_str = entry.get("timestamp")
                    session_id = entry.get("sessionId", jsonl_file.stem)
                    msg = entry.get("message", {})
                    usage = msg.get("usage", {})
                    model = msg.get("model", "")
                    role = msg.get("role", entry.get("type", ""))

                    ts = None
                    if ts_str:
                        try:
                            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        except (ValueError, TypeError):
                            pass

                    p = projects[proj_key]
                    # Pick display name: prefer lowercase unless only uppercase exists
                    best_name = max(name_counts[proj_key], key=lambda n: (n.islower(), name_counts[proj_key][n]))
                    p["name"] = best_name
                    p["source"].add(source_label)
                    p["session_ids"].add(session_id)

                    if ts:
                        if p["first_ts"] is None or ts < p["first_ts"]:
                            p["first_ts"] = ts
                        if p["last_ts"] is None or ts > p["last_ts"]:
                            p["last_ts"] = ts

                    if usage and role == "assistant":
                        inp = usage.get("input_tokens", 0)
                        out = usage.get("output_tokens", 0)
                        cache_r = usage.get("cache_read_input_tokens", 0)
                        cache_c = usage.get("cache_creation_input_tokens", 0)
                        total_msg = inp + out + cache_r + cache_c

                        p["input_tokens"] += inp
                        p["output_tokens"] += out
                        p["cache_read"] += cache_r
                        p["cache_create"] += cache_c
                        p["messages"] += 1

                        if model:
                            p["models"][model] += total_msg
                            model_totals[model] += total_msg

                        if ts:
                            day_key = ts.strftime("%Y-%m-%d")
                            daily_tokens[day_key] += total_msg
                            if ts >= cutoff_5h:
                                tokens_5h += total_msg

                        if session_id not in sessions:
                            sessions[session_id] = {
                                "id": session_id[:8], "project": proj_name, "source": source_label,
                                "messages": 0, "input_tokens": 0, "output_tokens": 0,
                                "cache_read": 0, "cache_create": 0,
                                "first_ts": ts, "last_ts": ts, "model": model,
                            }
                        s = sessions[session_id]
                        s["messages"] += 1
                        s["input_tokens"] += inp
                        s["output_tokens"] += out
                        s["cache_read"] += cache_r
                        s["cache_create"] += cache_c
                        if ts:
                            if s["first_ts"] is None or ts < s["first_ts"]:
                                s["first_ts"] = ts
                            if s["last_ts"] is None or ts > s["last_ts"]:
                                s["last_ts"] = ts
                        if model:
                            s["model"] = model
                    elif role in ("user", "human"):
                        p["messages"] += 1

        except (OSError, IOError):
            continue

    project_list = []
    for pname, p in projects.items():
        p["sessions"] = len(p["session_ids"])
        p["total_tokens"] = p["input_tokens"] + p["output_tokens"] + p["cache_read"] + p["cache_create"]
        p["source"] = sorted(p["source"])
        del p["session_ids"]
        p["models"] = dict(p["models"])
        project_list.append(p)

    project_list.sort(key=lambda x: x["total_tokens"], reverse=True)

    session_list = sorted(
        sessions.values(),
        key=lambda x: x["last_ts"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    total_input = sum(p["input_tokens"] for p in project_list)
    total_output = sum(p["output_tokens"] for p in project_list)
    total_cache_r = sum(p["cache_read"] for p in project_list)
    total_cache_c = sum(p["cache_create"] for p in project_list)
    total_all = total_input + total_output + total_cache_r + total_cache_c
    total_messages = sum(p["messages"] for p in project_list)
    total_sessions = sum(p["sessions"] for p in project_list)

    cache_total = total_cache_r + total_cache_c
    cache_hit_rate = (total_cache_r / cache_total * 100) if cache_total > 0 else 0

    return {
        "projects": project_list,
        "sessions": session_list,
        "totals": {
            "input_tokens": total_input, "output_tokens": total_output,
            "cache_read": total_cache_r, "cache_create": total_cache_c,
            "total_tokens": total_all, "messages": total_messages,
            "sessions": total_sessions, "projects": len(project_list),
            "cache_hit_rate": cache_hit_rate,
        },
        "daily_tokens": dict(sorted(daily_tokens.items())),
        "models": dict(model_totals),
        "tokens_5h": tokens_5h,
    }


# ── Dashboard rendering ──────────────────────────────────────────────────────

def make_dashboard(api_data, local_data, last_ok, error_msg, tick, data_dirs_info, usage_history=None, window_hours=5, history_samples=None):
    """Build the full dashboard layout — with personality."""

    now_str = datetime.now().strftime("%H:%M:%S")
    dot_char = "●" if not error_msg else "✕"
    dot_color = GREEN if not error_msg else RED
    cutoff_5h = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    totals = local_data.get("totals", {})
    daily = local_data.get("daily_tokens", {})
    models = local_data.get("models", {})

    # Determine mood from 5h utilization
    fh_pct = 0
    if api_data:
        fh = api_data.get("five_hour") or api_data.get("fiveHour") or {}
        fh_pct = fh.get("utilization") or 0

    # ── HERO PANEL: Creature + Speech + Gauges ────────────────────────────
    creature_lines = get_dash_creature(fh_pct, tick)
    speech = get_creature_speech(fh_pct, tick, error_msg)

    hero_rows = []
    hero_rows.append(Text())

    # Creature with speech bubble side by side
    for i, line in enumerate(creature_lines):
        row = Text()
        markup = Text.from_markup(line) if line else Text()
        row.append_text(markup)
        # Add speech bubble on line 3 (face line)
        if i == 3:
            padding = 12 - len(markup.plain)
            row.append(" " * max(1, padding))
            row.append(f"< {speech} >", style=f"italic {MUTED}")
        hero_rows.append(row)

    hero_rows.append(Text())

    # Plan badge + promo badge + connection
    is_promo, promo_label = detect_promo(api_data, history_samples)
    if api_data:
        plan = api_data.get("plan") or api_data.get("subscription_type") or ""
        if plan:
            badge_row = Text()
            badge_row.append("  ")
            badge_row.append(f" {plan} ", style=f"bold {VIOLET} on #1e1b4b")
            if is_promo:
                badge_row.append("  ")
                badge_row.append(f" \u26a1 {promo_label} ", style=PROMO_STYLE)
            badge_row.append("  ")
            badge_row.append(dot_char, style=f"bold {dot_color}")
            badge_row.append(f" {now_str}", style=MUTED)
            hero_rows.append(badge_row)
            hero_rows.append(Text())

    # Big gauges — show cached data even during errors
    if api_data:
        fh = api_data.get("five_hour") or api_data.get("fiveHour") or {}
        sd = api_data.get("seven_day") or api_data.get("sevenDay") or {}
        fh_pct_v = fh.get("utilization")
        sd_pct_v = sd.get("utilization")
        fh_reset = fh.get("resets_at") or fh.get("time_until_reset_secs")
        sd_reset = sd.get("resets_at") or sd.get("time_until_reset_secs")

        # 5h: usage bar + time bar (labels must be same char width for alignment)
        hero_rows.append(big_bar(fh_pct_v, width=30, label="  5h"))
        fh_reset_iso = fh_reset if isinstance(fh_reset, str) else None
        hero_rows.append(time_bar(fh_reset_iso, window_secs=5*3600, width=30))
        hero_rows.append(Text())

        # 7d: usage bar + time bar
        hero_rows.append(big_bar(sd_pct_v, width=30, label="  7d"))
        sd_reset_iso = sd_reset if isinstance(sd_reset, str) else None
        hero_rows.append(time_bar(sd_reset_iso, window_secs=7*86400, width=30))

        if error_msg:
            hero_rows.append(Text())
            hero_rows.append(Text(f"  {error_msg}", style=f"italic {RED}"))
    elif error_msg:
        hero_rows.append(Text(f"  {error_msg}", style=f"italic {RED}"))
        if last_ok:
            hero_rows.append(Text(f"  last ok {last_ok}", style=MUTED))
    else:
        spinning = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"][tick % 10]
        hero_rows.append(Text(f"  {spinning} connecting…", style=MUTED))

    hero_content = Text("\n").join(hero_rows)
    hero_panel = Panel(
        hero_content,
        title=Text.from_markup(
            f"[bold {AMBER}]◆[/] [bold {WHITE}]clu[/] [bold {VIOLET}]{__version__}[/]"
        ),
        border_style=VIOLET_D,
        box=box.ROUNDED,
        padding=(0, 2),
    )

    # ── STATS PANEL ───────────────────────────────────────────────────────
    stats_rows = []
    stats_rows.append(Text())

    # Burn rate with explanation
    burn = get_burn_rate(daily)
    burn_row = Text()
    burn_row.append("  ")
    burn_row.append_text(flame_icon(tick))
    burn_row.append(f" {fmt_tokens(int(burn))}/h", style=f"bold {WHITE}")
    stats_rows.append(burn_row)
    stats_rows.append(Text("  tokens burned today/h", style=DIM))
    stats_rows.append(Text())

    # Compact stats grid
    stat_line1 = Text()
    stat_line1.append(f"  {fmt_tokens(totals.get('total_tokens', 0))}", style=f"bold {WHITE}")
    stat_line1.append(" total tokens", style=DIM)
    stats_rows.append(stat_line1)

    stat_line2 = Text()
    stat_line2.append(f"  {totals.get('projects', 0)}", style=f"bold {VIOLET}")
    stat_line2.append(" projects  ", style=DIM)
    stat_line2.append(f"{totals.get('sessions', 0)}", style=f"bold {CYAN}")
    stat_line2.append(" sessions", style=DIM)
    stats_rows.append(stat_line2)

    # Cache efficiency with explanation
    cache_rate = totals.get("cache_hit_rate", 0)
    stats_rows.append(Text())
    cache_row = Text()
    cache_row.append("  ")
    cache_filled = round(cache_rate / 100 * 10)
    cache_empty = 10 - cache_filled
    cache_color = GREEN if cache_rate > 80 else (AMBER_L if cache_rate > 50 else RED)
    cache_row.append("█" * cache_filled, style=f"bold {cache_color}")
    cache_row.append("░" * cache_empty, style=DIM_D)
    cache_row.append(f" {cache_rate:.0f}%", style=f"bold {cache_color}")
    cache_row.append(" cache hit", style=DIM)
    stats_rows.append(cache_row)
    if cache_rate > 80:
        stats_rows.append(Text("  reusing prior context well", style=DIM))
    elif cache_rate > 50:
        stats_rows.append(Text("  some context reuse", style=DIM))
    else:
        stats_rows.append(Text("  mostly fresh context", style=DIM))

    # Pace indicator
    pace_pct, _ = compute_pace(api_data)
    if pace_pct is not None:
        stats_rows.append(Text())
        pace_row = Text()
        pace_row.append("  pace ", style=DIM)
        if pace_pct <= 100:    pc = GREEN
        elif pace_pct <= 150:  pc = ORANGE
        else:                  pc = RED
        pace_row.append(f"{pace_pct:.0f}%", style=f"bold {pc}")
        if pace_pct <= 80:     pace_row.append(" under budget", style=DIM)
        elif pace_pct <= 120:  pace_row.append(" on track", style=DIM)
        else:                  pace_row.append(" burning fast", style=DIM)
        stats_rows.append(pace_row)

    # Daily sparkline + heatmap
    if daily:
        stats_rows.append(Text())
        daily_vals = list(daily.values())[-14:]
        sp_row = Text()
        sp_row.append("  ")
        sp_row.append_text(sparkline(daily_vals, width=14))
        sp_row.append(" \u2190", style=MUTED)
        stats_rows.append(sp_row)
        stats_rows.append(Text("  daily usage, 14d (\u2190 today)", style=DIM))

        # Heatmap
        heatmap = render_heatmap(daily)
        if heatmap:
            stats_rows.append(Text())
            stats_rows.append(heatmap)

    # Models
    if models:
        stats_rows.append(Text())
        for model_name, tok_count in sorted(models.items(), key=lambda x: -x[1])[:3]:
            short = fmt_model(model_name)
            if short == "—" or "synthetic" in model_name: continue
            r = Text()
            r.append(f"  {short} ", style=f"{BLUE}")
            r.append(fmt_tokens(tok_count), style=f"bold {WHITE}")
            stats_rows.append(r)

    # Sources
    if data_dirs_info:
        stats_rows.append(Text())
        for src in data_dirs_info:
            r = Text()
            r.append(f"  ● ", style=GREEN)
            r.append(src, style=MUTED)
            stats_rows.append(r)

    stats_rows.append(Text())

    stats_content = Text("\n").join(stats_rows)
    stats_panel = Panel(
        stats_content,
        title=Text.from_markup(f"[bold {CYAN}]◈ stats[/]"),
        border_style=DIM,
        box=box.ROUNDED,
        padding=(0, 1),
    )

    # ── PROJECTS PANEL with visual bars ───────────────────────────────────
    proj_rows = []

    # Estimate external/untracked usage from API utilization vs local 5h tokens
    # We can't convert utilization % to exact tokens without knowing rate limit capacity,
    # so we estimate using the ratio: if local accounts for L tokens in 5h and API
    # reports U% utilization, then external ≈ max(0, U% - L/capacity*100).
    # Without capacity, we approximate: if local_5h > 0, assume capacity ≈ local_5h/(U/100).
    # When external usage exists, this underestimates capacity, making external_pct ≈ 0.
    # So instead: show external as the full API utilization bar when local_5h == 0,
    # and as a "gap" indicator when local is small relative to API-reported usage.
    external_pct = 0  # % of rate limit consumed by untracked sources
    local_5h = local_data.get("tokens_5h", 0)
    if api_data:
        fh = api_data.get("five_hour") or api_data.get("fiveHour") or {}
        api_5h_pct = fh.get("utilization") or 0
        if api_5h_pct > 2:
            if local_5h == 0:
                # No local usage in 5h but API shows utilization — all external
                external_pct = api_5h_pct
            else:
                # Heuristic: compare local burn rate to what utilization implies.
                # If local tokens seem too small for the reported utilization,
                # the gap suggests external usage. We use a rough capacity
                # estimate based on plan type (~tokens per 100% in 5h window).
                plan = (api_data.get("plan") or api_data.get("subscription_type") or "").lower()
                # Conservative capacity estimates (tokens per 5h at 100%)
                if "team" in plan or "enterprise" in plan:
                    cap_est = 50_000_000
                else:
                    cap_est = 30_000_000  # pro/individual
                local_pct_est = min(api_5h_pct, local_5h / cap_est * 100)
                external_pct = max(0, api_5h_pct - local_pct_est)

    # Include external in max calculation for proportional bars
    all_project_tokens = [p["total_tokens"] for p in local_data.get("projects", [])]
    max_tokens = max(all_project_tokens, default=1)

    medals = ["◆", "◆", "◆", "◇", "◇", "○", "○", "○", "·", "·"]
    medal_colors = [YELLOW, AMBER_L, ORANGE, VIOLET, VIOLET, BLUE, BLUE, CYAN, MUTED, MUTED]

    all_projects = local_data.get("projects", [])
    display_projects = [p for p in all_projects
                        if p.get("last_ts") and p["last_ts"] >= cutoff_5h][:12]
    name_w = max((len(p["name"]) for p in display_projects), default=14)
    name_w = max(name_w, 8)  # minimum width

    for i, p in enumerate(display_projects):
        total_tok = p["total_tokens"]
        pct_of_max = (total_tok / max_tokens * 100) if max_tokens > 0 else 0
        last_active = fmt_ago(p["last_ts"]) if p["last_ts"] else "—"

        medal = medals[min(i, len(medals) - 1)]
        medal_c = medal_colors[min(i, len(medal_colors) - 1)]

        r = Text()
        r.append(f" {medal} ", style=f"bold {medal_c}")
        r.append(f"{p['name']:<{name_w}}", style=f"bold {WHITE}" if i < 3 else WHITE)
        r.append(" ")
        r.append_text(project_bar(pct_of_max, width=18))
        r.append(f"  {fmt_tokens(total_tok):>6}", style=f"bold {AMBER_L}" if i < 3 else AMBER_L)
        r.append(f"  {p['sessions']:>2}s", style=MUTED)
        r.append(f"  {last_active:>8}", style=MUTED)
        proj_rows.append(r)

    # Add external/untracked bar if there's usage not accounted for locally
    if external_pct > 1:
        r = Text()
        r.append(f" ☁ ", style=f"bold {PINK}")
        r.append(f"{'external':<{name_w}}", style=f"italic {PINK}")
        r.append(" ")
        # Bar proportional to external % of rate limit (out of 100%)
        ext_ratio = min(external_pct / 100.0, 1.0)
        ext_filled = max(1, round(ext_ratio * 18))
        ext_empty = 18 - ext_filled
        r.append("░" * ext_filled, style=PINK)
        r.append("·" * ext_empty, style=DIM_D)
        r.append(f"  ~{external_pct:.0f}%", style=f"bold {PINK}")
        r.append(f" of limit", style=MUTED)
        proj_rows.append(r)

    proj_subtitle_parts = [f"{len(display_projects)} in last {window_hours}h"]
    if external_pct > 1:
        proj_subtitle_parts.append("+ external")
    proj_content = Text("\n").join(proj_rows)
    proj_panel = Panel(
        proj_content,
        title=Text.from_markup(f"[bold {AMBER}]▤ projects[/]"),
        subtitle=Text.from_markup(f"[{MUTED}]{' '.join(proj_subtitle_parts)}[/]"),
        border_style=DIM,
        box=box.ROUNDED,
        padding=(1, 1),
    )

    # ── SESSIONS PANEL ────────────────────────────────────────────────────
    recent_sessions = [s for s in local_data.get("sessions", [])
                       if s.get("last_ts") and s["last_ts"] >= cutoff_5h]
    sess_rows = []
    for i, s in enumerate(recent_sessions[:8]):
        total_tok = s["input_tokens"] + s["output_tokens"] + s["cache_read"] + s["cache_create"]
        duration = "—"
        if s["first_ts"] and s["last_ts"]:
            dur_secs = (s["last_ts"] - s["first_ts"]).total_seconds()
            duration = fmt_duration(dur_secs) if dur_secs > 0 else "<1m"

        when = fmt_ago(s["last_ts"]) if s["last_ts"] else "—"
        model_short = fmt_model(s.get("model", ""))

        r = Text()
        # Active indicator if session had activity in last 5 minutes
        now_utc = datetime.now(timezone.utc)
        is_active = s.get("last_ts") and (now_utc - s["last_ts"]).total_seconds() < 300
        if is_active:
            r.append(" ▸ ", style=f"bold {GREEN}")
        else:
            r.append("   ", style=MUTED)
        r.append(f"{s['project']:<12}", style=f"bold {WHITE}" if i == 0 else WHITE)
        r.append(f" {s['messages']:>3} msgs", style=MUTED)
        r.append(f"  {fmt_tokens(total_tok):>6}", style=AMBER_L)
        r.append(f"  {model_short:<8}", style=BLUE)
        r.append(f"  {duration:>5}", style=CYAN)
        r.append(f"  {when:>8}", style=MUTED)
        sess_rows.append(r)

    sess_content = Text("\n").join(sess_rows)
    sess_panel = Panel(
        sess_content,
        title=Text.from_markup(f"[bold {CYAN}]◉ sessions[/]"),
        subtitle=Text.from_markup(f"[{MUTED}]{len(recent_sessions)} in last {window_hours}h[/]"),
        border_style=DIM,
        box=box.ROUNDED,
        padding=(1, 1),
    )

    # ── CHART PANEL: Cumulative usage + budget line + rate ──────────────
    has_chart = False
    if api_data or (history_samples and len(history_samples) > 0):
        chart_rows = []

        # Cumulative usage chart with budget line
        cum_chart = render_cumulative_chart(history_samples, api_data, width=36, height=8)
        chart_rows.append(cum_chart)

        # Consumption rate chart (derivative)
        rate_chart = render_rate_chart(history_samples, width=36, height=4)
        if rate_chart:
            chart_rows.append(Text())
            chart_rows.append(rate_chart)

        chart_content = Text("\n").join(chart_rows)

        # Pace in subtitle
        pace_pct, _ = compute_pace(api_data)
        pace_str = ""
        if pace_pct is not None:
            if pace_pct <= 100:    pace_color = GREEN
            elif pace_pct <= 150:  pace_color = ORANGE
            else:                  pace_color = RED
            pace_str = f" · pace [{pace_color}]{pace_pct:.0f}%[/]"

        chart_panel = Panel(
            chart_content,
            title=Text.from_markup(f"[bold {AMBER_L}]\u25a4 cumulative[/][{MUTED}] \u00b7 5h window[/]"),
            subtitle=Text.from_markup(f"[{MUTED}]budget vs actual{pace_str}[/]"),
            border_style=DIM,
            box=box.ROUNDED,
            padding=(0, 1),
        )
        has_chart = True

    # ── Assemble layout ──────────────────────────────────────────────────
    layout = Layout()
    layout.split_column(
        Layout(name="top", ratio=4, minimum_size=18),
        Layout(name="bottom", ratio=5),
    )
    layout["top"].split_row(
        Layout(hero_panel, name="hero", ratio=2),
        Layout(stats_panel, name="stats", ratio=3),
    )
    if has_chart:
        layout["bottom"].split_row(
            Layout(name="bottom_left", ratio=2),
            Layout(name="bottom_right", ratio=3),
        )
        layout["bottom_left"].update(proj_panel)
        layout["bottom_right"].split_column(
            Layout(chart_panel, name="chart", ratio=3),
            Layout(sess_panel, name="sessions", ratio=3),
        )
    else:
        layout["bottom"].split_row(
            Layout(proj_panel, name="projects", ratio=2),
            Layout(sess_panel, name="sessions", ratio=3),
        )

    return layout


# ── Widget rendering (original clu) ─────────────────────────────────────────

def make_widget(data, last_ok, error_msg=None, tick=0, history_samples=None):
    """Build the full renderable widget (original cute mode)."""

    now_str = datetime.now().strftime("%H:%M:%S")
    dot_char = "●" if not error_msg else "✕"
    dot_color = GREEN if not error_msg else RED
    spinning = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"][tick % 10]

    creature_lines = get_creature_lines_widget(tick)

    rows = []
    for line in creature_lines:
        rows.append(Text.from_markup(line) if line else Text())
    rows.append(Text())

    header = Text()
    header.append(f"  ◆ ", style=f"bold {AMBER}")
    header.append("claude", style=f"bold {WHITE}")
    header.append("·", style=MUTED)
    header.append("usage", style=f"bold {VIOLET}")
    header.append(f"    ", style="")
    header.append(dot_char, style=f"bold {dot_color}")
    header.append(f" {now_str}", style=MUTED)
    rows.append(header)
    rows.append(Text())

    if data:
        fh = data.get("five_hour") or data.get("fiveHour") or {}
        sd = data.get("seven_day") or data.get("sevenDay") or {}
        plan = data.get("plan") or data.get("subscription_type") or ""

        fh_pct = fh.get("utilization")
        sd_pct = sd.get("utilization")
        fh_reset = fh.get("resets_at") or fh.get("time_until_reset_secs")
        sd_reset = sd.get("resets_at") or sd.get("time_until_reset_secs")

        is_promo, promo_label = detect_promo(data, history_samples)
        if plan:
            badge = Text()
            badge.append("  ")
            badge.append(f" {plan} ", style=f"bold {VIOLET} on #1e1b4b")
            if is_promo:
                badge.append("  ")
                badge.append(f" \u26a1 {promo_label} ", style=PROMO_STYLE)
            rows.append(badge)
            rows.append(Text())

        # 5h gauge with pace
        pace_pct, _ = compute_pace(data)
        label_5h = Text()
        label_5h.append("  5h  ", style=f"bold {AMBER}")
        label_5h.append(bar(fh_pct))
        label_5h.append(f"  {fmt_pct(fh_pct)}", style=f"bold {WHITE}")
        rows.append(label_5h)

        reset_5h = Text()
        reset_5h.append(f"       resets in ", style=MUTED)
        if isinstance(fh_reset, str):
            reset_5h.append(fmt_time_until(fh_reset), style=CYAN)
        else:
            reset_5h.append("\u2014", style=CYAN)
        if pace_pct is not None:
            if pace_pct <= 100:    pc = GREEN
            elif pace_pct <= 150:  pc = ORANGE
            else:                  pc = RED
            reset_5h.append(f"  pace ", style=DIM)
            reset_5h.append(f"{pace_pct:.0f}%", style=f"bold {pc}")
        rows.append(reset_5h)
        rows.append(Text())

        label_7d = Text()
        label_7d.append("  7d  ", style=f"bold {VIOLET}")
        label_7d.append(bar(sd_pct))
        label_7d.append(f"  {fmt_pct(sd_pct)}", style=f"bold {WHITE}")
        rows.append(label_7d)

        reset_7d = Text()
        reset_7d.append(f"       resets in ", style=MUTED)
        if isinstance(sd_reset, str):
            reset_7d.append(fmt_time_until(sd_reset), style=CYAN)
        else:
            reset_7d.append("\u2014", style=CYAN)
        rows.append(reset_7d)

        # Trend sparkline from persistent history
        if history_samples and len(history_samples) >= 3:
            rows.append(Text())
            trend_row = Text()
            trend_row.append("  \u25c8  ", style=MUTED)
            recent_5h = [s["5h"] for s in history_samples[-20:]]
            trend_row.append_text(sparkline(recent_5h, width=20))
            trend_row.append(" trend", style=DIM)
            rows.append(trend_row)

        total = data.get("total_tokens") or data.get("totalTokens")
        if total:
            rows.append(Text())
            tok_row = Text()
            tok_row.append(f"  \u25c8  ", style=f"{MUTED}")
            tok_row.append(fmt_tokens(total), style=f"bold {WHITE}")
            tok_row.append("  tokens this period", style=MUTED)
            rows.append(tok_row)

        if error_msg:
            rows.append(Text())
            rows.append(Text(f"  {error_msg}", style=f"italic {RED}"))
    elif error_msg:
        rows.append(Text(f"  {error_msg}", style=f"italic {RED}"))
        if last_ok:
            rows.append(Text(f"  last ok  {last_ok}", style=MUTED))
    else:
        rows.append(Text(f"  {spinning} fetching…", style=MUTED))

    rows.append(Text())

    footer = Text()
    footer.append(f"  refreshes every {REFRESH_SECS}s", style=MUTED)
    rows.append(footer)

    combined = Text("\n").join(rows)
    panel = Panel(
        combined,
        border_style=DIM,
        padding=(0, 0),
        box=box.SIMPLE,
    )
    return panel


# ── Terminal helpers ──────────────────────────────────────────────────────────

WIDGET_COLS = 46
WIDGET_ROWS = 22

def _cleanup():
    """Restore terminal state on exit."""
    sys.stdout.write("\033[?25h")
    sys.stdout.write("\033[0m")
    sys.stdout.flush()

def _setup_terminal(dash=False):
    """Clear screen, resize window, hide cursor, set title."""
    atexit.register(_cleanup)

    if dash:
        sys.stdout.write(f"\033]0;clu · dashboard\007")
    else:
        sys.stdout.write(f"\033]0;claude·usage\007")
        sys.stdout.write(f"\033[8;{WIDGET_ROWS};{WIDGET_COLS}t")

    sys.stdout.write("\033[2J\033[H")
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()


# ── Main loop ─────────────────────────────────────────────────────────────────

REFRESH_SECS = 90
_INITIAL_BACKOFF = 30

def main():
    global REFRESH_SECS

    parser = argparse.ArgumentParser(
        description="clu — Claude Usage Monitor",
        epilog="Examples:\n"
               "  clu                          # cute widget mode\n"
               "  clu --dash                   # full dashboard\n"
               "  clu --dash --data-dir ~/hpc-sync/.claude\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dash", action="store_true",
                        help="Full-terminal dashboard with per-project stats")
    parser.add_argument("--refresh", type=int, default=90,
                        help="API refresh interval in seconds (default: 90)")
    parser.add_argument("--token", type=str, default=None,
                        help="Override OAuth token")
    parser.add_argument("--session-key", type=str, default=None,
                        help="claude.ai sessionKey cookie (or set CLU_SESSION_KEY env var)")
    parser.add_argument("--no-resize", action="store_true",
                        help="Don't resize the terminal window")
    parser.add_argument("--window", type=int, default=5, choices=[5, 15, 24],
                        help="Time window in hours for sessions/projects (default: 5)")
    parser.add_argument("--data-dir", type=str, action="append", default=None,
                        help="Additional .claude data directory (e.g. synced from HPC). "
                             "Can be specified multiple times.")
    parser.add_argument("--serve", action="store_true",
                        help="Run a local JSON server for the M5StickC hardware widget")
    parser.add_argument("--port", type=int, default=8765,
                        help="Port for --serve mode (default: 8765)")
    parser.add_argument("--tray", action="store_true",
                        help="Run as menu bar / system tray app (requires: pip install clu-widget[tray])")
    args = parser.parse_args()

    REFRESH_SECS = args.refresh
    if args.token:
        os.environ["CLAUDE_TOKEN"] = args.token
    if args.session_key:
        os.environ["CLU_SESSION_KEY"] = args.session_key

    data_dirs = [Path.home() / ".claude"]
    data_dirs_info = ["~/.claude (local)"]
    if args.data_dir:
        for d in args.data_dir:
            p = Path(d).expanduser().resolve()
            if p.exists():
                data_dirs.append(p)
                data_dirs_info.append(str(d))
            else:
                print(f"Warning: data directory not found: {d}")

    token = get_token()
    cached = _load_cached_usage()
    api_data  = cached
    last_ok   = "cached" if cached else None
    error_msg = None
    tick      = 0

    if not token:
        console = Console(highlight=False)
        console.print(Panel(
            Text.from_markup(
                f"\n  [bold {AMBER}]◆[/]  [bold {WHITE}]clu[/]\n\n"
                f"  [bold {RED}]Token not found.[/]\n\n"
                f"  Make sure Claude Code is installed and\n"
                f"  you've run [bold {CYAN}]claude[/] at least once to login.\n\n"
                f"  Or pass it directly:\n"
                f"  [bold {CYAN}]clu --token sk-ant-…[/]\n"
            ),
            border_style=DIM,
            box=box.SIMPLE,
        ))
        sys.exit(1)

    if args.serve:
        try:
            _serve_mode(token, args.port, args.refresh)
        except KeyboardInterrupt:
            pass
        return

    if args.tray:
        try:
            _tray_mode(token, REFRESH_SECS)
        except KeyboardInterrupt:
            pass
        return

    # Ensure session key for claude.ai API (one-time interactive setup)
    if not args.session_key and not os.environ.get("CLU_SESSION_KEY"):
        _ensure_session_key()

    try:
        _run_loop(args, token, api_data, last_ok, error_msg, tick, data_dirs, data_dirs_info)
    except KeyboardInterrupt:
        _cleanup()

def _secs_until(iso_str):
    """Return seconds until an ISO timestamp, or None."""
    if not iso_str or not isinstance(iso_str, str):
        return None
    try:
        target = datetime.fromisoformat(iso_str)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        return max(0, int((target - datetime.now(timezone.utc)).total_seconds()))
    except Exception:
        return None


def _serve_mode(token, port=8765, refresh_secs=90):
    """Serve live usage JSON for the menu bar app and hardware widget."""
    import threading
    import socket
    from http.server import HTTPServer, BaseHTTPRequestHandler

    state = {"data": _load_cached_usage(), "error": None, "last_fetch": 0}

    def _do_fetch():
        try:
            state["data"] = fetch_usage(token)
            state["error"] = None
            state["last_fetch"] = time.time()
            _save_cached_usage(state["data"])
            _save_history_sample(state["data"])
        except RateLimited as e:
            wait = e.retry_after or 30
            state["error"] = f"rate limited ({int(wait)}s)"
        except Exception as e:
            state["error"] = str(e)[:60]

    def _payload_full():
        """Rich payload for the Swift menu bar app."""
        data = state["data"]
        if not data:
            return {"error": state["error"] or "no data yet"}

        fh = data.get("five_hour") or data.get("fiveHour") or {}
        sd = data.get("seven_day") or data.get("sevenDay") or {}
        fh_pct = fh.get("utilization") or 0
        sd_pct = sd.get("utilization") or 0
        fh_reset_iso = fh.get("resets_at")
        sd_reset_iso = sd.get("resets_at")
        tokens_5h = (fh.get("input_tokens") or 0) + (fh.get("output_tokens") or 0)

        # Pace
        pace_pct, elapsed_ratio = compute_pace(data)

        # Promo
        history = _load_history()
        is_promo, promo_label = detect_promo(data, history)

        # Plan
        plan = data.get("plan") or data.get("subscription_type") or ""

        # Full history for charts (up to 30 days — let clients filter per window)
        recent_history = history

        # Per-model breakdown from API response
        models = {}
        for key in ["seven_day_opus", "seven_day_sonnet"]:
            val = data.get(key)
            if val and isinstance(val, dict):
                model_name = key.replace("seven_day_", "")
                models[model_name] = {
                    "utilization": val.get("utilization"),
                    "resets_at": val.get("resets_at"),
                }

        # Extra usage credits
        extra = data.get("extra_usage")
        extra_info = None
        if extra and isinstance(extra, dict):
            extra_info = {
                "enabled": extra.get("is_enabled", False),
                "limit": extra.get("monthly_limit"),
                "used": extra.get("used_credits"),
                "utilization": extra.get("utilization"),
            }

        return {
            "pct_5h": fh_pct,
            "pct_7d": sd_pct,
            "reset_5h_iso": fh_reset_iso,
            "reset_7d_iso": sd_reset_iso,
            "reset_5h_secs": _secs_until(fh_reset_iso),
            "reset_7d_secs": _secs_until(sd_reset_iso),
            "tokens_5h": tokens_5h,
            "pace_pct": pace_pct,
            "elapsed_ratio": round(elapsed_ratio, 4) if elapsed_ratio else None,
            "plan": plan,
            "is_promo": is_promo,
            "promo_label": promo_label if is_promo else None,
            "models": models if models else None,
            "extra_usage": extra_info,
            "history": recent_history,
            "last_fetch": int(state["last_fetch"]),
            "error": state["error"],
        }

    def _payload_simple():
        """Lightweight payload for M5StickC hardware widget."""
        data = state["data"]
        if not data:
            return {"error": state["error"] or "no data yet"}
        fh = data.get("five_hour") or data.get("fiveHour") or {}
        sd = data.get("seven_day") or data.get("sevenDay") or {}
        tokens_5h = (fh.get("input_tokens") or 0) + (fh.get("output_tokens") or 0)
        return {
            "pct_5h": fh.get("utilization"),
            "pct_7d": sd.get("utilization"),
            "reset_5h_secs": _secs_until(fh.get("resets_at")),
            "reset_7d_secs": _secs_until(sd.get("resets_at")),
            "tokens_5h": tokens_5h,
            "error": state["error"],
        }

    def _fetch_loop():
        _do_fetch()  # immediate first fetch
        while True:
            time.sleep(refresh_secs)
            _do_fetch()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api":
                body = json.dumps(_payload_full()).encode()
                self._respond(200, body)
            elif self.path == "/api/simple":
                body = json.dumps(_payload_simple()).encode()
                self._respond(200, body)
            elif self.path == "/api/refresh":
                threading.Thread(target=_do_fetch, daemon=True).start()
                self._respond(200, b'{"ok":true}')
            else:
                self.send_response(404)
                self.end_headers()

        def _respond(self, code, body):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    threading.Thread(target=_fetch_loop, daemon=True).start()

    server = HTTPServer(("0.0.0.0", port), Handler)
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "your-mac-ip"
    print(f"clu serve \u00b7 http://{local_ip}:{port}/api")
    print(f"Endpoints:")
    print(f"  /api          full payload (menu bar app)")
    print(f"  /api/simple   lightweight (hardware widget)")
    print(f"  /api/refresh  trigger immediate fetch")
    print(f"Ctrl+C to stop")
    server.serve_forever()


def _tray_mode(token, refresh_secs):
    """Menu bar (macOS) or system tray (cross-platform) app."""
    if sys.platform == "darwin":
        try:
            import objc  # noqa: F401
            import AppKit  # noqa: F401
            return _tray_rumps(token, refresh_secs)  # now uses PyObjC directly
        except ImportError:
            pass
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
        return _tray_pystray(token, refresh_secs)
    except ImportError:
        pass
    print("Tray mode requires:")
    print("  macOS:         pip install pyobjc-framework-Cocoa")
    print("  Linux/Windows: pip install pystray Pillow")
    sys.exit(1)

def _tray_rumps(token, refresh_secs):
    """Native macOS menu bar app with popover dashboard using PyObjC."""
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    warnings.filterwarnings("ignore", message="PyObjCPointer")
    import threading
    import objc
    from AppKit import (
        NSApplication, NSApp, NSStatusBar, NSVariableStatusItemLength,
        NSPopover, NSViewController, NSView, NSColor, NSFont,
        NSTextField, NSBezierPath, NSMakeRect, NSButton, NSPoint, NSSize,
        NSApplicationActivationPolicyAccessory, NSPopoverBehaviorTransient,
    )
    from Foundation import NSObject, NSTimer
    from PyObjCTools import AppHelper

    POPOVER_W, POPOVER_H = 320, 460

    def hex_to_ns(hex_str):
        h = hex_str.lstrip("#")
        r, g, b = int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255
        return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0)

    def pct_color_hex(pct):
        if pct >= 90: return RED
        if pct >= 70: return ORANGE
        if pct >= 40: return AMBER_L
        return GREEN

    # ── Shared state ──
    state = {"data": _load_cached_usage(), "error": None, "history": _load_history()}
    state_lock = threading.Lock()

    def do_fetch():
        try:
            data = fetch_usage(token)
            with state_lock:
                state["data"] = data
                state["error"] = None
            _save_cached_usage(data)
            _save_history_sample(data)
            with state_lock:
                state["history"] = _load_history()
        except RateLimited:
            with state_lock:
                state["error"] = "rate limited"
        except Exception as e:
            with state_lock:
                state["error"] = str(e)[:40]

    # ── Chart view — draws line chart ──
    class ChartView(NSView):
        def drawRect_(self, rect):
            w, h = rect.size.width, rect.size.height
            pad_l, pad_r, pad_b, pad_t = 36, 8, 20, 5
            cw, ch = w - pad_l - pad_r, h - pad_b - pad_t

            # Background
            hex_to_ns("#131320").set()
            NSBezierPath.fillRect_(rect)

            # Grid lines
            for pct in [0, 25, 50, 75, 100]:
                y = pad_b + (pct / 100) * ch
                hex_to_ns("#262640").set()
                p = NSBezierPath.bezierPath()
                p.moveToPoint_(NSPoint(pad_l, y))
                p.lineToPoint_(NSPoint(w - pad_r, y))
                p.setLineWidth_(0.5)
                p.stroke()
                # Y-axis label
                hex_to_ns("#555577").set()
                s = f"{pct}%"
                attrs = {
                    objc.lookUpClass("NSFontAttributeName"): NSFont.monospacedSystemFontOfSize_weight_(8, 0.0),
                    objc.lookUpClass("NSForegroundColorAttributeName"): hex_to_ns("#555577"),
                }

            with state_lock:
                history = list(state.get("history", []))

            if len(history) < 2:
                return

            now_ts = time.time()
            cutoff = now_ts - 6 * 3600
            recent = [s for s in history if s.get("ts", 0) >= cutoff]
            if len(recent) < 2:
                recent = history[-60:]

            ts_min = recent[0]["ts"]
            ts_range = max(1, now_ts - ts_min)

            # Draw lines
            for key, color_hex in [("7d", ORANGE), ("5h", CYAN)]:
                hex_to_ns(color_hex).set()
                p = NSBezierPath.bezierPath()
                p.setLineWidth_(2.0)
                for i, s in enumerate(recent):
                    x = pad_l + ((s["ts"] - ts_min) / ts_range) * cw
                    y = pad_b + (s.get(key, 0) / 100) * ch
                    if i == 0:
                        p.moveToPoint_(NSPoint(x, y))
                    else:
                        p.lineToPoint_(NSPoint(x, y))
                p.stroke()

    # ── Progress bar view ──
    class BarView(NSView):
        _pct = 0
        _color_hex = GREEN

        def set_pct_(self, pct):
            self._pct = pct
            self._color_hex = pct_color_hex(pct)
            self.setNeedsDisplay_(True)

        def drawRect_(self, rect):
            w, h = rect.size.width, rect.size.height
            # Background track
            hex_to_ns("#374151").set()
            track = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, 4, 4)
            track.fill()
            # Filled portion
            filled_w = max(0, self._pct / 100 * w)
            if filled_w > 0:
                hex_to_ns(self._color_hex).set()
                fill_rect = NSMakeRect(0, 0, filled_w, h)
                fill_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(fill_rect, 4, 4)
                fill_path.fill()

    # ── Dashboard content view ──
    class DashboardView(NSView):
        def initWithFrame_(self, frame):
            self = objc.super(DashboardView, self).initWithFrame_(frame)
            if self is None:
                return None

            w = int(frame.size.width)
            y = int(frame.size.height) - 15

            # Title
            y -= 24
            self._title = self._label("Claude Usage", 15, y, 200, 22,
                                      NSFont.boldSystemFontOfSize_(16), "#f3f4f6")

            # ── 5-Hour Window ──
            y -= 28
            self._lbl_5h = self._label("5-Hour Window", 15, y, 180, 18,
                                       NSFont.systemFontOfSize_(13), "#d1d5db")
            self._pct_5h = self._label("", w - 60, y, 45, 18,
                                       NSFont.boldSystemFontOfSize_(13), "#f3f4f6")
            y -= 14
            self._bar_5h = BarView.alloc().initWithFrame_(NSMakeRect(15, y, w - 30, 10))
            self.addSubview_(self._bar_5h)

            y -= 20
            self._reset_5h = self._label("", 15, y, 280, 16,
                                         NSFont.systemFontOfSize_(11), "#6b7280")

            # ── 7-Day Window ──
            y -= 26
            self._lbl_7d = self._label("7-Day Window", 15, y, 180, 18,
                                       NSFont.systemFontOfSize_(13), "#d1d5db")
            self._pct_7d = self._label("", w - 60, y, 45, 18,
                                       NSFont.boldSystemFontOfSize_(13), "#f3f4f6")
            y -= 14
            self._bar_7d = BarView.alloc().initWithFrame_(NSMakeRect(15, y, w - 30, 10))
            self.addSubview_(self._bar_7d)

            y -= 20
            self._reset_7d = self._label("", 15, y, 280, 16,
                                         NSFont.systemFontOfSize_(11), "#6b7280")

            # ── Pace ──
            y -= 24
            self._pace = self._label("", 15, y, 280, 18,
                                     NSFont.boldSystemFontOfSize_(12), CYAN)

            # ── Chart ──
            y -= 8
            chart_h = 150
            y -= chart_h
            self._chart = ChartView.alloc().initWithFrame_(NSMakeRect(10, y, w - 20, chart_h))
            self.addSubview_(self._chart)

            # Legend
            y -= 20
            self._label("\u25cf 5h", 20, y, 30, 16, NSFont.systemFontOfSize_(11), CYAN)
            self._label("\u25cf 7d", 60, y, 30, 16, NSFont.systemFontOfSize_(11), ORANGE)

            # Footer
            y -= 26
            self._updated = self._label("", 15, y, 150, 14,
                                        NSFont.systemFontOfSize_(10), "#6b7280")
            self.btn_refresh = self._button("Refresh", w - 130, y - 2, 55, 20, "refresh:")
            self.btn_quit = self._button("Quit", w - 55, y - 2, 40, 20, "quit:")

            return self

        def _label(self, text, x, y, w, h, font, color):
            lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
            lbl.setStringValue_(text)
            lbl.setFont_(font)
            lbl.setTextColor_(hex_to_ns(color))
            lbl.setBezeled_(False)
            lbl.setDrawsBackground_(False)
            lbl.setEditable_(False)
            lbl.setSelectable_(False)
            self.addSubview_(lbl)
            return lbl

        def _button(self, title, x, y, w, h, action):
            btn = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
            btn.setTitle_(title)
            btn.setBezelStyle_(14)
            btn.setFont_(NSFont.systemFontOfSize_(10))
            btn.setAction_(action)
            self.addSubview_(btn)
            return btn

        def drawRect_(self, rect):
            hex_to_ns("#111827").set()
            NSBezierPath.fillRect_(rect)

        def refresh_data(self):
            with state_lock:
                data = state.get("data")

            if not data:
                return

            fh = data.get("five_hour") or data.get("fiveHour") or {}
            sd = data.get("seven_day") or data.get("sevenDay") or {}
            fh_pct = fh.get("utilization") or 0
            sd_pct = sd.get("utilization") or 0

            self._pct_5h.setStringValue_(f"{fh_pct:.0f}%")
            self._pct_7d.setStringValue_(f"{sd_pct:.0f}%")
            self._bar_5h.set_pct_(fh_pct)
            self._bar_7d.set_pct_(sd_pct)

            fh_reset = fh.get("resets_at")
            sd_reset = sd.get("resets_at")
            self._reset_5h.setStringValue_(f"Resets {fmt_time_until(fh_reset)}")
            self._reset_7d.setStringValue_(f"Resets {fmt_time_until(sd_reset)}")

            pace_pct, _ = compute_pace(data)
            if pace_pct is not None:
                if pace_pct <= 100:
                    self._pace.setStringValue_(f"Pace: {pace_pct:.0f}% \u2714 under budget")
                    self._pace.setTextColor_(hex_to_ns(GREEN))
                elif pace_pct <= 150:
                    self._pace.setStringValue_(f"Pace: {pace_pct:.0f}% \u25b2 ahead of budget")
                    self._pace.setTextColor_(hex_to_ns(ORANGE))
                else:
                    self._pace.setStringValue_(f"Pace: {pace_pct:.0f}% \u26a0 burning fast")
                    self._pace.setTextColor_(hex_to_ns(RED))

            self._chart.setNeedsDisplay_(True)
            self._updated.setStringValue_(f"Updated {datetime.now().strftime('%H:%M:%S')}")

    # ── App delegate ──
    class AppDelegate(NSObject):
        statusItem = objc.ivar()
        popover = objc.ivar()
        dashboard = objc.ivar()

        def applicationDidFinishLaunching_(self, notification):
            self.statusItem = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
            self.statusItem.button().setTitle_("clu \u00b7\u00b7\u00b7")
            self.statusItem.button().setAction_("togglePopover:")
            self.statusItem.button().setTarget_(self)

            self.popover = NSPopover.alloc().init()
            self.popover.setBehavior_(NSPopoverBehaviorTransient)

            vc = NSViewController.alloc().init()
            self.dashboard = DashboardView.alloc().initWithFrame_(
                NSMakeRect(0, 0, POPOVER_W, POPOVER_H))
            vc.setView_(self.dashboard)
            self.popover.setContentSize_(NSSize(POPOVER_W, POPOVER_H))
            self.popover.setContentViewController_(vc)

            self.dashboard.btn_refresh.setTarget_(self)
            self.dashboard.btn_quit.setTarget_(self)

            threading.Thread(target=self._fetch_loop, daemon=True).start()

            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                5.0, self, "refreshUI:", None, True)
            self._update_ui()

        def _fetch_loop(self):
            do_fetch()
            while True:
                time.sleep(refresh_secs)
                do_fetch()
                self.performSelectorOnMainThread_withObject_waitUntilDone_("refreshUI:", None, False)

        @objc.typedSelector(b"v@:@")
        def togglePopover_(self, sender):
            if self.popover.isShown():
                self.popover.close()
            else:
                self._update_ui()
                self.popover.showRelativeToRect_ofView_preferredEdge_(
                    self.statusItem.button().bounds(),
                    self.statusItem.button(), 3)

        @objc.typedSelector(b"v@:@")
        def refreshUI_(self, timer):
            self._update_ui()

        @objc.typedSelector(b"v@:@")
        def refresh_(self, sender):
            threading.Thread(target=do_fetch, daemon=True).start()

        @objc.typedSelector(b"v@:@")
        def quit_(self, sender):
            NSApp.terminate_(None)

        def _update_ui(self):
            with state_lock:
                data = state.get("data")
            if data:
                fh = data.get("five_hour") or data.get("fiveHour") or {}
                pct = fh.get("utilization") or 0
                if pct >= 90:     dot = "\U0001f534"
                elif pct >= 70:   dot = "\U0001f7e0"
                elif pct >= 40:   dot = "\U0001f7e1"
                else:             dot = "\U0001f7e2"
                self.statusItem.button().setTitle_(f"{dot} {pct:.0f}%")
            if self.dashboard:
                self.dashboard.refresh_data()

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()

def _tray_pystray(token, refresh_secs):
    """Cross-platform system tray using pystray + Pillow."""
    import pystray
    from PIL import Image, ImageDraw
    import threading

    state = {"data": _load_cached_usage(), "error": None, "running": True}
    lock = threading.Lock()

    def create_icon_image(pct):
        """Generate a 64x64 icon with colored circle."""
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        if pct >= 90:     color = (248, 113, 113)   # RED
        elif pct >= 70:   color = (251, 146, 60)     # ORANGE
        elif pct >= 40:   color = (251, 191, 36)     # AMBER
        else:             color = (52, 211, 153)      # GREEN
        draw.ellipse([4, 4, 60, 60], fill=color)
        # Draw text
        try:
            from PIL import ImageFont
            font = ImageFont.load_default()
            text = f"{pct:.0f}"
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text(((64 - tw) // 2, (64 - th) // 2), text, fill=(255, 255, 255), font=font)
        except Exception:
            pass
        return img

    def fetch_loop():
        backoff = _INITIAL_BACKOFF
        while state["running"]:
            try:
                data = fetch_usage(token)
                with lock:
                    state["data"] = data
                    state["error"] = None
                _save_cached_usage(data)
                _save_history_sample(data)
                # Update icon
                fh = data.get("five_hour") or data.get("fiveHour") or {}
                fh_pct = fh.get("utilization") or 0
                try:
                    icon.icon = create_icon_image(fh_pct)
                except Exception:
                    pass
            except RateLimited:
                with lock:
                    state["error"] = "rate limited"
            except Exception as e:
                with lock:
                    state["error"] = str(e)[:40]
            time.sleep(refresh_secs)

    def make_menu():
        with lock:
            data = state["data"]
        if not data:
            return pystray.Menu(
                pystray.MenuItem("No data yet", lambda: None),
                pystray.MenuItem("Quit", lambda icon, item: icon.stop()),
            )
        fh = data.get("five_hour") or data.get("fiveHour") or {}
        sd = data.get("seven_day") or data.get("sevenDay") or {}
        fh_pct = fh.get("utilization") or 0
        sd_pct = sd.get("utilization") or 0
        plan = data.get("plan") or data.get("subscription_type") or "\u2014"
        fh_reset = fh.get("resets_at")
        sd_reset = sd.get("resets_at")
        pace_pct, _ = compute_pace(data)
        pace_str = f"{pace_pct:.0f}%" if pace_pct is not None else "\u2014"

        return pystray.Menu(
            pystray.MenuItem(f"5h: {fh_pct:.0f}%", lambda: None),
            pystray.MenuItem(f"7d: {sd_pct:.0f}%", lambda: None),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"Resets in {fmt_time_until(fh_reset)}", lambda: None),
            pystray.MenuItem(f"Pace: {pace_str}", lambda: None),
            pystray.MenuItem(f"Plan: {plan}", lambda: None),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda icon, item: icon.stop()),
        )

    icon = pystray.Icon("clu", create_icon_image(0), "clu", make_menu())
    threading.Thread(target=fetch_loop, daemon=True).start()

    # Periodic menu refresh
    def menu_updater():
        while state["running"]:
            time.sleep(30)
            try:
                icon.menu = make_menu()
            except Exception:
                pass

    threading.Thread(target=menu_updater, daemon=True).start()
    icon.run()


def _initial_fetch_time(api_data):
    """If we have cached data, delay first fetch to avoid 429."""
    if api_data and api_data.get("_cached_at"):
        elapsed = time.time() - api_data["_cached_at"]
        remaining = max(0, REFRESH_SECS - elapsed)
        if remaining > 0:
            return time.time() + remaining
    return 0

def _run_loop(args, token, api_data, last_ok, error_msg, tick, data_dirs, data_dirs_info):
    # Load persistent history for both modes
    history_samples = _load_history()

    if args.dash:
        _setup_terminal(dash=True)
        console = Console(highlight=False)

        local_data = parse_project_data(data_dirs)
        history = UsageHistory(max_samples=60)
        history.load_from_persistent(history_samples)
        next_fetch = _initial_fetch_time(api_data)
        backoff = _INITIAL_BACKOFF
        next_local_refresh = time.time() + 300

        with Live(
            make_dashboard(api_data, local_data, last_ok, error_msg, tick, data_dirs_info, history, args.window, history_samples),
            console=console,
            refresh_per_second=2,
            transient=False,
        ) as live:
            while True:
                now_ts = time.time()

                if now_ts >= next_fetch:
                    try:
                        api_data  = fetch_usage(token)
                        error_msg = None
                        last_ok   = datetime.now().strftime("%H:%M:%S")
                        next_fetch = now_ts + REFRESH_SECS
                        backoff = _INITIAL_BACKOFF
                        # Record sample for live chart + persistent history
                        history.record(api_data)
                        _save_history_sample(api_data)
                    except RateLimited as e:
                        if e.retry_after is not None:
                            wait = max(e.retry_after, 2)
                            backoff = _INITIAL_BACKOFF
                        else:
                            wait = min(backoff * 2, REFRESH_SECS)
                            backoff = wait
                        next_fetch = now_ts + wait + random.uniform(0, 3)
                        error_msg = "rate limited"
                    except requests.HTTPError as e:
                        error_msg = f"HTTP {e.response.status_code}"
                        next_fetch = now_ts + REFRESH_SECS
                    except Exception as e:
                        error_msg = str(e)[:36]
                        next_fetch = now_ts + 10

                # Live countdown for any pending retry
                retry_secs = max(0, int(next_fetch - time.time()))
                display_error = error_msg
                if error_msg == "rate limited":
                    if retry_secs > 0:
                        display_error = f"rate limited (retry in {retry_secs}s)"
                    else:
                        display_error = None

                if now_ts >= next_local_refresh:
                    local_data = parse_project_data(data_dirs)
                    next_local_refresh = now_ts + 300

                live.update(make_dashboard(
                    api_data, local_data, last_ok, display_error, tick, data_dirs_info, history, args.window, history_samples
                ))
                tick += 1
                time.sleep(0.5)

    else:
        console = Console(width=WIDGET_COLS, highlight=False)

        if not args.no_resize:
            _setup_terminal()
        else:
            atexit.register(_cleanup)
            sys.stdout.write("\033[2J\033[H\033[?25l")
            sys.stdout.write(f"\033]0;claude·usage\007")
            sys.stdout.flush()

        next_fetch = _initial_fetch_time(api_data)
        backoff = _INITIAL_BACKOFF

        with Live(make_widget(api_data, last_ok, error_msg, tick, history_samples),
                  console=console,
                  refresh_per_second=2,
                  transient=False) as live:
            while True:
                now_ts = time.time()

                if now_ts >= next_fetch:
                    try:
                        api_data  = fetch_usage(token)
                        error_msg = None
                        last_ok   = datetime.now().strftime("%H:%M:%S")
                        next_fetch = now_ts + REFRESH_SECS
                        backoff = _INITIAL_BACKOFF
                        _save_history_sample(api_data)
                    except RateLimited as e:
                        if e.retry_after is not None:
                            wait = max(e.retry_after, 2)
                            backoff = _INITIAL_BACKOFF
                        else:
                            wait = min(backoff * 2, REFRESH_SECS)
                            backoff = wait
                        next_fetch = now_ts + wait + random.uniform(0, 3)
                        error_msg = "rate limited"
                    except requests.HTTPError as e:
                        error_msg = f"HTTP {e.response.status_code}"
                        next_fetch = now_ts + REFRESH_SECS
                    except Exception as e:
                        error_msg = str(e)[:36]
                        next_fetch = now_ts + 10

                # Live countdown for any pending retry
                retry_secs = max(0, int(next_fetch - time.time()))
                display_error = error_msg
                if error_msg == "rate limited":
                    if retry_secs > 0:
                        display_error = f"rate limited (retry in {retry_secs}s)"
                    else:
                        display_error = None

                live.update(make_widget(api_data, last_ok, display_error, tick, history_samples))
                tick += 1
                time.sleep(0.5)


if __name__ == "__main__":
    main()
