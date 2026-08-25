"""The two-line status bar along the foot of the window.

The pinned header claims a row at the top and pays for it in scrollback: with a
top margin set, nothing ever leaves the top of the screen, and leaving the top
of the screen is how a line earns a place in the history buffer. A bottom
margin costs nothing of the sort. Lines still scroll off row 1 exactly as they
always did, and most terminals go on saving them; the two rows at the foot are
simply held back from the scrolling. It is the same trick apt uses to keep a
progress bar under its output, and it fails the same way on the terminals that
decline to save margined output at all, kitty and iTerm2 among them.

Reading the collector and drawing on a terminal are kept apart here, the split
keys.py already makes between what a key means and what a terminal does.
`snapshot` and `status_lines` are ordinary functions over ordinary values, so
what the bar says can be tested without a terminal anywhere in sight, and
`StatusBar` is the half that needs one.
"""

import re
import shutil
import sys
import time
from collections import deque

from .colour import C
from .sticky import MIN_STICKY_ROWS, enable_windows_vt, scroll_region
from .values import human_bits, human_bytes, human_count

STATUS_ROWS = 2          # screen rows reserved at the foot
MIN_STATUS_ROWS = 6      # two rows of bar over four of flows is the least worth having
REPAINT_INTERVAL = 0.5   # seconds between redraws, however busy the network is
RATE_WINDOW = 5.0        # seconds the "per second" figures are averaged over
MIN_SAMPLE_GAP = 0.1     # closer than this and a sample tells us nothing new

SEPARATOR = "  "         # two spaces, which is all the parting a field needs

# Where a segment sits in the queue to be dropped when the window is narrow.
# Lower goes last. Everything that says something is wrong shares the front of
# the queue, so a narrow window loses the leading service before it loses the
# export gaps.
TROUBLE = 0


class Rates:
    """Per-second figures, averaged over the last few seconds.

    Fed the running totals rather than increments, because that is what the
    collector has to hand, and differencing two samples of a total is proof
    against a missed call in a way that summing increments is not.
    """

    def __init__(self, window=RATE_WINDOW):
        self.window = window
        self._samples = deque(maxlen=256)

    def observe(self, packets, flows, octets, now=None):
        """Note where the totals stand. Cheap enough to call every time round.

        On the monotonic clock, not the wall one: these are spans between two
        samples, and a clock the NTP daemon can step backwards would freeze
        every rate on the bar for as long as the correction lasted.
        """
        now = time.monotonic() if now is None else now
        if self._samples:
            last = self._samples[-1]
            if now - last[0] < MIN_SAMPLE_GAP:
                return
            if packets < last[1] or flows < last[2] or octets < last[3]:
                # The counters went backwards, so the c key cleared them. An
                # average across that moment would be a negative rate.
                self._samples.clear()
        self._samples.append((now, packets, flows, octets))
        cutoff = now - self.window
        while len(self._samples) > 2 and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def per_second(self):
        """Datagrams, flows and bits per second. Zeroes until there are two samples."""
        if len(self._samples) < 2:
            return 0.0, 0.0, 0.0
        first, last = self._samples[0], self._samples[-1]
        span = last[0] - first[0]
        if span <= 0:
            return 0.0, 0.0, 0.0
        return ((last[1] - first[1]) / span,
                (last[2] - first[2]) / span,
                (last[3] - first[3]) * 8 / span)


def _clock(seconds):
    """A runtime that stays the same width as it grows."""
    seconds = int(max(seconds, 0))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _per_second(n):
    """A rate, given no more precision than it deserves."""
    if n >= 1000:
        return f"{human_count(int(n))}/s"
    if n >= 10:
        return f"{int(n)}/s"
    return f"{n:.1f}/s"


def _share(part, whole):
    """A percentage, or None when there is nothing to take a share of."""
    if not whole:
        return None
    return int(round(100.0 * part / whole))


_FIGURE = re.compile(r"\d[\d.,:]*")


