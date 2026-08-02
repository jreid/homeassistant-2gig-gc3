# Changelog

All notable changes to `pygc3` are recorded here. This project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — unreleased

First public release. Extracted from the `gc3_bridge.py` MQTT bridge that has
been driving a live GC3 panel, and rewritten on `aiohttp`.

### Added

- `GC3Client` — async client for the panel's local automation API over HTTPS
  on port 3000, authenticating with the `X-Api-Key` / `X-Pairing-Key` header
  pair.
  - `status()`, `zones()`, `get_pairing()` reads.
  - `arm()`, `disarm()`, `pair()` writes.
  - Optional session injection, so a host application (Home Assistant) can
    supply its own `aiohttp.ClientSession`; an injected session is never closed
    by the client.
  - `verify_ssl=False` by default — the panel serves a self-signed certificate.
- Typed models `PanelStatus`, `Zone`, `ZoneStatus`, `Pairing`, each keeping the
  panel's untouched JSON in `.raw` so callers can reach unmodelled fields
  without a library change.
- Exception hierarchy `GC3Error` → `GC3ConnectionError`, `GC3AuthError`,
  `GC3ResponseError`, mapped to the two outcomes a consumer must distinguish:
  bad credentials (re-authenticate) versus an unreachable panel (retry).
- `py.typed` marker; the package is checked with `mypy --strict`.

### Known limitations

- The panel reports only `armState` and `isArmedStay` on `/status`, so an
  armed-night state cannot be read back. Consumers that offer a night mode must
  remember they issued it.
- `POST /api/v1/pair` requires an existing API key and a panel-side pairing
  mode that has not proved reachable on current firmware. Credentials are
  obtained by capturing them from an already-paired controller.
