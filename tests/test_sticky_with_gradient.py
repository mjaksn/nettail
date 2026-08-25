"""--sticky-header and the size gradient together, the pairing the rebase created."""
import io
import re
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
SIZES = [40, 900, 12_000, 400_000]
ESC = re.compile(r"\033\[[0-9;?]*[A-Za-z]")   # every CSI, not just colour
STAMP = re.compile(r"\d{2}:\d{2}:\d{2}\.\d{3}")


def v5_packet():
    now = int(time.time())
    pkt = V5_HDR.pack(5, len(SIZES), 100000, now, 0, 0, 0, 0, 0)
    for i, octets in enumerate(SIZES):
        pkt += V5_REC.pack(
            bytes([192, 168, 1, 10 + i]), bytes([8, 8, 8, 8]), bytes([192, 168, 1, 1]),
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


nettail.sticky.enable_windows_vt = lambda: True
shutil.get_terminal_size = lambda fallback=(80, 24): shutil.os.terminal_size((160, 30))
socket.socket = FakeSocket

out, err = FakeTTY(), io.StringIO()
real_out, real_err = sys.stdout, sys.stderr
sys.stdout, sys.stderr = out, err
sys.argv = ["nettail", "--resolve", "off", "--sticky-header",
            "--size-scale-window", "2", "--hide-status"]
try:
    main.main()
finally:
    sys.stdout, sys.stderr = real_out, real_err

o = out.getvalue()
rows = [ln for ln in o.splitlines() if "8.8.8.8" in ln]
tints = [int(re.search(r"\033\[38;5;(\d+)m", ln).group(1)) for ln in rows]

check("the header is pinned", "\033[2;30r" in o and o.count("\033[1;1H") == 1)
check("every flow row is tinted", len(tints) == len(SIZES))
check("tints rise with flow size", tints == sorted(tints, key=main.SIZE_RAMP.index),
      str(tints))
check("the biggest flow is at the top of the ramp", tints[-1] == main.SIZE_RAMP[-1])
# The sticky header paints with cursor moves and no newline, so in a captured
# stream its output shares a line with the first flow. Measure from the
# timestamp that starts each flow row instead.
def flow_row(line):
    bare = ESC.sub("", line)
    return bare[STAMP.search(bare).start():]


widths = [len(flow_row(ln)) for ln in rows]
check("rows stay aligned under both features", len(set(widths)) == 1, str(widths))
check("the header is not repainted over the flows", o.count("TIME") == 1)
check("the scroll region is released at the end", "\033[r" in o)
check("the region is released after the last flow",
      o.index("\033[r") > o.rindex("8.8.8.8"))
check("the summary survives", "flows decoded      4" in plain(err.getvalue()),
      repr(plain(err.getvalue())[-200:]))

finish("combined sticky + gradient")
