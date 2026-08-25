"""Top talkers must credit whichever end of a flow is public."""
import io
import socket
import struct
import sys
import time

from harness import FakeTTY, check, finish

import nettail as main

V5_HDR = struct.Struct("!HHIIIIBBH")
V5_REC = struct.Struct("!4s4s4sHHIIIIHHBBBBHHBBH")

# outbound to one public host, inbound from another, and one purely internal
FLOWS = [((192, 168, 1, 10), (93, 184, 216, 34), 5000),     # out, dst public
         ((9, 9, 9, 9), (192, 168, 1, 20), 9000),       # in, src public
         ((192, 168, 1, 11), (192, 168, 1, 12), 100000)]    # internal, ignored


def v5_packet():
    now = int(time.time())
    pkt = V5_HDR.pack(5, len(FLOWS), 100000, now, 0, 0, 0, 0, 0)
    for i, (src, dst, octets) in enumerate(FLOWS):
        pkt += V5_REC.pack(
            bytes(src), bytes(dst), bytes([192, 168, 1, 1]),
            1, 2, 12, octets, 90000, 100000, 51000 + i, 443, 0,
            0x18, 6, 0, 0, 0, 24, 24, 0)
    return pkt


def ipfix_totals_packet():
    """An IPFIX exporter reporting octetDeltaCount as element 85, not 1."""
    fields = [(8, 4), (12, 4), (85, 4)]
    tmpl = struct.pack("!HH", 500, len(fields))
    tmpl += b"".join(struct.pack("!HH", eid, ln) for eid, ln in fields)
    set2 = struct.pack("!HH", 2, 4 + len(tmpl)) + tmpl
    rec = bytes([192, 168, 1, 30]) + bytes([8, 8, 4, 4]) + struct.pack("!I", 7777)
    set_data = struct.pack("!HH", 500, 4 + len(rec)) + rec
    body = set2 + set_data
    return struct.pack("!HHIII", 10, 16 + len(body), int(time.time()), 0, 0) + body


def run(argv, packets):
    queue = list(packets)

    class FakeSocket:
        def __init__(self, *a, **kw):
            pass

        def setsockopt(self, *a):
            pass

        def bind(self, *a):
            pass

        def settimeout(self, *a):
            pass

        def close(self):
            pass

        def recvfrom(self, _n):
            if not queue:
                raise KeyboardInterrupt
            return queue.pop(0), ("10.0.0.1", 2055)

    socket.socket = FakeSocket
    out, err = FakeTTY(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    sys.argv = ["nettail", "--resolve", "off", "--no-color"] + argv
    try:
        main.main()
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    return out.getvalue(), err.getvalue()


def table(err):
    """The top-talkers rows of the summary, as {address: size string}."""
    rows = {}
    seen = False
    for line in err.splitlines():
        if "Top external" in line:
            seen = True
            continue
        if seen:
            parts = line.split()
            if len(parts) == 2:
                rows[parts[0]] = parts[1]
            elif parts:
                break
    return rows


out, err = run([], [v5_packet()])
rows = table(err)
check("the outbound public destination is listed", "93.184.216.34" in rows, str(rows))
check("the inbound public source is listed too", "9.9.9.9" in rows, str(rows))
check("the internal-only flow is not listed",
      not any(a.startswith("192.168.") for a in rows), str(rows))
check("bytes are attributed to the destination", rows.get("93.184.216.34") == "4.9K",
      str(rows))
check("bytes are attributed to the source", rows.get("9.9.9.9") == "8.8K",
      str(rows))
check("the heading no longer says destinations only",
      "Top external addresses by bytes" in err)

# --external-only shows both of those flows, so both must be counted
out, err = run(["--external-only"], [v5_packet()])
rows = table(err)
check("both directions still counted under --external-only",
      set(rows) == {"93.184.216.34", "9.9.9.9"}, str(rows))

# IPFIX exporters that report octetDeltaCount as element 85
out, err = run([], [ipfix_totals_packet()])
rows = table(err)
check("octets_total is counted, not read as zero", rows.get("8.8.4.4") == "7.6K",
      str(rows))

finish("top talker")
