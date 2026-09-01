# 2GIG GC3 → Home Assistant integration — build plan

Turn the working `gc3_bridge.py` MQTT bridge into a first-class Home Assistant
integration (`gc3`), built to the
[Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/).

The bridge stays as the reference implementation and fallback; this plan is about
packaging the same panel API behind a native config-flow integration.

**Status: built.** `pygc3` and the integration are both complete, tested and
lint/type clean; `quality_scale.yaml` records every rule. What is *not* done is
soak-testing against the live panel since the rewrite, plus one external item
(the brands logo) — see §5. Both investigative questions are now closed: the SSE
endpoint is a stub, so polling is final, and the panel advertises no mDNS, so
discovery is exempt (§6). Sections 1 and
2 below are the investigation record and are unchanged; sections 3–6 have been
updated to describe what was actually built where it diverged from the plan.

---

## 1. What the panel actually exposes (investigation results)

Probed against a live GC3 (firmware current as of 2026-08). All over HTTPS on
port 3000, self-signed cert, client certs not validated → `verify=False`.

**Auth:** two headers on every request, **validated independently** (probed):

| Header | Value | Nature |
|---|---|---|
| `X-Pairing-Key` | 6-digit (e.g. `123456`) | A pairing code. **Not displayed anywhere obvious in this panel's UI** — origin unclear on this firmware (see §2). |
| `X-Api-Key` | 36-char, UUID-shaped | Durable per-controller token bound to a paired controller. |

Both are checked separately:

| Sent | Result |
|---|---|
| valid api-key + valid pairing-key | `200` |
| valid api-key, wrong/missing pairing-key | `403 {"message":"Invalid pairing key"}` |
| wrong/missing api-key, valid pairing-key | `403 {"message":"Invalid API key"}` |
| pairing code used as the api-key | `403 {"message":"Invalid API key"}` |

The api-key is checked first, so **any request without a valid api-key returns
`"Invalid API key"` regardless of the pairing key** — including `POST /api/v1/pair`.

**Endpoints** (from `OPTIONS`, and the bridge):

| Method | Path | Purpose | Response shape |
|---|---|---|---|
| GET | `/api/v1/status` | Panel arm state | `{"armState":"ready","isArmedStay":false}` — **only these two fields** |
| GET | `/api/v1/zones` | All zones | array of zone objects (schema below) |
| GET/POST | `/api/v1/pair` | Read / create a pairing | see §2 |
| GET/POST | `/api/v1/actions/panel/arm?partition=N` | Arm | — |
| GET/POST | `/api/v1/actions/panel/disarm?partition=N` | Disarm | — |
| GET | `/api/v1/events` | SSE stream | **A stub.** Connects (`200`, `text/event-stream`, chunked), emits the banner `Event stream:\n`, then nothing — ever. See §6. Unused. |

Anything else under `/api`, `/api/v1`, `/api/v1/{info,system,version,config,
devices,clients,users,partitions,…}` → `404 Endpoint Not Found`. The surface is
small.

**Zone object** (real capture, zone 1):

```json
{
  "id": "1", "deviceSerialNumber": 937133, "equipmentCode": 2862,
  "voiceDescriptor": "Mud Room Door", "connectionType": "wireless",
  "zoneAlarmType": 1, "zonePhysicalType": 1, "partitionAssignment": 1,
  "sensorSupervisionEnabled": true, "sensorReportingEnabled": true,
  "status": {
    "open": false, "inAlarm": false, "inAlarmType": 0,
    "batteryLow": false, "tampered": false, "crossed": false,
    "bypassable": true, "bypassed": false, "bypassedByUser": 0, "bypassType": 0,
    "lossOfSupervision": false, "notReady": false
  }
}
```

`zonePhysicalType` / `zoneAlarmType` / `equipmentCode` are numeric and may let us
derive device_class more reliably than the current name-substring heuristic —
worth a mapping table once we have values from several sensor types.

**Arm semantics** (from the bridge): arm body is
`{partition, armStay, noExitDelay, noEntryDelay, bypassNotReadyZones,
silentEntryExit}`. `noEntryDelay=true` + `armStay=true` = "night". Disarm body is
`{userPIN, partition}`.

**Known limitation — `armed_night` is not observable.** `/status` returns only
`armState`+`isArmedStay`; night is indistinguishable from stay on read-back. The
integration must track "I issued night" locally, exactly as the bridge does.

---

