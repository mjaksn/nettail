"""Checks for the BYTES column colour gradient. Run from the repo root."""
import argparse
import io
import re
import subprocess
import sys

from harness import SCRIPT, check, finish
from lanname import Resolver

import nettail as main

ESC = re.compile(r"\033\[[0-9;]*m")


def ramp_index(scale, octets):
    """The xterm colour index paint() would use, or None if uncoloured."""
    painted = scale.paint("x", octets)
    m = re.match(r"\033\[38;5;(\d+)m", painted)
    return int(m.group(1)) if m else None


# --- fixed scale: log positioning ------------------------------------------
s = main.SizeScale()
check("default top is 100K", s.top == 100 * 1024)
check("floor and below sit at the cold end",
      s.fraction(64) == 0.0 and s.fraction(1) == 0.0)
check("the top of the scale is the hot end", s.fraction(100 * 1024) == 1.0)
check("above the top clamps, does not overflow", s.fraction(50 * 1024 * 1024) == 1.0)
check("None and zero are cold", s.fraction(None) == 0.0 and s.fraction(0) == 0.0)

mid = s.fraction(int((64 * 100 * 1024) ** 0.5))     # geometric mean of the ends
check("the geometric midpoint lands mid-ramp", abs(mid - 0.5) < 0.01, "%.3f" % mid)

fracs = [s.fraction(n) for n in (100, 1000, 10_000, 100_000)]
check("fraction rises with size", all(a < b for a, b in zip(fracs, fracs[1:])),
      str(fracs))
check("a decade is a constant step on the ramp",
      abs((fracs[1] - fracs[0]) - (fracs[2] - fracs[1])) < 1e-9)
check("every fraction stays in range", all(0.0 <= f <= 1.0 for f in fracs))

# --- colours actually differ across the range ------------------------------
indices = [ramp_index(s, n) for n in (64, 512, 4096, 32_768, 100 * 1024)]
check("cold end is the first ramp colour", indices[0] == main.SIZE_RAMP[0])
check("hot end is the last ramp colour", indices[-1] == main.SIZE_RAMP[-1])
check("distinct sizes get distinct colours", len(set(indices)) == len(indices),
      str(indices))
check("no colour for an unknown byte count", ramp_index(s, None) is None)

# --- custom fixed top -------------------------------------------------------
s2 = main.SizeScale(top=1024)
check("custom top is the hot end", s2.fraction(1024) == 1.0)
check("a flow above a small top clamps", ramp_index(s2, 10 ** 9) == main.SIZE_RAMP[-1])

# --- dynamic scale ----------------------------------------------------------
d = main.SizeScale(dynamic=True)
check("dynamic starts at the minimum", d.top == main.MIN_DYNAMIC_SCALE_MAX)
d.observe(2_000_000)
check("dynamic grows to the largest flow", d.top == 2_000_000)
check("the largest flow so far is the hot end",
      ramp_index(d, 2_000_000) == main.SIZE_RAMP[-1])
d.observe(1000)
check("dynamic never shrinks", d.top == 2_000_000)
check("a small flow is cooler once the scale has grown",
      d.fraction(1000) < 0.5)
before = ramp_index(d, 3_000_000)
d.observe(3_000_000)
check("observing a new record re-ranges the ramp",
      before == main.SIZE_RAMP[-1] and d.top == 3_000_000)

fixed = main.SizeScale()
fixed.observe(9_000_000)
check("a fixed scale ignores observations", fixed.top == 100 * 1024)

# --- argument parsing -------------------------------------------------------
check("plain byte count parses", main.size_scale_arg("100000") == 100000)
check("K suffix is KiB", main.size_scale_arg("100k") == 100 * 1024)
check("M suffix and fractions parse",
      main.size_scale_arg("1.5M") == int(1.5 * 1024 ** 2))
check("uppercase suffix parses", main.size_scale_arg("2G") == 2 * 1024 ** 3)
for bad in ("banana", "", "12x", "0", "64"):
    try:
        main.size_scale_arg(bad)
        check("rejects %r" % bad, False)
    except argparse.ArgumentTypeError:
        check("rejects %r" % bad, True)

# --- colour switched off ----------------------------------------------------
saved = {n: getattr(main.C, n) for n in dir(main.C) if n.isupper()}
main.C.disable()
check("paint() is a no-op with colour disabled", s.paint("  1.5K", 5000) == "  1.5K")
check("C.enabled() reports disabled", main.C.enabled() is False)
for n, v in saved.items():
    setattr(main.C, n, v)
