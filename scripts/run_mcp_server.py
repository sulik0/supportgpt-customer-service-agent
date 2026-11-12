"""Absolute-path entry point for stdio MCP hosts with an arbitrary working directory."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.mcp_server.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
