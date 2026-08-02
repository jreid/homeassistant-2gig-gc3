#!/usr/bin/env python3
"""
ONE-OFF EXPERIMENT (mutating) -- enrol a NEW controller identity.

Authenticates with the EXISTING creds (gate 1), then POSTs /api/v1/pair with a
new deviceName to try to mint a distinct credential for Home Assistant. Requires
the panel to be in System Pairing mode (gate 2) at the moment it runs.

Run from inside the gc3-bridge container:
    docker cp tools/pairnew.py gc3-bridge:/tmp/pairnew.py
    docker exec gc3-bridge python /tmp/pairnew.py

Secrets: credential-looking values are MASKED in stdout (so the response shape is
visible without leaking a live key into logs). The full raw response is written
to /tmp/ha_pair_result.json INSIDE the container -- retrieve with:
    docker exec gc3-bridge cat /tmp/ha_pair_result.json
"""
import json, os, re, requests, urllib3
urllib3.disable_warnings()

base = os.environ["PANEL_URL"].rstrip("/")
AK, PK = os.environ["API_KEY"], os.environ["PAIRING_KEY"]
H = {"X-Api-Key": AK, "X-Pairing-Key": PK, "Content-Type": "application/json"}
NEW_NAME = os.environ.get("HA_DEVICE_NAME", "Home Assistant")

_SENS = re.compile(r"(api.?key|token|pairing)", re.I)


def mask(v):
    if isinstance(v, dict):
        return {k: (mask(val) if _SENS.search(k) else mask(val)) for k, val in v.items()}
    if isinstance(v, str) and len(v) >= 6:
        return f"<{v[:2]}…{v[-2:]} ({len(v)} chars)>"
    return v


def g(path):
    return requests.get(base + path, headers=H, timeout=8, verify=False)


print("=== BASELINE ===")
st = g("/api/v1/status"); print("status:", st.status_code, st.text)
pr = g("/api/v1/pair");   print("pair  :", pr.status_code, mask(pr.json()) if pr.ok else pr.text)
if not (st.ok and pr.ok):
    print("!! baseline not healthy; aborting."); raise SystemExit(2)

body = {
    "deviceName": NEW_NAME,
    "allPartitionAccess": True,
    "isSecurityDevice": False,
    "memberOfPartition": "1",
}
print(f"\n=== POST /api/v1/pair  (new identity: {NEW_NAME!r}) ===")
r = requests.post(base + "/api/v1/pair", headers=H, json=body, timeout=15, verify=False)
print("POST  :", r.status_code)
try:
    data = r.json()
    print("resp  :", json.dumps(mask(data)))
    with open("/tmp/ha_pair_result.json", "w") as f:
        json.dump(data, f, indent=2)
    print("      (full raw response written to /tmp/ha_pair_result.json in-container)")
except ValueError:
    print("resp  :", r.text[:300])

print("\n=== POST-STATE: existing creds still work? ===")
st2 = g("/api/v1/status"); print("status(old creds):", st2.status_code, st2.text[:80])
pr2 = g("/api/v1/pair");   print("pair  (old creds):", pr2.status_code,
                                  mask(pr2.json()) if pr2.ok else pr2.text)
print("\nold creds survived:", st2.ok and pr2.ok)
