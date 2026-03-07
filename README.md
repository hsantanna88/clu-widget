# clu - Claude Usage Monitor

A terminal tool that monitors your Claude Code usage — from a cute animated widget to a full per-project dashboard.

[![PyPI version](https://img.shields.io/pypi/v/clu-widget)](https://pypi.org/project/clu-widget/)
![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue)

![clu dashboard](screenshot.png)

## What it does

**Widget mode** (default) — the original cute clu:
- 5-hour and 7-day sliding window usage with progress bars
- Reset countdowns for each window
- Token counts for the current period
- A little animated creature to keep you company

**Dashboard mode** (`--dash`) — full terminal dashboard:
- Everything from widget mode, plus:
- Per-project token breakdown with smart project names (parsed from local Claude Code data)
- Real-time utilization chart (live 5h usage over time)
- Session history with duration, message count, and model used
- Cache hit rate and efficiency metrics
- Daily token sparkline (14-day trend)
- Model usage breakdown
- External/untracked usage estimation (percentage of rate limit)
- Multi-source support (local + HPC + any synced `.claude` directory)

## Installation

```bash
# Install from PyPI
pip install clu-widget

# Or install from source
git clone https://github.com/hsantanna/clu.git
cd clu
pip install .
```

## Usage

```bash
# Widget mode — cute animated companion
clu

# Dashboard mode — full terminal dashboard
clu --dash

# Include data from a remote machine (e.g. HPC)
clu --dash --data-dir ~/hpc-sync/.claude

# Multiple remote sources
clu --dash --data-dir ~/hpc-sync/.claude --data-dir ~/server-sync/.claude

# Custom refresh interval
clu --refresh 60

# Don't resize the terminal window
clu --no-resize

# Pass a token directly
clu --token sk-ant-...
```

### Options

| Flag | Description |
|------|-------------|
| `--dash` | Full-terminal dashboard with per-project stats |
| `--data-dir PATH` | Additional `.claude` data directory (repeatable) |
| `--refresh N` | API refresh interval in seconds (default: 60) |
| `--window N` | Time window for sessions/projects: 5, 15, or 24 hours (default: 5) |
| `--no-resize` | Don't resize the terminal window |
| `--token TOKEN` | Override OAuth token |

## Using with HPC / Remote Machines

The dashboard reads local Claude Code conversation data from `~/.claude/projects/`. If you run Claude Code on an HPC or remote server, you can sync that data to see it locally:

```bash
# Sync from HPC (run periodically or via cron)
rsync -az hpc:~/.claude/ ~/hpc-sync/.claude/

# Then view everything together
clu --dash --data-dir ~/hpc-sync/.claude
```

The API usage (5h/7d windows) is account-level — it shows all usage regardless of where Claude Code runs. The per-project breakdown comes from local JSONL files, so those need to be synced from remote machines.

## Token Resolution

`clu` automatically finds your Claude Code OAuth token by checking (in order):

1. `CLAUDE_TOKEN` environment variable
2. macOS Keychain (multiple known service names)
3. Credential JSON files (`~/.claude/.credentials.json`, etc.)

If you've used Claude Code at least once, it should just work.

## Requirements

- Python 3.9+
- `rich` - terminal formatting
- `requests` - HTTP client

## Changelog

### v2.2.3

Fix default refresh interval (was 30s, now 60s as documented). Cache last API response to disk so restarts show data immediately and avoid unnecessary 429s.

### v2.2.2

Faster cold-start recovery — first rate-limit retry is now 10s instead of 60-120s.

### v2.2.1

Version display now reads from package metadata instead of being hardcoded.

### v2.2.0

Dashboard UX improvements and smarter project naming.

- **Live retry countdown**: rate limit errors now show a ticking countdown instead of a static message
- **Cached data persists**: usage bars stay visible during API errors instead of being replaced by error text
- **Softer backoff**: max retry capped at 120s (was 300s)
- **Confused mascot**: creature reacts appropriately during errors ("ugh, hold on...", "waiting...")
- **Time window filter**: sessions and projects panels filter to last 5h by default, configurable via `--window` (5, 15, or 24 hours)
- **Smart project names**: filesystem-aware leaf folder detection — `bad-controls` becomes "Bad Controls" instead of "Professor Research Bad Controls"
- **Better layout**: right panels (stats/sessions) get more horizontal space
- **Active indicator**: green arrow only appears on sessions active in the last 5 minutes

### v2.1.1

Clean exit on Ctrl+C — no more traceback or errno 130 when installed via pip/pipx.

### v2.1.0

Mascot redesign and rate limit handling.

- **New pixel-art mascot**: chunky background-colored sprite matching Claude Code's style — no more line-gap rendering artifacts
- **Animated eyes**: mascot blinks with `^ ^` eyes periodically and during bounce animations
- **Antenna**: cute violet `*|` antenna on top of the mascot
- **429 rate limit handling**: respects `Retry-After` header from the API with exponential backoff
- **Default refresh interval**: increased from 30s to 60s to reduce API rate limiting

### v2.0.0

Full dashboard mode with per-project analytics.

- **Dashboard mode** (`--dash`): full-terminal layout with hero panel, stats, projects, sessions, and live chart
- **Smart project names**: directory paths are parsed into human-readable names with title casing and acronym detection (e.g. `sis-employment` becomes `SIS Employment`)
- **Per-project breakdown**: ranked list of projects by token usage with proportional bars, session counts, and last-active timestamps
- **Session history**: recent sessions with message count, token usage, model, duration, and time ago
- **Live utilization chart**: real-time ASCII bar chart tracking 5h utilization over time with color-coded thresholds (green/amber/orange/red)
- **External usage estimation**: detects untracked usage from other devices as a percentage of rate limit capacity
- **Daily sparkline**: 14-day token usage trend with directional indicator
- **Cache hit rate**: shows cache efficiency as a percentage of total token volume
- **Model breakdown**: per-model token usage stats
- **Multi-source data**: `--data-dir` flag to include synced `.claude` directories from remote machines (HPC, servers)
- **Stats panel**: cost estimate, daily averages, cache hit rate, model split, and sparkline in a dedicated panel

### v1.0.0

Initial release — cute terminal widget.

- 5h and 7d utilization bars with reset countdowns
- Animated ASCII creature with mood based on usage level
- Automatic OAuth token discovery (Keychain, credential files, env var)
- Auto-refresh with configurable interval

## License

MIT
