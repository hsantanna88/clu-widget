#!/usr/bin/env python3
"""
clu — Claude Usage Monitor 2.0

Widget mode (default):
    clu                          # cute animated widget
    clu --refresh 60             # custom refresh interval

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

try:
    import requests
except ImportError:
    print("Missing: pip install rich requests --break-system-packages")
    sys.exit(1)

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

def get_dash_creature(utilization, tick):
    """Get a happy bouncy creature — chunky pixel-art style."""
    bounce = (tick % 80) < 12  # bounce every 40 seconds
    frame = (tick % 80) // 3 if bounce else -1

    # Background-colored spaces — no block chars, no line-gap stripes
    ant  = f"      [{VIOLET}]*[/]"
    stk  = f"      [{VIOLET}]|[/]"
    top  = f"  [on {SKIN}]          [/]"
    face  = f"  [on {SKIN}]  [/][on {DIM_D}] [{VIOLET} on {DIM_D}]*[/][on {DIM_D}]  [{VIOLET} on {DIM_D}]*[/][on {DIM_D}] [/][on {SKIN}]  [/]"
    blink = f"  [on {SKIN}]  [/][on {DIM_D}] [{VIOLET} on {DIM_D}]^[/][on {DIM_D}]  [{VIOLET} on {DIM_D}]^[/][on {DIM_D}] [/][on {SKIN}]  [/]"
    chin = f"  [on {SKIN}]          [/]"
    body = f"   [on {SKIN}]        [/]"
    legs = f"   [on {SKIN}]  [/]    [on {SKIN}]  [/]"

    # Blink every ~10 seconds for 1 second
    is_blink = (tick % 20) in (0, 1)
    eyes = blink if is_blink else face

    if bounce and 0 <= frame < 4:
        frames = [
            [ant, stk, top, eyes, chin, body, legs],
            [f"", ant, top, blink, chin, body, f""],
            [ant, stk, top, blink, chin, body, f""],
            [f"", ant, top, eyes, chin, body, legs],
        ]
        return frames[frame]
    return [ant, stk, top, eyes, chin, body, legs]


def get_creature_speech(utilization, tick):
    """Get a cheerful speech bubble — always positive vibes."""
    phrases = [
        "let's go!", "vibing~", "all good!", "smooth sailing~",
        "doing great!", "feeling good!", "cruising along~", "no worries!",
        "looking good!", "keep going!", "nice work!", "on a roll!",
    ]
    idx = (tick // 20) % len(phrases)
    return phrases[idx]


# ── Original widget creature (wider spacing for 46-col widget) ───────────────
# Background-colored spaces — no block chars, no line-gap stripes
_W_ANT   = f"          [{VIOLET}]*[/]"
_W_STK   = f"          [{VIOLET}]|[/]"
_W_TOP   = f"      [on {SKIN}]          [/]"
_W_FACE  = f"      [on {SKIN}]  [/][on {DIM_D}] [{VIOLET} on {DIM_D}]*[/][on {DIM_D}]  [{VIOLET} on {DIM_D}]*[/][on {DIM_D}] [/][on {SKIN}]  [/]"
_W_BLINK = f"      [on {SKIN}]  [/][on {DIM_D}] [{VIOLET} on {DIM_D}]^[/][on {DIM_D}]  [{VIOLET} on {DIM_D}]^[/][on {DIM_D}] [/][on {SKIN}]  [/]"
_W_CHIN  = f"      [on {SKIN}]          [/]"
_W_BODY  = f"       [on {SKIN}]        [/]"
_W_LEGS  = f"       [on {SKIN}]  [/]    [on {SKIN}]  [/]"

CREATURE_IDLE_WIDGET = [
    [_W_ANT, _W_STK, _W_TOP, _W_FACE, _W_CHIN, _W_BODY, _W_LEGS],
]

CREATURE_BLINK_WIDGET = [
    [_W_ANT, _W_STK, _W_TOP, _W_BLINK, _W_CHIN, _W_BODY, _W_LEGS],
]

CREATURE_BOUNCE_WIDGET = [
    [_W_ANT, _W_STK, _W_TOP, _W_FACE,  _W_CHIN, _W_BODY, _W_LEGS],
    [f"",    _W_ANT, _W_TOP, _W_BLINK, _W_CHIN, _W_BODY, f""],
    [_W_ANT, _W_STK, _W_TOP, _W_BLINK, _W_CHIN, _W_BODY, f""],
    [f"",    _W_ANT, _W_TOP, _W_FACE,  _W_CHIN, _W_BODY, _W_LEGS],
]

BOUNCE_INTERVAL = 120
BOUNCE_FRAME_HOLD = 3

def get_creature_lines_widget(tick):
    """Return the creature lines for widget mode."""
    cycle_pos = tick % BOUNCE_INTERVAL
    bounce_total_ticks = len(CREATURE_BOUNCE_WIDGET) * BOUNCE_FRAME_HOLD
    if cycle_pos < bounce_total_ticks:
        frame_idx = cycle_pos // BOUNCE_FRAME_HOLD
        return CREATURE_BOUNCE_WIDGET[frame_idx]
    # Blink every ~10 seconds for 1 second
    elif (tick % 20) in (0, 1):
        return CREATURE_BLINK_WIDGET[0]
    else:
        return CREATURE_IDLE_WIDGET[0]


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


# ── Real-time usage history ───────────────────────────────────────────────────

class UsageHistory:
    """Ring buffer that records 5h utilization samples over time."""

    def __init__(self, max_samples=60):
        self.max_samples = max_samples  # ~30 min at 30s refresh
        self.samples_5h = []
        self.samples_7d = []
        self.timestamps = []

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

    def render_chart(self, width=30, height=6, show_7d=False):
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

            # Plot each sample
            for v in vals:
                v_pos = (v / mx) * (height - 1) if mx > 0 else 0
                filled_row = height - 1 - row_idx  # row 0 is top
                if v_pos >= filled_row + 0.5:
                    if v >= 90:   c = RED
                    elif v >= 70: c = ORANGE
                    elif v >= 40: c = color
                    else:         c = GREEN
                    r.append("█", style=c)
                elif v_pos >= filled_row:
                    if v >= 90:   c = RED
                    elif v >= 70: c = ORANGE
                    elif v >= 40: c = color
                    else:         c = GREEN
                    r.append("▄", style=c)
                else:
                    r.append(" ")

            rows.append(r)

        # Bottom axis
        axis = Text()
        axis.append("   └", style=DIM)
        axis.append("─" * len(vals), style=DIM)
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

def fetch_usage(token):
    """Hit Anthropic's oauth/usage endpoint. Returns dict or raises."""
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
        raise RateLimited(secs)
    resp.raise_for_status()
    return resp.json()


