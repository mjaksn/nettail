"""Checks for --size-scale-window, the sliding-window dynamic scale."""
import argparse
import io
import random
import re
import socket
import struct
import subprocess
import sys
import time

from harness import SCRIPT, FakeTTY, check, finish

import nettail as main

FLOOR = main.MIN_DYNAMIC_SCALE_MAX

# --- the window forgets old flows ------------------------------------------
s = main.SizeScale(dynamic=True, window=3)
s.observe(5_000_000)
check("the newest flow sets the top", s.top == 5_000_000)
s.observe(10_000)
check("the big flow is still in the window", s.top == 5_000_000)
s.observe(20_000)
check("still in scope two flows later", s.top == 5_000_000)
s.observe(30_000)
check("the big flow drops out on the fourth flow", s.top == 30_000,
      "top=%d" % s.top)
s.observe(100)
check("the top follows the window down", s.top == 30_000)
s.observe(100)
s.observe(100)
check("an all-tiny window falls back to the floor", s.top == FLOOR, "top=%d" % s.top)

# --- unbounded still means unbounded ---------------------------------------
u = main.SizeScale(dynamic=True)
u.observe(5_000_000)
for _ in range(50):
    u.observe(100)
check("without a window nothing is forgotten", u.top == 5_000_000)

# --- against a brute-force reference ---------------------------------------
rng = random.Random(20260820)
for window in (1, 2, 5, 37):
    ref = []
    sc = main.SizeScale(dynamic=True, window=window)
    ok = True
    for _ in range(400):
        n = rng.choice([0, 40, 500, 1500, 9_000, 250_000, 4_000_000])
        ref.append(n)
        sc.observe(n)
        expected = max(max(ref[-window:]), FLOOR)
        if sc.top != expected:
            ok = False
            detail = "window=%d after %d flows: %d != %d" % (
                window, len(ref), sc.top, expected)
            break
    check("window of %d tracks a brute-force maximum" % window, ok,
          detail if not ok else "")
    check("window of %d keeps the deque bounded" % window,
          len(sc._recent) <= window, "%d entries" % len(sc._recent))

# --- a window on a fixed scale is inert ------------------------------------
f = main.SizeScale(window=5)
f.observe(9_000_000)
check("a fixed scale ignores the window entirely", f.top == main.DEFAULT_SIZE_SCALE_MAX)
check("the all-time largest is still tracked", f.largest == 9_000_000)

# --- argument parsing -------------------------------------------------------
check("a plain count parses", main.size_window_arg("250") == 250)
for bad in ("0", "-3", "abc", "", "2.5"):
    try:
        main.size_window_arg(bad)
        check("rejects %r" % bad, False)
    except argparse.ArgumentTypeError:
        check("rejects %r" % bad, True)


def run_cli(argv):
    return subprocess.run([sys.executable, *SCRIPT] + argv,
                          capture_output=True, text=True, timeout=30)


clash = run_cli(["--size-scale-window", "100", "--size-scale-max", "1M"])
check("--size-scale-window and --size-scale-max are refused together",
      clash.returncode == 2 and "cannot be combined" in clash.stderr,
      repr(clash.stderr[-160:]))
bad = run_cli(["--size-scale-window", "0"])
check("a window of 0 is refused at startup", bad.returncode == 2)
check("--size-scale-window and --size-scale-dynamic may be combined",
      run_cli(["--size-scale-window", "5", "--size-scale-dynamic", "--help"]
              ).returncode == 0)

# --- end to end: the window changes what you see ---------------------------
V5_HDR = struct.Struct("!HHIIIIBBH")
V5_REC = struct.Struct("!4s4s4sHHIIIIHHBBBBHHBBH")
SIZES = [400_000, 12_000, 12_000, 12_000]   # one whale, then steady traffic


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


def colours(argv):
    socket.socket = FakeSocket
    out, err = FakeTTY(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    sys.argv = ["nettail", "--resolve", "off"] + argv
    try:
        main.main()
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    rows = [ln for ln in out.getvalue().splitlines() if "8.8.8.8" in ln]
    return [int(re.search(r"\033\[38;5;(\d+)m", ln).group(1)) for ln in rows]


hot = main.SIZE_RAMP[-1]
unbounded = colours(["--size-scale-dynamic"])
windowed = colours(["--size-scale-window", "2"])

check("unbounded: the whale pins the scale for the rest of the run",
      unbounded[0] == hot and all(c != hot for c in unbounded[1:]),
      str(unbounded))
check("windowed: the scale recovers once the whale ages out",
      windowed[0] == hot and windowed[1] != hot and windowed[2] == hot,
      str(windowed))
check("--size-scale-window implies dynamic",
      colours(["--size-scale-window", "2"]) == windowed
      and windowed != colours([]), str(colours([])))

finish("sliding-window")
