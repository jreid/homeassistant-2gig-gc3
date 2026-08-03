#!/usr/bin/env python3
"""
GC3 mDNS/zeroconf probe -- answer the last open Gold question (read-only).

INTEGRATION_PLAN.md 4/6 leaves one investigative item: does the GC3 advertise
itself over mDNS? The answer decides whether the Gold `discovery` and
`discovery-update-info` rules become a feature (a zeroconf block in
manifest.json plus a discovery step in the config flow) or a documented
exemption. Panel addresses are static in practice, so this is convenience, not
function -- but the rule has to be answered either way.

A single "I didn't see anything" is not an answer, so this probes three
independent ways. Only agreement between them is conclusive:

  1. PASSIVE SNIFF -- join 224.0.0.251:5353 and log every packet whose source is
     the panel. This is the ground truth: if the panel multicasts anything at
     all, it lands here. Everything else on the LAN is counted too, so a run
     that sees 0 panel packets *and* 0 packets from anyone is a broken capture,
     not a negative result. Distinguishing those two is the entire point.
  2. ACTIVE UNICAST QUERY -- send mDNS queries straight to the panel's port 5353
     (QU bit set). Some embedded stacks answer a direct query but never announce
     unsolicited, which a passive sniff alone would score as absent. Asks for
     the service-enumeration PTR, the reverse-address PTR, and A records for the
     obvious names.
  3. SERVICE ENUMERATION -- python-zeroconf's meta-query walks every service type
     anyone on the LAN advertises, then resolves each one and matches on the
     panel's address. Catches an advert under a service type we'd never guess.

TIMING MATTERS. mDNS announcement is loudest when a device joins the network: a
compliant stack sends its announcement burst on boot and then goes quiet for the
record TTL (often 75 minutes). A passive window on an idle panel can miss a
device that does advertise. To probe properly, start this with `--seconds 300`
and reboot the panel while it runs -- phase 1 will catch the join burst.

Nothing here touches the panel's HTTP API and nothing mutates state. Phase 2
sends UDP to port 5353 (a name-service query, not an alarm command); phases 1
and 3 are pure listening.

USAGE
    export PANEL_IP=<panel-ip>

    # quick check against an already-running panel (no reboot)
    python3 tools/mdns_probe.py --seconds 45

    # the real test -- start this, then power-cycle the panel
    python3 tools/mdns_probe.py --seconds 300

    # if the box is multi-homed and the capture looks empty, pin the interface
    python3 tools/mdns_probe.py --iface-ip <this-host-ip> --seconds 60

Requires python-zeroconf for phase 3 only; phases 1 and 2 are stdlib.
"""

from __future__ import annotations

import argparse
import os
import socket
import struct
import sys
import time
from collections import Counter
from dataclasses import dataclass, field

MCAST_ADDR = "224.0.0.251"
MCAST_PORT = 5353

# DNS RR types we bother to name in the log.
TYPES = {1: "A", 12: "PTR", 16: "TXT", 28: "AAAA", 33: "SRV", 47: "NSEC"}


def ts() -> str:
    return time.strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)


# --------------------------------------------------------------------------
# Minimal DNS wire codec. dnspython is not a dependency of this repo and
# zeroconf's codec is private API that moves between releases, so the handful
# of bytes we need are done by hand.
# --------------------------------------------------------------------------


def encode_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode("utf-8")
        if len(raw) > 63:
            raise ValueError(f"label too long: {label}")
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def decode_name(buf: bytes, off: int) -> tuple[str, int]:
    """Decode a (possibly compressed) name. Returns (name, offset-after)."""
    labels: list[str] = []
    jumped = False
    end = off
    hops = 0
    while True:
        if off >= len(buf):
            break
        ln = buf[off]
        if ln == 0:
            off += 1
            if not jumped:
                end = off
            break
        if ln & 0xC0 == 0xC0:  # compression pointer
            if off + 1 >= len(buf):
                break
            ptr = ((ln & 0x3F) << 8) | buf[off + 1]
            if not jumped:
                end = off + 2
            off = ptr
            jumped = True
            hops += 1
            if hops > 20:  # malformed / hostile packet
                break
            continue
        off += 1
        labels.append(buf[off : off + ln].decode("utf-8", "replace"))
        off += ln
        if not jumped:
            end = off
    return ".".join(labels), end


