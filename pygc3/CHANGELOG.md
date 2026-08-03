# Changelog

All notable changes to `pygc3` are recorded here. This project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-08-03

### Changed — breaking

- `GC3Client.arm()`'s `night` argument is renamed `no_entry_delay`. There is no
  night flag on the wire; `night` was this library naming a panel flag
  (`noEntryDelay`) after one thing consumers commonly build with it, which is
  precisely the confusion a transport library should not introduce. Every arm
  argument is now named for the wire flag it sets.

  Migration is a rename: `arm(stay=True, night=True)` becomes
  `arm(stay=True, no_entry_delay=True)`. No alias is kept — an alias would
  preserve the ambiguity, and at 0.x with the API days old, a clean break is
  cheaper than living with it.

  Note that `no_entry_delay=True` means the siren sounds the instant an entry
  zone opens, with no chance to disarm. If you were passing `night=True` to
  label a state rather than to get that behaviour, you wanted neither argument:
  arm without it and track the label yourself, since `status()` returns only
  `armState` and `isArmedStay` and cannot report a night mode back.

### Documentation

- Documented that `GET /api/v1/events` is a stub, so nobody builds a push
  client on it. It accepts any number of concurrent subscribers and does not
  interfere with the REST API, but emits only the banner `Event stream:` and
  never an event — verified across a full arm cycle, with no `event:`/`data:`
  framing and no query argument that changes it. Poll instead.
- Corrected the 0.1.0 entry below, which still read `unreleased` after that
  version was published to PyPI on 2026-08-03.

## [0.1.0] — 2026-08-03

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
