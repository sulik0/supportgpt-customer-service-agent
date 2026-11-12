"""Small, offline protocol tests; also runnable without tests/conftest.py.

python -m unittest discover -s tests/mcp -v
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

from src.mcp_server.client import SupportGPTClient
from src.mcp_server.config import MCPSettings
from src.mcp_server.server import create_server


class SettingsTests(unittest.TestCase):
    def test_reject_unsafe_configuration(self):
        for url in (
            "http://example.com", "ftp://localhost", "https://user:secret@example.com",
            "https://example.com?token=secret", "https://example.com/#secret",
            "https://example.com:invalid", "https:///missing-host",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                MCPSettings(api_url=url, api_token="staff-token")
        for token in ("", " ", "a\nb", "a b"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                MCPSettings(api_token=token)
        for timeout in (0, 301, float("nan"), float("inf")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                MCPSettings(api_token="staff-token", timeout_seconds=timeout)

    def test_env_configuration_and_secret_repr(self):
        with patch.dict(os.environ, {
            "SUPPORTGPT_API_URL": "https://support.example.com/api/",
            "SUPPORTGPT_API_TOKEN": "private-staff-token",
            "SUPPORTGPT_MCP_TIMEOUT_SECONDS": "30",
        }):
            settings = MCPSettings.from_env()
        self.assertEqual(settings.timeout_seconds, 30)
        self.assertNotIn("private-staff-token", repr(settings))


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def session(self, handler):
        settings = MCPSettings(api_url="https://backend.example/api", api_token="private-staff-token")
        client = SupportGPTClient(settings, transport=httpx.MockTransport(handler))
        with patch("src.mcp_server.server.SupportGPTClient", return_value=client):
            async with create_connected_server_and_client_session(create_server(settings)) as session:
                yield session
        self.assertTrue(client._client.is_closed)

    async def test_discovery_annotations_resources_and_prompt(self):
        def no_http(request):
            self.fail("Discovery must not call the backend")

        async with self.session(no_http) as session:
            catalog = {tool.name: tool for tool in (await session.list_tools()).tools}
            self.assertEqual(set(catalog), {
                "support_health", "support_chat", "support_customer_context",
                "support_create_ticket", "support_list_tickets", "support_ticket_result",
                "support_pending_approvals", "support_execution_status",
            })
            for name, tool in catalog.items():
                self.assertNotIn("ctx", tool.inputSchema["properties"])
                self.assertNotIn("role", tool.inputSchema["properties"])
                self.assertNotIn("token", tool.inputSchema["properties"])
                self.assertEqual(tool.annotations.readOnlyHint, name not in {"support_chat", "support_create_ticket"})
            schema = catalog["support_chat"].inputSchema
            self.assertEqual(schema["properties"]["message"]["maxLength"], 5000)
            self.assertEqual(set(schema["required"]), {"session_id", "customer_id", "message"})
            resources = await session.list_resources()
            self.assertEqual(str(resources.resources[0].uri), "supportgpt://guide")
            resource = await session.read_resource("supportgpt://guide")
            self.assertIn("人工", resource.contents[0].text)
            self.assertEqual((await session.list_prompts()).prompts[0].name, "support_assistant")
            prompt = await session.get_prompt("support_assistant")
            self.assertIn("approval_required", prompt.messages[0].content.text)

    async def test_chat_preserves_governance_fields_and_staff_identity(self):
        requests = []
        payload = {
            "response": "待审草稿", "approval_required": True, "approval_id": 7,
            "risk_level": "high", "citations": [{"source": "demo"}],
            "tool_calls": [{"tool_name": "orders.get_order_history"}],
            "cost_metadata": {"tokens_input": 10},
        }

        def backend(request):
            requests.append(request)
            self.assertEqual(request.headers["authorization"], "Bearer private-staff-token")
            self.assertEqual(request.url.path, "/api/chat")
            self.assertEqual(json.loads(request.content), {
                "session_id": "session-1", "customer_id": "cust_201",
                "message": "查询退款", "kb_version": "v1",
            })
            return httpx.Response(200, json=payload)

        async with self.session(backend) as session:
            result = await session.call_tool("support_chat", {
                "session_id": "session-1", "customer_id": "cust_201", "message": "查询退款",
            })
            self.assertFalse(result.isError)
            self.assertEqual(result.structuredContent, payload)
        self.assertEqual(len(requests), 1)

    async def test_routes_and_bodies(self):
        cases = [
            ("support_health", {}, "GET", "/health", None, {"status": "ok"}),
            ("support_customer_context", {"customer_id": "cust_201"}, "POST", "/customer-context", {"customer_id": "cust_201"}, {"customer_id": "cust_201"}),
            ("support_create_ticket", {"customer_id": "cust_201", "subject": "物流", "description": "未收到"}, "POST", "/tickets", {"customer_id": "cust_201", "subject": "物流", "description": "未收到", "kb_version": "v1"}, {"id": 5}),
            ("support_list_tickets", {}, "GET", "/tickets", None, []),
            ("support_ticket_result", {"ticket_id": 5}, "GET", "/tickets/5/agent-result", None, {"approval_required": True}),
            ("support_pending_approvals", {}, "GET", "/approvals/pending", None, [{"status": "pending"}]),
            ("support_execution_status", {"execution_id": "exec-123"}, "GET", "/agent-executions/exec-123", None, {"status": "paused"}),
        ]
        for name, args, method, path, body, response in cases:
            with self.subTest(name=name):
                def backend(request):
                    self.assertEqual(request.method, method)
                    self.assertEqual(request.url.path, "/api" + path)
                    self.assertEqual(json.loads(request.content) if request.content else None, body)
                    return httpx.Response(200, json=response)
                async with self.session(backend) as session:
                    result = await session.call_tool(name, args)
                    self.assertFalse(result.isError)
                    expected = {"tickets": response} if name == "support_list_tickets" else {"approvals": response} if name == "support_pending_approvals" else response
                    self.assertEqual(result.structuredContent, expected)

    async def test_invalid_arguments_never_reach_backend(self):
        def no_http(request):
            self.fail("Invalid arguments must fail before HTTP")

        async with self.session(no_http) as session:
            for name, args in (
                ("support_chat", {"session_id": "s", "customer_id": "c", "message": ""}),
                ("support_chat", {"session_id": "s", "customer_id": "c", "message": "x" * 5001}),
                ("support_ticket_result", {"ticket_id": -1}),
                ("support_ticket_result", {"ticket_id": True}),
                ("support_execution_status", {"execution_id": "../tickets?token=1"}),
                ("support_create_ticket", {"customer_id": "c"}),
            ):
                with self.subTest(name=name, keys=list(args)):
                    self.assertTrue((await session.call_tool(name, args)).isError)

    async def test_http_failures_are_errors_redacted_and_not_retried(self):
        for status in (302, 401, 403, 404, 409, 422, 429, 500):
            with self.subTest(status=status):
                requests = []
                def backend(request):
                    requests.append(request)
                    return httpx.Response(status, text="private-staff-token secret-internal-detail", headers={"Location": "https://other.example/"})
                async with self.session(backend) as session:
                    result = await session.call_tool("support_health")
                    self.assertTrue(result.isError)
                    text = result.content[0].text
                    self.assertIn(str(status), text)
                    self.assertNotIn("private-staff-token", text)
                    self.assertNotIn("secret-internal-detail", text)
                self.assertEqual(len(requests), 1)

    async def test_timeout_and_invalid_json_do_not_retry_writes(self):
        for mode in ("timeout", "connect", "json"):
            with self.subTest(mode=mode):
                requests = []
                def backend(request):
                    requests.append(request)
                    if mode == "timeout":
                        raise httpx.ReadTimeout("secret-internal-detail", request=request)
                    if mode == "connect":
                        raise httpx.ConnectError("secret-internal-detail", request=request)
                    return httpx.Response(200, text="secret-internal-detail")
                async with self.session(backend) as session:
                    result = await session.call_tool("support_create_ticket", {
                        "customer_id": "c", "subject": "test", "description": "test",
                    })
                    self.assertTrue(result.isError)
                    self.assertNotIn("secret-internal-detail", result.content[0].text)
                self.assertEqual(len(requests), 1)

    async def test_stdio_handshake_from_arbitrary_directory(self):
        script = Path(__file__).resolve().parents[2] / "scripts/run_mcp_server.py"
        self.assertTrue(script.is_file())
        parameters = StdioServerParameters(
            command=sys.executable, args=[str(script)], cwd=tempfile.gettempdir(),
            env={"SUPPORTGPT_API_TOKEN": "offline-test-token", "SUPPORTGPT_API_URL": "http://127.0.0.1:1"},
        )
        async with asyncio.timeout(15):
            with tempfile.TemporaryFile(mode="w+") as errors:
                async with stdio_client(parameters, errlog=errors) as (read, write):
                    async with ClientSession(read, write) as session:
                        info = await session.initialize()
                        self.assertEqual(info.serverInfo.name, "SupportGPT")
                        self.assertEqual(len((await session.list_tools()).tools), 8)
                        self.assertIn("演示", (await session.read_resource("supportgpt://guide")).contents[0].text)


if __name__ == "__main__":
    unittest.main()
