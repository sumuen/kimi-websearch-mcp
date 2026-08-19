from __future__ import annotations

import argparse
import ipaddress
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx2
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from kimi_websearch_mcp.client import KimiWebClient, SearchResult

DEFAULT_SEARCH_COUNT = 10
MAX_SEARCH_COUNT = 20
DEFAULT_FETCH_LENGTH = 20_000
MAX_FETCH_LENGTH = 200_000


class WebSearchResult(BaseModel):
    title: str = Field(description="Title of the result.")
    url: str = Field(description="Canonical or result URL.")
    snippet: str = Field(description="Short excerpt describing the result.")
    site_name: str | None = Field(default=None, description="Source website name, when available.")
    published_at: str | None = Field(
        default=None,
        description="Publication date or time supplied by the search provider, when available.",
    )


class WebSearchOutput(BaseModel):
    query: str
    results: list[WebSearchResult]
    result_count: int = Field(description="Number of results returned in this response.")
    total_available: int = Field(description="Number of results made available by the provider.")


class WebFetchOutput(BaseModel):
    url: str
    content: str = Field(description="Extracted page content in Markdown.")
    content_type: Literal["text/markdown"] = "text/markdown"
    start_index: int
    returned_characters: int
    total_characters: int
    truncated: bool
    next_start_index: int | None = Field(
        default=None,
        description=(
            "Character index to pass as start_index to continue reading, or null at the end."
        ),
    )


@dataclass(slots=True)
class AppContext:
    web: KimiWebClient


def create_server(
    *,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> MCPServer[AppContext]:
    @asynccontextmanager
    async def lifespan(_: MCPServer[AppContext]) -> AsyncIterator[AppContext]:
        timeout = httpx2.Timeout(30.0, connect=10.0, write=15.0, pool=10.0)
        async with httpx2.AsyncClient(timeout=timeout, transport=transport) as http_client:
            yield AppContext(web=KimiWebClient(http_client))

    server: MCPServer[AppContext] = MCPServer(
        "kimi-websearch-mcp",
        version="0.1.0",
        description="Provider-neutral web search and page fetch tools backed by Kimi.",
        instructions=(
            "Use WebSearch to discover relevant public pages, then WebFetch to read only the pages "
            "needed. Cite source URLs in answers based on these tools."
        ),
        lifespan=lifespan,
    )

    @server.tool(
        name="WebSearch",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
        structured_output=True,
    )
    async def web_search(
        query: Annotated[
            str,
            Field(
                min_length=1,
                max_length=1_000,
                description="Search terms describing the information to find.",
            ),
        ],
        ctx: Context[AppContext],
        count: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_SEARCH_COUNT,
                description="Maximum number of results to return.",
            ),
        ] = DEFAULT_SEARCH_COUNT,
    ) -> WebSearchOutput:
        """Search the public web for current information and return ranked source metadata."""
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must contain at least one non-whitespace character.")

        upstream_results = await ctx.request_context.lifespan_context.web.search(normalized_query)
        results = [_to_web_search_result(result) for result in upstream_results[:count]]
        return WebSearchOutput(
            query=normalized_query,
            results=results,
            result_count=len(results),
            total_available=len(upstream_results),
        )

    @server.tool(
        name="WebFetch",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
        structured_output=True,
    )
    async def web_fetch(
        url: Annotated[
            str,
            Field(description="Fully qualified public HTTP or HTTPS URL to fetch."),
        ],
        ctx: Context[AppContext],
        max_length: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_FETCH_LENGTH,
                description="Maximum number of content characters to return.",
            ),
        ] = DEFAULT_FETCH_LENGTH,
        start_index: Annotated[
            int,
            Field(
                ge=0,
                description="Character index at which to start; use next_start_index to continue.",
            ),
        ] = 0,
    ) -> WebFetchOutput:
        """Fetch a public web page as Markdown, with character-based pagination."""
        normalized_url = _validate_public_url(url)
        content = await ctx.request_context.lifespan_context.web.fetch(normalized_url)
        total_characters = len(content)

        if start_index > total_characters:
            raise ValueError(
                f"start_index {start_index} is beyond the content length {total_characters}."
            )

        end_index = min(start_index + max_length, total_characters)
        chunk = content[start_index:end_index]
        truncated = end_index < total_characters
        return WebFetchOutput(
            url=normalized_url,
            content=chunk,
            start_index=start_index,
            returned_characters=len(chunk),
            total_characters=total_characters,
            truncated=truncated,
            next_start_index=end_index if truncated else None,
        )

    return server


def _to_web_search_result(result: SearchResult) -> WebSearchResult:
    return WebSearchResult(
        title=result.title,
        url=result.url,
        snippet=result.snippet,
        site_name=result.site_name,
        published_at=result.published_at,
    )


def _validate_public_url(value: str) -> str:
    url = value.strip()
    if not url:
        raise ValueError("url must not be empty.")
    if len(url) > 8_192:
        raise ValueError("url is too long.")

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"Invalid URL: {error}") from error

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("url must use the http or https scheme.")
    if parsed.hostname is None:
        raise ValueError("url must include a hostname.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("url must not contain embedded credentials.")
    if port is not None and not 1 <= port <= 65_535:
        raise ValueError("url contains an invalid port.")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("url must not target localhost.")

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError(
            "url must not target a private, loopback, link-local, or reserved address."
        )

    return url


mcp = create_server()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Kimi-backed web tools MCP server.")
    parser.add_argument("--host", default="127.0.0.1", help="Address to bind (default: 127.0.0.1).")
    parser.add_argument("--port", default=8000, type=int, help="Port to bind (default: 8000).")
    args = parser.parse_args()
    with suppress(KeyboardInterrupt):
        mcp.run(
            transport="streamable-http",
            host=args.host,
            port=args.port,
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
        )


if __name__ == "__main__":
    main()
