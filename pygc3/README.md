# pygc3

Async client for the **2GIG GC3** panel's local automation API — the same
header-authenticated HTTPS API the Elan SC100 used.

Transport + typed models only. HA state mapping, MQTT, and night-mode tracking
live in the consumer, not here.

## Install

```bash
pip install pygc3
```

Requires Python ≥3.11 and `aiohttp`.

## Use

```python
import asyncio
from pygc3 import GC3Client

async def main():
    async with GC3Client("192.168.1.25", pairing_key="123456",
                         api_key="<issued-token>") as gc3:
        status = await gc3.status()          # PanelStatus
        zones = await gc3.zones()            # list[Zone], console filtered out
        print(status.arm_state, len(zones))

        await gc3.arm(stay=True, night=True) # night = stay + instant entry
        await gc3.disarm("1234")

asyncio.run(main())
```

Pass an existing session to reuse Home Assistant's shared one (it won't be
closed for you):

```python
GC3Client(host, pairing_key, api_key, session=hass_session)
```

### Enrolment (no api_key yet)

With the panel in **System Pairing** mode, enrol without a token — the panel
issues one back:

```python
async with GC3Client(host, pairing_key="123456") as gc3:  # no api_key
    issued = await gc3.pair("Home Assistant")
```

## API

| Method | Endpoint | Returns |
|---|---|---|
| `status()` | `GET /api/v1/status` | `PanelStatus` |
| `zones(include_console=False)` | `GET /api/v1/zones` | `list[Zone]` |
| `get_pairing()` | `GET /api/v1/pair` | `Pairing` |
| `arm(*, stay, night=…, …)` | `POST …/panel/arm` | raw dict |
| `disarm(pin)` | `POST …/panel/disarm` | raw dict |
| `pair(device_name)` | `POST /api/v1/pair` | raw dict |

Errors: `GC3AuthError` (403 / bad key → reauth), `GC3ConnectionError`
(unreachable / timeout → retry), `GC3ResponseError` (bad status or non-JSON),
all subclasses of `GC3Error`.

TLS verification is off by default (`verify_ssl=False`) for the panel's
self-signed cert.

### There is no push API — poll

The panel exposes `GET /api/v1/events` as SSE, and it is a stub. Probed against
firmware as of 2026-08-03: it accepts any number of concurrent subscribers
without evicting any, does not starve the REST endpoints while streams are
held, and sends each subscriber the literal 14-byte banner `Event stream:` —
then nothing, ever, including across a full arm cycle that concurrent `/status`
polls captured in full. There is no `event:`/`data:` framing and no query
argument that changes it. The handler writes a header and was never wired to
the panel's event bus.

So this library does not wrap it, and consumers should poll `status()` and
`zones()`. If a firmware update ever makes it emit, keep a slow poll as a
safety net regardless: the panel refuses connections for roughly a minute after
it reboots, and a silently dropped stream must not freeze your state.

## Develop

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test,dev]"
pytest --cov=pygc3      # 27 tests, ~98% coverage; HTTP faked via the session seam
mypy                    # strict, clean
ruff check src tests
```

Fixtures under `tests/fixtures/` are real panel captures; the pairing fixture's
6-digit code is redacted to `000000`.

## Release

```bash
rm -rf dist build src/*.egg-info
python -m build          # sdist + wheel
twine check --strict dist/*
twine upload dist/*
```

Version lives in `pyproject.toml` and `pygc3/__init__.__version__`; bump both,
update [`CHANGELOG.md`](CHANGELOG.md), then tag `pygc3-v<version>`.

## Licence

MIT — see [LICENSE](LICENSE).
