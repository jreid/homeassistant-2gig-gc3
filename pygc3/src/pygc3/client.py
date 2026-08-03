"""Async client for the 2GIG GC3 local automation API.

Port of the transport in ``gc3_bridge.py`` to aiohttp, with typed models and a
small exception hierarchy. State derivation and MQTT are intentionally *not*
here -- this is the wire layer a Home Assistant integration (or the bridge) sits
on top of.

The panel serves self-signed TLS on :3000 and does not validate clients, so TLS
verification is disabled by default (``verify_ssl=False``). It is a header-auth
API: ``X-Api-Key`` (the issued token) + ``X-Pairing-Key`` (the 6-digit code).
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

import aiohttp

from .exceptions import (
    GC3AuthError,
    GC3ConnectionError,
    GC3ResponseError,
)
from .models import Pairing, PanelStatus, Zone

DEFAULT_PORT = 3000
DEFAULT_TIMEOUT = 8.0
DEFAULT_PARTITION = 1


class GC3Client:
    """Talk to one GC3 panel.

    Args:
        host: panel IP or hostname, no scheme (e.g. ``192.168.1.25``).
        pairing_key: the 6-digit pairing code shown on the panel.
        api_key: the issued API token. Optional -- omit it only for the initial
            :meth:`pair` enrolment, where no token exists yet.
        port: TCP port, default 3000.
        session: an existing aiohttp session to reuse. If given it is *not*
            closed by this client (supports HA's shared session). If omitted the
            client creates and owns one.
        partition: default partition for arm/disarm.
        timeout: per-request total timeout, seconds.
        verify_ssl: leave False for the panel's self-signed cert.
    """

    def __init__(
        self,
        host: str,
        pairing_key: str,
        api_key: str | None = None,
        *,
        port: int = DEFAULT_PORT,
        session: aiohttp.ClientSession | None = None,
        partition: int = DEFAULT_PARTITION,
        timeout: float = DEFAULT_TIMEOUT,
        verify_ssl: bool = False,
    ) -> None:
        self._base = f"https://{host}:{port}"
        self._api_key = api_key
        self._pairing_key = pairing_key
        self._partition = partition
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        # aiohttp: ssl=False disables verification, ssl=True is the default verify.
        self._ssl: bool = bool(verify_ssl)
        self._session = session
        self._owns_session = session is None

    # -- lifecycle ----------------------------------------------------------
    async def __aenter__(self) -> GC3Client:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the session if we created it. No-op for injected sessions."""
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    def _headers(self) -> dict[str, str]:
        headers = {
            "X-Pairing-Key": self._pairing_key,
            "Content-Type": "application/json",
        }
        if self._api_key:
            headers["X-Api-Key"] = self._api_key
        return headers

    # -- core request -------------------------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        session = self._ensure_session()
        try:
            async with session.request(
                method,
                self._base + path,
                json=json_body,
                params=params,
                headers=self._headers(),
                ssl=self._ssl,
                timeout=self._timeout,
            ) as resp:
                if resp.status == 403:
                    # Panel returns {"message":"Invalid API key"} for bad creds.
                    raise GC3AuthError(await _safe_message(resp) or "Invalid API key")
                if resp.status >= 400:
                    raise GC3ResponseError(
                        f"{method} {path} -> HTTP {resp.status}: "
                        f"{await _safe_message(resp)}",
                        status=resp.status,
                    )
                try:
                    return await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError) as err:
                    raise GC3ResponseError(
                        f"{method} {path}: response was not JSON", status=resp.status
                    ) from err
        except (TimeoutError, aiohttp.ClientError) as err:
            raise GC3ConnectionError(f"{method} {path}: {err}") from err

    # -- read ---------------------------------------------------------------
    async def status(self) -> PanelStatus:
        """`GET /api/v1/status`."""
        return PanelStatus.from_json(await self._request("GET", "/api/v1/status"))

    async def zones(self, *, include_console: bool = False) -> list[Zone]:
        """`GET /api/v1/zones`. Console/zone-0 filtered out unless asked for."""
        data = await self._request("GET", "/api/v1/zones")
        if not isinstance(data, list):
            raise GC3ResponseError("zones: expected a JSON array")
        parsed = [Zone.from_json(z) for z in data]
        if include_console:
            return parsed
        return [z for z in parsed if not z.is_console]

    async def get_pairing(self, *, partition: int | None = None) -> Pairing:
        """`GET /api/v1/pair` -- the controller currently enrolled."""
        params = {"partition": partition or self._partition}
        return Pairing.from_json(
            await self._request("GET", "/api/v1/pair", params=params)
        )

    # -- write --------------------------------------------------------------
    async def arm(
        self,
        *,
        stay: bool,
        no_entry_delay: bool = False,
        no_exit_delay: bool = False,
        bypass_not_ready: bool = False,
        silent: bool = False,
        partition: int | None = None,
    ) -> Any:
        """Arm the panel.

        Each argument is one flag in the panel's arm request, named after it.
        ``no_entry_delay`` sets ``noEntryDelay``: the alarm sounds the moment an
        entry zone opens, with no countdown to disarm in.

        There is no night flag on the wire, and :meth:`status` reports only
        ``armState`` and ``isArmedStay``. A consumer offering a night mode owns
        that concept: it chooses which flags night sends and remembers it armed
        that way.
        """
        part = partition or self._partition
        body = {
            "partition": part,
            "armStay": stay,
            "noExitDelay": no_exit_delay,
            "noEntryDelay": no_entry_delay,
            "bypassNotReadyZones": bypass_not_ready,
            "silentEntryExit": silent,
        }
        return await self._request(
            "POST",
            "/api/v1/actions/panel/arm",
            json_body=body,
            params={"partition": part},
        )

    async def disarm(self, pin: str, *, partition: int | None = None) -> Any:
        """Disarm with a user PIN."""
        part = partition or self._partition
        body = {"userPIN": pin, "partition": part}
        return await self._request(
            "POST",
            "/api/v1/actions/panel/disarm",
            json_body=body,
            params={"partition": part},
        )

    async def pair(
        self,
        device_name: str,
        *,
        all_partition_access: bool = True,
        is_security_device: bool = False,
        partition: int | None = None,
    ) -> Any:
        """Enrol this controller: `POST /api/v1/pair`.

        Requires the panel to be in System Pairing mode and this client's
        ``pairing_key`` to match the code it is showing. Returns the panel's raw
        response; the issued API-key field name is not yet pinned down (see
        INTEGRATION_PLAN.md), so the caller should inspect the result. No
        ``api_key`` is required to call this.
        """
        body = {
            "deviceName": device_name,
            "allPartitionAccess": all_partition_access,
            "isSecurityDevice": is_security_device,
            "memberOfPartition": str(partition or self._partition),
        }
        return await self._request("POST", "/api/v1/pair", json_body=body)


async def _safe_message(resp: aiohttp.ClientResponse) -> str:
    """Best-effort human-readable body for error paths; never raises."""
    try:
        data = await resp.json(content_type=None)
    except (aiohttp.ContentTypeError, ValueError, aiohttp.ClientError):
        try:
            return (await resp.text())[:200]
        except Exception:
            return ""
    if isinstance(data, dict):
        return str(data.get("message") or data)
    return str(data)
