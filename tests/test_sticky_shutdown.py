"""Drives main.main() in-process with a fake socket so the finally block runs.

Covers the paths a terminate()d subprocess on Windows never reaches: the
summary, and sticky.stop() releasing the scroll region on Ctrl-C.
"""
import io
import shutil
import socket
import struct
import sys
import time

from harness import FakeTTY, check, finish, plain

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
    """Hands out a few datagrams then behaves like Ctrl-C."""

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


nettail.sticky.enable_windows_vt = lambda: True
shutil.get_terminal_size = lambda fallback=(80, 24): shutil.os.terminal_size((120, 30))
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

o, e = out.getvalue(), err.getvalue()


check("scroll region claimed at startup", "\033[2;30r" in o)
check("header painted on row 1 exactly once", o.count("\033[1;1H") == 1)
check("no periodic header reprint while pinned", o.count("TIME") == 1,
      "found %d" % o.count("TIME"))
check("flows rendered", o.count("8.8.8.8") == 120, "found %d" % o.count("8.8.8.8"))
check("scroll region released on exit", "\033[r" in o)
check("region released before the summary is written",
      o.index("\033[r") > o.rindex("8.8.8.8"))
check("summary still printed", "flows decoded      120" in plain(e),
      repr(plain(e)[-400:]))
check("no fallback notice when sticky is active",
      "falling back" not in e)

finish("shutdown")
