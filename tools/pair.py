#!/usr/bin/env python3
"""
GC3 pairing helper -- obtain API credentials WITHOUT MITM.

The panel's local automation API enrols controllers through POST /api/v1/pair.
This is the same handshake the Elan SC100 (and any other paired controller) uses:
the panel is put into "System Pairing" mode from the Installer Toolbox and
*listens* for a pairing request from a third-party system. A controller then
POSTs to /api/v1/pair; the panel replies with a long-lived API key bound to it.

Auth model (confirmed by probing a live GC3, firmware as of 2026-08):
  X-Pairing-Key : a 6-digit code                       (e.g. "123456")
  X-Api-Key     : a 36-char (UUID-shaped) token the panel ISSUES at pairing time

The API key is the durable per-controller credential you want to capture. The
6-digit pairing key is a shared secret -- but WHERE it comes from during a fresh
enrol is not pinned down. The GC3/GC3e Install & Programming Guide (10023748A,
p.37) documents Installer Toolbox -> System Pairing -> press "+" -> a "System
Pairing - Enter Key" screen while "the system listens for the pairing request
from the third party system." The guide does not say whether that screen
*displays* a key (read it, send it) or *expects* one the controller generates --
`probe` exists to find out empirically.

USAGE
  # 1. Read the currently-paired controller (safe, read-only) -- needs BOTH an
  #    existing api key and pairing key. Useful to confirm connectivity/creds.
  ./pair.py show --panel https://192.168.1.25:3000 \
      --api-key <existing> --pairing-key <existing>

  # 2. Probe the System Pairing listen window (unauthenticated -- never sends an
  #    api key). Open Installer Toolbox -> System Pairing, press "+", then run
  #    this DURING the listen window. Pass --pairing-key only if the Enter Key
  #    screen shows one; omit it to test whether the window needs no key at all.
  ./pair.py probe --panel https://192.168.1.25:3000            # no key shown
  ./pair.py probe --panel https://192.168.1.25:3000 --pairing-key 123456
  # -> dumps the raw request+response and interprets it against known outcomes.

  # 3. Enrol THIS machine as a new controller once probing shows what the window
  #    wants. Read the 6-digit code off the panel, then:
  ./pair.py enrol --panel https://192.168.1.25:3000 \
      --pairing-key 123456 --device-name "Home Assistant"
  # -> prints the issued API key. Put it (with the pairing code) in .env.

NOTES / UNKNOWNS (verify on first real run)
  * The exact auth the POST accepts during pairing mode is not 100% pinned down.
    `probe` sends X-Api-Key NEVER and X-Pairing-Key only if you pass one, then
    DUMPS THE RAW RESPONSE and maps it to the outcomes seen in INTEGRATION_PLAN
    section 2a (gate-1 "Invalid API key" vs gate-2 "Pairing not authorized" vs a
    success that carries an issued key). `enrol` additionally sends X-Api-Key if
    you pass one.
  * Whether enrolling a NEW controller evicts the SC100 (single-controller
    panels) or coexists (multi) is unknown -- fine here, the SC100 is being
    retired, but see INTEGRATION_PLAN.md "Open questions".
  * `show` is read-only. `probe` and `enrol` POST to /api/v1/pair and can
    register a device / mutate panel state; only run them against your own panel.
"""
import argparse
import json
import sys

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PAIR_PATH = "/api/v1/pair"
# Response field names that have been seen / are plausible for the issued token.
_KEY_FIELDS = ("apiKey", "ApiKey", "api_key", "apikey", "key", "token")


def _session(api_key: str | None, pairing_key: str | None) -> requests.Session:
    s = requests.Session()
    s.verify = False
    if pairing_key:
        s.headers["X-Pairing-Key"] = pairing_key
    if api_key:
        s.headers["X-Api-Key"] = api_key
    s.headers["Content-Type"] = "application/json"
    return s


def _enrol_body(device_name: str, partition: int) -> dict:
    return {
        "deviceName": device_name,
        "allPartitionAccess": True,
        "isSecurityDevice": False,
        "memberOfPartition": str(partition),
    }


def _issued_key(data: dict) -> str | None:
    return next((data[k] for k in _KEY_FIELDS if k in data), None)


