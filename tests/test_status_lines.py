"""What the status bar says, and what it drops when the window is narrow.

Both line builders are ordinary functions over an ordinary dictionary, so
everything here runs without a terminal in sight.
"""
import re

from harness import check, finish, plain

import nettail as main
from nettail.statusbar import Rates, run_line, status_lines, wire_line

WIDE = 200
NARROW = 80
MB = 1024 * 1024


def snap(**over):
    """A busy but untroubled collector, unless a check says otherwise."""
    base = {
        "elapsed": 252.0,
        "packets": 3100, "flows": 12400, "bytes_rx": 48 * MB,
        "pkt_rate": 12.0, "flow_rate": 1200.0, "bit_rate": 9.4e6,
        # Byte figures are binary here as they are everywhere else: the bar
        # prints them through the same human_bytes the BYTES column uses.
        "external_bytes": 41 * MB, "inbound": 28.1 * MB, "outbound": 12.9 * MB,
        "counted_bytes": 67 * MB,
        "peak": 142e6,
        "top_talker": ("93.184.216.34", "edge", 5.4 * MB),
        "resolve": "all", "fqdn": False,
        "names_found": 412, "names_missed": 88, "names_dropped": 0,
        "scale_top": 4 * MB, "scale_dynamic": True, "scale_window": 0,
        "external_only": False, "paused": False, "held": 0,
        "versions": ["v9"], "templates": 6,
        "deferred": 0, "gaps": 0, "parse_errors": 0, "sampling": 0,
        "lead_proto": ("TCP", 78), "lead_service": ("443/https", 41),
    }
    base.update(over)
    return base


# --- line one carries the wire, widest first --------------------------------
wire = plain(wire_line(snap(), WIDE))
for field in ("up 04:12", "pkts 3.1k", "flows 12.4k", "rx 48.0M",
              "ext 61%", "in 28.1M", "out 12.9M", "top 93.184.216.34 (edge)"):
    check(f"wire line carries {field!r}", field in wire, repr(wire))
check("bit rate is spelt as the summary spells it", "9.4 Mbps" in wire, repr(wire))
check("wire line fits the window", len(wire) <= WIDE, str(len(wire)))

# --- line two carries the run -----------------------------------------------
run = plain(run_line(snap(), WIDE))
for field in ("names all/short", "412 found", "88 missed", "scale dyn 4.0M",
              "all flows", "live", "v9 tmpl 6", "TCP 78%", "443/https 41%"):
    check(f"run line carries {field!r}", field in run, repr(run))

# --- fields are parted by two spaces, and nothing wraps ---------------------
check("segments are parted by two spaces", "  " in wire and "\n" not in wire)
check("both lines are ASCII", all(ord(ch) < 128 for ch in wire + run),
      repr([ch for ch in wire + run if ord(ch) > 127]))

# --- narrowing drops the tail, never cuts a field in half -------------------
narrow = plain(wire_line(snap(), NARROW))
check("narrow wire line fits", len(narrow) <= NARROW, f"{len(narrow)}: {narrow!r}")
check("the leftmost field survives", narrow.startswith("up 04:12"), repr(narrow))
check("the rightmost field is the one that goes", "top 93.184" not in narrow,
      repr(narrow))
check("a surviving field is whole", "ext 61%" not in narrow or
      "out 12.9M" in narrow, repr(narrow))

# --- trouble outranks anything merely informational -------------------------
troubled = snap(gaps=2, deferred=5, parse_errors=3, sampling=100,
                names_dropped=4)
wide_trouble = plain(run_line(troubled, WIDE))
narrow_trouble = plain(run_line(troubled, NARROW))
check("narrow troubled line fits", len(narrow_trouble) <= NARROW,
      f"{len(narrow_trouble)}: {narrow_trouble!r}")
for field in ("gaps 2", "defer 5", "bad 3", "sampled 1:100", "lookups lost 4"):
    check(f"{field!r} survives at 80 columns", field in narrow_trouble,
          repr(narrow_trouble))
check("the leading service is dropped to make room", "443/https" in wide_trouble
      and "443/https" not in narrow_trouble, repr(narrow_trouble))

# --- paused is a state you are not allowed to miss --------------------------
held = plain(run_line(snap(paused=True, held=412), NARROW))
check("paused says how much is being held", "paused 412 held" in held, repr(held))
check("paused survives a narrow window", len(held) <= NARROW, repr(held))
check("live is what it says when it is not paused",
      "live" in plain(run_line(snap(), WIDE)))

# --- the other shapes each field takes --------------------------------------
check("resolution off says so briefly",
      "names off" in plain(run_line(snap(resolve="off"), WIDE)))
check("fqdn is visible in the mode", "all/fqdn" in
      plain(run_line(snap(fqdn=True), WIDE)))
check("a windowed scale says how long the window is", "scale dyn/64" in
      plain(run_line(snap(scale_window=64), WIDE)))
check("a fixed scale is just the figure", "scale 100.0K" in
      plain(run_line(snap(scale_dynamic=False, scale_top=102400), WIDE)))
check("external-only is spelt out", "external only" in
      plain(run_line(snap(external_only=True), WIDE)))
check("more than one version is joined", "v5+v9 tmpl 6" in
      plain(run_line(snap(versions=["v5", "v9"]), WIDE)))
check("an unnamed talker loses only the parentheses",
      "top 8.8.8.8 5.4M" in plain(wire_line(
          snap(top_talker=("8.8.8.8", None, 5.4 * MB)), WIDE)))