## 2. Credentials: pairing (preferred) vs MITM (fallback)

### 2a. Pairing via the API — investigated, **not reproducible on this firmware**

The endpoint exists (`OPTIONS /api/v1/pair` → `Allow: POST,GET`), and an
authenticated `GET /api/v1/pair` returns the current controller
(`{"PairingKey":…,"deviceName":"SC Controller",…}`). The reported `PairingKey`
equals the `X-Pairing-Key` in `.env`. So far so promising — but enrolling a
*new* controller from scratch does not work through this API:

- **`POST /api/v1/pair` requires a valid `X-Api-Key`** (checked first; see §1). A
  brand-new controller has none — chicken-and-egg. `enrol` sends no api-key and
  gets `403 "Invalid API key"`, whether the panel is in System Pairing mode or
  not, and regardless of the pairing code.
- **The panel does not display a pairing code** in its UI (confirmed on the
  device). The 6-digit `X-Pairing-Key` we have came from the MITM capture, not
  from a screen. So there is no code to type into a fresh enrolment even if the
  api-key requirement were relaxed.
- **`POST /api/v1/pair` has a second gate: a panel-side "pairing authorized"
  mode.** With *valid* creds, an identity-preserving re-assert POST returns
  `403 {"Error":"Pairing not authorized"}` — note the different shape (`Error`,
  not `message`), i.e. it cleared auth and was refused by the pairing handler
  because the panel wasn't in System Pairing mode at the time. (The write was a
  verified clean no-op: pairing key and device name unchanged, existing creds
  still 200.)

Two independent gates on the pairing write, then:
1. auth middleware — valid `X-Api-Key` **and** `X-Pairing-Key` (our creds pass);
2. panel in an authorized pairing state — else `"Pairing not authorized"`.

Conclusion:
- **From-scratch bootstrap (no api-key) is blocked** — the middleware is not
  relaxed in listening mode, and the panel surfaces no code. `tools/pair.py enrol`
  is a dead end on this firmware.
- **Re-pairing WITH the creds we already have — tested 3×, gate 2 never opened.**
  Identity-preserving re-assert (2×) and a fresh `deviceName: "Home Assistant"`
  (1×, multi-pairing confirmed so no eviction risk), each with the panel reportedly
  in pairing mode: all returned `403 {"Error":"Pairing not authorized"}`. Existing
  creds verified intact after every attempt. So the panel isn't reaching the
  pairing-authorized state from the keypad steps taken, **or** that state relaxes
  the middleware only for an *unauthenticated new* device (which we can't drive
  without the real installer pairing screen). The remaining hypothesis — an
  *unauthenticated* `POST /api/v1/pair` during a genuinely-active window — has
  since been **tested and disproved** against the real System Pairing screen; see
  the System Pairing note below.

**Corroborating datapoint — the SP1 keypad pairing flow.** AlarmGrid's SP1
enrollment guide describes a Wi-Fi/LAN device paired from **Installer Toolbox →
System Configuration → Keypads** by a mutual button-press: press **Pair** under
*Device ID* on the panel, then **Pair** on the physical keypad. Two things it
confirms: (1) the panel surfaces **no pairing code** in this flow — enrollment
is button-press mutual-authorize, so the §2a hope for a screen that *reveals* a
code is likely misguided by design; (2) the SP1 arrives with **no prior
api-key** yet gets enrolled, which is a working existence proof of the single
untested hypothesis above — an *unauthenticated* new device enrolling during a
genuinely-active pairing window opened from a specific installer screen. Caveat:
the SP1 is a **keypad** (equipment code `1060`), a different device class from
the automation/SC controller our api-key belongs to, so this screen won't enroll
the controller. The actionable next probe is to hunt the Installer Toolbox for a
controller/automation/"Services" enrollment slot analogous to Keypads; if one
exists, pressing its **Pair** should open gate 2 for an automation device, and
an unauthenticated `POST /api/v1/pair` during that window becomes the test worth
running. Source:
<https://www.alarmgrid.com/faq/how-do-i-pair-the-2gig-sp1-keypad-with-the-2gig-gc3>