def _interpret(r: requests.Response) -> str:
    """Map a /api/v1/pair POST response to the outcomes catalogued in
    INTEGRATION_PLAN.md section 2a, so a run at the panel is self-explaining."""
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.ok:
        if _issued_key(body):
            return ("SUCCESS: the panel issued an API key (see above). The System "
                    "Pairing window accepts an unauthenticated enrol -- bootstrap "
                    "works. Capture the key + pairing code into .env.")
        return ("2xx but no recognised key field. Inspect the body for a ~36-char "
                "UUID-shaped value; that is the API key.")
    # Two distinct 403 shapes were observed; they mean different things.
    err = str(body.get("Error") or body.get("message") or r.text)
    if r.status_code == 403 and "not authorized" in err.lower():
        return ("Gate 2 (pairing-authorized state) is still closed: the panel is "
                "NOT in the System Pairing listen window right now. Open Installer "
                "Toolbox -> System Pairing, press '+', and re-run within the window.")
    if r.status_code in (401, 403) and "api key" in err.lower():
        return ("Gate 1 (auth middleware) rejected the request for lack of a valid "
                "api key -- the listen window does NOT relax the middleware for an "
                "unauthenticated device. If the Enter Key screen shows a code, pass "
                "it as --pairing-key and retry; otherwise unauthenticated bootstrap "
                "is blocked on this firmware.")
    return "Unrecognised outcome -- record the status + body above in INTEGRATION_PLAN.md."


def show(args) -> int:
    s = _session(args.api_key, args.pairing_key)
    r = s.get(args.panel.rstrip("/") + PAIR_PATH, timeout=8)
    print(f"HTTP {r.status_code}")
    print(r.text)
    return 0 if r.ok else 1


def probe(args) -> int:
    """Fire an UNAUTHENTICATED enrol at the System Pairing listen window and
    report what came back. Never sends X-Api-Key. See INTEGRATION_PLAN.md 2a."""
    s = _session(api_key=None, pairing_key=args.pairing_key)
    body = _enrol_body(args.device_name, args.partition)
    url = args.panel.rstrip("/") + PAIR_PATH
    print(f"POST {url}")
    print(f"  headers: {dict(s.headers)}")
    print(f"  body:    {json.dumps(body)}")
    print("(panel must be in Installer Toolbox -> System Pairing, '+' pressed)\n")
    r = s.post(url, json=body, timeout=15)
    print(f"HTTP {r.status_code}")
    print(r.text)
    print("\n" + "=" * 60)
    print(_interpret(r))
    print("=" * 60)
    return 0 if r.ok else 1


def enrol(args) -> int:
    s = _session(args.api_key, args.pairing_key)
    body = _enrol_body(args.device_name, args.partition)
    print(f"POST {PAIR_PATH}  body={json.dumps(body)}")
    print("(panel must be in System Pairing mode and showing this pairing code)")
    r = s.post(args.panel.rstrip("/") + PAIR_PATH, json=body, timeout=15)
    print(f"HTTP {r.status_code}")
    print(r.text)
    if not r.ok:
        print("\nEnrolment failed:", file=sys.stderr)
        print(_interpret(r), file=sys.stderr)
        return 1
    try:
        data = r.json()
    except ValueError:
        print("\nResponse was not JSON -- inspect the body above for the API key.")
        return 0
    issued = _issued_key(data)
    print("\n" + "=" * 60)
    if issued:
        print("API key issued:")
        print(f"  API_KEY={issued}")
        print(f"  PAIRING_KEY={args.pairing_key}")
        print("\nPut both in .env (API_KEY / PAIRING_KEY).")
    else:
        print("Could not auto-detect the API-key field in the response above.")
        print("Look for a ~36-char UUID-shaped value and use it as API_KEY.")
    print("=" * 60)
    return 0


def main() -> int:
    # Shared options live on a parent parser attached to each subcommand, so they
    # can be passed AFTER the subcommand -- `enrol --panel ... --pairing-key ...`,
    # the natural order -- not just before it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--panel", required=True, help="https://<panel-ip>:3000")
    common.add_argument("--pairing-key", required=True,
                        help="6-digit code from Installer Toolbox -> System Pairing")
    common.add_argument("--api-key", default=None,
                        help="existing API key (for `show`; omit when enrolling fresh)")

    # `probe` is the unauthenticated experiment: --panel required, --pairing-key
    # optional (the Enter Key screen may show no code), no --api-key at all.
    probe_common = argparse.ArgumentParser(add_help=False)
    probe_common.add_argument("--panel", required=True, help="https://<panel-ip>:3000")
    probe_common.add_argument("--pairing-key", default=None,
                              help="only if the Enter Key screen shows a code")

    p = argparse.ArgumentParser(description="GC3 pairing helper (no MITM).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", parents=[common],
                   help="read current pairing (read-only)").set_defaults(fn=show)
    pr = sub.add_parser("probe", parents=[probe_common],
                        help="unauthenticated enrol against the System Pairing window")
    pr.add_argument("--device-name", default="Home Assistant")
    pr.add_argument("--partition", type=int, default=1)
    pr.set_defaults(fn=probe)
    e = sub.add_parser("enrol", parents=[common],
                       help="register this controller, get an API key")
    e.add_argument("--device-name", default="Home Assistant")
    e.add_argument("--partition", type=int, default=1)
    e.set_defaults(fn=enrol)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
