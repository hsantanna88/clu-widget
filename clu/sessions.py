"""Session discovery and JSONL parsing for Claude Code local sessions."""

import json
from pathlib import Path
from datetime import datetime, timezone


def _parse_session_file(path: Path) -> dict | None:
    """Parse a single session JSONL file and extract metadata + stats."""
    slug = None
    cwd = None
    session_id = None
    model = None
    first_ts = None
    last_ts = None
    turn_count = 0
    tool_count = 0

    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue

                t = d.get("type")
                ts = d.get("timestamp")

                if ts:
                    if first_ts is None:
                        first_ts = ts
                    last_ts = ts

                if t == "user":
                    turn_count += 1
                    if slug is None:
                        slug = d.get("slug")
                        cwd = d.get("cwd")
                        session_id = d.get("sessionId")

                elif t == "assistant":
                    if model is None:
                        msg = d.get("message", {})
                        model = msg.get("model")

                    # Count tool_use blocks in assistant content
                    msg = d.get("message", {})
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                tool_count += 1
    except (OSError, IOError):
        return None

    if turn_count == 0 or slug is None:
        return None

    # Shorten cwd to last path component
    project = Path(cwd).name if cwd else "unknown"

    # Shorten model name
    short_model = model or "unknown"
    if "opus" in short_model:
        short_model = "opus"
    elif "sonnet" in short_model:
        short_model = "sonnet"
    elif "haiku" in short_model:
        short_model = "haiku"

    # Check if active (last activity < 5 minutes ago)
    is_active = False
    if last_ts:
        try:
            last_dt = datetime.fromisoformat(last_ts)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            is_active = (now - last_dt).total_seconds() < 300
        except Exception:
            pass

    return {
        "session_id": session_id or path.stem,
        "slug": slug,
        "cwd": cwd,
        "project": project,
        "model": short_model,
        "turn_count": turn_count,
        "tool_count": tool_count,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "is_active": is_active,
    }


def scan_sessions(limit=10, sort_by="recent") -> list[dict]:
    """Discover and parse all local Claude Code sessions.

    Args:
        limit: Maximum number of sessions to return.
        sort_by: Sort order — "recent" sorts by last_ts descending.

    Returns:
        List of session dicts sorted by recency.
    """
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        return []

    sessions = []
    for jsonl_path in claude_dir.glob("*/*.jsonl"):
        session = _parse_session_file(jsonl_path)
        if session:
            sessions.append(session)

    # Sort by last timestamp (most recent first)
    sessions.sort(key=lambda s: s.get("last_ts") or "", reverse=True)

    return sessions[:limit]
