"""The status bar driven through the real receive loop.

Covers the parts only main() decides: that the bar comes up on a terminal
without being asked for, that --hide-status is enough to be rid of it, that it
shares one scroll region with the pinned header, and that it lets go of the
screen before the summary is written into it.
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


nettail.statusbar.enable_windows_vt = lambda: True
nettail.sticky.enable_windows_vt = lambda: True
shutil.get_terminal_size = lambda fallback=(80, 24): shutil.os.terminal_size((120, 30))
socket.socket = FakeSocket


def without_the_bar(text):
    """The screen with the bar's own repaints taken back out of it.

    The bar names the top external talker, and in these packets that is the
    same 8.8.8.8 every flow row carries, so counting flows in the raw stream
    counts the bar along with them. How many times the bar painted depends on
    whether the run happened to cross its half-second repaint interval, which
    on a loaded machine is the difference between 120 and 121.

    Everything the bar draws sits between a cursor save and the restore that
    follows it, which is what makes it separable from the flows at all.
    """
    kept, rest = [], text
    while "\0337" in rest:
        head, _, rest = rest.partition("\0337")
        kept.append(head)
        _bar, _, rest = rest.partition("\0338")
    kept.append(rest)
    return "".join(kept)


def run(*argv):
    out, err = FakeTTY(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    sys.argv = ["nettail", "--resolve", "off"] + list(argv)
    try:
        main.main()
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    return out.getvalue(), err.getvalue()


# --- on by default, with nothing asked for ----------------------------------
o, e = run("--header-every", "5")
check("the bar claims the foot of the window on its own", "\033[1;28r" in o,
      repr(o[:60]))
check("the top of the window is left to scroll", "\033[2;28r" not in o)
check("it draws on the bottom two rows", "\033[29;1H" in o and "\033[30;1H" in o)
check("it saves and restores the cursor around every paint",
      o.count("\0337") == o.count("\0338") and o.count("\0337") >= 1,
      "%d saves, %d restores" % (o.count("\0337"), o.count("\0338")))
# Both rows reach the screen. What is on them is a moment in a run these
# datagrams cross in a millisecond, so the figures themselves are checked
# against a snapshot in test_status_lines rather than against the clock here.
bar_text = plain(o.split("\0337")[1].split("\0338")[0])
check("the wire row reaches the bar", "up " in bar_text and "pkts " in bar_text,
      repr(bar_text))
check("the run row reaches the bar", "names off" in bar_text
      and "all flows" in bar_text, repr(bar_text))
flows_only = without_the_bar(o)
check("flows rendered underneath it", flows_only.count("8.8.8.8") == 120,
      "found %d" % flows_only.count("8.8.8.8"))
check("the header still repeats, since nothing is pinned above",
      o.count("TIME") > 1, "found %d" % o.count("TIME"))
check("the rows are wiped on the way out", "\033[29;1H\033[2K\033[30;1H\033[2K"
      in o.split("8.8.8.8")[-1], repr(o[-120:]))
check("the region is released", "\033[r" in o)
check("released after the last flow", o.index("\033[r") > o.rindex("8.8.8.8"))
check("the summary still prints", "flows decoded      120" in plain(e),
      repr(plain(e)[-200:]))
# The key list names the b key, which is a different thing from announcing
# that the bar has started, so it is not what this is looking for.
announced = "\n".join(line for line in plain(e).splitlines()
                      if not line.startswith("keys:"))
check("nothing is said about the bar starting or not",
      "status" not in announced.lower(), repr(announced[:400]))
# Against the key table rather than against this run's banner: whether the
# reminder line is printed at all depends on stdin being a terminal, which is a
# fact about how the suite was invoked and not about the key. The table is what
# the ? listing prints, and is where a key is advertised now that the reminder
# line only points at it.
check("but the b key is offered like every other",
      "status bar" in dict(main.KEYS)["b"], dict(main.KEYS).get("b"))

# --- --hide-status is enough to be rid of it --------------------------------
o, e = run("--header-every", "5", "--hide-status")
check("no region claimed under --hide-status", "r" not in o.replace("\033[0m", "")
      or "\033[1;28r" not in o, repr(o[:60]))
check("nothing painted on the bottom rows", "\033[29;1H" not in o)
check("no cursor save and restore either", "\0337" not in o)
check("flows still render", without_the_bar(o).count("8.8.8.8") == 120,
      "found %d" % without_the_bar(o).count("8.8.8.8"))
check("the summary is unaffected", "flows decoded      120" in plain(e))

# --- alongside the pinned header: one region, both features ------------------
o, e = run("--sticky-header", "--header-every", "5")
check("one region covers the header and the bar", "\033[2;28r" in o, repr(o[:80]))
check("no second region for the bar alone", "\033[1;28r" not in o, repr(o[:80]))
check("the header is painted once on row 1", o.count("\033[1;1H") == 1)
check("the bar is painted on the bottom rows", "\033[29;1H" in o)
check("flows render between the two", without_the_bar(o).count("8.8.8.8") == 120,
      "found %d" % without_the_bar(o).count("8.8.8.8"))
check("everything is released before the summary",
      o.index("\033[r") > o.rindex("8.8.8.8"))
check("the summary survives both", "flows decoded      120" in plain(e))

# --- --json is a stream, not a screen ---------------------------------------
o, e = run("--json")
check("no bar under --json", "\033[29;1H" not in o and "\0337" not in o)
check("no scroll region under --json", "\033[1;28r" not in o)
check("the JSON is clean", o.lstrip().startswith("{"), repr(o[:60]))

finish("status shutdown")
