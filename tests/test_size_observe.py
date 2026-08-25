"""A dynamic scale must range against every decoded flow, hidden ones included."""
import io
import re
import socket
import struct
import sys
import time

from harness import FakeTTY, check, finish

import nettail as main

V5_HDR = struct.Struct("!HHIIIIBBH")
V5_REC = struct.Struct("!4s4s4sHHIIIIHHBBBBHHBBH")

# A big internal flow --external-only will hide, then a small external one.
FLOWS = [((192, 168, 1, 10), (192, 168, 1, 20), 5_000_000),
         ((192, 168, 1, 11), (8, 8, 8, 8), 12_000)]


def v5_packet():
    now = int(time.time())
    pkt = V5_HDR.pack(5, len(FLOWS), 100000, now, 0, 0, 0, 0, 0)
    for i, (src, dst, octets) in enumerate(FLOWS):
        pkt += V5_REC.pack(
            bytes(src), bytes(dst), bytes([192, 168, 1, 1]),
            1, 2, 12, octets, 90000, 100000, 51000 + i, 443, 0,
            0x18, 6, 0, 0, 0, 24, 24, 0)
    return pkt


class FakeSocket:
    def __init__(self, *a, **kw):
        self.left = 1

    def setsockopt(self, *a):
        pass

    def bind(self, *a):
        pass

    def settimeout(self, *a):
        pass

    def close(self):
        pass

    def recvfrom(self, _n):
        if self.left <= 0:
            raise KeyboardInterrupt
        self.left -= 1
        return v5_packet(), ("10.0.0.1", 2055)


def run(argv):
    socket.socket = FakeSocket
    out, err = FakeTTY(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    sys.argv = ["nettail", "--resolve", "off"] + argv
    try:
        main.main()
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    return out.getvalue()


def colour_of(text, marker):
    line = [ln for ln in text.splitlines() if marker in ln]
    assert len(line) == 1, line
    return int(re.search(r"\033\[38;5;(\d+)m", line[0]).group(1))


hidden = run(["--external-only", "--size-scale-dynamic"])
check("--external-only really hides the internal flow",
      "192.168.1.20" not in hidden)
check("the visible flow is not painted as the biggest",
      colour_of(hidden, "8.8.8.8") != main.SIZE_RAMP[-1],
      "index %d" % colour_of(hidden, "8.8.8.8"))

shown = run(["--size-scale-dynamic"])
check("the hidden flow ranges the scale the same as a shown one",
      colour_of(hidden, "8.8.8.8") == colour_of(shown, "8.8.8.8"),
      "%d vs %d" % (colour_of(hidden, "8.8.8.8"), colour_of(shown, "8.8.8.8")))
check("the 5M flow itself is at the top when shown",
      colour_of(shown, "192.168.1.20") == main.SIZE_RAMP[-1])

fixed = run(["--external-only"])
plain = main.SizeScale()
expected = main.SIZE_RAMP[round(plain.fraction(12_000) * (len(main.SIZE_RAMP) - 1))]
check("a fixed scale is unaffected by the hidden flow",
      colour_of(fixed, "8.8.8.8") == expected,
      "%d vs %d" % (colour_of(fixed, "8.8.8.8"), expected))

finish("hidden-flow observation")
