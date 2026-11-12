"""Bounded HTTP bridge. Identity and business policy stay in FastAPI."""

import asyncio
from typing import Any

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from .config import MCPSettings


class SupportGPTClient:
    def __init__(
        self, settings: MCPSettings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.api_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {settings.api_token}"},
            timeout=httpx.Timeout(settings.timeout_seconds, connect=5.0),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        # A local client must not fan out many full Agent runs on a small machine.
        self._request_slot = asyncio.Semaphore(1)

    async def close(self) -> None:
        await self._client.aclose()

    async def request(
        self, method: str, path: str, *, payload: dict[str, Any] | None = None
    ) -> Any:
        try:
            async with self._request_slot:
                response = await self._client.request(
                    method, path.lstrip("/"), json=payload
                )
        except httpx.TimeoutException:
            raise ToolError(
                "SupportGPT request timed out. The backend may still be processing it; "
                "check tickets/approval status before retrying. No automatic retry was made."
            ) from None
        except httpx.RequestError:
            raise ToolError(
                "SupportGPT connection failed. Check the backend and API URL. "
                "Before repeating a write, check whether it was already processed."
            ) from None

        messages = {
            401: "Staff token is invalid or expired; refresh SUPPORTGPT_API_TOKEN and restart MCP.",
            403: "The staff account does not have permission for this operation.",
            404: "The requested record or API endpoint was not found.",
            409: "The request conflicts with the current session or workflow state.",
            422: "The backend rejected the request parameters.",
            429: "The backend rate limit was reached; wait before submitting again.",
        }
        if not response.is_success:
            message = messages.get(
                response.status_code,
                "Backend request failed; check backend logs and record status before retrying.",
            )
            # Never forward arbitrary error bodies, tokens, headers or exception URLs.
            raise ToolError(f"SupportGPT HTTP {response.status_code}: {message}")
        try:
            return response.json()
        except ValueError:
            raise ToolError(
                "SupportGPT returned invalid JSON; check record status before retrying."
            ) from None