def _figures(text, colour):
    """A value with its figures in `colour` and the rest of it dimmed.

    A field on the bar is a number or two wrapped in units, prefixes and
    abbreviations, and it is the numbers being read: `48.0` wants to carry
    further than the `M` after it, `78` further than the `TCP` before it.
    Dimming the wrapping rather than giving it a colour of its own keeps the
    palette exactly where it was, so that red still means loss and green
    still means running, and asks the eye to sort figure from unit by weight.

    A value with no figures in it is left alone. `live` and `ok` are the whole
    of what they say, and there is nothing there to tell apart.
    """
    if not C.enabled() or not _FIGURE.search(text):
        return f"{colour}{text}{C.RESET}"
    painted, at = [], 0
    for match in _FIGURE.finditer(text):
        if match.start() > at:
            painted.append(f"{C.DIM}{colour}{text[at:match.start()]}{C.RESET}")
        painted.append(f"{colour}{match.group()}{C.RESET}")
        at = match.end()
    if at < len(text):
        painted.append(f"{C.DIM}{colour}{text[at:]}{C.RESET}")
    return "".join(painted)


def _seg(rank, label, value, colour=None):
    """One field of the bar: a grey label and a figure, kept together.

    A narrow window drops whole segments rather than cutting one in half, so
    each carries the width it needs alongside the text it paints.
    """
    colour = C.CYAN if colour is None else colour
    value = str(value)
    if label:
        return (rank, f"{label} {value}",
                f"{C.GREY}{label}{C.RESET} {_figures(value, colour)}")
    return (rank, value, _figures(value, colour))


def _assemble(segments, width):
    """Fit as many segments as will go, dropping the least important first."""
    kept = [seg for seg in segments if seg is not None]
    while len(kept) > 1 and len(SEPARATOR.join(s[1] for s in kept)) > width:
        # Least important goes first; on a tie the rightmost, so a line loses
        # its tail rather than develops a hole in the middle.
        worst = max(range(len(kept)), key=lambda i: (kept[i][0], i))
        kept.pop(worst)
    plain = SEPARATOR.join(s[1] for s in kept)
    if len(plain) > width:
        # One segment on its own, wider than the window. Colour cannot be cut
        # to length without cutting an escape in half, so the plain text goes.
        return plain[:width]
    if len(kept) < 2:
        return "".join(s[2] for s in kept)

    # The room left over is shared out between the fields rather than left in
    # one heap at the right-hand end, so the line uses the window it was given
    # instead of huddling against the left edge of it. Evenly, and the odd
    # column to the leftmost gaps, which keeps the last field flush with the
    # right margin whatever the arithmetic works out to.
    #
    # This is no more restless than left-aligning would be: a figure that
    # gains a digit takes a column out of one gap, so what follows it shifts
    # by one either way.
    slack = width - len(plain)
    gaps = len(kept) - 1
    even, odd = divmod(slack, gaps)
    line = []
    for index, segment in enumerate(kept[:-1]):
        line.append(segment[2])
        line.append(" " * (len(SEPARATOR) + even + (1 if index < odd else 0)))
    line.append(kept[-1][2])
    return "".join(line)


