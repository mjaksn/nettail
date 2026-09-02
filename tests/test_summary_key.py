"""The s key prints the traffic summary without stopping the collector."""
import argparse
import io
import os
import socket
import struct
import sys
import time
from collections import Counter

from harness import FakeTTY, check, finish, plain
from lanname import Resolver
from netflume import SamplingWatch, SequenceWatch

import nettail as main
from nettail import cli

# --- write_summary stands on its own ---------------------------------------
args = argparse.Namespace(resolve="all", json=False, external_only=False,
                          fqdn=False, header_every=40, verbose=False)
stats = Counter({"packets": 12, "bytes_rx": 4096, "flows": 30,
                 "templates_new": 2, "option_records": 1})
tally = main.Tally()
for peer, octets in (("9.9.9.9", 5000), ("1.1.1.1", 100)):
    tally.add({"src_addr": "192.168.1.5", "dst_addr": peer, "proto": 6,
               "octets": octets, "packets": 10, "src_port": 51000,
               "dst_port": 443}, {"exporter": "10.0.0.1"})
resolver = Resolver(mode="off", workers=1)
sequences = SequenceWatch()
sampling = SamplingWatch()

out = io.StringIO()
cli.write_summary(stats, tally, resolver, sequences, sampling, args,
                  time.time() - 42, out=out)
report = plain(out.getvalue())
check("the report has a heading", "Summary" in report)
check("it counts datagrams", "datagrams received 12" in report, repr(report[:200]))
check("it counts flows", "flows decoded      30" in report)
check("it reports the runtime as a clock", "runtime            00:00:42" in report,
      repr([ln for ln in report.splitlines() if "runtime" in ln]))
check("the clock pads the hours", main.human_clock(42) == "00:00:42",
      main.human_clock(42))
check("and fills them in as they arrive", main.human_clock(3 * 3600 + 4 * 60 + 5)
      == "03:04:05", main.human_clock(3 * 3600 + 4 * 60 + 5))
check("a run past a day keeps counting hours rather than wrapping",
      main.human_clock(50 * 3600) == "50:00:00", main.human_clock(50 * 3600))
check("nothing yet is still a clock", main.human_clock(0) == "00:00:00")
check("and a missing runtime is not", main.human_clock(None) == "-")
check("it lists the top talkers", "9.9.9.9" in report and "1.1.1.1" in report)
lines = report.splitlines()
check("each external address is split by direction",
      any(ln.split() == ["9.9.9.9", "4.9K", "0B/4.9K"] for ln in lines),
      repr([ln for ln in lines if "9.9.9.9" in ln]))
check("it lists the internal addresses too",
      "Top internal addresses by bytes" in report
      and any(ln.split() == ["192.168.1.5", "5.0K", "0B/5.0K"] for ln in lines),
      repr([ln for ln in lines if "192.168.1.5" in ln]))
check("it writes where it is told", sys.stdout is not out)

# --- the arrows share a column ----------------------------------------------
# Every table that puts two endpoints on a row pads the first half to one
# width, so the arrows fall in a column rather than wherever the address in
# front of them happened to stop. Nothing else would fail if that came back:
# the rows would still be right, and only a reader would pay for it.
NOW = 1700000000.0
WIDE_HDR = {"exporter": "10.0.0.1", "unix_secs": int(NOW)}
wide = main.Tally()
for src, dst, octets in (("10.0.0.1", "9.9.9.9", 5000),
                         ("192.168.1.5", "1.1.1.1", 4000),
                         ("172.16.30.100", "203.0.113.9", 3000)):
    wide.add({"src_addr": src, "dst_addr": dst, "proto": 6, "octets": octets,
              "packets": 10, "src_port": 51000, "dst_port": 443,
              "flow_start_ms": int(NOW * 1000),
              "flow_end_ms": int((NOW + 3) * 1000)}, WIDE_HDR)
buf = io.StringIO()
cli.write_summary(stats, wide, resolver, sequences, sampling, args,
                  time.time() - 42, out=buf)
wide_report = plain(buf.getvalue())


def arrow_columns(text, heading, arrow):
    """Where the arrow sits in each row of one table, as a set of columns."""
    columns = set()
    seen = False
    for line in text.splitlines():
        if line.strip() == heading:
            seen = True
        elif seen and not line.strip():
            break
        elif seen and arrow in line:
            columns.add(line.index(arrow))
    return columns


