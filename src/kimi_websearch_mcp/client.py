from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx2
from dotenv import load_dotenv

load_dotenv()

KIMI_SEARCH_URL = "https://api.kimi.com/coding/v1/search"
KIMI_FETCH_URL = "https://api.kimi.com/coding/v1/fetch"


class KimiWebError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    site_name: str | None = None
    published_at: str | None = None


class KimiWebClient:
    def __init__(self, http_client: httpx2.AsyncClient) -> None:
        self._http_client = http_client
        self._api_key = os.getenv("KIMI_API_KEY")
        if not self._api_key:
            raise KimiWebError("KIMI_API_KEY is not set. Add it to the .env file.")

    async def search(self, query: str) -> list[SearchResult]:
        response = await self._post(
            KIMI_SEARCH_URL,
            operation="Web search",
            json={"text_query": query},
        )
        try:
            payload: Any = response.json()
        except ValueError as error:
            raise KimiWebError("Web search returned invalid JSON.") from error

        if not isinstance(payload, dict):
            raise KimiWebError("Web search returned an unexpected response shape.")

        raw_results = payload.get("search_results", [])
        if not isinstance(raw_results, list):
            raise KimiWebError("Web search returned an invalid search_results field.")

        results: list[SearchResult] = []
        for raw_result in raw_results:
            if not isinstance(raw_result, dict):
                continue
            results.append(
                SearchResult(
                    title=_string_value(raw_result.get("title")),
                    url=_string_value(raw_result.get("url")),
                    snippet=_string_value(raw_result.get("snippet")),
                    site_name=_optional_string(raw_result.get("site_name")),
                    published_at=_optional_string(raw_result.get("date")),
                )
            )
        return results

    async def fetch(self, url: str) -> str:
        response = await self._post(
            KIMI_FETCH_URL,
            operation="Web fetch",
            json={"url": url},
            accept="text/markdown",
        )
        return response.text

    async def _post(
        self,
        url: str,
        *,
        operation: str,
        json: dict[str, str],
        accept: str | None = None,
    ) -> httpx2.Response:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if accept is not None:
            headers["Accept"] = accept

        try:
            response = await self._http_client.post(url, headers=headers, json=json)
        except httpx2.TimeoutException as error:
            message = f"{operation} timed out while contacting the upstream service."
            raise KimiWebError(message) from error
        except httpx2.RequestError as error:
            message = f"{operation} could not reach the upstream service: {error}"
            raise KimiWebError(message) from error

        if response.status_code != 200:
            raise KimiWebError(_status_error(operation, response))
        return response


def _string_value(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _status_error(operation: str, response: httpx2.Response) -> str:
    status = response.status_code
    detail = " ".join(response.text.split())[:500]
    suffix = f" Details: {detail}" if detail else ""

    if status in {401, 403}:
        return (
            f"{operation} authentication failed (HTTP {status}). "
            f"Check KIMI_API_KEY in the .env file.{suffix}"
        )
    if status == 429:
        return (
            f"{operation} was rate limited by the upstream service (HTTP 429). Retry later.{suffix}"
        )
    if status >= 500:
        return f"{operation} upstream service failed (HTTP {status}). Retry later.{suffix}"
    return f"{operation} request was rejected by the upstream service (HTTP {status}).{suffix}"