def snapshot(stats, tally, resolver, sequences, sampling, scale, args,
             controls, rates):
    """Everything the bar shows, read out of the collector in one pass.

    A plain dictionary rather than a view onto the live objects, so the two
    rows describe one consistent moment, and so a test can write the moment it
    wants to see rendered without building a collector to produce it.
    """
    pkt_rate, flow_rate, bit_rate = rates.per_second()
    proto = tally.proto_bytes.most_common(1)
    service = tally.service_bytes.most_common(1)
    talker = tally.talkers.most_common(1)
    proto_total = sum(tally.proto_bytes.values())
    service_total = sum(tally.service_bytes.values())

    top = None
    if talker:
        addr, octets = talker[0]
        top = (addr, resolver.lookup(addr), octets)

    return {
        "elapsed": time.time() - controls.started,
        "packets": stats["packets"],
        "flows": stats["flows"],
        "bytes_rx": stats["bytes_rx"],
        "pkt_rate": pkt_rate,
        "flow_rate": flow_rate,
        "bit_rate": bit_rate,
        "external_bytes": tally.external_bytes,
        "inbound": tally.inbound_bytes,
        "outbound": tally.outbound_bytes,
        # Every decoded flow lands in one protocol bucket or another, so that
        # total is what the external share is a share of.
        "counted_bytes": proto_total,
        # The floor the summary reports as the minimum link speed: the fastest
        # the link is known to have run, rather than an estimate of what it was
        # asked for. A figure that cannot be argued with belongs on a bar that
        # has no room to qualify it.
        "peak": tally.min_link_speed(),
        "top_talker": top,
        "resolve": args.resolve,
        "fqdn": bool(getattr(args, "fqdn", False)),
        "names_found": resolver.stats["resolved"],
        "names_missed": resolver.stats["missed"],
        "names_dropped": resolver.stats["dropped"],
        "scale_top": scale.top,
        "scale_dynamic": scale.dynamic,
        "scale_window": scale.window,
        "external_only": bool(args.external_only),
        "paused": controls.paused,
        "held": len(controls.held),
        "versions": [f"v{v}" for v in (5, 9, 10) if stats[f"v{v}_msgs"]],
        "templates": stats["templates_new"],
        "deferred": stats["deferred"],
        "gaps": sum(sequences.missed.values()),
        "parse_errors": stats["parse_errors"] + stats["malformed"],
        "sampling": max(sampling.rates.values()) if sampling.rates else 0,
        "lead_proto": ((proto[0][0], _share(proto[0][1], proto_total))
                       if proto else None),
        "lead_service": (service[0][0], _share(service[0][1], service_total))
                        if service else None,
    }


def _wire_segments(snap):
    """Row one's fields: what is arriving, and how fast."""
    segments = [
        _seg(1, "up", _clock(snap["elapsed"])),
        _seg(2, "pkts", f"{human_count(snap['packets'])} "
                        f"{_per_second(snap['pkt_rate'])}"),
        _seg(3, "flows", f"{human_count(snap['flows'])} "
                         f"{_per_second(snap['flow_rate'])}"),
        _seg(4, "rx", f"{human_bytes(snap['bytes_rx'])} "
                      f"{human_bits(snap['bit_rate'])}"),
    ]

    share = _share(snap["external_bytes"], snap["counted_bytes"])
    if share is not None:
        segments.append(_seg(5, "ext", f"{share}% in {human_bytes(snap['inbound'])} "
                                       f"out {human_bytes(snap['outbound'])}"))
    if snap["peak"]:
        # Ranked below the top talker despite sitting to its left: of the two,
        # who is talking is the more useful thing to keep in a narrow window.
        segments.append(_seg(7, "peak", human_bits(snap["peak"])))
    if snap["top_talker"]:
        addr, name, octets = snap["top_talker"]
        named = f"{addr} ({name})" if name else str(addr)
        segments.append(_seg(6, "top", f"{named} {human_bytes(octets)}"))
    return segments


def _trouble(snap):
    """The one field for everything going wrong, or for nothing being wrong.

    All of it in a single column, so the two rows keep the same number of
    fields however much there is to report. Left as one field per complaint,
    a run with four things wrong would push the run row four fields past the
    wire row and the columns would stop lining up exactly when the bar was
    most worth reading.
    """
    parts = []
    if snap["gaps"]:
        parts.append(("gaps", snap["gaps"], C.RED))
    if snap["deferred"]:
        parts.append(("defer", snap["deferred"], C.YELLOW))
    if snap["parse_errors"]:
        parts.append(("bad", snap["parse_errors"], C.RED))
    if snap["names_dropped"]:
        parts.append(("lookups lost", snap["names_dropped"], C.YELLOW))
    if snap["sampling"]:
        parts.append(("sampled", f"1:{snap['sampling']}", C.YELLOW))
    if not parts:
        return _seg(TROUBLE, "", "ok", C.GREEN)
    plain = " ".join(f"{label} {value}" for label, value, _colour in parts)
    painted = " ".join(f"{C.GREY}{label}{C.RESET} {_figures(str(value), colour)}"
                       for label, value, colour in parts)
    return (TROUBLE, plain, painted)