check("C.enabled() reports enabled again", main.C.enabled() is True)

# --- column alignment: colour must not shift the layout ---------------------
hdr = {"exporter": "10.0.0.1", "sys_uptime": 100000, "unix_secs": 1700000000,
       "version": 5}
rec = {"src_addr": "192.168.1.10", "dst_addr": "8.8.8.8", "src_port": 51000,
       "dst_port": 443, "proto": 6, "packets": 12, "octets": 1500,
       "tcp_flags": 0x18, "first_switched": 90000, "last_switched": 100000}
ns = argparse.Namespace(verbose=False)
resolver = Resolver(mode="off")


def render_line(octets, colour, scale=None):
    out = io.StringIO()
    real, sys.stdout = sys.stdout, out
    try:
        if not colour:
            main.C.disable()
        r = dict(rec, octets=octets)
        main.render(r, hdr, ns, resolver, scale or main.SizeScale())
    finally:
        sys.stdout = real
        for n, v in saved.items():
            setattr(main.C, n, v)
    return out.getvalue().rstrip("\n")


for octets in (40, 1500, 250_000):
    coloured = ESC.sub("", render_line(octets, True))
    plain = render_line(octets, False)
    check("colour leaves the %d-byte row aligned" % octets, coloured == plain,
          "\n  %r\n  %r" % (coloured, plain))

check("the BYTES cell keeps its 8-column width",
      ESC.sub("", render_line(1500, True)) == render_line(1500, False))
check("a coloured row really contains a ramp colour",
      "\033[38;5;" in render_line(1500, True))

resolver.shutdown()

# --- the two flags are mutually exclusive -----------------------------------
proc = subprocess.run([sys.executable, *SCRIPT, "--size-scale-max", "1M",
                       "--size-scale-dynamic"],
                      capture_output=True, text=True)
check("--size-scale-max and --size-scale-dynamic cannot be combined",
      proc.returncode == 2 and "not allowed with" in proc.stderr,
      repr(proc.stderr[-200:]))

bad = subprocess.run([sys.executable, *SCRIPT, "--size-scale-max", "nope"],
                     capture_output=True, text=True)
check("a bad --size-scale-max is rejected at startup", bad.returncode == 2)

# --- the ramp has to sit alongside the rest of the palette -------------------
# The report is otherwise drawn in the terminal's own sixteen colours, which
# take their tone from the reader's theme. A ramp of primaries beside them
# looks like it wandered in from another program, so every step is picked from
# the muted part of the cube.
CUBE_LEVELS = (0, 95, 135, 175, 215, 255)


def cube_rgb(index):
    """The RGB an xterm-256 index stands for, or None outside the colour cube."""
    if not 16 <= index <= 231:
        return None
    index -= 16
    return (CUBE_LEVELS[index // 36], CUBE_LEVELS[(index // 6) % 6],
            CUBE_LEVELS[index % 6])


def cube_saturation(index):
    parts = cube_rgb(index)
    high, low = max(parts), min(parts)
    return 0.0 if high == 0 else (high - low) / high


check("every step is a colour cube entry",
      all(cube_rgb(step) for step in main.SIZE_RAMP),
      str([step for step in main.SIZE_RAMP if not cube_rgb(step)]))
check("no step is fully saturated",
      all(cube_saturation(step) < 1.0 for step in main.SIZE_RAMP),
      str([step for step in main.SIZE_RAMP if cube_saturation(step) >= 1.0]))
check("no step has a channel turned off entirely",
      all(min(cube_rgb(step)) > 0 for step in main.SIZE_RAMP),
      str([step for step in main.SIZE_RAMP if min(cube_rgb(step)) == 0]))
check("none of it is at the top of the cube either",
      all(max(cube_rgb(step)) < 255 for step in main.SIZE_RAMP),
      str([step for step in main.SIZE_RAMP if max(cube_rgb(step)) == 255]))
check("there are enough steps to rank a column",
      len(main.SIZE_RAMP) >= 8, str(len(main.SIZE_RAMP)))
check("every step is distinct", len(set(main.SIZE_RAMP)) == len(main.SIZE_RAMP))

cold, hot = cube_rgb(main.SIZE_RAMP[0]), cube_rgb(main.SIZE_RAMP[-1])
check("the ramp still runs from cold to warm",
      (hot[0] - hot[2]) > (cold[0] - cold[2]),
      "%s -> %s" % (cold, hot))


finish("size-scale")
