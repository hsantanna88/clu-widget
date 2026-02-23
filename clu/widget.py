"""
clu — tiny Claude usage widget for your terminal split-pane.
Reads your Claude Code OAuth token (no extra setup needed) and shows
live 5-hour + 7-day usage against Anthropic's servers.
"""

import sys
import os
import json
import time
import argparse
import subprocess
from pathlib import Path
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    print("Missing dependency: pip install requests")
    sys.exit(1)

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich import box

from clu.common import (
    AMBER, AMBER_L, VIOLET, CYAN, MUTED, DIM, WHITE, GREEN, ORANGE, RED,
    WIDGET_COLS, WIDGET_ROWS,
    get_creature_lines, _cleanup, _setup_terminal,
    fmt_pct, fmt_time_until, fmt_tokens, bar, time_bar,
)


# ── Token resolution ──────────────────────────────────────────────────────────

def get_token():
    """Try every known location Claude Code stores its OAuth token."""

    # 1. Env override
    if os.environ.get("CLAUDE_TOKEN"):
        return os.environ["CLAUDE_TOKEN"].strip()

    # 2. macOS Keychain via `security` CLI
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

        # keychain via keyring package (optional)
        try:
            import keyring
            for svc in services:
                t = keyring.get_password(svc, "default") or \
                    keyring.get_password(svc, "oauth_token") or \
                    keyring.get_password(svc, "claude")
                if t: return t.strip()
        except Exception:
            pass

    # 3. Credential JSON files
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
    resp.raise_for_status()
    return resp.json()


# ── Widget rendering ──────────────────────────────────────────────────────────

def make_widget(data, last_ok, error_msg=None, tick=0, refresh_secs=30):
    """Build the full renderable widget."""

    now_str = datetime.now().strftime("%H:%M:%S")
    dot_char = "●" if not error_msg else "✕"
    dot_color = GREEN if not error_msg else RED
    spinning = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"][tick % 10]

    creature_lines = get_creature_lines(tick)

    rows = []

    for line in creature_lines:
        rows.append(Text.from_markup(line) if line else Text())

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
        if isinstance(fh_reset, str):
            rows.append(time_bar(fh_reset, 5 * 3600))

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
        if isinstance(sd_reset, str):
            rows.append(time_bar(sd_reset, 7 * 86400))

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


# ── Main loop ─────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(description="Tiny Claude usage widget")
    parser.add_argument("--refresh", type=int, default=30, help="Refresh interval in seconds (default: 30)")
    parser.add_argument("--token",   type=str, default=None, help="Override OAuth token")
    parser.add_argument("--no-resize", action="store_true", help="Don't resize the terminal window")
    args = parser.parse_args(argv)

    refresh_secs = args.refresh
    if args.token:
        os.environ["CLAUDE_TOKEN"] = args.token

    console = Console(width=WIDGET_COLS, highlight=False)

    token     = get_token()
    data      = None
    last_ok   = None
    error_msg = None
    tick      = 0

    if not token:
        console.print(Panel(
            Text.from_markup(
                f"\n  [bold {AMBER}]◆[/]  [bold {WHITE}]claude·usage[/]\n\n"
                f"  [bold {RED}]Token not found.[/]\n\n"
                f"  Make sure Claude Code is installed and\n"
                f"  you've run [bold {CYAN}]claude[/] at least once to login.\n\n"
                f"  Or pass it directly:\n"
                f"  [bold {CYAN}]CLAUDE_TOKEN=sk-ant-… clu[/]\n"
            ),
            border_style=DIM,
            box=box.SIMPLE,
        ))
        sys.exit(1)

    _setup_terminal(resize=not args.no_resize)

    next_fetch = 0

    with Live(make_widget(data, last_ok, error_msg, tick, refresh_secs),
              console=console,
              refresh_per_second=2,
              transient=False) as live:
        while True:
            now_ts = time.time()

            if now_ts >= next_fetch:
                try:
                    data      = fetch_usage(token)
                    error_msg = None
                    last_ok   = datetime.now().strftime("%H:%M:%S")
                    next_fetch = now_ts + refresh_secs
                except requests.HTTPError as e:
                    error_msg = f"HTTP {e.response.status_code}"
                    next_fetch = now_ts + refresh_secs
                except Exception as e:
                    error_msg = str(e)[:36]
                    next_fetch = now_ts + 10
            live.update(make_widget(data, last_ok, error_msg, tick, refresh_secs))
            tick += 1
            time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _cleanup()
        print()
