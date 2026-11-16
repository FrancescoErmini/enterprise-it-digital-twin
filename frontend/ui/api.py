"""Thin client for digital-twin-api. The UI only talks to generic routes."""

from __future__ import annotations

import os
from typing import Any

import requests

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")
TIMEOUT = float(os.environ.get("UI_API_TIMEOUT_SECONDS", "300"))


class ApiError(RuntimeError):
    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


def _handle(resp: requests.Response) -> Any:
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        raise ApiError(resp.status_code, detail)
    return resp.json()


def get(path: str, **params: Any) -> Any:
    return _handle(requests.get(f"{API_BASE_URL}{path}", params=params or None, timeout=TIMEOUT))


def post(path: str, body: dict[str, Any]) -> Any:
    return _handle(requests.post(f"{API_BASE_URL}{path}", json=body, timeout=TIMEOUT))