# ── JSONL Project Data Parser ────────────────────────────────────────────────

# Structural path segments to strip when extracting project names
_STRUCTURAL = {
    "users", "work", "desktop", "documents", "downloads",
    "projects", "code", "source", "repos", "git", "github",
    "home", "research", "development", "dev", "src",
}

def _extract_project_name(dir_name):
    """Extract a meaningful, human-readable project name from a Claude project directory name.

    Directory names are full paths with '-' replacing '/', e.g.:
      -Users-hsantanna-Work-Research-sis-employment
    We strip the structural prefix (Users, username, Work, Research...)
    and prettify the remaining tail: title-case words, uppercase short
    acronyms (SIS, CLO), and convert hyphens to spaces.
    """
    # Split into segments (filter empties from leading -)
    parts = [p for p in dir_name.split("-") if p]

    if not parts:
        return dir_name

    # Strip structural prefix segments + likely username (index 1 after "Users")
    start = 0
    has_users_prefix = parts[0].lower() == "users" and len(parts) >= 2

    for i, part in enumerate(parts):
        if part.lower() in _STRUCTURAL:
            start = i + 1
            continue
        if has_users_prefix and i == 1:
            start = i + 1
            continue
        break

    tail = parts[start:] if start < len(parts) else [parts[-1]]

    # Prettify each word: short words (<=3 chars) become UPPERCASE (likely
    # acronyms like SIS, CLO, API), longer words get Title Case.
    # Common short English words are excluded from the acronym rule.
    _COMMON_SHORT = {
        "a", "an", "and", "the", "of", "or", "in", "on", "to", "at",
        "for", "is", "it", "my", "by", "do", "if", "no", "so", "up",
        "us", "we", "bad", "big", "new", "old", "all", "any", "but",
        "can", "did", "get", "has", "her", "him", "his", "how", "its",
        "let", "may", "not", "now", "our", "out", "own", "run", "say",
        "she", "too", "two", "use", "was", "way", "who", "why", "yet",
    }
    pretty = []
    for word in tail:
        if len(word) <= 3 and word.lower() not in _COMMON_SHORT:
            pretty.append(word.upper())
        else:
            pretty.append(word.capitalize())

    name = " ".join(pretty)

    # Truncate to reasonable length for display (break at word boundary)
    if len(name) > 20:
        truncated = name[:20].rsplit(" ", 1)[0]
        name = truncated if truncated else name[:20]

    return name if name else parts[-1]


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

