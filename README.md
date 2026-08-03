# Home Assistant — 2GIG GC3 integration

Local, cloud-free Home Assistant integration for the **2GIG GC3** security
panel, talking to the same on-panel automation API the Elan/Nice SC100 used.
No cloud account, no vendor bridge, no polling anything outside your LAN.

| | |
|---|---|
| `alarm_control_panel` | arm home / away / night, disarm |
| `binary_sensor` (one per zone) | open/closed, with battery / tamper / bypass / supervision attributes |
| `binary_sensor` (four, diagnostic) | panel health: battery low, tamper, supervision lost, zones bypassed — each naming the offending zones |
| `sensor` | open-zone count, naming the zones that would block an arm |

Everything lives on one device in HA, with diagnostics, reauth, reconfigure and
a repair issue when a sensor stops checking in.

> **This drives a real alarm.** Arming and disarming through Home Assistant
> sounds sirens and, on a monitored panel, can dispatch responders. Test
> accordingly.

## Layout

| Path | What |
|---|---|
| `custom_components/gc3/` | The Home Assistant integration |
| `pygc3/` | Standalone async client library for the panel API — published to PyPI as [`pygc3`](https://pypi.org/project/pygc3/) |
| `tests/` | Integration test suite (69 tests, ≥95% coverage enforced) |
| `tools/` | `pair.py` credential diagnostics; `pairexp.py` / `pairnew.py` are the recorded dead-end pairing experiments |
| `INTEGRATION_PLAN.md` | The panel-API investigation, credential findings, and quality-scale roadmap |

The original MQTT bridge this was derived from (still the reference
implementation and a working fallback) lives separately in the docker
deployment, not in this repo.

## Requirements

- Home Assistant 2026.7 or newer
- A 2GIG GC3 reachable on your LAN, with its automation API on port 3000
- The panel's `X-Api-Key` and `X-Pairing-Key` (see below)

## Credentials

The panel authenticates with two headers, checked independently: a 36-char
`X-Api-Key` and a 6-digit `X-Pairing-Key`.

On this firmware **they can only be obtained by capturing them from an
already-paired controller.** API-driven pairing from scratch is not
reproducible: `POST /api/v1/pair` demands a valid api-key before it will do
anything (chicken-and-egg for a new controller), and the panel displays no
pairing code anywhere in its UI. The full investigation — three failed
re-pairing attempts, the two independent gates on the pairing write, and the
MITM capture procedure — is in [`INTEGRATION_PLAN.md`](INTEGRATION_PLAN.md) §2.

Once captured, the keys are durable: they keep authenticating after the
original controller is powered off.

`tools/pair.py show` reads the current pairing back for diagnostics.

## Install

### HACS (recommended)

1. HACS → ⋮ → **Custom repositories** → add this repo as an **Integration**.
2. Install **2GIG GC3**, then restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → 2GIG GC3.**

### Manually

1. Copy `custom_components/gc3/` into your HA `config/custom_components/`.
2. Restart Home Assistant and add the integration from the UI.

`pygc3` is declared in the manifest's `requirements`, so Home Assistant installs
it for you either way.

## Configure

The setup dialog asks for:

| Field | Notes |
|---|---|
| Host | Panel IP or hostname |
| API key | The captured `X-Api-Key` |
| Pairing key | The captured 6-digit `X-Pairing-Key` |
| Disarm PIN | Optional. Leave blank to be prompted for a code on every disarm |
| Port | Defaults to 3000 |
| Partition | Defaults to 1 |

The credentials are verified against the panel before the entry is created, so
a typo fails in the dialog rather than at the first poll.

Under **Configure** on the integration afterwards:

| Option | Default | Notes |
|---|---|---|
| Poll interval | 3 s | 1–60. The panel is local and cheap to poll |
| Arm with no exit delay | off | Arms instantly instead of counting down |
| Bypass open zones when arming | off | Arms past open zones instead of failing |

Host, port and PIN can be changed later with **Reconfigure**; if the panel
rejects the keys, HA raises a reauthentication prompt.

## How it works

A single `DataUpdateCoordinator` polls `GET /api/v1/status` and
`GET /api/v1/zones` on the configured interval and feeds every entity. Zones
enrolled or deleted on the panel are picked up on the next poll — no reload.

Device classes are derived from each zone's voice descriptor (`Front Door` →
`door`, `Basement Motion` → `motion`, `Garage Overhead Door West` →
`garage_door`, and so on). Keyfobs and pendants are created **disabled by
default** — they are momentary and noisy.

Five entities roll the zones up so you don't have to template over them:

| Entity | On / value when |
|---|---|
| `binary_sensor.*_battery_low` | any zone reports a low battery |
| `binary_sensor.*_tamper` | any zone reports tamper |
| `binary_sensor.*_supervision_lost` | any zone has stopped checking in |
| `binary_sensor.*_zones_bypassed` | any zone is bypassed |
| `sensor.*_open_zones` | count of open doors, windows and other openings — motion and flood zones don't count |

Each carries a `zones` attribute listing the zones responsible, which is what
makes a notification worth reading (`Replace battery: Deck Door, Keyfob`). The
roll-ups cover every zone the panel reports, including keyfobs whose own entity
is disabled.

## Known limitations

- **`armed_night` is inferred, not observed.** `/api/v1/status` returns only
  `armState` and `isArmedStay`; night is indistinguishable from stay on
  read-back. The integration remembers that *it* issued a night arm. Restart
  Home Assistant while armed-night and it falls back to `armed_home`. Any
  disarm, from any source, clears the flag.
- **Polling, not push.** The panel's SSE endpoint (`/api/v1/events`) is a stub on
  this firmware. Measured 2026-08-02 with `tools/sse_probe.py`: it accepts any
  number of concurrent subscribers, does not interfere with the REST API, sends
  each one the literal banner `Event stream:` — and then nothing, through zone
  faults and a full arm cycle. So the integration polls, and `iot_class` stays
  `local_polling`.
- **The panel refuses API connections for roughly a minute after it reboots.**
  Entities go unavailable and recover on their own; the coordinator logs it
  once rather than every poll.
- **One session at a time.** Arm/disarm commands are serialised
  (`PARALLEL_UPDATES = 1`); the panel is stingy about concurrency.
- **No panel identity on the API.** `/status` exposes no serial, so the config
  entry is keyed on host.
- **TLS verification is off.** The panel serves a self-signed certificate and
  does not validate clients. This is a property of the panel, noted here as a
  security observation.

## Troubleshooting

**"Failed to connect to the panel."** Check the host and port 3000 from the HA
host (`curl -k https://<panel>:3000/api/v1/status`). If the panel restarted
recently, wait a minute.

**"The panel rejected the API/pairing key."** Both headers are validated
separately, and the api-key is checked first — so a wrong api-key reports
`Invalid API key` no matter what the pairing key is. Re-capture both.

**A zone shows a repair issue about supervision.** The panel has not heard from
that sensor for ~20 consecutive polls. Usually a dead battery, a removed
sensor, or one out of range. The zone's state in HA is stale until the panel
hears from it again.

**Filing a bug.** Download **Diagnostics** from the integration page. It
includes the panel's raw `/status` and `/zones` payloads with credentials, host
and sensor serials redacted.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md).

```bash
pip install -r requirements-test.txt && pip install ./pygc3
pytest --cov=custom_components/gc3
mypy custom_components/gc3
ruff check custom_components tests
```

Progress against the
[Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/)
is tracked rule by rule in
[`custom_components/gc3/quality_scale.yaml`](custom_components/gc3/quality_scale.yaml).

## Licence

MIT — see [LICENSE](LICENSE).