def build_query(questions: list[tuple[str, int]], unicast: bool = True) -> bytes:
    """Build an mDNS query. `unicast` sets the QU bit so the reply comes to us."""
    header = struct.pack("!HHHHHH", 0x0000, 0x0000, len(questions), 0, 0, 0)
    body = b""
    for name, qtype in questions:
        qclass = 0x0001 | (0x8000 if unicast else 0)
        body += encode_name(name) + struct.pack("!HH", qtype, qclass)
    return header + body


@dataclass
class Record:
    name: str
    rtype: int
    data: str

    def __str__(self) -> str:
        t = TYPES.get(self.rtype, str(self.rtype))
        return f"{self.name} {t} {self.data}".rstrip()


def parse_message(buf: bytes) -> tuple[list[Record], list[Record]]:
    """Return (questions, answers). Answers cover AN+NS+AR sections."""
    if len(buf) < 12:
        return [], []
    _id, _flags, qd, an, ns, ar = struct.unpack("!HHHHHH", buf[:12])
    off = 12
    questions: list[Record] = []
    for _ in range(qd):
        name, off = decode_name(buf, off)
        if off + 4 > len(buf):
            return questions, []
        qtype, _qclass = struct.unpack("!HH", buf[off : off + 4])
        off += 4
        questions.append(Record(name, qtype, ""))

    answers: list[Record] = []
    for _ in range(an + ns + ar):
        if off >= len(buf):
            break
        name, off = decode_name(buf, off)
        if off + 10 > len(buf):
            break
        rtype, _rclass, _ttl, rdlen = struct.unpack("!HHIH", buf[off : off + 10])
        off += 10
        rdata = buf[off : off + rdlen]
        answers.append(Record(name, rtype, decode_rdata(buf, off, rtype, rdata)))
        off += rdlen
    return questions, answers


def decode_rdata(buf: bytes, off: int, rtype: int, rdata: bytes) -> str:
    try:
        if rtype == 1 and len(rdata) == 4:
            return socket.inet_ntoa(rdata)
        if rtype == 28 and len(rdata) == 16:
            return socket.inet_ntop(socket.AF_INET6, rdata)
        if rtype == 12:
            return decode_name(buf, off)[0]
        if rtype == 33 and len(rdata) >= 6:
            _pri, _w, port = struct.unpack("!HHH", rdata[:6])
            target = decode_name(buf, off + 6)[0]
            return f"{target}:{port}"
        if rtype == 16:
            parts: list[str] = []
            i = 0
            while i < len(rdata):
                ln = rdata[i]
                parts.append(rdata[i + 1 : i + 1 + ln].decode("utf-8", "replace"))
                i += 1 + ln
            return " ".join(p for p in parts if p)
    except Exception:  # noqa: BLE001 - a malformed RR must not kill the probe
        pass
    return rdata.hex()[:48]


# --------------------------------------------------------------------------
# Phase 1 -- passive multicast sniff
# --------------------------------------------------------------------------


@dataclass
class SniffResult:
    panel_packets: int = 0
    total_packets: int = 0
    sources: Counter[str] = field(default_factory=Counter)
    panel_records: list[str] = field(default_factory=list)


def open_mcast_socket(iface_ip: str | None) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # avahi-daemon already holds 5353; SO_REUSEPORT lets us listen alongside it
    # instead of fighting it for the port.
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    s.bind(("", MCAST_PORT))
    mreq = socket.inet_aton(MCAST_ADDR) + socket.inet_aton(iface_ip or "0.0.0.0")
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    except OSError as exc:
        log(f"  ! could not join {MCAST_ADDR} on {iface_ip or 'default iface'}: {exc}")
    s.settimeout(1.0)
    return s