**The screen we were missing — `Installer Toolbox → System Pairing`.** The
official GC3/GC3e Install & Programming Guide (10023748A, p.37, "Pairing with a
System": *"This feature allows the panel to pair with approved third-party
systems"*) documents a **dedicated top-level Installer Toolbox item** — distinct
from the `System Configuration → Keypads` slot the SP1 uses:
1. Installer Toolbox → **System Pairing**.
2. On the *System - Pairing Mode* screen, press the **+** button to start.
3. The *System Pairing - Enter Key* screen appears and **"the system will listen
   for the pairing request from the third party system."**

We hoped this was the gate-2 window §2a couldn't open. It was tested via
`tools/pair.py probe` (unauthenticated `POST /api/v1/pair`, no `X-Api-Key`) with
the panel in **System Pairing** listen mode (`+` pressed) — **and it was rejected
at gate 1: `403 "Invalid API key"`.** So the listen window does **not** relax the
auth middleware; the panel demands a valid api-key even inside System Pairing,
before the request ever reaches the pairing handler. This **closes the last
untested hypothesis** from §2a: there is no panel-issued, from-scratch local
bootstrap on this firmware.

What the *Enter Key* screen actually is, then: **not** a panel-hands-you-a-key
step. A controller must **arrive already holding a valid api-key** (provisioned
out-of-band by the backend — Alarm.com / the `stratus` cloud found in the Mgmt
Cloud app, reached via the `gstun0X.corebrands.com` relays), and System Pairing
only *authorizes the association* of that already-credentialed controller. The
api-key originates in the cloud, not the panel — which is why every local
bootstrap attempt is chicken-and-egg by design.

Corollary — **keypad pairing is not a workaround.** The SP1/SP2 flow enrolls a
*secondary display* (equipment codes `1060`/`1061`) over Wi-Fi, on a separate
keypad protocol — not the `/api/v1` automation REST API `pygc3` speaks. Pairing
as a keypad would need a from-scratch client, yields a UI-mirror rather than
structured zone/partition/event state, and does not mint an automation api-key.
Wrong door, lower ceiling. The api-key is per-controller and cloud-issued, **not**
a value shared across systems — do not expect a universal/hardcoded key.

Source (System Pairing menu path, default installer code `1561`):
<https://2gig.com/wp-content/uploads/10023748a_x3-installandprogrammingguide.pdf>
(p.37). Bootstrap is now **ruled out, not parked** — MITM (§2b) is the only local
route to a real automation key; §2c stands, we already hold working creds.

**What this means practically:** credential *bootstrap* without MITM is not
achievable here today. But it doesn't matter much — see §2c: we already hold
durable working credentials, and that is all the integration needs.

`tools/pair.py show` (authenticated read) still works and is useful for
inspecting/confirming the current pairing.

### 2b. MITM — fallback for panels where pairing mode is inaccessible

If you can't reach the Installer Toolbox (no installer code, or the panel is
managed), capture the headers off a **functioning paired controller** instead.
This is how the current `.env` values were obtained.

`mitmproxy` in transparent mode, with the controller's traffic to the panel
redirected through it:

```yaml
  mitm:
    image: mitmproxy/mitmproxy:latest
    profiles: ["tools"]
    command: >
      mitmdump --mode transparent --ssl-insecure
      --set flow_detail=3
      -w /caps/gc3.flows
      "~d 192.168.1.25 & ~u /api/"
    volumes: [./caps:/caps]
    cap_add: [NET_ADMIN]
    networks: { 192_lan: {} }
```

Redirect one of these ways (pick per your network):
- **ARP spoof** the controller→panel path to the mitm host (ettercap/bettercap),
  transparent-proxy port 3000 → 8080; or
- **DNS/static route**: point the controller at the mitm host as the panel IP and
  have mitm forward to the real panel; or
- span/mirror the switch port if you have a managed switch.

Then extract the two headers:

```bash
mitmdump -nr caps/gc3.flows \
  | grep -iE 'X-Api-Key|X-Pairing-Key' | sort -u
```

Ethics/scope: only against your own panel and controller. The self-signed cert
and absent client validation are what make this trivial — noted as a security
observation, not a hardening ask.

### 2c. What we actually use: the credentials we already have

The captured `X-Api-Key` + `X-Pairing-Key` are **durable** — they authenticate
today and keep working after the SC100 is powered off (the bridge has been
running on them). Credential acquisition is therefore already solved for this
deployment; the pairing-bootstrap detour (§2a) was about *avoiding* the MITM
capture, not about getting working access, which we have.