for heading in ("Busiest 5 pairs by volume", "Busiest 5 pairs by packets"):
    found = arrow_columns(wide_report, heading, cli.PAIR_ARROW)
    check("every arrow under %s sits in one column" % heading,
          len(found) == 1, "%s in %r" % (sorted(found), heading))
found = arrow_columns(wide_report, "Longest 5 flows", cli.FLOW_ARROW)
check("and so does every arrow among the longest flows",
      len(found) == 1, str(sorted(found)))
check("the fixture really had first halves of differing width to line up",
      len({len(pair[0]) for pair, _octets in wide.top_pairs_by_bytes()}) > 1,
      str([pair[0] for pair, _octets in wide.top_pairs_by_bytes()]))

# --- the names share a column too -------------------------------------------
# A name opens three spaces past the widest named address in its column, so
# the brackets line up down a table rather than trailing each address by one.
# An address with no name does not count: nothing on its row needs clearing.


class Names:
    """A resolver that knows two addresses of different widths, and no more."""

    # Short names, so that the longest-flow rows, which carry a port on each
    # end, still fit their column once the names are set in: a row wider than
    # the table has its first half trimmed, and the check below is about
    # where a name opens and not about what the trim does.
    # 172.16.30.100 is the widest address on the left and has no name, so
    # it is the one the measure has to look past.
    KNOWN = {"10.0.0.1": "a", "192.168.1.5": "b",
             "9.9.9.9": "q", "203.0.113.9": "d"}
    stats = Counter({"resolved": 4, "via_dns": 4, "via_mdns": 0,
                     "via_netbios": 0, "missed": 2, "dropped": 0,
                     "evicted": 0})

    def lookup(self, addr):
        return self.KNOWN.get(addr)


buf = io.StringIO()
cli.write_summary(stats, wide, Names(), sequences, sampling, args,
                  time.time() - 42, out=buf)
named_report = plain(buf.getvalue())


def bracket_columns(text, heading, arrow=None):
    """Where the first name opens in each row of one table, as a set."""
    columns = set()
    seen = False
    for line in text.splitlines():
        if line.strip() == heading:
            seen = True
        elif seen and not line.strip():
            break
        elif seen and "(" in line:
            # Past the arrow it is the other column's name, aligned on its own.
            head = line.split(arrow)[0] if arrow else line
            if "(" in head:
                columns.add(head.index("("))
    return columns


for heading, arrow, widest in (("Busiest 5 pairs by volume", cli.PAIR_ARROW,
                                "192.168.1.5"),
                               ("Longest 5 flows", cli.FLOW_ARROW,
                                "192.168.1.5:51000"),
                               ("Top internal addresses by bytes", None,
                                "192.168.1.5")):
    found = bracket_columns(named_report, heading, arrow)
    check("every name under %s opens in one column" % heading,
          len(found) == 1, "%s in %r" % (sorted(found), heading))
    check("and three spaces past the widest named address there",
          any(widest + "   (" in line for line in named_report.splitlines()),
          repr([ln for ln in named_report.splitlines() if widest in ln][:2]))
check("a wider address with no name does not push the names out",
      "10.0.0.1      (a)" in named_report and "172.16.30.100   (" not in named_report,
      repr([ln for ln in named_report.splitlines() if "10.0.0.1" in ln][:2]))
# --- a column widens to show a name whole ------------------------------------
# Drawn at its old width, a longest-flow row with a name on each end had its
# first half trimmed. The column now takes what the rows need when the window
# has it, and only a window without the room trims.


class LongNames(Names):
    KNOWN = {"10.0.0.1": "homeassistant", "172.16.30.100": "homeassistant",
             "192.168.1.5": "homeassistant",
             "9.9.9.9": "quad9-resolver", "203.0.113.9": "documentation"}


def report_at(columns):
    os.environ["COLUMNS"] = str(columns)
    try:
        buf = io.StringIO()
        cli.write_summary(stats, wide, LongNames(), sequences, sampling, args,
                          time.time() - 42, out=buf)
    finally:
        del os.environ["COLUMNS"]
    return plain(buf.getvalue())


roomy = report_at(140)
check("with room, every name is shown whole",
      "..." not in roomy and "(homeassistant) -> 203.0.113.9:443" in roomy,
      repr([ln for ln in roomy.splitlines() if "..." in ln or "-> 203" in ln]))
check("and no row runs past the window",
      all(len(ln) <= 140 for ln in roomy.splitlines()),
      str(max(len(ln) for ln in roomy.splitlines())))
