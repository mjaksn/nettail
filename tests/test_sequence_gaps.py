"""What a reader is told when exports go missing.

How a gap is spotted is netflume's question and netflume's suite answers it.
What is left for this program is everything downstream of that: the running
warning, the Export gaps section of the summary, and the fact that the flows
which did arrive are still shown.
"""
import io
import socket
import struct
import sys
import time

from harness import FakeTTY, check, finish

import nettail as main

V5_HDR = struct.Struct("!HHIIIIBBH")
V5_REC = struct.Struct("!4s4s4sHHIIIIHHBBBBHHBBH")


def v5_packet(seq, count=3):
    now = int(time.time())
    pkt = V5_HDR.pack(5, count, 100000, now, 0, seq, 0, 0, 0)
    for i in range(count):
        pkt += V5_REC.pack(
            bytes([192, 168, 1, 10 + i]), bytes([8, 8, 8, 8]), bytes([192, 168, 1, 1]),
            1, 2, 12, 1500, 90000, 100000, 51000 + i, 443, 0,
            0x18, 6, 0, 0, 0, 24, 24, 0)
    return pkt


def run(packets):
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
    sys.argv = ["nettail", "--resolve", "off", "--no-color"]
    try:
        main.main()
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    return out.getvalue(), err.getvalue()


out, err = run([v5_packet(s) for s in (0, 3, 6, 9)])
check("a clean run says so", "export gaps        none" in err,
      repr([ln for ln in err.splitlines() if "gap" in ln]))
check("a clean run has no Export gaps section", "Export gaps" not in err)

out, err = run([v5_packet(s) for s in (0, 3, 6, 15)])
check("a lossy run warns while running", "Exports are being lost" in err)
check("the running warning is printed once per exporter",
      err.count("Exports are being lost") == 1,
      repr([ln for ln in err.splitlines() if "Exports are being lost" in ln]))
check("a lossy run reports the total in the summary",
      "Export gaps" in err and "6 flow records never arrived" in err,
      repr([ln for ln in err.splitlines() if "never arrived" in ln]))
check("the flows that did arrive are still displayed",
      out.count("8.8.8.8") == 12, "%d rows" % out.count("8.8.8.8"))

out, err = run([v5_packet(s) for s in (0, 3, 6, 15)])
rows = [ln.strip() for ln in err.splitlines() if "never arrived" in ln]
check("a single-stream summary stays unqualified",
      rows == ["10.0.0.1           6 flow records never arrived"], str(rows))

finish("sequence gap")
