#!/usr/bin/env python3
"""
GC3 pairing helper -- obtain API credentials WITHOUT MITM.

The panel's local automation API enrols controllers through POST /api/v1/pair.
This is the same handshake the Elan SC100 (and any other paired controller) uses:
the panel is put into "System Pairing" mode from the Installer Toolbox, displays a
6-digit pairing code, and *listens*. A controller then POSTs to /api/v1/pair; the
panel replies with a long-lived API key bound to that controller.

Auth model (confirmed by probing a live GC3, firmware as of 2026-08):
  X-Pairing-Key : the 6-digit code shown on the panel  (e.g. "123456")
  X-Api-Key     : a 36-char (UUID-shaped) token the panel ISSUES at pairing time

So the pairing code is a short-lived shared secret you read off the panel screen;
the API key is the durable per-controller credential you want to capture here.

USAGE
  # 1. Read the currently-paired controller (safe, read-only) -- needs BOTH an
  #    existing api key and pairing key. Useful to confirm connectivity/creds.
  ./pair.py show --panel https://192.168.1.25:3000 \
      --api-key <existing> --pairing-key <existing>

  # 2. Enrol THIS machine as a new controller. Put the panel in pairing mode
  #    first (Installer Toolbox -> System Pairing), read the 6-digit code, then:
  ./pair.py enrol --panel https://192.168.1.25:3000 \
      --pairing-key 123456 --device-name "Home Assistant"
  # -> prints the issued API key. Put it (with the pairing code) in .env.

NOTES / UNKNOWNS (verify on first real run)
  * The exact auth the POST accepts during pairing mode is not 100% pinned down:
    the panel may accept the pairing code alone, or may want a placeholder api
    key header too. This script sends X-Pairing-Key always and X-Api-Key only if
    you pass one, then DUMPS THE RAW RESPONSE so you can see which field carries
    the issued key (guessed keys tried below, else inspect the JSON).
  * Whether enrolling a NEW controller evicts the SC100 (single-controller
    panels) or coexists (multi) is unknown -- fine here, the SC100 is being
    retired, but see INTEGRATION_PLAN.md "Open questions".
  * Nothing here mutates panel state except `enrol`, which registers a device.
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


def _session(api_key: str | None, pairing_key: str) -> requests.Session:
    s = requests.Session()
    s.verify = False
    s.headers["X-Pairing-Key"] = pairing_key
    if api_key:
        s.headers["X-Api-Key"] = api_key
    s.headers["Content-Type"] = "application/json"
    return s


def show(args) -> int:
    s = _session(args.api_key, args.pairing_key)
    r = s.get(args.panel.rstrip("/") + PAIR_PATH, timeout=8)
    print(f"HTTP {r.status_code}")
    print(r.text)
    return 0 if r.ok else 1


def enrol(args) -> int:
    s = _session(args.api_key, args.pairing_key)
    body = {
        "deviceName": args.device_name,
        "allPartitionAccess": True,
        "isSecurityDevice": False,
        "memberOfPartition": str(args.partition),
    }
    print(f"POST {PAIR_PATH}  body={json.dumps(body)}")
    print("(panel must be in System Pairing mode and showing this pairing code)")
    r = s.post(args.panel.rstrip("/") + PAIR_PATH, json=body, timeout=15)
    print(f"HTTP {r.status_code}")
    print(r.text)
    if not r.ok:
        print("\nEnrolment failed. Most likely: panel not in pairing mode, or the "
              "pairing code has changed/expired. Re-enter System Pairing and retry.",
              file=sys.stderr)
        return 1
    try:
        data = r.json()
    except ValueError:
        print("\nResponse was not JSON -- inspect the body above for the API key.")
        return 0
    issued = next((data[k] for k in _KEY_FIELDS if k in data), None)
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

    p = argparse.ArgumentParser(description="GC3 pairing helper (no MITM).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", parents=[common],
                   help="read current pairing (read-only)").set_defaults(fn=show)
    e = sub.add_parser("enrol", parents=[common],
                       help="register this controller, get an API key")
    e.add_argument("--device-name", default="Home Assistant")
    e.add_argument("--partition", type=int, default=1)
    e.set_defaults(fn=enrol)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
