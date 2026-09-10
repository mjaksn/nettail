#!/usr/bin/env python3
"""Send NetFlow at a running collector, so that a manual check has traffic.

Nothing in the suite draws on a terminal or opens a browser, so a handful of
things here are pinned only by somebody looking at them: the sticky header and
the status bar sharing one scroll region, the QR code, a country flag, the
details dialog, and whether the columns still line up when a name or a flag is
sitting in them. Every one of those needs flows arriving while a person
watches, and this is what sends them.

    python tests/tools/send_flows.py --port 2057
    python tests/tools/send_flows.py --port 2057 --exporter ipfix

Which exporter is asked for is the one thing worth knowing before using this.
NetFlow v5 has a fixed record with no field for a hardware address and no
templates at all, so a v5 run can never make the `p` key or `--templates` show
anything, however long it is left running. That is the program behaving as
documented, and on screen it is indistinguishable from a key that has stopped
working: the line under a flow is not drawn at all rather than drawn empty,
precisely so that turning `p` on costs nothing on an exporter that cannot
answer. The IPFIX exporter sends the ingress MAC pair, elements 56 and 80, and
resends its template as a real exporter does, so it is the one to reach for
when what is being looked at is the `p`, `t` or `v` key.

This is a tool and not a suite. `tests/run.py` collects `test_*.py` and
nothing else, so nothing in here is ever run as part of the suite, and it
takes no dependency the suite does not already have, which is none.

The addresses are private on one side and public on the other, so that the
internal and external split, the top talkers table and `--external-only` all
have something to sort. Sizes run from a few dozen bytes to several megabytes
so that the size ramp has a range to colour, and the ports are ones a services
database names, so that a column reads `443/https` rather than a bare number.
"""

import argparse
import random
import socket
import struct
import sys
import time

V5_HEADER = struct.Struct("!HHIIIIBBH")
V5_RECORD = struct.Struct("!4s4s4sHHIIIIHHBBBBHHBBH")

# The template an IPFIX run announces and then sends records against. The last
# two are the ingress MAC pair, which is the whole reason this exporter is
# here, since v5 has nowhere to put them.
IPFIX_FIELDS = [(8, 4), (12, 4), (7, 2), (11, 2), (4, 1), (1, 4), (2, 4),
                (56, 6), (80, 6)]
IPFIX_TEMPLATE_ID = 256

HOSTS = [(192, 168, 1, n) for n in (10, 11, 12, 42, 77)]
PEERS = [(8, 8, 8, 8), (1, 1, 1, 1), (140, 82, 114, 4), (93, 184, 216, 34),
         (13, 107, 21, 200)]
PORTS = [443, 80, 53, 22, 993, 3478]
SIZES = [64, 512, 4096, 65536, 1048576, 8388608]

# One address per host and a single gateway on the other side, which is what
# an exporter that is the gateway really reports: a MAC is hop by hop, so the
# destination is the exporter's own interface on every routed flow and repeats
# itself all the way down the column. That repetition is part of what the p
# key is worth looking at.
HOST_MACS = ["a483e71c9d02", "3c22fb1a4e77", "d83bbf90aa11", "0022480e5c33",
             "6c4b90f7e218"]
GATEWAY_MAC = "f01898cd4400"


def one_flow():
    """One flow's worth of invented facts, in the shape both exporters want."""
    which = random.randrange(len(HOSTS))
    return {
        "src": bytes(HOSTS[which]),
        "dst": bytes(random.choice(PEERS)),
        "src_mac": bytes.fromhex(HOST_MACS[which]),
        "dst_mac": bytes.fromhex(GATEWAY_MAC),
        "src_port": random.randrange(20000, 60000),
        "dst_port": random.choice(PORTS),
        "octets": random.choice(SIZES),
        "packets": 12,
    }