def phase_sniff(panel_ip: str, seconds: int, iface_ip: str | None) -> SniffResult:
    log(f"PHASE 1: passive sniff of {MCAST_ADDR}:{MCAST_PORT} for {seconds}s")
    log(f"  watching for source {panel_ip}; all other sources counted as a control")
    res = SniffResult()
    try:
        sock = open_mcast_socket(iface_ip)
    except OSError as exc:
        log(f"  ! cannot bind :{MCAST_PORT} ({exc}) -- phase 1 skipped")
        return res

    deadline = time.time() + seconds
    with sock:
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                break
            src = addr[0]
            res.total_packets += 1
            res.sources[src] += 1
            if src != panel_ip:
                continue
            res.panel_packets += 1
            questions, answers = parse_message(data)
            log(f"  *** PANEL PACKET #{res.panel_packets} ({len(data)}B) ***")
            for q in questions:
                line = f"question {q.name} {TYPES.get(q.rtype, q.rtype)}"
                log(f"      {line}")
                res.panel_records.append(line)
            for a in answers:
                log(f"      {a}")
                res.panel_records.append(str(a))
    return res


# --------------------------------------------------------------------------
# Phase 2 -- active unicast query straight at the panel
# --------------------------------------------------------------------------


def reverse_name(ip: str) -> str:
    return ".".join(reversed(ip.split("."))) + ".in-addr.arpa"


def phase_unicast(panel_ip: str, wait: float, extra_names: list[str]) -> list[Record]:
    log(f"PHASE 2: unicast mDNS queries direct to {panel_ip}:{MCAST_PORT}")
    questions: list[tuple[str, int]] = [
        ("_services._dns-sd._udp.local", 12),  # what services do you have?
        (reverse_name(panel_ip), 12),  # what is your name?
    ]
    for n in extra_names:
        questions.append((n, 1))

    found: list[Record] = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(wait)
    with sock:
        for name, qtype in questions:
            pkt = build_query([(name, qtype)])
            label = f"{name} {TYPES.get(qtype, qtype)}"
            try:
                sock.sendto(pkt, (panel_ip, MCAST_PORT))
            except OSError as exc:
                log(f"  {label:<48} send failed: {exc}")
                continue
            deadline = time.time() + wait
            got = False
            while time.time() < deadline:
                try:
                    data, addr = sock.recvfrom(9000)
                except socket.timeout:
                    break
                except OSError:
                    break
                if addr[0] != panel_ip:
                    continue
                got = True
                _q, answers = parse_message(data)
                log(f"  {label:<48} *** REPLIED ({len(data)}B) ***")
                for a in answers:
                    log(f"      {a}")
                    found.append(a)
            if not got:
                log(f"  {label:<48} no reply")
    return found


# --------------------------------------------------------------------------
# Phase 3 -- service-type enumeration via python-zeroconf
# --------------------------------------------------------------------------


def phase_enumerate(panel_ip: str, seconds: int) -> list[str]:
    log(f"PHASE 3: enumerating every advertised service type ({seconds}s)")
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf, ZeroconfServiceTypes
    except ImportError:
        log("  ! python-zeroconf not installed -- phase 3 skipped")
        log("    pip install zeroconf")
        return []

    hits: list[str] = []
    try:
        types = sorted(ZeroconfServiceTypes.find(timeout=min(seconds, 10)))
    except Exception as exc:  # noqa: BLE001
        log(f"  ! meta-query failed: {exc}")
        return []
    log(f"  {len(types)} service types advertised on this LAN")

    class Listener(ServiceListener):
        def _check(self, zc: "Zeroconf", type_: str, name: str) -> None:
            info = zc.get_service_info(type_, name, timeout=2000)
            if not info:
                return
            for addr in info.parsed_addresses():
                if addr == panel_ip:
                    line = f"{name} [{type_}] {addr}:{info.port}"
                    log(f"  *** PANEL SERVICE: {line} ***")
                    if info.properties:
                        for k, v in info.properties.items():
                            log(f"      TXT {k!r}={v!r}")
                    hits.append(line)

        def add_service(self, zc: "Zeroconf", type_: str, name: str) -> None:
            self._check(zc, type_, name)

        def update_service(self, zc: "Zeroconf", type_: str, name: str) -> None:
            self._check(zc, type_, name)

        def remove_service(self, zc: "Zeroconf", type_: str, name: str) -> None:
            pass

    zc = Zeroconf()
    try:
        browsers = [ServiceBrowser(zc, t, Listener()) for t in types]
        end = time.time() + seconds
        while time.time() < end:
            time.sleep(0.5)
        for b in browsers:
            b.cancel()
    finally:
        zc.close()
    return hits


