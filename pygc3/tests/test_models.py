"""Model parsing tests against the recorded fixtures."""

from __future__ import annotations

from pygc3.models import Pairing, PanelStatus, Zone, ZoneStatus, _as_bool


def test_as_bool_variants():
    assert _as_bool(True) is True
    assert _as_bool(False) is False
    assert _as_bool(1) is True
    assert _as_bool(0) is False
    assert _as_bool("true") is True
    assert _as_bool("False") is False
    assert _as_bool("on") is True
    assert _as_bool(None) is False


def test_panel_status_from_fixture(status_json):
    st = PanelStatus.from_json(status_json)
    assert st.arm_state == "ready"  # lowercased
    assert st.is_armed_stay is False
    assert st.raw is status_json


def test_panel_status_lowercases_armstate():
    st = PanelStatus.from_json({"armState": "ARMING", "isArmedStay": True})
    assert st.arm_state == "arming"
    assert st.is_armed_stay is True


def test_zone_from_fixture(zones_json):
    z1 = Zone.from_json(next(z for z in zones_json if z["id"] == "1"))
    assert z1.id == "1"
    assert z1.name == "Mud Room Door"
    assert z1.serial == 937133
    assert z1.connection_type == "wireless"
    assert z1.partition == 1
    assert z1.supervision_enabled is True
    assert isinstance(z1.status, ZoneStatus)
    assert z1.status.open is False
    assert z1.status.bypassable is True
    assert z1.is_console is False


def test_zone_console_detection(zones_json):
    z0 = Zone.from_json(next(z for z in zones_json if z["id"] == "0"))
    assert z0.is_console is True


def test_zone_missing_name_falls_back():
    z = Zone.from_json({"id": "42", "status": {}})
    assert z.name == "Zone 42"
    assert z.serial is None
    assert z.status.open is False  # empty status still parses


def test_pairing_from_fixture(pair_json):
    p = Pairing.from_json(pair_json)
    assert p.pairing_key == "000000"  # redacted in fixture
    assert p.device_name == "TEST Controller"
    assert p.all_partition_access is True
    assert p.member_of_partition == "1"
