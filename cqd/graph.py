import random
import re
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

import requests


GRAPH_ROOT = "https://graph.microsoft.us"
GRAPH_SCOPE = f"{GRAPH_ROOT}/.default"


class GraphError(RuntimeError):
    pass


class GraphHttpError(GraphError):
    def __init__(
        self,
        status: int,
        operation: str,
        error_code: str | None,
        request_id: str | None,
        exhausted: bool = False,
    ):
        self.status = status
        self.operation = operation
        self.error_code = error_code
        self.request_id = request_id
        message = (
            f"Graph HTTP {status} during {operation}; "
            f"code={error_code or 'unavailable'}; request_id={request_id or 'unavailable'}"
        )
        if exhausted:
            message += "; retries exhausted"
        super().__init__(message)


class GraphClient:
    def __init__(
        self,
        token_provider: Callable[[], str],
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 5,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.token_provider = token_provider
        self.session = session or requests.Session()
        self.sleep = sleep
        self.max_attempts = max_attempts

    def get(self, url: str, responses: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "graph.microsoft.us"
            or not (
                parsed.path == "/v1.0/communications/callRecords"
                or parsed.path.startswith("/v1.0/communications/callRecords/")
            )
            or parsed.fragment
        ):
            raise GraphError("Graph URL is outside the approved GCC High call-record endpoint")
        for attempt in range(self.max_attempts):
            try:
                response = self.session.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.token_provider()}",
                        "Prefer": "include-unknown-enum-members",
                    },
                    timeout=(10, 90),
                    allow_redirects=False,
                )
            except requests.RequestException:
                if attempt + 1 == self.max_attempts:
                    raise GraphError("Graph transport retries exhausted") from None
                self.sleep(2**attempt + random.random())
                continue
            if response.status_code in (429, 500, 502, 503, 504):
                delay = self._retry_delay(response.headers.get("Retry-After"), attempt)
                self.sleep(delay)
                if attempt + 1 == self.max_attempts:
                    raise self._http_error(response, url, exhausted=True)
                continue
            if response.status_code != 200:
                raise self._http_error(response, url)
            try:
                payload = response.json()
            except ValueError:
                raise GraphError("Graph response is not valid JSON") from None
            if not isinstance(payload, dict):
                raise GraphError("Graph response must be a JSON object")
            if responses is not None:
                responses.append(
                    {
                        "url": url,
                        "request_id": response.headers.get("request-id"),
                        "received_at": datetime.now(timezone.utc).isoformat(),
                        "body": response.text,
                    }
                )
            return payload
        raise GraphError("Graph retries exhausted")

    @staticmethod
    def _http_error(response: requests.Response, url: str, exhausted: bool = False) -> GraphHttpError:
        parsed = urlsplit(url)
        relationships = parsed.path.split("/")[4:]
        if not relationships:
            operation = "list call records"
        elif "participants_v2" in relationships:
            operation = "list participants"
        elif "segments" in relationships:
            operation = "list segments"
        elif "sessions" in relationships:
            operation = "list sessions"
        elif "$expand" in parse_qs(parsed.query):
            operation = "get expanded call record"
        else:
            operation = "get call-record details"
        error_code = None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            code = payload["error"].get("code")
            if isinstance(code, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", code):
                error_code = code
        request_id = response.headers.get("request-id")
        if isinstance(request_id, str):
            try:
                request_id = str(UUID(request_id))
            except ValueError:
                request_id = None
        else:
            request_id = None
        return GraphHttpError(response.status_code, operation, error_code, request_id, exhausted)

    @staticmethod
    def _retry_delay(header: str | None, attempt: int) -> float:
        if header is None:
            return 2**attempt + random.random()
        try:
            delay = float(header)
        except ValueError:
            try:
                delay = max(0, (
                    parsedate_to_datetime(header) - datetime.now(timezone.utc)
                ).total_seconds())
            except (ValueError, TypeError, OverflowError):
                raise GraphError("Graph returned an invalid Retry-After header") from None
        if not 0 <= delay < float("inf"):
            raise GraphError("Graph returned an invalid Retry-After delay")
        return delay

    def collection(
        self, url: str, responses: list[dict[str, Any]] | None = None
    ) -> Iterator[dict[str, Any]]:
        seen: set[str] = set()
        while url:
            if url in seen:
                raise GraphError("Graph pagination contains a cycle")
            seen.add(url)
            payload = self.get(url, responses)
            values = payload.get("value")
            if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
                raise GraphError("Graph collection is missing an object array")
            yield from values
            url = payload.get("@odata.nextLink", "")
            if not isinstance(url, str):
                raise GraphError("Graph nextLink must be a string")

    @staticmethod
    def record_list_url(start: datetime, end: datetime, utc_z: bool = False) -> str:
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError("Record window must contain ordered, timezone-aware timestamps")
        start_text = start.astimezone(timezone.utc).isoformat()
        end_text = end.astimezone(timezone.utc).isoformat()
        if utc_z:
            start_text = start_text.replace("+00:00", "Z")
            end_text = end_text.replace("+00:00", "Z")
        query = urlencode(
            {
                "$filter": (
                    f"startDateTime ge {start_text} and startDateTime lt {end_text}"
                )
            }
        )
        return f"{GRAPH_ROOT}/v1.0/communications/callRecords?{query}"

    def list_records(self, start: datetime, end: datetime) -> Iterator[dict[str, Any]]:
        return self.collection(self.record_list_url(start, end))

    def snapshot(self, record_id: str) -> dict[str, Any]:
        record_id = str(UUID(record_id))
        responses: list[dict[str, Any]] = []
        base = f"{GRAPH_ROOT}/v1.0/communications/callRecords/{record_id}"
        record = self.get(f"{base}?$expand=sessions($expand=segments)", responses)
        if not isinstance(record.get("sessions"), list):
            raise GraphError("Expanded call record is missing sessions")
        self._expand_pages(record, responses)
        record["participants_v2"] = list(self.collection(f"{base}/participants_v2", responses))
        if record.get("id") != record_id:
            raise GraphError("Graph returned a different call-record ID")
        version = record.get("version")
        if type(version) is not int or version < 1:
            raise GraphError("Graph call record has an invalid version")
        # Expanded relationships are separate requests; reject a changing parent version.
        final = self.get(base, responses)
        if final.get("id") != record_id or final.get("version") != version:
            raise GraphError("Call-record version changed during retrieval; retry the snapshot")
        return {"record": record, "responses": responses}

    def _expand_pages(self, value: Any, responses: list[dict[str, Any]]) -> None:
        if isinstance(value, list):
            for item in value:
                self._expand_pages(item, responses)
        elif isinstance(value, dict):
            for key in list(value):
                if not key.endswith("@odata.nextLink"):
                    continue
                relationship = key.removesuffix("@odata.nextLink")
                items = value.get(relationship)
                if not isinstance(items, list):
                    raise GraphError("Paged relationship is missing its array")
                next_link = value.pop(key)
                if not isinstance(next_link, str):
                    raise GraphError("Graph nextLink must be a string")
                items.extend(self.collection(next_link, responses))
            for child in value.values():
                self._expand_pages(child, responses)
