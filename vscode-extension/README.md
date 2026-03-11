# clu — Claude Usage Monitor

Real-time Claude Code token usage in your VS Code status bar, with a full dashboard panel.

## Features

**Status bar** — always visible, zero friction:
- Live 5h utilization bar and percentage
- 7-day window percentage
- Color-coded severity (green → amber → orange → red)
- Click to open the dashboard
- Countdown timer during rate limits

**Dashboard panel** — click the status bar item to open:
- 5h and 7d usage gauges with reset countdowns
- Today's token burn rate (tokens/hour)
- Total tokens, projects, sessions, last-5h tokens
- Cache hit rate with efficiency label
- 14-day daily token sparkline
- Top models breakdown
- Animated creature companion

## Setup

clu works automatically if you've used Claude Code — it reads your auth token from the same place Claude Code stores it (macOS Keychain or credential files).

For usage data, clu uses the claude.ai web API via your session cookie:

**Auto-detect (macOS):** clu tries to read the session cookie from Claude Desktop automatically. You'll get a one-time Keychain approval prompt.

**Manual setup:**
1. Go to [claude.ai](https://claude.ai) in Chrome
2. Open DevTools → Application → Cookies → `https://claude.ai`
3. Copy the `sessionKey` value
4. Set it in VS Code settings: `clu.sessionKey`

The session key is cached in `~/.claude/.clu_session_key` so you only need to do this once.

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `clu.sessionKey` | _(auto)_ | Your `sessionKey` cookie from claude.ai. Leave blank to auto-detect. |
| `clu.orgId` | _(auto)_ | Your Anthropic org UUID. Leave blank to auto-fetch. |
| `clu.refreshInterval` | `90` | Refresh interval in seconds (minimum 30). |

## Commands

- **clu: Refresh Usage Now** — force an immediate API refresh
- **clu: Open Dashboard** — open the dashboard panel (also triggered by clicking the status bar)

## Requirements

- VS Code 1.85+
- Claude Code must have been used at least once (for auth token)

## Notes

- Shares the disk cache (`~/.claude/.clu_cache.json`) with the [clu terminal widget](https://github.com/hsantanna88/clu-widget), so both tools stay in sync
- The OAuth usage API may be blocked on consumer plans (Max/Pro) — the claude.ai session key is the recommended auth method

## License

MIT
