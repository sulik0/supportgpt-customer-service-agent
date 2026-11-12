"""Configuration belongs to the local MCP host, never to tool arguments."""

import math
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class MCPSettings:
    api_url: str = "http://127.0.0.1:8000"
    api_token: str = field(default="", repr=False)
    timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        try:
            url = urlsplit(self.api_url)
            port = url.port
        except ValueError:
            raise ValueError("SUPPORTGPT_API_URL must be a valid HTTP(S) URL") from None
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or (port is not None and port == 0)
        ):
            raise ValueError(
                "SUPPORTGPT_API_URL must have no credentials, query or fragment"
            )
        if url.scheme == "http" and url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Remote SUPPORTGPT_API_URL requires HTTPS")
        if (
            not self.api_token.strip()
            or not self.api_token.isascii()
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in self.api_token)
        ):
            raise ValueError(
                "SUPPORTGPT_API_TOKEN must contain a staff JWT without whitespace"
            )
        if not math.isfinite(self.timeout_seconds) or not 1 <= self.timeout_seconds <= 300:
            raise ValueError("SUPPORTGPT_MCP_TIMEOUT_SECONDS must be between 1 and 300")

    @classmethod
    def from_env(cls) -> "MCPSettings":
        try:
            timeout = float(os.getenv("SUPPORTGPT_MCP_TIMEOUT_SECONDS", "120"))
        except ValueError:
            raise ValueError("SUPPORTGPT_MCP_TIMEOUT_SECONDS must be a number") from None
        return cls(
            api_url=os.getenv("SUPPORTGPT_API_URL", "http://127.0.0.1:8000"),
            api_token=os.getenv("SUPPORTGPT_API_TOKEN", ""),
            timeout_seconds=timeout,
        )