def make_dashboard(api_data, local_data, last_ok, error_msg, tick, data_dirs_info, usage_history=None):
    """Build the full dashboard layout — with personality."""

    now_str = datetime.now().strftime("%H:%M:%S")
    dot_char = "●" if not error_msg else "✕"
    dot_color = GREEN if not error_msg else RED
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
    speech = get_creature_speech(fh_pct, tick)

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

    # Plan badge + connection
    if api_data:
        plan = api_data.get("plan") or api_data.get("subscription_type") or ""
        if plan:
            badge_row = Text()
            badge_row.append("  ")
            badge_row.append(f" {plan} ", style=f"bold {VIOLET} on #1e1b4b")
            badge_row.append("  ")
            badge_row.append(dot_char, style=f"bold {dot_color}")
            badge_row.append(f" {now_str}", style=MUTED)
            hero_rows.append(badge_row)
            hero_rows.append(Text())

    # Big gauges
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
            f"[bold {AMBER}]◆[/] [bold {WHITE}]clu[/] [bold {VIOLET}]2.0[/]"
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

    # Daily sparkline
    if daily:
        stats_rows.append(Text())
        daily_vals = list(daily.values())[-14:]
        sp_row = Text()
        sp_row.append("  ")
        sp_row.append_text(sparkline(daily_vals, width=14))
        sp_row.append(" ←", style=MUTED)
        stats_rows.append(sp_row)
        stats_rows.append(Text("  daily usage, 14d (← today)", style=DIM))

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

    display_projects = local_data.get("projects", [])[:12]
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

    proj_subtitle_parts = [f"{totals.get('projects', 0)} local"]
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
    sess_rows = []
    for i, s in enumerate(local_data.get("sessions", [])[:8]):
        total_tok = s["input_tokens"] + s["output_tokens"] + s["cache_read"] + s["cache_create"]
        duration = "—"
        if s["first_ts"] and s["last_ts"]:
            dur_secs = (s["last_ts"] - s["first_ts"]).total_seconds()
            duration = fmt_duration(dur_secs) if dur_secs > 0 else "<1m"

        when = fmt_ago(s["last_ts"]) if s["last_ts"] else "—"
        model_short = fmt_model(s.get("model", ""))

        r = Text()
        # Active indicator for most recent
        if i == 0:
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
        subtitle=Text.from_markup(f"[{MUTED}]{totals.get('sessions', 0)} total[/]"),
        border_style=DIM,
        box=box.ROUNDED,
        padding=(1, 1),
    )

    # ── CHART PANEL: Real-time usage graph ──────────────────────────────
    if usage_history and len(usage_history.samples_5h) >= 1:
        chart_content_rows = []
        chart_content_rows.append(usage_history.render_chart(width=30, height=5, show_7d=False))
        chart_content = Text("\n").join(chart_content_rows)
        n_pts = len(usage_history.samples_5h)
        elapsed_s = n_pts * 30
        elapsed_m = elapsed_s // 60
        sub = f"last {elapsed_m}m" if elapsed_m > 0 else "starting…"
        chart_panel = Panel(
            chart_content,
            title=Text.from_markup(f"[bold {AMBER_L}]▤ live[/]"),
            subtitle=Text.from_markup(f"[{MUTED}]{sub}[/]"),
            border_style=DIM,
            box=box.ROUNDED,
            padding=(0, 1),
        )
        has_chart = True
    else:
        has_chart = False

    # ── Assemble layout ──────────────────────────────────────────────────
    layout = Layout()
    layout.split_column(
        Layout(name="top", ratio=4, minimum_size=18),
        Layout(name="bottom", ratio=5),
    )
    layout["top"].split_row(
        Layout(hero_panel, name="hero", ratio=3),
        Layout(stats_panel, name="stats", ratio=2),
    )
    if has_chart:
        layout["bottom"].split_row(
            Layout(name="bottom_left", ratio=3),
            Layout(name="bottom_right", ratio=2),
        )
        layout["bottom_left"].update(proj_panel)
        layout["bottom_right"].split_column(
            Layout(chart_panel, name="chart", ratio=2),
            Layout(sess_panel, name="sessions", ratio=3),
        )
    else:
        layout["bottom"].split_row(
            Layout(proj_panel, name="projects", ratio=3),
            Layout(sess_panel, name="sessions", ratio=2),
        )

    return layout


