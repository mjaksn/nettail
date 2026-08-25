"""End-to-end: real datagrams through main.py, fixed and dynamic scales."""
import io
import re
import socket
import struct
import subprocess
import sys
import time

from harness import SCRIPT, FakeTTY, check, finish

import nettail as main

PORT = 29956
V5_HDR = struct.Struct("!HHIIIIBBH")
V5_REC = struct.Struct("!4s4s4sHHIIIIHHBBBBHHBBH")
SIZES = [40, 900, 12_000, 400_000]
ESC = re.compile(r"\033\[[0-9;]*m")


def v5_packet():
    now = int(time.time())
    pkt = V5_HDR.pack(5, len(SIZES), 100000, now, 0, 0, 0, 0, 0)
    for i, octets in enumerate(SIZES):
        pkt += V5_REC.pack(
            bytes([192, 168, 1, 10 + i]), bytes([8, 8, 8, 8]), bytes([192, 168, 1, 1]),
            1, 2, 12, octets, 90000, 100000, 51000 + i, 443, 0,
            0x18, 6, 0, 0, 0, 24, 24, 0)
    return pkt


# --- subprocess over a pipe: no escapes, columns aligned --------------------
proc = subprocess.Popen(
    [sys.executable, "-u", *SCRIPT, "--bind", "127.0.0.1", "--port", str(PORT),
     "--resolve", "off"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

# Wait to be told the socket is bound rather than guessing at a sleep. The
# banner is printed immediately after bind, and UDP has no retry: a datagram
# sent a moment too early is simply gone, and this test would then fail for
# reasons that have nothing to do with the collector.
banner = []
while True:
    line = proc.stderr.readline()
    if not line:
        break                     # it died; the assertions below will say so
    banner.append(line)
    if "Listening for NetFlow" in line:
        break

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.sendto(v5_packet(), ("127.0.0.1", PORT))
time.sleep(1.0)
proc.terminate()
out, err = proc.communicate(timeout=15)
err = "".join(banner) + err

check("flows still render end to end", out.count("8.8.8.8") == len(SIZES),
      "found %d" % out.count("8.8.8.8"))
check("no escapes when stdout is a pipe", "\033[" not in out)
rows = [ln for ln in out.splitlines() if "8.8.8.8" in ln]
check("all rows the same width", len({len(ln) for ln in rows}) == 1,
      str([len(ln) for ln in rows]))
check("byte counts are intact", "390.6K" in out and "40B" in out, repr(rows))


# --- in-process on a fake TTY: colours present and ordered ------------------


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
    return [ln for ln in out.getvalue().splitlines() if "8.8.8.8" in ln]


def colours(lines):
    return [int(re.search(r"\033\[38;5;(\d+)m", ln).group(1)) for ln in lines]


fixed = run([])
check("fixed scale colours every row", len(colours(fixed)) == len(SIZES))
check("fixed scale colours rise with flow size",
      colours(fixed) == sorted(colours(fixed), key=main.SIZE_RAMP.index),
      str(colours(fixed)))
check("the 400K flow is at the top of a 100K scale",
      colours(fixed)[-1] == main.SIZE_RAMP[-1])
check("rows stay aligned once coloured",
      len({len(ESC.sub("", ln)) for ln in fixed}) == 1,
      str([len(ESC.sub("", ln)) for ln in fixed]))

wide = run(["--size-scale-max", "10M"])
check("a wider fixed top cools the big flow",
      main.SIZE_RAMP.index(colours(wide)[-1])
      < main.SIZE_RAMP.index(colours(fixed)[-1]),
      "%s vs %s" % (colours(wide), colours(fixed)))

dyn = run(["--size-scale-dynamic"])
check("dynamic puts the largest flow at the top",
      colours(dyn)[-1] == main.SIZE_RAMP[-1], str(colours(dyn)))
check("dynamic keeps smaller flows cooler",
      main.SIZE_RAMP.index(colours(dyn)[0]) < main.SIZE_RAMP.index(colours(dyn)[-1]))

plain = run(["--no-color"])
check("--no-color drops the gradient", not any("\033[" in ln for ln in plain))

finish("end-to-end size-colour")
