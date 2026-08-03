#!/usr/bin/env python3
"""
GC3 SSE probe -- answer the open question on `/api/v1/events` (read-only).

`/api/v1/events` was long assumed to be single-subscriber, with the Elan SC100
holding the only slot. The SC100 is decommissioned, and this probe re-tested the
endpoint from scratch to decide whether the integration can move from
`local_polling` to `local_push`. It cannot -- but not for the assumed reason
(see MEASURED below).

It answers, in order:

  1. Does the endpoint exist and what does it answer with?  (OPTIONS, then GET;
     unauthenticated first, so a 403 "Invalid API key" can be told apart from a
     404 "Endpoint Not Found" -- the rest of this API returns the latter for
     anything it does not implement.)
  2. What does it actually speak? Content-Type, whether frames are real SSE
     (`event:` / `data:` / `id:` / `retry:`) or newline-delimited JSON, and what
     a payload looks like.
  3. Does holding the stream cost us the REST API? `/api/v1/status` is polled
     throughout; the panel is known to be stingy about concurrency, and a push
     transport that starves arm/disarm is not worth having.
  4. Is it still single-subscriber? A second stream is opened partway through
     (`--second-subscriber`) to see whether the panel serves both, refuses the
     newcomer, or silently drops the incumbent.
  5. Is it alive? Silence is measured including the tail, so a stream that greets
     you and then says nothing for 90s reports 90s -- not 0.

MEASURED 2026-08-02 -- QUESTION CLOSED: the endpoint is a stub. It exists,
accepts *many* concurrent subscribers (the SC100 was never the reason it looked
single-subscriber), does not disturb the REST API, sends every subscriber the
literal 14-byte banner b"Event stream:\n" -- and then nothing at all, through
zone faults and a full arm cycle. Eight `--variants` (partition, subscribe,
topic, Accept, Last-Event-ID, sub-path) all got the identical banner, so it is a
handler that writes a header and was never wired to the panel's event bus.

The integration therefore stays `local_polling`. Keep this script for one job:
re-run `--variants` after a firmware update to see whether the stub grew a
body.

Nothing here mutates panel state -- every request is a GET.

USAGE
    export PANEL_URL=https://<panel-ip>:3000
    export API_KEY=... PAIRING_KEY=...
    pygc3/.venv/bin/python tools/sse_probe.py --seconds 120 --second-subscriber

    # or fully explicit
    pygc3/.venv/bin/python tools/sse_probe.py \
        --panel https://<panel-ip>:3000 --api-key ... --pairing-key ... \
        --seconds 180 --second-subscriber --raw

    # hunt for whatever makes it emit -- all candidates at once
    pygc3/.venv/bin/python tools/sse_probe.py --seconds 120 --variants

WHILE IT RUNS: trip a zone (open a door), and arm/disarm from the keypad. The
log is timestamped, so what the panel pushes -- and how fast -- lines up against
the `/status` polls printed alongside it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field

import aiohttp

DEFAULT_PATH = "/api/v1/events"
# Anything the panel does not implement answers with this, so it is the tell for
# "endpoint absent" vs "endpoint present but refusing us".
NOT_FOUND_MARKER = "Endpoint Not Found"

T0 = time.monotonic()


def log(msg: str) -> None:
    print(f"[{time.monotonic() - T0:7.2f}s] {msg}", flush=True)


@dataclass
class StreamResult:
    """What one subscriber saw."""

    label: str
    connected: bool = False
    status: int | None = None
    content_type: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    frames: int = 0
    bytes_read: int = 0
    first_frame_at: float | None = None
    last_frame_at: float | None = None
    max_gap: float = 0.0
    sse_fields: set[str] = field(default_factory=set)
    samples: list[str] = field(default_factory=list)
    error: str = ""
    closed_by_server: bool = False
    residue: str = ""
    silence_at_close: float = 0.0
    open_for: float = 0.0
    # frames minus anything the panel volunteered on connect -- this, not
    # `frames`, is what decides whether the endpoint actually pushes.
    events: int = 0
    preamble: str = ""
    chunks: int = 0
    read_timed_out: bool = False


def _record_frame(
    res: StreamResult, raw: bytes, label: str, gap: float, *, preamble: bool = False
) -> None:
    """Count one framed payload and classify its fields.

    `preamble` marks anything that arrived in the very first chunk. A greeting the
    panel emits on connect is not an event, and counting it as one would report a
    stub stream as a working push transport.
    """
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return
    res.frames += 1
    if preamble:
        res.preamble = text[:200]
    else:
        res.events += 1
    for line in text.splitlines():
        name = line.split(":", 1)[0].strip()
        if name in ("event", "data", "id", "retry"):
            res.sse_fields.add(name)
        elif line.startswith(":"):
            res.sse_fields.add("comment(keepalive)")
    log(f"[{label}] +{gap:5.2f}s frame#{res.frames}: {text[:400]}")
    if len(res.samples) < 8:
        res.samples.append(text[:1500])


def headers_for(api_key: str | None, pairing_key: str) -> dict[str, str]:
    h = {"Accept": "text/event-stream", "Cache-Control": "no-cache"}
    if pairing_key:
        h["X-Pairing-Key"] = pairing_key
    if api_key:
        h["X-Api-Key"] = api_key
    return h


async def probe_endpoint(
    session: aiohttp.ClientSession, base: str, path: str, args: argparse.Namespace
) -> None:
    """Phase 1 -- existence and auth shape, before committing to a long read."""
    log("=== PHASE 1: endpoint shape ===")

    # OPTIONS only -- deliberately no HEAD. Most servers route HEAD to the GET
    # handler, so a HEAD here would open (and on a single-subscriber panel,
    # occupy) the very stream phase 2 is about to test.
    try:
        async with session.options(
            base + path,
            headers=headers_for(args.api_key, args.pairing_key),
            ssl=False,
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            allow = r.headers.get("Allow", "-")
            log(f"OPTIONS {path} -> HTTP {r.status}  Allow: {allow}")
    except (TimeoutError, aiohttp.ClientError) as err:
        log(f"OPTIONS {path} -> {type(err).__name__}: {err}")

    # Unauthenticated GET: distinguishes 403 (exists, wants creds) from
    # 404 "Endpoint Not Found" (not implemented on this firmware).
    try:
        async with session.get(
            base + path,
            headers={"Accept": "text/event-stream"},
            ssl=False,
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            body = (await r.text())[:200]
            verdict = (
                "NOT IMPLEMENTED on this firmware"
                if NOT_FOUND_MARKER in body
                else "exists, refuses unauthenticated (expected)"
                if r.status == 403
                else "unexpected -- see body"
            )
            log(f"GET (no auth) -> HTTP {r.status}: {body!r}")
            log(f"  => {verdict}")
    except (TimeoutError, aiohttp.ClientError) as err:
        # A hang here is itself informative: the panel may accept and hold the
        # connection open without ever authenticating it.
        log(f"GET (no auth) -> {type(err).__name__}: {err} (endpoint may be streaming)")


async def stream(
    session: aiohttp.ClientSession,
    base: str,
    path: str,
    args: argparse.Namespace,
    *,
    label: str,
    seconds: float,
    delay: float = 0.0,
    retries: int = 0,
    params: dict[str, str] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> StreamResult:
    """Hold one subscriber open for `seconds`, logging everything it receives.

    `retries` re-attempts a *refused* connection after a pause: if the panel is
    single-subscriber it may still be releasing a slot from an earlier probe, and
    "refused once" is not the same finding as "refused persistently".
    """
    res = StreamResult(label=label)
    if delay:
        await asyncio.sleep(delay)

    if params is None:
        params = {"partition": str(args.partition)} if args.partition else None
    headers = headers_for(args.api_key, args.pairing_key)
    headers.update(extra_headers or {})
    log(f"[{label}] connecting {path}" + (f" params={params}" if params else "")
        + (f" +headers={extra_headers}" if extra_headers else ""))
    deadline = time.monotonic() + seconds

    try:
        # total=None: a stream is supposed to stay open. sock_read bounds how long
        # we sit on a silent socket, which is what tells a hung connection apart
        # from a live one that simply has nothing to say.
        async with session.get(
            base + path,
            params=params,
            headers=headers,
            ssl=False,
            timeout=aiohttp.ClientTimeout(
                total=None, sock_connect=8, sock_read=args.read_timeout or None
            ),
        ) as r:
            res.status = r.status
            res.headers = dict(r.headers)
            res.content_type = r.headers.get("Content-Type", "")
            log(f"[{label}] HTTP {r.status}  Content-Type: {res.content_type or '-'}")
            for k in ("Transfer-Encoding", "Connection", "Cache-Control",
                      "Content-Length"):
                if k in r.headers:
                    log(f"[{label}]   {k}: {r.headers[k]}")

            if r.status >= 400:
                res.error = (await r.text())[:300]
                log(f"[{label}] refused: {res.error!r}")
                if retries > 0 and time.monotonic() + args.retry_after < deadline:
                    log(f"[{label}] retrying in {args.retry_after:.0f}s "
                        f"({retries} left) in case a slot is still being released")
                    await asyncio.sleep(args.retry_after)
                    return await stream(
                        session, base, path, args,
                        label=label,
                        seconds=deadline - time.monotonic(),
                        retries=retries - 1,
                        params=params,
                        extra_headers=extra_headers,
                    )
                return res

            res.connected = True
            # Framing is NOT taken from the Content-Type. This panel advertises
            # text/event-stream and then sent 14 bytes containing no blank line at
            # all, so trusting the header made the payload invisible. Blank-line
            # framing is tried first; anything still unterminated is re-tried as
            # single-newline (NDJSON) framing, and whatever never frames at all is
            # reported verbatim as residue rather than silently dropped.
            blank_framed = args.framing in ("auto", "blank")
            buf = b""
            last = time.monotonic()
            opened_at = last

            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                try:
                    chunk = await asyncio.wait_for(
                        r.content.read(4096), timeout=remaining
                    )
                except TimeoutError:
                    # Two very different events land here, and conflating them
                    # previously reported a 90s aiohttp sock_read timeout as
                    # "deadline reached", silently truncating a 180s window to 90s.
                    if time.monotonic() < deadline - 1:
                        res.read_timed_out = True
                        log(f"[{label}] sock_read timeout after {args.read_timeout}s "
                            f"of silence -- OUR limit, not the panel's; it may well "
                            f"have kept the connection open. Raise --read-timeout "
                            f"to hold longer.")
                    break
                if not chunk:
                    res.closed_by_server = True
                    log(f"[{label}] server closed the stream")
                    break

                now = time.monotonic()
                gap = now - last
                last = now
                res.chunks += 1
                res.bytes_read += len(chunk)
                res.max_gap = max(res.max_gap, gap)
                if res.first_frame_at is None:
                    res.first_frame_at = now - T0
                res.last_frame_at = now - T0

                # Always show the bytes. Hiding them behind --raw is what made the
                # panel's 14-byte preamble unexplainable on the first real run.
                limit = 2000 if args.raw else 300
                log(f"[{label}] +{gap:5.2f}s raw {len(chunk)}B {chunk[:limit]!r}")

                buf += chunk
                sep = b"\n\n" if blank_framed else b"\n"
                while sep in buf:
                    frame, _, buf = buf.partition(sep)
                    _record_frame(res, frame, label, gap, preamble=res.chunks == 1)

            # Whatever is left never satisfied the framing we chose. Re-frame it by
            # line, and if even that leaves something, print it raw -- an
            # unterminated preamble is a finding, not noise.
            if buf:
                if blank_framed and b"\n" in buf:
                    log(f"[{label}] residue did not blank-line frame; "
                        f"re-framing by line")
                    for line in buf.split(b"\n"):
                        if line.strip():
                            _record_frame(res, line, label, 0.0,
                                          preamble=res.chunks <= 1)
                    buf = b""
                else:
                    res.residue = buf.decode("utf-8", "replace")
                    log(f"[{label}] UNFRAMED RESIDUE ({len(buf)}B): {buf!r}")

            # Silence has to include the tail: max_gap only ever saw gaps *between*
            # arrivals, so a stream that goes quiet and stays quiet scored 0.0.
            now = time.monotonic()
            res.silence_at_close = now - last
            res.max_gap = max(res.max_gap, res.silence_at_close)
            res.open_for = now - opened_at
            if res.connected and not res.closed_by_server and not res.read_timed_out:
                log(f"[{label}] deadline reached, closing our end "
                    f"(silent for the last {res.silence_at_close:.0f}s)")

    except TimeoutError:
        res.error = f"timed out with no data (read-timeout {args.read_timeout}s)"
        log(f"[{label}] {res.error}")
    except aiohttp.ClientError as err:
        res.error = f"{type(err).__name__}: {err}"
        log(f"[{label}] {res.error}")
    return res


async def poll_status(
    session: aiohttp.ClientSession,
    base: str,
    args: argparse.Namespace,
    *,
    seconds: float,
    every: float,
) -> tuple[int, int]:
    """Phase 3 -- does the REST API keep working while a stream is held open?"""
    ok = fail = 0
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            async with session.get(
                base + "/api/v1/status",
                headers=headers_for(args.api_key, args.pairing_key),
                ssl=False,
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                body = (await r.text())[:120]
                if r.ok:
                    ok += 1
                else:
                    fail += 1
                log(f"[poll]  /status -> {r.status} {body}")
        except (TimeoutError, aiohttp.ClientError) as err:
            fail += 1
            log(f"[poll]  /status -> {type(err).__name__}: {err}")
        await asyncio.sleep(every)
    return ok, fail


# Candidate ways of asking the panel to actually send something. The endpoint
# accepts the connection and then says nothing, so the question is whether it
# wants an argument we have not guessed. These run CONCURRENTLY -- the panel was
# measured serving multiple subscribers at once, so one zone trip tests them all.
VARIANTS: tuple[tuple[str, str, dict[str, str] | None, dict[str, str] | None], ...] = (
    ("bare",        DEFAULT_PATH, None, None),
    ("partition1",  DEFAULT_PATH, {"partition": "1"}, None),
    ("allparts",    DEFAULT_PATH, {"partition": "1", "allPartitions": "true"}, None),
    ("subscribeall", DEFAULT_PATH, {"subscribe": "all"}, None),
    ("accept-any",  DEFAULT_PATH, None, {"Accept": "*/*"}),
    ("last-event",  DEFAULT_PATH, None, {"Last-Event-ID": "0"}),
    ("zones-topic", DEFAULT_PATH, {"topic": "zones"}, None),
    ("subpath",     DEFAULT_PATH + "/1", None, None),
)


async def run_variants(
    session: aiohttp.ClientSession, base: str, args: argparse.Namespace
) -> None:
    """Race every candidate subscription form against one zone trip."""
    log(f"=== VARIANTS: {len(VARIANTS)} concurrent subscribers, {args.seconds}s ===")
    log("=== trip a zone NOW -- whichever variant speaks is the answer ===")
    results = await asyncio.gather(
        *(
            stream(session, base, path, args, label=label,
                   seconds=args.seconds, params=params, extra_headers=hdrs)
            for label, path, params, hdrs in VARIANTS
        )
    )
    print("\n" + "=" * 68)
    print("VARIANT RESULTS")
    print("=" * 68)
    print(f"  {'variant':<14} {'http':>5} {'bytes':>7} {'events':>7}  preamble/residue")
    for res in results:
        status = str(res.status or "-")
        row = f"  {res.label:<14} {status:>5} {res.bytes_read:>7} {res.events:>7}"
        extra = res.residue or res.preamble
        if extra:
            row += f"  {extra[:40]!r}"
        print(row)
    talkers = [r for r in results if r.events]
    print()
    if talkers:
        print("  These variants produced real traffic:")
        for r in talkers:
            print(f"    {r.label}: {r.events} events ({r.frames} frames, "
                  f"{r.bytes_read}B)")
            for s in r.samples[:3]:
                print(f"      {s[:200]}")
        print("\n  => build the push transport on the winning variant.")
    else:
        print("  No variant produced anything beyond the connect-time preamble.")
        print("  => the endpoint is a stub on this firmware. Stay on local_polling,")
        print("     record it in §6, and stop spending time here.")


def verdict(
    primary: StreamResult, second: StreamResult | None, polls: tuple[int, int]
) -> None:
    ok, fail = polls
    print("\n" + "=" * 68)
    print("VERDICT")
    print("=" * 68)

    if not primary.connected:
        print(f"  stream          : NOT USABLE -- HTTP {primary.status} "
              f"{primary.error}")
        print("  => stays local_polling. Record the status/body above in §6 of")
        print("     INTEGRATION_PLAN.md and leave the integration on polling.")
        return

    print(f"  stream          : connected, HTTP {primary.status}")
    print(f"  content-type    : {primary.content_type or '(none sent)'}")
    print(f"  frames / bytes  : {primary.frames} / {primary.bytes_read}")
    print(f"  actual events   : {primary.events}  (frames after the connect chunk)")
    if primary.preamble:
        print(f"  connect preamble: {primary.preamble!r}")
    first = f"{primary.first_frame_at:.2f}s" if primary.first_frame_at else "never"
    print(f"  first bytes at  : {first}")
    print(f"  held open for   : {primary.open_for:.0f}s")
    print(f"  longest silence : {primary.max_gap:.0f}s "
          f"(last {primary.silence_at_close:.0f}s of it at close)")
    fields = ", ".join(sorted(primary.sse_fields)) or "(nothing framed)"
    print(f"  frame fields    : {fields}")
    if primary.residue:
        print(f"  UNFRAMED bytes  : {primary.residue!r}")
    print(f"  server closed   : {primary.closed_by_server}")
    if primary.read_timed_out:
        print("  ended early     : our --read-timeout fired, not the panel")
    print(f"  /status polls   : {ok} ok, {fail} failed while streaming")

    if second is not None:
        outcome = (
            "ACCEPTED" if second.connected
            else f"REFUSED (HTTP {second.status} {second.error})"
        )
        incumbent = "WAS DROPPED" if primary.closed_by_server else "survived"
        print(f"  2nd subscriber  : {outcome}; incumbent {incumbent}")

    print("\n  Reading:")
    if primary.events == 0 and primary.bytes_read:
        print(f"    - Greeted us with {primary.bytes_read}B on connect, then said")
        print(f"      nothing for {primary.silence_at_close:.0f}s. If the panel")
        print("      changed state in that window (see the /status lines above), it")
        print("      accepts subscribers but never pushes -- a stub, or it wants a")
        print("      subscription argument we have not guessed.")
        print("      Next: --variants, to race candidate params/headers side by side.")
    elif primary.events == 0:
        print("    - Connected but not a single byte. Re-run and trip a zone; if it")
        print("      stays empty the endpoint is a stub.")
    if fail:
        print("    - REST calls failed while the stream was held: push would starve")
        print("      arm/disarm. Do not switch to local_push on this evidence.")
    elif primary.events:
        print("    - Real events arrived and /status kept working: local_push is on")
        print("      the table.")
        print("      Next: confirm every state the coordinator needs is represented in")
        print("      the payloads below, and keep polling as a slow safety net.")
    if primary.samples:
        print("\n  Payload samples:")
        for s in primary.samples:
            try:
                body = s.split("data:", 1)[1] if "data:" in s else s
                print("    " + json.dumps(json.loads(body.strip()))[:400])
            except (ValueError, IndexError):
                print("    " + s.replace("\n", "\\n")[:400])


async def main_async(args: argparse.Namespace) -> int:
    base = args.panel.rstrip("/")
    log(f"panel {base}  path {args.path}  window {args.seconds}s")

    connector = aiohttp.TCPConnector(ssl=False, limit=10, force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        # Baseline: creds good, panel healthy, before we blame the stream for anything.
        try:
            async with session.get(
                base + "/api/v1/status",
                headers=headers_for(args.api_key, args.pairing_key),
                ssl=False,
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                log(f"baseline /status -> HTTP {r.status} {(await r.text())[:120]}")
                if not r.ok:
                    log("baseline unhealthy -- fix creds/connectivity first; aborting.")
                    return 2
        except (TimeoutError, aiohttp.ClientError) as err:
            log(f"baseline /status -> {type(err).__name__}: {err}; aborting.")
            return 2

        await probe_endpoint(session, base, args.path, args)

        # Let the panel drop anything phase 1 left half-open before the real test.
        await asyncio.sleep(args.settle)

        if args.variants:
            await run_variants(session, base, args)
            return 0

        log(f"=== PHASE 2/3: holding the stream {args.seconds}s "
            f"(trip a zone / arm / disarm now) ===")
        tasks = [
            stream(session, base, args.path, args, label="sub1",
                   seconds=args.seconds, retries=1),
            poll_status(session, base, args, seconds=args.seconds,
                        every=args.poll_every),
        ]
        if args.second_subscriber:
            delay = args.seconds / 3
            log(f"=== PHASE 4: a second subscriber joins at +{delay:.0f}s ===")
            tasks.append(
                stream(
                    session,
                    base,
                    args.path,
                    args,
                    label="sub2",
                    seconds=args.seconds - delay,
                    delay=delay,
                )
            )

        results = await asyncio.gather(*tasks)
        primary: StreamResult = results[0]
        polls: tuple[int, int] = results[1]
        second: StreamResult | None = results[2] if args.second_subscriber else None

    verdict(primary, second, polls)
    return 0 if primary.connected else 1


def main() -> int:
    p = argparse.ArgumentParser(
        description="Probe the GC3 panel's /api/v1/events SSE endpoint (read-only).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--panel", default=os.environ.get("PANEL_URL"),
                   help="https://<panel-ip>:3000 (or $PANEL_URL)")
    p.add_argument("--api-key", default=os.environ.get("API_KEY"),
                   help="X-Api-Key (or $API_KEY)")
    p.add_argument("--pairing-key", default=os.environ.get("PAIRING_KEY"),
                   help="X-Pairing-Key (or $PAIRING_KEY)")
    p.add_argument("--path", default=DEFAULT_PATH, help=f"default {DEFAULT_PATH}")
    p.add_argument("--seconds", type=float, default=120.0,
                   help="how long to hold the stream (default 120)")
    p.add_argument("--poll-every", type=float, default=10.0,
                   help="/status poll interval while streaming (default 10)")
    p.add_argument("--read-timeout", type=float, default=0.0,
                   help="give up early if the socket is silent this long; "
                        "0 (default) means hold for the full --seconds window")
    p.add_argument("--settle", type=float, default=3.0,
                   help="pause between phase 1 and the real stream (default 3)")
    p.add_argument("--retry-after", type=float, default=5.0,
                   help="if the stream is refused, retry once after this many s")
    p.add_argument("--partition", type=int, default=0,
                   help="send ?partition=N (0 = omit, the default)")
    p.add_argument("--second-subscriber", action="store_true",
                   help="open a 2nd stream partway through to test single-subscriber")
    p.add_argument("--variants", action="store_true",
                   help="race candidate params/headers concurrently against one "
                        "zone trip, to find what makes the stream actually emit")
    p.add_argument("--framing", choices=("auto", "blank", "line"), default="auto",
                   help="frame on blank lines (SSE) or single newlines (NDJSON); "
                        "auto tries blank first then re-frames the residue by line")
    p.add_argument("--raw", action="store_true",
                   help="show full chunk bytes (they are always shown truncated)")
    args = p.parse_args()

    required = (
        ("--panel/$PANEL_URL", args.panel),
        ("--api-key/$API_KEY", args.api_key),
        ("--pairing-key/$PAIRING_KEY", args.pairing_key),
    )
    missing = [name for name, value in required if not value]
    if missing:
        p.error("missing: " + ", ".join(missing))

    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