# --------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(
        description="Probe whether the GC3 panel advertises over mDNS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--panel-ip", default=os.environ.get("PANEL_IP"),
                   help="panel IP (or $PANEL_IP)")
    p.add_argument("--seconds", type=int, default=60, help="passive sniff window (default 60)")
    p.add_argument("--iface-ip", default=None, help="local IP of the interface to join on")
    p.add_argument("--enumerate-seconds", type=int, default=15, help="phase 3 browse window")
    p.add_argument("--skip-sniff", action="store_true")
    p.add_argument("--skip-unicast", action="store_true")
    p.add_argument("--skip-enumerate", action="store_true")
    p.add_argument(
        "--name",
        action="append",
        default=[],
        dest="names",
        help="extra .local name to A-query in phase 2 (repeatable)",
    )
    args = p.parse_args()

    if not args.panel_ip:
        p.error("missing: --panel-ip/$PANEL_IP")

    names = args.names or ["gc3.local", "2gig.local", "panel.local", "gc3panel.local"]

    log("=" * 68)
    log(f"GC3 mDNS probe -- panel {args.panel_ip}")
    log("=" * 68)
    log("Reminder: mDNS is loudest at boot. For a conclusive negative, reboot")
    log("the panel during phase 1.")

    sniff = SniffResult()
    if not args.skip_sniff:
        sniff = phase_sniff(args.panel_ip, args.seconds, args.iface_ip)

    unicast: list[Record] = []
    if not args.skip_unicast:
        unicast = phase_unicast(args.panel_ip, 3.0, names)

    enumerated: list[str] = []
    if not args.skip_enumerate:
        enumerated = phase_enumerate(args.panel_ip, args.enumerate_seconds)

    log("=" * 68)
    log("VERDICT")
    log("=" * 68)

    capture_ok = sniff.total_packets > 0 or args.skip_sniff
    if not args.skip_sniff:
        log(f"  phase 1 sniff       : {sniff.panel_packets} panel / "
            f"{sniff.total_packets} total packets from {len(sniff.sources)} hosts")
        if sniff.total_packets == 0:
            log("    ! ZERO packets from ANYONE -- the capture is broken, not the panel.")
            log("      Re-run with --iface-ip set to the NIC on the panel's subnet.")
        else:
            top = ", ".join(f"{ip}({n})" for ip, n in sniff.sources.most_common(3))
            log(f"    busiest sources: {top}")
    log(f"  phase 2 unicast     : {len(unicast)} records returned by the panel")
    log(f"  phase 3 enumeration : {len(enumerated)} services matched the panel's address")

    positive = bool(sniff.panel_packets or unicast or enumerated)
    log("")
    if positive:
        log("  RESULT: the panel DOES speak mDNS. Capture the service type and TXT")
        log("  keys above, add a `zeroconf` block to manifest.json and a discovery")
        log("  step to the config flow, then flip `discovery` to done.")
    elif not capture_ok:
        log("  RESULT: INCONCLUSIVE -- capture was empty. Fix the interface and re-run.")
        return 2
    else:
        log("  RESULT: no mDNS presence detected. If this run included a panel reboot,")
        log("  that is a sound negative: mark `discovery` and `discovery-update-info`")
        log("  exempt in quality_scale.yaml. If it did not, re-run with --seconds 300")
        log("  and power-cycle the panel before calling it.")
    return 0 if positive else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("interrupted")
        sys.exit(130)
