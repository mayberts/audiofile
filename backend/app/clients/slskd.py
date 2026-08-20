from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from ..config import Settings


class SlskdError(RuntimeError):
    pass


class SlskdClient:
    """Thin wrapper around the slskd REST API (https://github.com/slskd/slskd)."""

    def __init__(self, base_url: str, api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"X-API-Key": self.api_key} if self.api_key else {},
            timeout=30.0,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "SlskdClient":
        return cls(settings.slskd_url, settings.slskd_api_key)

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise SlskdError(f"could not reach slskd at {self.base_url}: {exc}") from exc
        if resp.status_code >= 400:
            raise SlskdError(f"slskd {method} {path} -> {resp.status_code}: {resp.text[:300]}")
        return resp

    def search(self, query: str, timeout_ms: int = 15000, poll_interval_s: float = 0.5) -> list[dict]:
        """Start a search and block (with polling) until slskd reports it complete.

        slskd's searchTimeout field is seconds, not milliseconds — it's an
        idle timeout that resets on every new response, with a server-side
        minimum of 5. Sending it milliseconds (e.g. 15000) told slskd to
        wait ~4 hours of silence before calling the search done, since the
        [Range(5, int.MaxValue)] validator happily accepts a number that
        large without complaint. Converting to seconds here is what actually
        lines slskd's own notion of "done" up with how long we're willing to
        poll for.
        """
        search_timeout_s = max(5, round(timeout_ms / 1000))
        resp = self._request(
            "POST", "/api/v0/searches", json={"searchText": query, "searchTimeout": search_timeout_s}
        )
        search = resp.json()
        search_id = search["id"]

        deadline = time.monotonic() + search_timeout_s + 10.0
        while time.monotonic() < deadline:
            status_resp = self._request("GET", f"/api/v0/searches/{search_id}")
            status = status_resp.json()
            if status.get("isComplete"):
                break
            time.sleep(poll_interval_s)

        responses_resp = self._request("GET", f"/api/v0/searches/{search_id}/responses")
        return responses_resp.json()

    def enqueue_download(self, username: str, files: list[dict]) -> None:
        """files: list of {filename, size} (as returned by search results)."""
        payload = [{"filename": f["filename"], "size": f["size"]} for f in files]
        self._request("POST", f"/api/v0/transfers/downloads/{username}", json=payload)

    def get_all_downloads(self) -> list[dict]:
        resp = self._request("GET", "/api/v0/transfers/downloads")
        return resp.json()

    def cancel_download(self, username: str, transfer_id: str) -> None:
        self._request(
            "DELETE",
            f"/api/v0/transfers/downloads/{username}/{transfer_id}",
            params={"remove": "true"},
        )

    def health(self) -> bool:
        try:
            self._request("GET", "/api/v0/application")
            return True
        except SlskdError:
            return False


def find_transfer(all_downloads: list[dict], username: str, filename: str) -> Optional[dict]:
    """slskd groups downloads by user, each with a list of directories/files."""
    for user_block in all_downloads:
        if user_block.get("username") != username:
            continue
        for directory in user_block.get("directories", []):
            for f in directory.get("files", []):
                if f.get("filename") == filename:
                    return f
    return None