check("nothing external yet means no share is claimed",
      "ext " not in plain(wire_line(snap(counted_bytes=0), WIDE)))

# --- a window too narrow for even one field ---------------------------------
# Width is counted in what a reader sees, so the check has to count it that way.
tiny = wire_line(snap(), 10)
check("one field is kept when only one will go", plain(tiny) == "up 04:12",
      repr(plain(tiny)))
check("and it is still coloured", tiny != plain(tiny), repr(tiny))

hopeless = wire_line(snap(), 5)
check("a field wider than the window is cut to fit", len(hopeless) <= 5,
      repr(hopeless))
check("cutting it drops the colour rather than sever an escape",
      "\033" not in hopeless, repr(hopeless))

# --- both rows come back together, sharing one set of columns ---------------
def fields(line):
    """Each field on a row, and the column it starts in.

    Fields are parted by at least two spaces and never contain two in a row,
    which is what makes them findable without the segments that built them.
    """
    return [(m.start(), m.group()) for m in
            re.finditer(r"(?:^|(?<=  ))\S(?:\S| (?! ))*", line)]


first, second = status_lines(snap(), WIDE)
top_fields, bottom_fields = fields(plain(first)), fields(plain(second))
check("status_lines returns the two rows",
      [text for _at, text in top_fields] == [text for _at, text in fields(wire)]
      and [text for _at, text in bottom_fields] == [text for _at, text in fields(run)],
      repr(plain(first)))
shared = min(len(top_fields), len(bottom_fields))
check("the two rows start their fields in the same columns",
      [at for at, _t in top_fields[:shared]]
      == [at for at, _t in bottom_fields[:shared]],
      "%s vs %s" % ([at for at, _t in top_fields],
                    [at for at, _t in bottom_fields]))
check("seven fields on each row when the window can hold them",
      len(top_fields) == 7 and len(bottom_fields) == 7,
      "%d and %d" % (len(top_fields), len(bottom_fields)))
narrowed = [len(fields(plain(row))) for row in status_lines(snap(), 100)]
check("and fewer, on both rows, when it cannot", max(narrowed) < 7, str(narrowed))

# --- the rate window --------------------------------------------------------
r = Rates()
check("no rate until there are two samples", r.per_second() == (0.0, 0.0, 0.0))
r.observe(0, 0, 0, now=1000.0)
r.observe(10, 100, 1000, now=1001.0)
pkts, flows, bits = r.per_second()
check("datagrams per second", pkts == 10.0, str(pkts))
check("flows per second", flows == 100.0, str(flows))
check("bits per second counts eight to the byte", bits == 8000.0, str(bits))

r.observe(11, 110, 1100, now=1001.05)
check("a sample too soon to matter is ignored", r.per_second()[0] == 10.0,
      str(r.per_second()))

r.observe(20, 200, 2000, now=1010.0)
check("samples older than the window fall out of it",
      len(r._samples) == 2, str(list(r._samples)))

r.observe(0, 0, 0, now=1011.0)
check("clearing the counters does not make the rate negative",
      r.per_second() == (0.0, 0.0, 0.0), str(r.per_second()))

# --- figures carry further than the units around them -----------------------
painted = main.statusbar._seg(3, "rx", "48.0M 9.4 Mbps")[2]
check("the figure is painted plainly", main.C.CYAN + "48.0" + main.C.RESET in painted,
      repr(painted))
check("the unit after it is dimmed", main.C.DIM + main.C.CYAN + "M " in painted,
      repr(painted))
check("both figures on a field get it", main.C.CYAN + "9.4" + main.C.RESET in painted,
      repr(painted))
abbreviated = main.statusbar._seg(3, "", "TCP 78% 443/https 41%")[2]
check("an abbreviation before a figure is dimmed too",
      abbreviated.startswith(main.C.DIM + main.C.CYAN + "TCP "), repr(abbreviated))
check("a field with no figures in it is left whole",
      main.statusbar._seg(5, "", "live", main.C.GREEN)[2]
      == main.C.GREEN + "live" + main.C.RESET,
      repr(main.statusbar._seg(5, "", "live", main.C.GREEN)[2]))
check("the plain text is untouched by any of it",
      main.statusbar._seg(3, "rx", "48.0M 9.4 Mbps")[1] == "rx 48.0M 9.4 Mbps")

# --- and it all still reads with the colour off -----------------------------
main.C.disable()
bare_wire, bare_run = status_lines(snap(gaps=2), WIDE)
check("no escapes survive --no-color", "\033" not in bare_wire + bare_run)
check("the figures are still there", "flows 12.4k" in bare_wire
      and "gaps 2" in bare_run, repr(bare_run))
gap = bare_wire.split("up 04:12")[1]
check("whitespace is all the structure that is left, and never less than two",
      gap.startswith("  ") and gap.lstrip().startswith("pkts"), repr(bare_wire))
# The grid spans the window, not each row on its own: the last column is as
# wide as the wider of the two fields in it, so whichever row holds that one
# reaches the margin and the other stops short of it.
check("the grid is spread the whole width of the window",
      max(len(bare_wire), len(bare_run)) == WIDE,
      "%d and %d of %d" % (len(bare_wire), len(bare_run), WIDE))
check("neither row overruns it", max(len(bare_wire), len(bare_run)) <= WIDE)
check("and neither is left padded past its last field",
      not bare_wire.endswith(" ") and not bare_run.endswith(" "),
      repr(bare_wire[-20:]) + " / " + repr(bare_run[-20:]))

finish("status lines")