**Recommendation, as built:** the integration's config flow takes the api-key +
pairing-key directly, typed in by the user. No live pairing step, and no reading
of `.env` — the integration has no business touching the bridge's config file,
and HA stores the credentials in its own config entry once entered. MITM (§2b)
is documented only as how one would obtain creds on a *fresh* panel where they
weren't already captured. `tools/pair.py enrol` stays as a documented
dead-end/experiment, `show` as a diagnostic.

Reviving API pairing would need one of: an installer-menu screen that reveals or
sets the pairing code, or a bootstrap endpoint/port that doesn't require a prior
api-key. Neither is found yet; parked, not blocking.

---

## 3. Integration architecture

```
custom_components/gc3/
  __init__.py            # setup entry: build client, coordinator, forward platforms
  manifest.json          # domain, name, requirements=[pygc3], iot_class=local_polling
  config_flow.py         # user / reauth / reconfigure steps + options flow
  coordinator.py         # DataUpdateCoordinator: poll /status + /zones, night flag,
                         #   supervision repair issues, HA state mapping
  const.py               # domain, config keys, defaults
  alarm_control_panel.py # the panel entity
  binary_sensor.py       # one per zone, added/removed as the panel's zones change
  entity.py              # shared base (device_info, availability)
  diagnostics.py         # redacted dump (Gold)
  strings.json / translations/en.json
  quality_scale.yaml     # per-rule status
```

Typed models are **not** duplicated here — `pygc3` owns them, which is the whole
point of splitting the library out. The one piece of state that cannot live in
the wire layer, the locally-remembered night flag, lives in the coordinator.

**Panel client library — `pygc3`** (separate package, so it's reusable and the
Platinum `async-dependency` rule is satisfiable):

- `aiohttp` (not `requests`) — async, injectable session (`inject-websession`).
- Methods: `async status()`, `async zones()`, `async arm(...)`, `async disarm(...)`,
  `async pair(...)`, `async get_pairing()`.
- Own exception types: `GC3AuthError` (→ reauth), `GC3ConnectionError` (→
  unavailable/retry). Maps HTTP 403 → auth, timeouts/conn → connection.
- `verify_ssl=False` baked in with a clear comment; the panel cert is self-signed.

**Coordinator:** single `DataUpdateCoordinator` fetching `/status` and `/zones`
each interval (default 3s, `appropriate-polling`). Exposes the parsed snapshot to
all entities. `runtime_data` **is** the coordinator (it already holds the client);
no `hass.data` juggling.

**Entities:**
- `alarm_control_panel.2gig_gc3` — state map identical to the bridge
  (`ha_alarm_state`), night tracked in coordinator memory.
- `binary_sensor` per zone — `has_entity_name=True`, name = `voiceDescriptor`,
  device_class from the mapping, extra_state_attributes = the status flags.
  Zone 0 / console is filtered by `pygc3` itself.
- All on one `device` (the panel).
- **Identity is keyed on `entry_id`, not a panel serial** — `unique_id` is
  `{entry_id}_alarm` / `{entry_id}_zone_{id}`, device identifiers
  `{(DOMAIN, entry_id)}`. The panel exposes no serial (§1), and `entry_id` is
  stable for the life of the entry, which is what unique ids need. The cost is
  that removing and re-adding the integration orphans the old entities.

**Config flow, as built.** The two-step host-then-credentials design was
collapsed into one: an unauthenticated reachability probe adds nothing, because
without a valid api-key the panel returns the same `403` whether it is healthy
or misconfigured (§1). One form, one real check.

1. `user` step: host, api-key, pairing-key, and optionally disarm PIN, port and
   partition. `test-before-configure` = an authenticated `GET /status` with the
   entered pair, so a typo fails in the dialog.
2. `unique_id` = **host**, not panel serial → `unique-config-entry`. The panel
   exposes no serial; `/status` returns two fields and nothing identifying (§1).
3. `reauth` step re-asks for just the two keys when the panel starts rejecting
   them, and merges them over the existing entry data.
4. `reconfigure` step re-runs the full form for an existing entry, aborting with
   `wrong_panel` if the host now points somewhere else.
5. Options flow: poll interval (1–60 s), no-exit-delay, bypass-not-ready.

---

## 4. Quality-scale roadmap

Each rule mapped to concrete work for *this* integration. Every exempt rule now
carries its reason in `quality_scale.yaml`.

### 🥉 Bronze — target for first release
Rewritten as-built 2026-08-02; the earlier version of this table still described
a panel serial and an event subscription, neither of which exists.

