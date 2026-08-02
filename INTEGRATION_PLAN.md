# 2GIG GC3 → Home Assistant integration — build plan

Turn the working `gc3_bridge.py` MQTT bridge into a first-class Home Assistant
integration (`gc3`), built to the
[Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/).

The bridge stays as the reference implementation and fallback; this plan is about
packaging the same panel API behind a native config-flow integration.

**Status: built.** `pygc3` and the integration are both complete, tested and
lint/type clean; `quality_scale.yaml` records every rule. What is *not* done is
soak-testing against the live panel since the rewrite, plus three external items
(brands logo, the mDNS question, the SSE retest) — see §5 and §6. Sections 1 and
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
| — | `/api/v1/events` | SSE stream | unused; panel appears to serve a single subscriber (the SC100). Revisit once SC100 is fully removed. |

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
  without the real installer pairing screen). The single remaining untested
  hypothesis: an *unauthenticated* `POST /api/v1/pair` during a genuinely-active
  window. Parked — low reward per §2c, not worth more blind timing trials on a
  live alarm.

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

Each rule mapped to concrete work for *this* integration. `n/a` items still need
the explicit `quality_scale.yaml` exemption comment.

### 🥉 Bronze — target for first release
| Rule | Plan |
|---|---|
| `config-flow` | Host + credentials steps above; `data_description` in strings.json |
| `test-before-configure` | `GET /status` reachability check in the flow |
| `test-before-setup` | `async_setup_entry` does a first fetch, raises `ConfigEntryNotReady` on conn fail / `ConfigEntryAuthFailed` on 403 |
| `unique-config-entry` | `unique_id` = panel serial |
| `entity-unique-id` | panel: `{serial}_alarm`; zones: `{serial}_zone_{id}` |
| `has-entity-name` | `True` on the base entity |
| `runtime-data` | `entry.runtime_data = GC3Data(client, coordinator)` |
| `appropriate-polling` | coordinator interval, default 3s, user-configurable |
| `action-setup` / `docs-actions` | arm/disarm exposed via standard `alarm_control_panel` services; document the night caveat |
| `brands` | submit `gc3` logo/icon to `home-assistant/brands` |
| `common-modules` | `entity.py` base, `coordinator.py`, `models.py` |
| `dependency-transparency` | `pygc3` pinned in manifest `requirements` |
| `docs-*` (high-level, install, removal) | port + expand this repo's README |
| `entity-event-setup`, `config-flow-test-coverage` | subscribe in `async_added_to_hass`; pytest for the flow |

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
| `discovery` | GC3 advertises over mDNS? **investigate** — if it does, add zeroconf discovery; else document `n/a`. (Panel IP is static in practice.) |
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
| Rule | Plan |
|---|---|
| `async-dependency` | `pygc3` is aiohttp-native |
| `inject-websession` | client accepts `async_get_clientsession(hass)` |
| `strict-typing` | full annotations; add to HA's `.strict-typing`; `models.py` dataclasses |

**Deliverable of this section:** a `quality_scale.yaml` in the component with every
rule marked `done` / `todo` / `exempt` (+ reason), which is the file reviewers read.
✅ **Written.** Three rules remain open, all of them external or investigative
rather than code: `brands` (logo not yet submitted to `home-assistant/brands`)
and `discovery` / `discovery-update-info` (unknown whether the panel advertises
over mDNS). Everything else is `done` or `exempt` with a stated reason.

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
5. **Gold** — diagnostics ✅, dynamic/stale zones ✅, supervision repair issue ✅,
   exception/issue translations ✅, reconfigure flow ✅, full docs ✅.
   **Discovery is the one open item** (see §6).
6. **Platinum** ✅ done — `pygc3` is aiohttp-native, HA's shared session is
   injected, `mypy --strict` clean on both packages, `quality_scale.yaml`
   written.

   **Core submission is deliberately not started.** Ship as a HACS custom repo
   first: this has run against exactly one panel on one firmware, and several
   findings (§2a's pairing gates, the SSE single-subscriber behaviour, the
   zone-descriptor device-class heuristic) want confirmation from other people's
   hardware before a core PR is honest. `brands` and the discovery question are
   the concrete blockers.

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

- **mDNS/zeroconf discovery** — untested. Determines whether Gold `discovery`
  becomes a feature or a documented exemption. Panel addresses are static in
  practice, so this is convenience, not function. *This is the only thing
  standing between the current state and a fully-resolved Gold.*
- **SSE `/api/v1/events`** — currently single-subscriber, held by the SC100.
  Once that controller is off, retest: push would remove polling entirely and
  change `iot_class` to `local_push`. Worth doing before any core submission,
  since it would change the integration's shape.
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
