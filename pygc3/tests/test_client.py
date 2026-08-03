"""Client tests -- HTTP faked via the session-injection seam (see conftest)."""

from __future__ import annotations

import aiohttp
import pytest
from conftest import FakeResponse, FakeSession

from pygc3 import (
    GC3AuthError,
    GC3Client,
    GC3ConnectionError,
    GC3ResponseError,
)

HOST = "192.168.1.25"


def make_client(session: FakeSession, **kw) -> GC3Client:
    return GC3Client(
        HOST, pairing_key="123456", api_key="test-token", session=session, **kw
    )


async def test_status(status_json):
    client = make_client(FakeSession(FakeResponse(json_data=status_json)))
    st = await client.status()
    assert st.arm_state == "ready"
    assert st.is_armed_stay is False


async def test_status_url_and_method(status_json):
    sess = FakeSession(FakeResponse(json_data=status_json))
    await make_client(sess).status()
    call = sess.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "https://192.168.1.25:3000/api/v1/status"
    # self-signed panel cert -> verification disabled by default
    assert call["kwargs"]["ssl"] is False


async def test_zones_filters_console(zones_json):
    client = make_client(FakeSession(FakeResponse(json_data=zones_json)))
    zones = await client.zones()
    # fixture has 10 entries incl. zone 0 (console); default drops it.
    assert len(zones) == 9
    assert all(not z.is_console for z in zones)
    assert {z.id for z in zones} == {"1", "2", "3", "4", "5", "6", "8", "9", "301"}


async def test_zones_include_console(zones_json):
    client = make_client(FakeSession(FakeResponse(json_data=zones_json)))
    zones = await client.zones(include_console=True)
    assert len(zones) == 10


async def test_zones_non_list_raises():
    client = make_client(FakeSession(FakeResponse(json_data={"oops": True})))
    with pytest.raises(GC3ResponseError):
        await client.zones()


async def test_get_pairing(pair_json):
    client = make_client(FakeSession(FakeResponse(json_data=pair_json)))
    p = await client.get_pairing()
    assert p.device_name == "TEST Controller"


async def test_arm_no_entry_delay_body():
    """Each kwarg sets the wire flag it is named after, and only that one."""
    sess = FakeSession(FakeResponse(json_data={"ok": True}))
    await make_client(sess, partition=2).arm(stay=True, no_entry_delay=True)
    call = sess.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/v1/actions/panel/arm")
    assert call["kwargs"]["json"] == {
        "partition": 2,
        "armStay": True,
        "noExitDelay": False,
        "noEntryDelay": True,
        "bypassNotReadyZones": False,
        "silentEntryExit": False,
    }
    assert call["kwargs"]["params"] == {"partition": 2}


async def test_arm_has_no_night_concept():
    """There is no night flag on the wire, so the client must not invent one."""
    with pytest.raises(TypeError):
        await make_client(FakeSession(FakeResponse(json_data={}))).arm(
            stay=True,
            night=True,  # type: ignore[call-arg]
        )


async def test_arm_away_body():
    sess = FakeSession(FakeResponse(json_data={"ok": True}))
    await make_client(sess).arm(stay=False)
    body = sess.calls[0]["kwargs"]["json"]
    assert body["armStay"] is False
    assert body["noEntryDelay"] is False
    assert body["partition"] == 1  # default


async def test_disarm_body():
    sess = FakeSession(FakeResponse(json_data={"ok": True}))
    await make_client(sess).disarm("1234")
    call = sess.calls[0]
    assert call["url"].endswith("/api/v1/actions/panel/disarm")
    assert call["kwargs"]["json"] == {"userPIN": "1234", "partition": 1}


async def test_pair_needs_no_api_key():
    sess = FakeSession(FakeResponse(json_data={"apiKey": "issued-xyz"}))
    # construct WITHOUT an api key, as during first enrolment
    client = GC3Client(HOST, pairing_key="123456", session=sess)
    result = await client.pair("Home Assistant")
    assert result == {"apiKey": "issued-xyz"}
    headers = sess.calls[0]["kwargs"]["headers"]
    assert headers["X-Pairing-Key"] == "123456"
    assert "X-Api-Key" not in headers  # none available yet
    assert sess.calls[0]["kwargs"]["json"]["deviceName"] == "Home Assistant"


async def test_api_key_header_present_when_set():
    sess = FakeSession(
        FakeResponse(json_data={"armState": "ready", "isArmedStay": False})
    )
    await make_client(sess).status()
    assert sess.calls[0]["kwargs"]["headers"]["X-Api-Key"] == "test-token"


async def test_403_maps_to_auth_error():
    resp = FakeResponse(status=403, json_data={"message": "Invalid API key"})
    client = make_client(FakeSession(resp))
    with pytest.raises(GC3AuthError, match="Invalid API key"):
        await client.status()


async def test_500_maps_to_response_error():
    resp = FakeResponse(status=500, json_data={"message": "boom"})
    client = make_client(FakeSession(resp))
    with pytest.raises(GC3ResponseError) as ei:
        await client.status()
    assert ei.value.status == 500
    assert "boom" in str(ei.value)


async def test_error_with_non_json_body_uses_text():
    # 500 whose body is not JSON -> _safe_message falls back to text()
    resp = FakeResponse(
        status=500, json_error=ValueError("nope"), text_data="Internal Server Error"
    )
    client = make_client(FakeSession(resp))
    with pytest.raises(GC3ResponseError, match="Internal Server Error"):
        await client.status()


async def test_non_json_maps_to_response_error():
    resp = FakeResponse(
        json_data=None, json_error=ValueError("not json"), text_data="<html>nope</html>"
    )
    client = make_client(FakeSession(resp))
    with pytest.raises(GC3ResponseError):
        await client.status()


async def test_connection_error_maps():
    resp = FakeResponse(enter_error=aiohttp.ClientConnectionError("refused"))
    client = make_client(FakeSession(resp))
    with pytest.raises(GC3ConnectionError):
        await client.status()


async def test_timeout_maps_to_connection_error():
    resp = FakeResponse(enter_error=TimeoutError("slow"))
    client = make_client(FakeSession(resp))
    with pytest.raises(GC3ConnectionError):
        await client.status()


async def test_injected_session_not_closed():
    sess = FakeSession()
    client = GC3Client(HOST, "123456", "tok", session=sess)
    await client.close()
    assert sess.closed is False  # we did not own it


async def test_owned_session_is_created_and_closed():
    # No network: constructing/closing an aiohttp session does not connect.
    client = GC3Client(HOST, "123456", "tok")
    sess = client._ensure_session()
    assert isinstance(sess, aiohttp.ClientSession)
    await client.close()
    assert sess.closed is True
    assert client._session is None


async def test_context_manager_closes_owned_session():
    async with GC3Client(HOST, "123456", "tok") as client:
        sess = client._ensure_session()
    assert sess.closed is True