| Rule | As built |
|---|---|
| `config-flow` | One `user` step: host, api-key, pairing-key, optional PIN / port / partition. The two-step host-then-credentials split was dropped — an unauthenticated probe adds nothing, since the panel returns the same `403` whether it is healthy or misconfigured (§1, §3). `data_description` per field in strings.json |
| `test-before-configure` | Authenticated `GET /status` with the entered key pair, inside the flow, so a typo fails in the dialog rather than after setup |
| `test-before-setup` | `async_setup_entry` does a first refresh — `ConfigEntryNotReady` on connection failure, `ConfigEntryAuthFailed` on 403 |
| `unique-config-entry` | `unique_id` = **host**, lowercased. Not a serial: `/status` returns two fields and nothing identifying (§1) |
| `entity-unique-id` | Keyed on `entry_id`, not a serial — `{entry_id}_alarm`, `{entry_id}_zone_{id}`. Cost is that a remove-and-re-add orphans the old entities (§3) |
| `has-entity-name` | `True` on the base entity in `entity.py` |
| `runtime-data` | `entry.runtime_data` **is** the coordinator — it already holds the client. `GC3Data` is the coordinator's data payload (status + zones), not a runtime container |
| `appropriate-polling` | Coordinator interval, default 3 s, 1–60 s via options |
| `action-setup` / `docs-actions` | **exempt** — no custom actions; arm/disarm are the standard `alarm_control_panel` services, registered by that platform. The `armed_night` caveat is documented |
| `brands` | ⬜ **the one rule still open** — logo/icon not yet submitted to `home-assistant/brands` |
| `common-modules` | `entity.py` base, `coordinator.py` (which also holds the `GC3Data` dataclass). No separate `models.py` |
| `dependency-transparency` | `pygc3` pinned in manifest `requirements`, built from source in this repo, published to PyPI from a tagged commit |
| `docs-*` (high-level, install, removal) | README ported and expanded |
| `config-flow-test-coverage` | pytest over the user, reauth and reconfigure steps |
| `entity-event-setup` | **exempt** — polling only. The panel's SSE endpoint accepts subscribers but is a stub: a 14-byte banner on connect and never an event, verified across a full arm cycle (§6). There is nothing to subscribe to |

