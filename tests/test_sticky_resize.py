"""Regression: when the window shrinks too far mid-run, --header-every resumes."""
import io
import shutil
import socket
import struct
import sys
import time

from harness import FakeTTY, check, finish

import nettail
import nettail as main

V5_HDR = struct.Struct("!HHIIIIBBH")
V5_REC = struct.Struct("!4s4s4sHHIIIIHHBBBBHHBBH")


def v5_packet(n=3):
    now = int(time.time())
    pkt = V5_HDR.pack(5, n, 100000, now, 0, 0, 0, 0, 0)
    for i in range(n):
        pkt += V5_REC.pack(
            bytes([192, 168, 1, 10 + i]), bytes([8, 8, 8, 8]), bytes([192, 168, 1, 1]),
            1, 2, 12 + i, 1500 + i, 90000, 100000, 51000 + i, 443, 0,
            0x18, 6, 0, 0, 0, 24, 24, 0)
    return pkt


class FakeSocket:
    def __init__(self, *a, **kw):
        self.left = 40

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


state = {"shrunk": False}


def shrinking_size(fallback=(80, 24)):
    # Roomy until the header has been pinned, then too short to keep a row.
    return shutil.os.terminal_size((120, 3) if state["shrunk"] else (120, 30))


_real_start = main.StickyHeader.start


def start_then_shrink(self):
    started = _real_start(self)
    state["shrunk"] = True
    return started


main.StickyHeader.start = start_then_shrink
nettail.sticky.enable_windows_vt = lambda: True
shutil.get_terminal_size = shrinking_size
socket.socket = FakeSocket

out, err = FakeTTY(), io.StringIO()
real_out, real_err = sys.stdout, sys.stderr
sys.stdout, sys.stderr = out, err
sys.argv = ["nettail", "--resolve", "off", "--sticky-header",
            "--header-every", "5", "--hide-status"]
try:
    main.main()
finally:
    sys.stdout, sys.stderr = real_out, real_err

o = out.getvalue()


check("header was pinned at startup", "\033[2;30r" in o)
check("scroll region released when the window shrank", "\033[r" in o)
check("periodic header reprint resumed", o.count("TIME") > 1,
      "found %d headers" % o.count("TIME"))
check("flows kept rendering after the header gave up", o.count("8.8.8.8") == 120,
      "found %d" % o.count("8.8.8.8"))

finish("shrink regression")
