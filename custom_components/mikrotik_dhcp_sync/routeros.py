"""Synchronous RouterOS API client for MikroTik DHCP leases."""

from __future__ import annotations

from typing import Any
from threading import RLock


class RouterOSClient:
    """Small synchronous wrapper around librouteros."""

    def __init__(self, host: str, username: str, password: str, port: int) -> None:
        """Initialize the RouterOS client."""
        self._host = host
        self._username = username
        self._password = password
        self._port = port
        self._api: Any | None = None
        # Executor jobs can outlive cancellation of the coroutine awaiting them.
        self._lock = RLock()

    def connect(self) -> None:
        """Connect to RouterOS if not already connected."""
        with self._lock:
            self._connect()

    def _connect(self) -> None:
        """Connect while holding the client lock."""
        if self._api is not None:
            return
        import librouteros

        self._api = librouteros.connect(
            host=self._host,
            username=self._username,
            password=self._password,
            port=self._port,
        )

    def close(self) -> None:
        """Close the RouterOS API connection."""
        with self._lock:
            api = self._api
            self._api = None
            if api is None:
                return
            close = getattr(api, "close", None)
            if close is not None:
                close()

    def fetch_dhcp_leases(self) -> list[dict[str, Any]]:
        """Fetch all DHCP leases using /ip/dhcp-server/lease/getall."""
        with self._lock:
            self._connect()
            assert self._api is not None
            try:
                return [dict(lease) for lease in self._api("/ip/dhcp-server/lease/getall")]
            except Exception:
                self.close()
                raise


def looks_like_auth_error(err: Exception) -> bool:
    """Best-effort classification for RouterOS authentication failures."""
    message = str(err).lower()
    return "auth" in message or "login" in message or "invalid user" in message