# The same conversation seen both ways, named at the local end of each, is the
# shape that puts the name on the left in one row and on the right in the
# other. No single row is as wide as the table, since every first half is
# padded to the widest one, and measuring row by row trimmed both names.
both_ways = main.Tally()
for src, sport, dst, dport in (("192.168.1.5", 49296, "17.57.152.35", 993),
                               ("17.57.152.35", 993, "192.168.1.5", 49296)):
    both_ways.add({"src_addr": src, "dst_addr": dst, "proto": 6,
                   "octets": 5000, "packets": 10, "src_port": sport,
                   "dst_port": dport, "flow_start_ms": int(NOW * 1000),
                   "flow_end_ms": int((NOW + 3) * 1000)}, WIDE_HDR)
os.environ["COLUMNS"] = "120"
try:
    buf = io.StringIO()
    cli.write_summary(stats, both_ways, LongNames(), sequences, sampling, args,
                      time.time() - 42, out=buf)
finally:
    del os.environ["COLUMNS"]
two_way = plain(buf.getvalue())
check("a name on the right of one row and the left of the other is shown whole",
      "..." not in two_way
      and "(homeassistant) -> 17.57.152.35:993" in two_way
      and "-> 192.168.1.5:49296   (homeassistant)" in two_way,
      repr([ln for ln in two_way.splitlines() if cli.FLOW_ARROW in ln]))

# Ninety columns is room for the pair rows whole and not for the longest-flow
# rows, which carry a port on each end; narrower than a column's old width
# there is no trimming to do, since a column never draws narrower than that.
cramped = report_at(90)
check("without it, the row is trimmed rather than wrapped",
      any("..." in ln for ln in cramped.splitlines() if cli.FLOW_ARROW in ln)
      and all(len(ln) <= 90 for ln in cramped.splitlines()),
      str(max(len(ln) for ln in cramped.splitlines())))
check("and only where the room ran out",
      not any("..." in ln for ln in cramped.splitlines() if cli.PAIR_ARROW in ln),
      repr([ln for ln in cramped.splitlines() if "<->" in ln][:2]))
check("the right hand names line up among themselves",
      len({line.split(cli.PAIR_ARROW)[1].index("(")
           for line in named_report.splitlines()
           if cli.PAIR_ARROW in line and "(" in line.split(cli.PAIR_ARROW)[1]})
      == 1, repr([ln for ln in named_report.splitlines() if "<->" in ln]))

# --- the address table header sits over the figures it names ----------------
# The header row was drawn at a width written down beside the one the rows
# were measured at, and the two agreed only while nothing widened the column.
# A name long enough to widen it carried every figure right and left "bytes"
# and "in/out" standing short of the numbers they name. Both come off one
# width now. Nothing else fails when they part: the figures are all still
# right, and only a reader following a heading down to its column pays for it.


class WideName(Names):
    """Long enough on one row to push the column past the width it starts at."""

    KNOWN = dict(Names.KNOWN,
                 **{"192.168.1.5": "homeassistant.lounge.internal.example.lan"})


os.environ["COLUMNS"] = "140"
try:
    buf = io.StringIO()
    cli.write_summary(stats, wide, WideName(), sequences, sampling, args,
                      time.time() - 42, out=buf)
finally:
    del os.environ["COLUMNS"]
widened = plain(buf.getvalue())


def slash_columns(text, heading):
    """Where the in/out slash falls, over the header row and every row under it.

    One column means the header is above its own figures. The slash is the
    landmark because it is the only one in either line: an address has none
    and neither does a name.
    """
    columns, seen = set(), False
    for line in text.splitlines():
        if line.strip() == heading:
            seen = True
        elif seen and not line.strip():
            break
        elif seen and "/" in line:
            columns.add(line.index("/"))
    return columns


check("the name that widened the column is drawn whole",
      "(homeassistant.lounge.internal.example.lan)" in widened,
      repr([ln for ln in widened.splitlines() if "192.168.1.5" in ln][:2]))
for heading in ("Top external addresses by bytes",
                "Top internal addresses by bytes"):
    found = slash_columns(widened, heading)
    check("the header of %s sits over its figures" % heading,
          len(found) == 1, "%s in %r" % (sorted(found), heading))

# sampling and gaps appear when there is something to say
sampling.note("10.0.0.1", 0, {"sampling_interval": 1000})
for seq in (0, 10, 20):
    sequences.observe("10.0.0.1", 0, 10, seq, 10)
sequences.observe("10.0.0.1", 0, 10, 40, 10)
out = io.StringIO()
cli.write_summary(stats, tally, resolver, sequences, sampling, args,
                  time.time(), out=out)