def _run_segments(snap):
    """Row two's fields: what is switched on, and anything going wrong.

    Ordered narrowest first, which is not how the row reads so much as how it
    lines up: the columns are shared with the wire row above, and that row runs
    from a short runtime to a long address. Pairing wide with wide and narrow
    with narrow is the difference between a tidy grid and twenty-odd spaces
    sitting between `up 04:12` and whatever follows it.
    """
    if snap["resolve"] == "off":
        names = "off"
    else:
        names = (f"{snap['resolve']}/{'fqdn' if snap['fqdn'] else 'short'} "
                 f"{snap['names_found']} found {snap['names_missed']} missed")

    if snap["scale_dynamic"]:
        how = f"dyn/{snap['scale_window']}" if snap["scale_window"] else "dyn"
        scale = f"{how} {human_bytes(snap['scale_top'])}"
    else:
        scale = human_bytes(snap["scale_top"])

    if snap["paused"]:
        state = _seg(TROUBLE, "", f"paused {snap['held']} held", C.YELLOW)
    else:
        state = _seg(5, "", "live", C.GREEN)

    segments = [
        state,
        _seg(4, "", "external only" if snap["external_only"] else "all flows"),
    ]
    if snap["versions"]:
        segments.append(_seg(6, "+".join(snap["versions"]),
                             f"tmpl {snap['templates']}"))
    segments.append(_seg(3, "scale", scale))
    segments.append(_trouble(snap))

    lead = []
    if snap["lead_proto"] and snap["lead_proto"][1] is not None:
        lead.append(f"{snap['lead_proto'][0]} {snap['lead_proto'][1]}%")
    if snap["lead_service"] and snap["lead_service"][1] is not None:
        lead.append(f"{snap['lead_service'][0]} {snap['lead_service'][1]}%")
    if lead:
        segments.append(_seg(7, "", " ".join(lead)))

    segments.append(_seg(2, "names", names))
    return segments


def wire_line(snap, width):
    """Row one on its own, for looking at one row in isolation."""
    return _assemble(_wire_segments(snap), width)


def run_line(snap, width):
    """Row two on its own, for looking at one row in isolation."""
    return _assemble(_run_segments(snap), width)


def _columns(rows, width):
    """Both rows laid out down one set of columns.

    The bar reads downwards as well as across: the third field of the wire row
    and the third of the run row begin in the same place, so the eye can follow
    a column instead of hunting along a line for where one field ended and the
    next began.

    A column is as wide as the wider of the two fields sharing it, and that is
    what the alignment costs. The rows no longer each spend the window on their
    own contents, so between them they run out of room sooner and a field goes
    that would have fitted on a row of its own.
    """
    rows = [[seg for seg in row if seg is not None] for row in rows]

    def measure():
        count = max(len(row) for row in rows)
        widths = [max(len(row[i][1]) for row in rows if i < len(row))
                  for i in range(count)]
        return count, widths, sum(widths) + len(SEPARATOR) * (count - 1)

    count, widths, needed = measure()
    while needed > width:
        # The least important field anywhere on the bar goes, whichever row it
        # is on. On a tie the rightmost, so a row loses its tail rather than
        # develops a hole in the middle of itself.
        victim, worst = None, None
        for row in rows:
            if len(row) < 2:
                continue
            index = max(range(len(row)), key=lambda i: (row[i][0], i))
            if worst is None or (row[index][0], index) > worst:
                victim, worst = row, (row[index][0], index)
        if victim is None:
            break
        victim.pop(worst[1])
        count, widths, needed = measure()

    slack = max(width - needed, 0)
    even, odd = divmod(slack, count - 1) if count > 1 else (0, 0)

    drawn = []
    for row in rows:
        plain = SEPARATOR.join(seg[1] for seg in row)
        if len(plain) > width:
            # One field wider than the whole window. Colour cannot be cut to
            # length without cutting an escape in half, so the plain text goes.
            drawn.append(plain[:width])
            continue
        line = []
        for index, segment in enumerate(row):
            line.append(segment[2])
            if index < len(row) - 1:
                gap = (widths[index] - len(segment[1]) + len(SEPARATOR)
                       + even + (1 if index < odd else 0))
                line.append(" " * gap)
        drawn.append("".join(line))
    return drawn


