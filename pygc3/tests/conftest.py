"""Shared test fixtures.

The JSON fixtures under ``fixtures/`` are real captures from a live GC3 panel
(the pairing fixture has its secret code redacted to 000000).

HTTP is faked through the client's own ``session=`` injection seam rather than a
transport mocker: ``FakeSession`` stands in for an aiohttp ``ClientSession`` and
``FakeResponse`` for the async-context-manager response. This keeps the tests
independent of any third-party mocker tracking aiohttp's internals, and doubles
as a check that session injection (the Platinum ``inject-websession`` rule) works.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    """Minimal stand-in for aiohttp's response context manager."""

    def __init__(
        self,
        *,
        status: int = 200,
        json_data: Any = None,
        text_data: str = "",
        json_error: Exception | None = None,
        enter_error: Exception | None = None,
    ) -> None:
        self.status = status
        self._json = json_data
        self._text = text_data
        self._json_error = json_error
        self._enter_error = enter_error

    async def __aenter__(self) -> FakeResponse:
        if self._enter_error is not None:
            raise self._enter_error
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def json(self, content_type: str | None = None) -> Any:
        if self._json_error is not None:
            raise self._json_error
        return self._json

    async def text(self) -> str:
        return self._text


class FakeSession:
    """Records requests and returns a preconfigured ``FakeResponse``."""

    def __init__(self, response: FakeResponse | None = None) -> None:
        self._response = response or FakeResponse(
            json_data={"armState": "ready", "isArmedStay": False}
        )
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        return self._response

    async def close(self) -> None:
        self.closed = True


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def status_json() -> dict:
    return _load("status.json")


@pytest.fixture
def zones_json() -> list:
    return _load("zones.json")


@pytest.fixture
def pair_json() -> dict:
    return _load("pair.json")
