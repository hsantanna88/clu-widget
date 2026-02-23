"""Allow running as `python -m clu` with subcommand routing."""

import sys

from clu.common import _cleanup


def cli():
    # Quick check: if first positional arg is "sessions", route there
    if len(sys.argv) > 1 and sys.argv[1] == "sessions":
        from clu.sessions_widget import main as sessions_main
        # Remove "sessions" from argv so argparse in sessions_widget sees clean args
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        sessions_main()
    else:
        from clu.widget import main as usage_main
        usage_main()


if __name__ == "__main__":
    try:
        cli()
    except KeyboardInterrupt:
        _cleanup()
        print()