def status_lines(snap, width):
    """Both rows of the bar, coloured, sharing one set of columns."""
    first, second = _columns([_wire_segments(snap), _run_segments(snap)], width)
    return first, second


class StatusBar:
    """Holds the last two rows of the window and keeps them current."""

    def __init__(self, stream=None, sticky=None):
        self.stream = stream if stream is not None else sys.stdout
        # The pinned header, when there is one. DECSTBM is a single pair of
        # margins, so exactly one of us writes the region and it is whichever
        # claimed the screen first.
        self.sticky = sticky
        self.active = False
        self.rows = 0
        self.cols = 0
        self._painted_at = 0.0
        # What was last drawn, so the bar can be put back after something else
        # clears the screen without waiting for the next set of figures.
        self._last = None

    def usable(self):
        """Whether a bar could be drawn here.

        Asked before the header starts, because the header has to know how many
        rows are left for it, and it cannot find out by trying.
        """
        if not self.stream.isatty() or not enable_windows_vt():
            return False
        size = shutil.get_terminal_size(fallback=(0, 0))
        return size.lines >= MIN_STATUS_ROWS and size.columns >= 1

    def start(self):
        """Claim the bottom rows. True if the bar is now on screen."""
        if not self.usable():
            return False
        size = shutil.get_terminal_size(fallback=(0, 0))
        self.rows, self.cols = size.lines, size.columns
        self.active = True
        if self.sticky is None or not self.sticky.active:
            # Scrolled clear rather than erased, and only ever here, on the
            # first claim. This bar is on by default, so it starts on a screen
            # someone was already using: \033[2J would wipe that outright,
            # while a windowful of newlines pushes it up into the scrollback,
            # which is the very thing a bottom margin was chosen to protect.
            self.stream.write("\n" * self.rows)
        self._claim()
        return True

    def resume(self):
        """Take the rows back part way through a run. What the b key turns on.

        Not start(): that scrolls the window clear, which is right when the
        collector is starting and quite wrong when it has been running for an
        hour. Only the two rows the bar is about to cover are scrolled up, so
        the flows that were on them are pushed into the history rather than
        painted over.
        """
        if self.active or not self.usable():
            return False
        size = shutil.get_terminal_size(fallback=(0, 0))
        if (self.sticky is not None and self.sticky.active
                and size.lines - STATUS_ROWS < MIN_STICKY_ROWS):
            # Room for the header or the bar, not for both. The header was
            # asked for and the bar was not, so the header keeps the window.
            return False
        self.rows, self.cols = size.lines, size.columns
        self.stream.write("\n" * STATUS_ROWS)
        self.active = True
        if self.sticky is not None and self.sticky.active:
            self.sticky.rows, self.sticky.cols = size.lines, size.columns
            self.sticky.bottom_reserved = STATUS_ROWS
            self.sticky.reflow()
        else:
            self._claim()
        return True

    def _claim(self):
        """Set the scroll region, unless the header has already set it for both.

        Draws nothing and clears nothing, so it is as usable for laying the
        margins out again after a resize as it is for the first claim. DECSTBM
        homes the cursor, so it is put back at the foot of the region for the
        flows to go on scrolling up from.
        """
        if self.sticky is not None and self.sticky.active:
            # It wrote one region covering both reservations. A second DECSTBM
            # here would replace that, not add to it.
            return
        self.stream.write(
            scroll_region(self.rows, 0, STATUS_ROWS)    # scroll rows 1..bottom-2
            + f"\033[{self.rows - STATUS_ROWS};1H")     # cursor into the region
        self.stream.flush()

    def update(self, snap_of, now=None, force=False):
        """Redraw, at most every REPAINT_INTERVAL seconds.

        Takes something to call rather than a snapshot, so that reading the
        counters costs nothing on the calls that are too soon to draw. This
        is called every time round a loop that also turns on a quarter second
        socket timeout.
        """
        if not self.active:
            return False
        now = time.monotonic() if now is None else now
        if not force and now - self._painted_at < REPAINT_INTERVAL:
            return False
        self._painted_at = now
        self.check_resize()
        if not self.active:                 # the window shrank out from under us
            return False
        self._paint(snap_of())
        return True

    def repaint(self):
        """Draw the rows again, after something else cleared the screen."""
        if self.active and self._last is not None:
            self._paint(self._last)

    def _paint(self, snap):
        """Draw both rows, putting the cursor back where the flows are."""
        self._last = snap
        # One column short of the window: a line that fills the last cell of
        # the last row makes some terminals scroll, which would carry the bar
        # away with it.
        width = max(self.cols - 1, 1)
        first, second = status_lines(snap, width)
        top = self.rows - STATUS_ROWS + 1
        self.stream.write(
            "\0337"                                    # save the cursor
            f"\033[{top};1H\033[2K{first}"
            f"\033[{top + 1};1H\033[2K{second}"
            "\0338"                                    # and put it back
        )
        self.stream.flush()

    def check_resize(self):
        """Re-measure the window, and lay both features out again if it moved.

        The bar polls for the pair of them while it is on. Two pollers with
        two ideas of how many rows are spoken for is how the margins end up
        disagreeing with what is drawn in them.
        """
        if not self.active:
            return
        size = shutil.get_terminal_size(fallback=(self.cols, self.rows))
        if size.lines == self.rows and size.columns == self.cols:
            return
        if size.lines < MIN_STATUS_ROWS or size.columns < 1:
            # The new geometry first: stop() addresses rows by where the bar
            # is now, and a window that has already shrunk no longer has the
            # rows it was drawn on.
            self.rows, self.cols = max(size.lines, STATUS_ROWS), size.columns
            self.stop()
            return
        self.rows, self.cols = size.lines, size.columns
        if self.sticky is not None and self.sticky.active:
            if size.lines - STATUS_ROWS < MIN_STICKY_ROWS:
                # No longer room for both. The header stands down and the bar
                # carries on, which is the way round the two were documented:
                # the bar is the one nobody asked for and nobody can miss.
                self.sticky.rows, self.sticky.cols = size.lines, size.columns
                self.sticky.stop()
                self._claim()
                return
            # One region, one writer: hand the header the new size and let it
            # lay the margins out again on behalf of both of us.
            self.sticky.rows, self.sticky.cols = size.lines, size.columns
            self.sticky.repaint()
        else:
            self._claim()

    def stop(self):
        """Wipe the rows and give the margins back. Safe when never started."""
        if not self.active:
            return
        self.active = False
        top = self.rows - STATUS_ROWS + 1
        self.stream.write(f"\033[{top};1H\033[2K\033[{top + 1};1H\033[2K")
        if self.sticky is not None and self.sticky.active:
            # The header owns the region, so it has to be told the rows are
            # free and then asked to write the margins again. On the way out
            # that is merely tidy, but when the bar gives up mid-run it is the
            # difference between the flows using the whole window and them
            # scrolling in a region two rows short of it. reflow() rather than
            # repaint() because repainting clears the screen, and the flows on
            # it should stay where they are.
            self.sticky.bottom_reserved = 0
            self.sticky.reflow()
        else:
            self.stream.write(f"\033[r\033[{self.rows};1H\n")
        self.stream.flush()