### 🥈 Silver — reliability
| Rule | Plan |
|---|---|
| `entity-unavailable` | mark unavailable when the coordinator's last update failed (bridge already does availability via MQTT LWT; here it's `CoordinatorEntity.available`) |
| `log-when-unavailable` | log once on transition to/from unreachable (the panel's ~55s post-restart connection refusal is expected — log at debug, not warning, after first) |
| `reauthentication-flow` | 403 → `ConfigEntryAuthFailed` → reauth step reruns pairing |
| `action-exceptions` | arm/disarm raise `HomeAssistantError` (translated) on panel failure |
| `parallel-updates` | `PARALLEL_UPDATES = 1` — panel is single-session and stingy about concurrency |
| `config-entry-unloading` | `async_unload_entry` closes the client session |
| `integration-owner` | add `codeowners` in manifest |
| `test-coverage` | ≥95%; mock `pygc3` |
| `docs-configuration-parameters`, `docs-installation-parameters` | document poll interval, partition, no-exit-delay, bypass options |

### 🥇 Gold — completeness
| Rule | Plan |
|---|---|
| `devices` | single panel device, all entities attached |
| `diagnostics` | dump status+zones with `api_key`/`pairing_key`/serials redacted |
| `discovery` | ✅ investigated — the panel advertises nothing over mDNS (§6); `exempt`, along with `discovery-update-info`. Panel IP is static in practice. |
| `dynamic-devices` / `stale-devices` | add zones that appear at runtime; remove zones deleted on the panel between polls |
| `entity-category` | mark keyfob / diagnostic-ish sensors appropriately |
| `entity-device-class` | door/window/motion/moisture/smoke/CO/garage_door — reuse the bridge's fixed mapping; refine via `zonePhysicalType` |
| `entity-disabled-by-default` | disable noisy/rare zones (e.g. keyfob) by default |
| `entity-translations`, `exception-translations`, `icon-translations` | translation files |
| `reconfiguration-flow` | change host/poll interval without re-adding |
| `repair-issues` | raise a Repair when the API key goes invalid, or a zone reports `lossOfSupervision`/`batteryLow` persistently |
| `docs-*` (examples, limitations, supported-devices/functions, troubleshooting, use-cases, data-update) | expand docs; the `armed_night`, SSE, and single-session notes are the headline limitations |
| `update` (firmware) | `n/a` — panel firmware isn't updatable via this API |

### 🏆 Platinum
| Rule | As built |
|---|---|
| `async-dependency` | `pygc3` is aiohttp-native end to end — no executor threads, no sync shim wrapped in `async_add_executor_job`. It was written for this integration (`pygc3/` in this repo) rather than adapted from a blocking library |
| `inject-websession` | The client takes a session and never creates one. Both `__init__.py` and `config_flow.py` pass `async_get_clientsession(hass, verify_ssl=False)` — the panel serves a self-signed cert on `:3000` (§1), and HA keeps that non-verifying session separate from the verifying one |
| `strict-typing` | `mypy strict = true` over both the integration and `pygc3`, enforced in CI (`test.yml` runs `mypy` for each). `pygc3` ships `py.typed`. One deviation, commented in `pyproject.toml`: `no_implicit_reexport = false`, because HA's component packages re-export their constants without `__all__` and core integrations import them from the package root. Dataclasses live in `coordinator.py`; there is no `models.py` |

**Deliverable of this section:** a `quality_scale.yaml` in the component with every
rule marked `done` / `todo` / `exempt` (+ reason), which is the file reviewers read.
✅ **Written.** One rule remains open, and it is external rather than code:
`brands` (logo not yet submitted to `home-assistant/brands`). `discovery` and
`discovery-update-info` were the other two and are now closed as `exempt` — the
panel advertises nothing over mDNS (§6). Everything else is `done` or `exempt`
with a stated reason.

---

## 5. Phased milestones

1. **`pygc3` library** ✅ done — API calls extracted from `gc3_bridge.py` into an
   async, typed client with its own exceptions. 27 tests, mypy --strict clean,
   ~98% coverage, real (redacted) fixtures.
2. ~~Confirm pairing~~ — **investigated, parked.** API bootstrap not reproducible
   on this firmware (§2a); panel shows no code, `/api/v1/pair` needs a prior
   api-key. We use the existing durable creds (§2c) instead.
3. **Bronze integration** ✅ done — config flow with credential verification,
   coordinator, both platforms, entity parity with the bridge. Runs from
   `custom_components/`.
4. **Silver** ✅ done — reauth flow, `entity-unavailable`, translated action
   exceptions, `PARALLEL_UPDATES`, clean entry unload. 58 tests, 98.9% coverage
   (95% enforced in CI).
5. **Gold** ✅ done — diagnostics ✅, dynamic/stale zones ✅, supervision repair
   issue ✅, exception/issue translations ✅, reconfigure flow ✅, full docs ✅,
   discovery investigated and exempted ✅ (see §6).
6. **Platinum** ✅ done — `pygc3` is aiohttp-native, HA's shared session is
   injected, `mypy --strict` clean on both packages, `quality_scale.yaml`
   written.

   **Core submission is deliberately not started.** Ship as a HACS custom repo
   first: this has run against exactly one panel on one firmware, and several
   findings (§2a's pairing gates, §6's stub SSE endpoint and absent mDNS, the
   zone-descriptor device-class heuristic) want confirmation from other people's
   hardware before a core PR is honest. `brands` is now the only concrete
   blocker.

The bridge keeps running throughout; the integration only replaces it once
entity parity is verified against the live panel.

### Verification status

Everything above is verified by the test suite, `hassfest`, `mypy --strict` and
`ruff` — against a **mocked** panel. The tests deliberately fake at the `pygc3`
boundary, so they prove the integration's logic, not the panel's behaviour.
End-to-end confirmation against the live GC3 (arm/disarm round-trips, zone
churn, the post-reboot connection refusal) is the remaining validation step and
has not been done since the rewrite.

---

## 6. Open questions / risks

**Resolved during the build:**

- ~~Does enrolling a new controller evict the SC100?~~ Multi-pairing confirmed
  (§2a): a fresh `deviceName` was accepted as a distinct pairing with no
  eviction, and existing creds stayed valid through every attempt.
- ~~POST auth during pairing mode — pairing code alone, or also an api key?~~
  Resolved: the api-key is checked **first**, unconditionally. There is no
  relaxed mode for an unauthenticated enrolment.
- ~~`armed_night`~~ — accepted as inferred-not-observed, tracked in the
  coordinator, cleared on any disarm, and documented as a known limitation in
  the README.

**Still open:**

- ~~**mDNS/zeroconf discovery**~~ — **RESOLVED 2026-08-02: the panel advertises
  nothing.** Gold `discovery` and `discovery-update-info` are marked `exempt`.
  Probed with `tools/mdns_probe.py`, three independent methods in one run:

  | Method | Result |
  |---|---|
  | Passive sniff of `224.0.0.251:5353` | 0 packets from the panel — against 18 from 5 other hosts in the same window, so the capture was live |
  | Unicast mDNS straight at the panel | No reply to any of 6 queries: the `_services._dns-sd._udp.local` enumeration PTR, the reverse `in-addr.arpa` PTR, and 4 guessed `.local` A records |
  | Full service-type enumeration | 26 service types / 176 resolved records LAN-wide, **none** from the panel |

  The panel was demonstrably online throughout (`tcp/3000` open in 1ms, live ARP
  entry), and avahi's cache — running continuously and caching every other host
  — holds no entry for it. Not answering even a *direct unicast* query is the
  telling part; most mDNS responders will.

  **Caveat, stated rather than buried:** this covered steady state, not a
  boot-time announcement burst. A stack that announces only on network join and
  then goes quiet for the record TTL (often 75 min) would not have been caught.
  Rebooting a live alarm panel to close that gap was judged not worth it. If it
  ever is:

  ```
  python3 tools/mdns_probe.py --seconds 300    # then power-cycle the panel
  ```

  Exit code 0 = something found, 1 = clean negative, 2 = capture was empty.
- ~~**SSE `/api/v1/events`**~~ — **RESOLVED 2026-08-02: the endpoint is a stub.**
  The integration stays `local_polling`; `iot_class` does not change. Two probe
  runs with `tools/sse_probe.py` (180s each, zone tripped and a full arm cycle
  performed during the window):

  | Question | Result |
  |---|---|
  | Exists? | Yes — `OPTIONS` → `200 Allow: GET`; unauthenticated → `403 {"message":"Invalid API key"}` |
  | Connects? | Yes — `200`, `Content-Type: text/event-stream`, chunked, `Connection: keep-alive` |
  | Single-subscriber? | **No — the long-standing assumption was wrong.** Eight concurrent subscribers were all served `200`, none evicted. The SC100 was never the reason. |
  | Starves the REST API? | **No.** 18/18 `/status` polls succeeded while streams were held. |
  | Pushes events? | **No.** Every subscriber gets the literal 14-byte banner `b"Event stream:\n"` and nothing else, ever. |

  The banner is the finding: a bare label, byte-identical across every variant
  tried (`?partition=1`, `?partition=1&allPartitions=true`, `?subscribe=all`,
  `?topic=zones`, `Accept: */*`, `Last-Event-ID: 0`, and the bare GET), with no
  `event:`/`data:` framing and no traffic across a `ready → not_ready → arming →
  armed` cycle that the concurrent `/status` polls captured in full. A sub-path
  (`/api/v1/events/1`) returns the usual `404 Endpoint Not Found`, so the router
  is working normally — this handler writes a header and was never wired to the
  panel's event bus.

  **Do not spend more time here.** The remaining hypothesis (an undocumented
  argument nobody has guessed) is not worth chasing against a handler that emits
  a placeholder string. Re-run `--variants` only if a firmware update ships:

  ```
  export PANEL_URL=https://<panel-ip>:3000 API_KEY=... PAIRING_KEY=...
  pygc3/.venv/bin/python tools/sse_probe.py --seconds 120 --variants
  ```

  If it ever does emit, adopt `local_push` only if events arrive **and** the REST
  polls all still succeed, and keep a slow poll as a safety net regardless (the
  panel refuses connections for ~a minute after reboot, and a dropped stream must
  not silently freeze state).
- **Does re-pairing rotate the pairing code?** Never observed to (the code was
  unchanged across all three attempts), but all three were refused at gate 2,
  so a *successful* re-pair remains untested. Capture `GET /api/v1/pair` before
  and after if one ever succeeds.
- **Device-class heuristic generality** — the descriptor mapping is derived from
  one household's zone names. `zonePhysicalType` / `zoneAlarmType` /
  `equipmentCode` are numeric and would be more reliable, but we only have
  values from one panel's sensor mix. Collect values from other installs before
  switching.
- **Firmware spread** — every finding in §1 and §2 comes from a single GC3 on a
  single firmware. Treat the API surface as observed, not specified.
