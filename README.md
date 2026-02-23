# clu-widget

A tiny terminal widget that shows **live Claude Code usage stats** with an animated creature companion.

```
          *
          |
        ┌────┐
        │▪ ▪│
        └┬──┬┘
         │  │

  ◆ claude·usage    ● 14:32:07

   max_plus_5x

  5h  ▓▓▓▓▓▓░░░░░░░░░░░░  32%
       resets in 2h 41m
      ▓▓▓▓▓▓▓▓░░░░░░░░░░  🕐

  7d  ▓▓▓▓▓▓▓▓▓░░░░░░░░░  48%
       resets in 4d 11h
      ▓▓▓▓░░░░░░░░░░░░░░  🕐

  ◈  1.2M  tokens this period

  refreshes every 30s
```

## Features

- Live 5-hour and 7-day usage bars with color-coded thresholds
- Time-elapsed progress bars showing how far into each reset window you are
- **Sessions view** — browse your local Claude Code sessions (`clu sessions`)
- Animated creature that bounces every 60 seconds
- Auto-discovers your Claude Code OAuth token (no setup needed)
- Compact — fits in a narrow terminal split-pane
- Auto-resizes the terminal window to widget dimensions

## Install

### With pipx (recommended)

```bash
pipx install clu-widget
```

### With pip

```bash
pip install clu-widget
```

### From source

```bash
git clone https://github.com/hsantanna88/clu-widget.git
cd clu-widget
pip install .
```

## Usage

```bash
# Default — refreshes every 30 seconds
clu

# Custom refresh interval
clu --refresh 60

# Don't resize the terminal window
clu --no-resize

# Pass token explicitly
clu --token "sk-ant-..."

# Or via python module
python -m clu

# Sessions view — browse local Claude Code sessions
clu sessions

# Sessions with custom refresh and limit
clu sessions --refresh 5 --limit 20
```

## Token Resolution

The widget automatically finds your Claude Code OAuth token by checking (in order):

1. `CLAUDE_TOKEN` environment variable
2. macOS Keychain (`security` CLI) — services: `Claude Code-credentials`, `claude.ai`, etc.
3. macOS Keychain via `keyring` package (optional dependency)
4. Credential JSON files: `~/.claude/.credentials.json`, `~/.config/claude/credentials.json`, etc.

If you've used Claude Code at least once, the token is already there.

## Requirements

- Python 3.9+
- [rich](https://github.com/Textualize/rich)
- [requests](https://docs.python-requests.org/)
- Optional: [keyring](https://github.com/jaraco/keyring) for alternative token resolution

## License

MIT
