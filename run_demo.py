#!/usr/bin/env python3
"""Backward-compatible source-tree launcher for SHARD."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from shard.api.compat import compatibility_server_specs  # noqa: E402,F401
from shard.cli import (  # noqa: E402,F401
    ApplicationHTTPRequestHandler,
    NoCacheHTTPRequestHandler,
    main,
    parse_args,
)


if __name__ == "__main__":
    main()