# ── Widget rendering (original clu) ─────────────────────────────────────────

def make_widget(data, last_ok, error_msg=None, tick=0):
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

    if error_msg:
        rows.append(Text(f"  {error_msg}", style=f"italic {RED}"))
        if last_ok:
            rows.append(Text(f"  last ok  {last_ok}", style=MUTED))
    elif data:
        fh = data.get("five_hour") or data.get("fiveHour") or {}
        sd = data.get("seven_day") or data.get("sevenDay") or {}
        plan = data.get("plan") or data.get("subscription_type") or ""

        fh_pct = fh.get("utilization")
        sd_pct = sd.get("utilization")
        fh_reset = fh.get("resets_at") or fh.get("time_until_reset_secs")
        sd_reset = sd.get("resets_at") or sd.get("time_until_reset_secs")

        if plan:
            badge = Text()
            badge.append("  ")
            badge.append(f" {plan} ", style=f"bold {VIOLET} on #1e1b4b")
            rows.append(badge)
            rows.append(Text())

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
            reset_5h.append("—", style=CYAN)
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
            reset_7d.append("—", style=CYAN)
        rows.append(reset_7d)

        total = data.get("total_tokens") or data.get("totalTokens")
        if total:
            rows.append(Text())
            tok_row = Text()
            tok_row.append(f"  ◈  ", style=f"{MUTED}")
            tok_row.append(fmt_tokens(total), style=f"bold {WHITE}")
            tok_row.append("  tokens this period", style=MUTED)
            rows.append(tok_row)

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

REFRESH_SECS = 60

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
    parser.add_argument("--refresh", type=int, default=30,
                        help="API refresh interval in seconds (default: 30)")
    parser.add_argument("--token", type=str, default=None,
                        help="Override OAuth token")
    parser.add_argument("--no-resize", action="store_true",
                        help="Don't resize the terminal window")
    parser.add_argument("--data-dir", type=str, action="append", default=None,
                        help="Additional .claude data directory (e.g. synced from HPC). "
                             "Can be specified multiple times.")
    args = parser.parse_args()

    REFRESH_SECS = args.refresh
    if args.token:
        os.environ["CLAUDE_TOKEN"] = args.token

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
    api_data  = None
    last_ok   = None
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

    if args.dash:
        _setup_terminal(dash=True)
        console = Console(highlight=False)

        local_data = parse_project_data(data_dirs)
        history = UsageHistory(max_samples=60)
        next_fetch = 0
        backoff = REFRESH_SECS
        next_local_refresh = time.time() + 300

        with Live(
            make_dashboard(api_data, local_data, last_ok, error_msg, tick, data_dirs_info, history),
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
                        backoff = REFRESH_SECS
                        # Record sample for live chart
                        history.record(api_data)
                    except RateLimited as e:
                        wait = e.retry_after or min(backoff * 2, 300)
                        backoff = wait
                        error_msg = f"rate limited (retry in {wait}s)"
                        next_fetch = now_ts + wait
                    except requests.HTTPError as e:
                        error_msg = f"HTTP {e.response.status_code}"
                        next_fetch = now_ts + REFRESH_SECS
                    except Exception as e:
                        error_msg = str(e)[:36]
                        next_fetch = now_ts + 10

                if now_ts >= next_local_refresh:
                    local_data = parse_project_data(data_dirs)
                    next_local_refresh = now_ts + 300

                live.update(make_dashboard(
                    api_data, local_data, last_ok, error_msg, tick, data_dirs_info, history
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

        next_fetch = 0
        backoff = REFRESH_SECS

        with Live(make_widget(api_data, last_ok, error_msg, tick),
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
                        backoff = REFRESH_SECS
                    except RateLimited as e:
                        wait = e.retry_after or min(backoff * 2, 300)
                        backoff = wait
                        error_msg = f"rate limited (retry in {wait}s)"
                        next_fetch = now_ts + wait
                    except requests.HTTPError as e:
                        error_msg = f"HTTP {e.response.status_code}"
                        next_fetch = now_ts + REFRESH_SECS
                    except Exception as e:
                        error_msg = str(e)[:36]
                        next_fetch = now_ts + 10

                live.update(make_widget(api_data, last_ok, error_msg, tick))
                tick += 1
                time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _cleanup()
        print()  # clean exit
