"""Launch with python -m src.mcp_server. stdout is reserved for MCP."""

import logging
import sys

from .config import MCPSettings
from .server import create_server


def main() -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = MCPSettings.from_env()
    except ValueError as exc:
        print(f"MCP configuration error: {exc}", file=sys.stderr)
        return 2
    create_server(settings).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