def v5_datagram(seq, flows):
    """A v5 datagram: a header, then fixed records with no room for a MAC."""
    now = int(time.time())
    uptime = now * 1000 % 2 ** 32
    out = V5_HEADER.pack(5, len(flows), uptime, now, 0, seq, 0, 0, 0)
    for flow in flows:
        out += V5_RECORD.pack(
            flow["src"], flow["dst"], bytes([192, 168, 1, 1]),
            1, 2, flow["packets"], flow["octets"],
            uptime - 10000, uptime, flow["src_port"], flow["dst_port"],
            0, 0x18, 6, 0, 0, 0, 24, 24, 0)
    return out


def ipfix_set(set_id, body):
    return struct.pack("!HH", set_id, 4 + len(body)) + body


def ipfix_template():
    body = struct.pack("!HH", IPFIX_TEMPLATE_ID, len(IPFIX_FIELDS))
    body += b"".join(struct.pack("!HH", element, size)
                     for element, size in IPFIX_FIELDS)
    return ipfix_set(2, body)


def ipfix_datagram(seq, flows, with_template):
    """An IPFIX datagram, carrying the template whenever it is asked for.

    A collector that has not seen the template counts the records it cannot
    read as deferred and says so, which is worth watching once. Resending it
    is what a real exporter does, and it is what gives the t key something to
    report on a run that has been up for a while.
    """
    sets = []
    if with_template:
        sets.append(ipfix_template())
    body = b""
    for flow in flows:
        body += (flow["src"] + flow["dst"]
                 + struct.pack("!HH", flow["src_port"], flow["dst_port"])
                 + bytes([6])
                 + struct.pack("!II", flow["octets"], flow["packets"])
                 + flow["src_mac"] + flow["dst_mac"])
    sets.append(ipfix_set(IPFIX_TEMPLATE_ID, body))
    payload = b"".join(sets)
    return struct.pack("!HHIII", 10, 16 + len(payload), int(time.time()),
                       seq, 0) + payload


def build_parser():
    parser = argparse.ArgumentParser(
        description="Send NetFlow at a collector, for a manual check.")
    parser.add_argument("--host", default="127.0.0.1",
                        help="where the collector is listening "
                             "(default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=2055,
                        help="the port it is listening on (default: 2055)")
    parser.add_argument("--exporter", choices=("v5", "ipfix"), default="v5",
                        help="v5 has no hardware addresses and no templates; "
                             "ipfix sends both, and is the one to use when "
                             "looking at the p, t or v keys (default: v5)")
    parser.add_argument("--interval", type=float, default=0.4,
                        help="seconds between datagrams (default: 0.4)")
    parser.add_argument("--count", type=int, default=0,
                        help="stop after this many datagrams (default: 0, "
                             "which is to run until interrupted)")
    parser.add_argument("--resend-every", type=int, default=20,
                        help="datagrams between IPFIX template resends, and "
                             "nothing under v5, which has no templates "
                             "(default: 20)")
    return parser


def main():
    args = build_parser().parse_args()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = (args.host, args.port)
    seq = sent = 0
    print("sending %s at %s:%d, Ctrl-C to stop"
          % (args.exporter, args.host, args.port), file=sys.stderr)
    try:
        while not args.count or sent < args.count:
            flows = [one_flow() for _ in range(random.randint(1, 4))]
            if args.exporter == "v5":
                datagram = v5_datagram(seq, flows)
            else:
                # The template goes out ahead of the first records and then on
                # the interval asked for. Sent every time, a reader could not
                # tell a resend from the first one; sent once, a collector
                # started later would never learn the layout and would count
                # everything as deferred for ever.
                due = args.resend_every and sent % args.resend_every == 0
                datagram = ipfix_datagram(seq, flows, sent == 0 or due)
            # Both count records rather than datagrams, which is what each
            # protocol means by the field. A gap in it is what the collector
            # reports as a missed export, and a run of this should never show
            # one.
            seq += len(flows)
            sock.sendto(datagram, target)
            sent += 1
            if sent % 25 == 0:
                print("%d datagrams, %d flows" % (sent, seq), file=sys.stderr)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    print("stopped after %d datagrams, %d flows" % (sent, seq),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
