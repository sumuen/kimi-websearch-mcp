import json

import httpx2
import pytest
from mcp import Client
from mcp.types import CallToolResult, TextContent

from kimi_websearch_mcp.client import KIMI_FETCH_URL, KIMI_SEARCH_URL
from kimi_websearch_mcp.server import create_server


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def kimi_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIMI_API_KEY", "sk-kimi-token")


def _text(result: CallToolResult) -> str:
    content = result.content
    assert len(content) == 1
    block = content[0]
    assert isinstance(block, TextContent)
    return block.text


@pytest.mark.anyio
async def test_tools_expose_provider_neutral_structured_contracts() -> None:
    server = create_server(transport=httpx2.MockTransport(lambda _: httpx2.Response(500)))

    async with Client(server) as client:
        tools = (await client.list_tools()).tools

    assert [tool.name for tool in tools] == ["WebSearch", "WebFetch"]
    search, fetch = tools
    assert set(search.input_schema["properties"]) == {"query", "count"}
    assert search.input_schema["required"] == ["query"]
    assert search.output_schema is not None
    assert search.annotations is not None
    assert search.annotations.read_only_hint is True
    assert search.annotations.open_world_hint is True
    assert set(fetch.input_schema["properties"]) == {"url", "max_length", "start_index"}
    assert fetch.input_schema["required"] == ["url"]
    assert fetch.output_schema is not None


@pytest.mark.anyio
async def test_web_search_adapts_kimi_response_to_structured_results() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == KIMI_SEARCH_URL
        assert request.headers["authorization"] == "Bearer sk-kimi-token"
        assert json.loads(request.content) == {"text_query": "current MCP specification"}
        return httpx2.Response(
            200,
            json={
                "search_results": [
                    {
                        "title": "MCP specification",
                        "url": "https://example.com/spec",
                        "snippet": "The current protocol specification.",
                        "site_name": "Example",
                        "date": "2026-07-28",
                    },
                    {
                        "title": "SDK guide",
                        "url": "https://example.com/sdk",
                        "snippet": "A practical SDK guide.",
                    },
                ]
            },
        )

    server = create_server(transport=httpx2.MockTransport(handler))
    async with Client(server) as client:
        result = await client.call_tool(
            "WebSearch",
            {"query": "  current MCP specification  ", "count": 1},
        )

    assert result.is_error is False
    assert result.structured_content == {
        "query": "current MCP specification",
        "results": [
            {
                "title": "MCP specification",
                "url": "https://example.com/spec",
                "snippet": "The current protocol specification.",
                "site_name": "Example",
                "published_at": "2026-07-28",
            }
        ],
        "result_count": 1,
        "total_available": 2,
    }


@pytest.mark.anyio
async def test_web_fetch_supports_character_pagination() -> None:
    page = "0123456789abcdefghij"

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == KIMI_FETCH_URL
        assert request.headers["authorization"] == "Bearer sk-kimi-token"
        assert request.headers["accept"] == "text/markdown"
        assert json.loads(request.content) == {"url": "https://example.com/article"}
        return httpx2.Response(200, text=page, headers={"content-type": "text/markdown"})

    server = create_server(transport=httpx2.MockTransport(handler))
    async with Client(server) as client:
        first = await client.call_tool(
            "WebFetch",
            {"url": "https://example.com/article", "max_length": 8},
        )
        second = await client.call_tool(
            "WebFetch",
            {
                "url": "https://example.com/article",
                "max_length": 8,
                "start_index": 8,
            },
        )

    assert first.is_error is False
    assert first.structured_content == {
        "url": "https://example.com/article",
        "content": "01234567",
        "content_type": "text/markdown",
        "start_index": 0,
        "returned_characters": 8,
        "total_characters": 20,
        "truncated": True,
        "next_start_index": 8,
    }
    assert second.structured_content is not None
    assert second.structured_content["content"] == "89abcdef"
    assert second.structured_content["next_start_index"] == 16


@pytest.mark.anyio
async def test_invalid_inputs_fail_without_calling_upstream() -> None:
    calls = 0

    def handler(_: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(200)

    server = create_server(transport=httpx2.MockTransport(handler))
    async with Client(server) as client:
        blank_query = await client.call_tool("WebSearch", {"query": "   "})
        private_url = await client.call_tool("WebFetch", {"url": "http://127.0.0.1/private"})

    assert blank_query.is_error is True
    assert "non-whitespace" in _text(blank_query)
    assert private_url.is_error is True
    assert "private" in _text(private_url)
    assert calls == 0


@pytest.mark.anyio
async def test_upstream_authentication_failure_is_an_actionable_tool_error() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, text="invalid credential")

    server = create_server(transport=httpx2.MockTransport(handler))
    async with Client(server) as client:
        result = await client.call_tool("WebSearch", {"query": "example"})

    assert result.is_error is True
    message = _text(result)
    assert "authentication failed" in message
    assert "KIMI_API_KEY" in message