report = plain(out.getvalue())
check("export gaps appear when there are any", "Export gaps" in report
      and "never arrived" in report)
check("sampling appears when advertised",
      "Sampling" in report and "1 in 1000" in report)
resolver.shutdown()


# --- the key calls it -------------------------------------------------------
def build():
    a = argparse.Namespace(json=False, external_only=False, fqdn=False,
                           resolve="off", header_every=40, verbose=False)
    r = Resolver(mode="off", workers=1)
    printed = []
    controls = main.Controls(a, main.SizeScale(), r, None, Counter(), main.Tally(),
                             SequenceWatch(), out=io.StringIO(),
                             summary=lambda: printed.append("report"))
    return controls, printed, r


c, printed, r = build()
check("s prints the report", c.handle("s") is None and printed == ["report"],
      str(printed))
check("s adds no chatter of its own", c.out.getvalue() == "", repr(c.out.getvalue()))
check("s can be pressed again", c.handle("s") is None and len(printed) == 2)
check("uppercase S works too", c.handle("S") is None and len(printed) == 3)
check("s does not quit", c.quit is False)
check("s does not pause", c.paused is False)
r.shutdown()

c, printed, r = build()
c.summary = None
check("s with nothing to print is harmless", c.handle("s") is None)
r.shutdown()

# The reminder line points at the ? listing rather than naming the keys, so
# the listing is where the s key has to be advertised.
check("the key listing describes it",
      "summary" in dict(main.KEYS)["s"], dict(main.KEYS).get("s"))


# --- through the loop -------------------------------------------------------
V5_HDR = struct.Struct("!HHIIIIBBH")
V5_REC = struct.Struct("!4s4s4sHHIIIIHHBBBBHHBBH")


def v5_packet(seq, count=3):
    pkt = V5_HDR.pack(5, count, 100000, int(time.time()), 0, seq, 0, 0, 0)
    for i in range(count):
        pkt += V5_REC.pack(
            bytes([192, 168, 1, 10 + i]), bytes([8, 8, 8, 8]), bytes([192, 168, 1, 1]),
            1, 2, 12, 1500, 90000, 100000, 51000 + i, 443, 0,
            0x18, 6, 0, 0, 0, 24, 24, 0)
    return pkt


def run(script, packets, argv=()):
    keys = list(script)
    queue = list(packets)

    class FakeSocket:
        calls = 0

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
            FakeSocket.calls += 1
            if FakeSocket.calls > 500:
                raise KeyboardInterrupt
            if queue:
                return queue.pop(0), ("10.0.0.1", 2055)
            raise socket.timeout

    main.Keyboard.start = lambda self: setattr(self, "enabled", True) or True
    main.Keyboard.poll = lambda self: (keys.pop(0) if keys and self.enabled else None)
    main.Keyboard.stop = lambda self: setattr(self, "enabled", False)
    socket.socket = FakeSocket

    out, err = FakeTTY(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    sys.argv = ["nettail", "--resolve", "off", "--no-color"] + list(argv)
    try:
        main.main()
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    return out.getvalue(), err.getvalue()


out, err = run([None, "s", None, "\x1b", None], [v5_packet(0)])
check("the report is printed twice: once for s, once on the way out",
      err.count("Summary") == 2, "%d times" % err.count("Summary"))
check("the mid-run report counts what had arrived by then",
      err.count("flows decoded      3") == 2,
      repr([ln for ln in err.splitlines() if "flows decoded" in ln]))
check("flows keep printing after the report", len(
    [ln for ln in out.splitlines() if "8.8.8.8" in ln]) == 3)
check("s did not stop the collector", "closing" in err)

# the report reflects the moment it is asked for
out, err = run([None, "s", None, None, "s", None, "\x1b", None],
               [v5_packet(0), v5_packet(3)])
decoded = [ln.strip() for ln in err.splitlines() if "flows decoded" in ln]
check("three reports in all", len(decoded) == 3, str(decoded))
check("each report is a snapshot of that moment",
      decoded == ["flows decoded      3", "flows decoded      6",
                  "flows decoded      6"], str(decoded))

# c then s: the report reflects the cleared counters
out, err = run([None, "c", None, "s", None, "\x1b", None], [v5_packet(0)])
decoded = [ln.strip() for ln in err.splitlines() if "flows decoded" in ln]
check("clearing statistics shows through in the report",
      decoded == ["flows decoded      0", "flows decoded      0"], str(decoded))

finish("summary key")
