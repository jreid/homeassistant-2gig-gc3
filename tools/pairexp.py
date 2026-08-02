#!/usr/bin/env python3
"""
ONE-OFF EXPERIMENT (mutating) -- authenticated POST /api/v1/pair.

Run from inside the gc3-bridge container (it has PANEL_URL/API_KEY/PAIRING_KEY):
    docker cp tools/pairexp.py gc3-bridge:/tmp/pairexp.py
    docker exec gc3-bridge python /tmp/pairexp.py

Phases:
  1. BASELINE  -- GET /status + /pair; abort if unhealthy.
  2. WRITE     -- POST /api/v1/pair mirroring the CURRENT pairing back verbatim
                  (identity-preserving re-assert; NOT a rename). Prints response.
  3. VERIFY    -- re-check with the ORIGINAL creds; report if they survived and
                  whether the pairing/api key changed.

Rollback: .env is backed up separately; the bridge is left running. If the POST
response contains a new key, capture it before restarting anything.
"""
import os, json, requests, urllib3
urllib3.disable_warnings()

base = os.environ["PANEL_URL"].rstrip("/")
AK, PK = os.environ["API_KEY"], os.environ["PAIRING_KEY"]
H = {"X-Api-Key": AK, "X-Pairing-Key": PK, "Content-Type": "application/json"}


def g(path):
    return requests.get(base + path, headers=H, timeout=8, verify=False)


print("=== BASELINE (rollback reference) ===")
st = g("/api/v1/status"); print("status:", st.status_code, st.text)
pr = g("/api/v1/pair");   print("pair  :", pr.status_code, pr.text)
if not (st.ok and pr.ok):
    print("!! baseline not healthy; aborting before any write."); raise SystemExit(2)
base_pair = pr.json()

body = {
    "deviceName": base_pair.get("deviceName"),
    "allPartitionAccess": base_pair.get("allPartitionAccess"),
    "isSecurityDevice": base_pair.get("isSecurityDevice"),
    "memberOfPartition": base_pair.get("memberOfPartition"),
    "PairingKey": base_pair.get("PairingKey"),  # bias toward keeping it stable
}
print("\n=== POST /api/v1/pair  (identical re-assert) ===")
print("body:", json.dumps({**body, "PairingKey": "<redacted>"}))
pr2 = requests.post(base + "/api/v1/pair", headers=H, json=body, timeout=15, verify=False)
print("POST  :", pr2.status_code)
print("resp  :", pr2.text)

print("\n=== POST-STATE: did our EXISTING creds still work? ===")
st2 = g("/api/v1/status"); print("status(old creds):", st2.status_code, st2.text[:80])
pr3 = g("/api/v1/pair");   print("pair  (old creds):", pr3.status_code, pr3.text)

print("\n=== VERDICT ===")
print("old creds survived  :", st2.ok and pr3.ok)
if pr3.ok:
    after = pr3.json()
    print("pairing key changed :", after.get("PairingKey") != base_pair.get("PairingKey"))
    print("device name changed :", after.get("deviceName") != base_pair.get("deviceName"))
