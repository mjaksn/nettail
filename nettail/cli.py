"""Argument parsing, the receive loop, and the exit summary.
"""

import argparse
import io
import json
import os
import queue
import signal
import socket
import sys
import time
from collections import Counter

from lanname import MODE_DESC, Resolver
from lanname.resolver import RESOLVER_CACHE_MAX
from netflume import (
    DecodeError,
    Decoder,
    ExportGap,
    SamplingChange,
    addr_kind,
    flow_endpoints,
    flow_timestamp,
)

from . import __version__, services
from .colour import C, PlainStream, colour_on, strip_colour
from .display import (
    COLUMNS,
    ENDPOINT_WIDTH,
    HEADER_LINE,
    proto_colour,
    render,
    row_cells,
)
from .feed import Feed
from .keys import (
    KEY_CHARS,
    KEY_HELP,
    QR_KEY,
    Controls,
    Keyboard,
    web_buttons,
    web_keys,
    write_keys,
)
from .qr import window as qr_window
from .qr import write_qr
from .sizescale import (
    DEFAULT_SIZE_SCALE_MAX,
    SizeScale,
    SpanScale,
    size_scale_arg,
    size_window_arg,
)
from .statusbar import (
    REPAINT_INTERVAL,
    STATUS_ROWS,
    Rates,
    StatusBar,
    snapshot,
)
from .sticky import HEADER_ROWS, MIN_STICKY_ROWS, StickyHeader
from .tally import Tally
from .values import human_bits, human_bytes, human_clock, human_count, human_duration
from .web import (
    DEFAULT_WEB_PORT,
    KEY_QUEUE_MAX,
    WEB_ENDPOINT_WIDTH,
    WEB_TOKEN_ENV,
    WebInterface,
    in_container,
    is_loopback,
    unpad,
    web_host_arg,
    web_token_arg,
)


def should_show(rec, args):
    if not args.external_only:
        return True
    # The same two ends the display and the summary use, so that what is hidden
    # here and what is counted as external there cannot disagree.
    src, dst = flow_endpoints(rec)
    return (bool(dst) and addr_kind(dst) == "public") or (
        bool(src) and addr_kind(src) == "public")


def flow_record(rec, hdr, resolver):
    """The dictionary `--json` prints, and the one the web feed carries.

    One builder for both, so that a parser reading stdout and a browser reading
    the stream can never come to disagree about what a flow is. The exporter and
    the version are read off the header rather than passed in, which is what
    lets a flow held during a pause be rebuilt on the way out without carrying
    either of them along in the buffer.

    Built only when something is going to read it. The display path wants none
    of this, and on a busy link assembling a dictionary per flow for nobody
    would be real work in the hot path.
    """
    out = dict(rec)
    out["_exporter"] = hdr["exporter"]
    out["_version"] = hdr["version"]
    out["_timestamp"] = flow_timestamp(rec, hdr)
    src_host = resolver.lookup(rec.get("src_addr"))
    dst_host = resolver.lookup(rec.get("dst_addr"))
    if src_host:
        out["src_host"] = src_host
    if dst_host:
        out["dst_host"] = dst_host
    return out


# Whether the browser is to be shown colour. Settled once by main() and read
# by the publish paths below, rather than threaded through a dozen callers
# that have no other interest in it. It stays True for a run with no web
# interface, where nothing reads it.
_WEB_COLOUR = True


def for_web(text):
    """Prose on its way to the feed, with colour taken out if it was refused.

    The terminal's copy has already been dealt with by then, either at the
    source or by the stream it was written to, so this is the browser's half
    of the same question.
    """
    return text if _WEB_COLOUR else strip_colour(text)


def colour_choice(args, isatty, no_colour_env):
    """Whether the terminal and the browser each take colour.

    Two answers because there are two readers. `--colour` is the terminal's,
    and means what it always meant: `auto` gives colour to a terminal and
    withholds it from a redirected stream, where escape codes are somebody
    else's problem to strip. `NO_COLOR` is a convention about a terminal, so
    it is scoped to that one and leaves the browser alone.

    The browser's answer is `--web-colour`, on unless it is turned off,
    because a browser is a colour-capable reader whatever stdout happens to
    be. It used to be decided by `--colour`, and a detached container, which
    has no terminal by definition and is the arrangement the image exists for,
    therefore served a colourless view unless somebody knew to say
    `--colour always`.
    """
    wanted = "never" if args.no_color else args.colour
    if wanted == "always":
        terminal = True
    elif wanted == "never":
        terminal = False
    else:
        terminal = bool(isatty) and not no_colour_env
    return terminal, bool(args.web) and args.web_colour == "on"


def tee(bus, kind, render, out=None, per_reader=False):
    """Print a block of prose, and hand the same characters to the feed.

    `render` is anything that takes an `out=` and prints to it, which is nearly
    every piece of prose this program emits: the summary, the host list, the key
    listing, the decoder notices. Capturing first and then writing what was
    captured is what keeps the two views honest. A browser is shown the
    characters the terminal was shown, rather than a second rendering of the
    same facts that could drift from the first.

    With nobody watching there is nothing to capture for, so the block is
    rendered straight at the terminal and no buffer is built at all.

    One rendering serves both readers even when they want different colour,
    because neither is handed the buffer directly: a terminal that is not
    having colour is behind a stream that takes it out, and a browser that is
    not having it gets the same treatment from `for_web`. Colour is the only
    thing that differs, and taking it out of finished text is exactly as good
    as never painting it.

    `per_reader` is for the one block where that is not true. The host list
    marks a superseded name with a trailing star when there is no colour to
    dim it with, so a reader without colour is shown different words rather
    than the same words undressed, and stripping cannot put the star back.
    That block is rendered once for each reader when the two disagree. It is
    a flag rather than the rule because rendering twice means reading live
    state twice: `write_summary` alone would recompute how long the run has
    been going and ask the resolver for every top talker's name again, so the
    two copies could differ by more than colour and the second pass would
    enqueue a second round of lookups.
    """
    out = out if out is not None else sys.stderr
    if bus is None or not bus.active:
        render(out)
        return
    if per_reader and colour_on(out) != _WEB_COLOUR:
        captured = io.StringIO()
        render(captured if _WEB_COLOUR else PlainStream(captured))
        render(out)
        out.flush()
        bus.prose(kind, captured.getvalue())
        return
    buffer = io.StringIO()
    render(buffer)
    text = buffer.getvalue()
    out.write(text)
    out.flush()
    bus.prose(kind, for_web(text))


class _ProseTee:
    """A stream that writes to the terminal and publishes finished lines.

    What `Controls` answers a key with is one line printed to its `out`, and
    print() reaches a stream twice for that, once with the text and once with
    the newline. Publishing on each write would put half a line in front of a
    browser and then an empty one, so this holds what it is handed until a
    newline arrives and publishes a line at a time.

    The buffer is split whether or not anything is watching, so that a browser
    connecting mid-line is never handed the tail of a sentence it missed the
    start of, and so that nothing accumulates through a long unwatched run.
    """

    def __init__(self, bus, stream):
        self.bus = bus
        self.stream = stream
        self._pending = ""

    def write(self, text):
        self.stream.write(text)
        self._pending += text
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            if line and self.bus is not None and self.bus.active:
                self.bus.prose("reply", for_web(line))
        return len(text)

    def flush(self):
        self.stream.flush()

    def isatty(self):
        return self.stream.isatty()


def _painted(*pieces):
    """A cell built from (text, colour) pieces.

    Hands back the plain text alongside the coloured text, because a column is
    padded to the width a reader sees and escape codes are not seen.
    """
    pieces = [piece for piece in pieces if piece[0]]
    plain_text = "".join(text for text, _colour in pieces)
    painted = "".join(f"{colour}{text}{C.RESET}" if colour else text
                      for text, colour in pieces)
    return plain_text, painted


def _address_colour(addr):
    """Cyan for a public address, blue for a local one, grey for the rest.

    Looked up when it is needed rather than held in a table, so that turning
    colour off after import still takes effect.
    """
    kind = addr_kind(addr) if addr else "unknown"
    if kind == "public":
        return C.CYAN
    if kind == "private":
        return C.BLUE
    return C.GREY


PAIR_ARROW = " <-> "     # between the two ends of one conversation
FLOW_ARROW = " -> "      # from a flow's source towards its destination
SUMMARY_WIDTH = 120      # how long a summary row may run with no window to ask


def _fitted(cell, width):
    """A cell padded to width, or trimmed to it saying so, text and paint both.

    Both come back because whatever goes next to the cell has to measure what
    a reader sees, and neither the escape codes nor the padding are that.
    """
    plain_text, painted = cell
    if len(plain_text) > width:
        plain_text = plain_text[:width - 3] + "..."
        return plain_text, f"{C.CYAN}{plain_text}{C.RESET}"
    pad = " " * (width - len(plain_text))
    return plain_text + pad, painted + pad


def _column(cell, width):
    """Pad a cell to width, or trim it saying so rather than let a row wrap."""
    return _fitted(cell, width)[1]


def _arrow_column(halves, arrow, width):
    """How wide the first half of an endpoint pair is drawn, for a whole table.

    Left aligned, an arrow stops wherever the address in front of it happens
    to stop, and the eye hunts for the break in every row. One width for the
    first half of every row puts the arrows in a column instead. Where the
    widest row fits as it is, that width is the widest first half and nothing
    is trimmed for it. Where it does not, the first half gets what is left
    once the widest second half has its room, and never less than half the
    line: past that the trimming has moved from one side to the other rather
    than stopped.
    """
    room = width - len(arrow[0])
    widest = max(len(plain) for (plain, _paint), _right in halves)
    facing = max(len(plain) for _left, (plain, _paint) in halves)
    if widest + facing <= room:
        return widest
    return min(widest, max(room - facing, room // 2))


def _endpoints(left, arrow, right, column):
    """Two ends of a conversation, the arrow between them starting at column."""
    text, colour = arrow
    left_plain, left_painted = _fitted(left, column)
    right_plain, right_painted = right
    return (f"{left_plain}{text}{right_plain}",
            f"{left_painted}{colour}{text}{C.RESET}{right_painted}")


def report_events(events, args, out=None):
    """Say out loud what the decoder noticed while reading the wire.

    netflume raises these as objects and prints nothing itself, which is what
    a library owes its caller. Putting them on screen is this program's job,
    and the wording is about what the reader has to do rather than about what
    went past: a sampling notice exists to say the counts below are short, and
    a gap notice to say the loss happened before the decoder ever saw it.

    Undecodable datagrams are held back unless --verbose. On a port anyone can
    reach, one malformed sender would otherwise scroll the flows away.
    """
    out = out if out is not None else sys.stderr
    for event in events:
        if isinstance(event, SamplingChange):
            if event.rate == 1:
                print(f"{C.GREEN}{event.exporter} now reports no sampling. "
                      f"Counts below are complete again.{C.RESET}", file=out)
            else:
                print(f"{C.YELLOW}{event.exporter} reports 1-in-{event.rate} "
                      f"sampling. The byte and packet counts shown are a "
                      f"sample, so real traffic is roughly {event.rate}x "
                      f"higher. Turn sampling off at the exporter for true "
                      f"counts.{C.RESET}", file=out)
        elif isinstance(event, ExportGap):
            print(f"{C.RED}{event.exporter} skipped {event.missed} "
                  f"{event.unit}. Exports are being lost before they reach "
                  f"the decoder; check the network path and whether this "
                  f"collector is keeping up.{C.RESET}", file=out)
        elif isinstance(event, DecodeError) and args.verbose:
            print(f"{C.RED}{event.reason} datagram from {event.exporter}: "
                  f"{event.detail}{C.RESET}", file=out)


def write_hosts(resolver, out=None, hosts=None):
    """List the local addresses seen this session and the names they answered to.

    Everything discovered at any point, not what happens to be cached: names
    expire and the cache evicts, and the useful question hours later is still
    "what did you see". Where an address answered to more than one name the
    most recent leads and the rest follow, dimmed, or marked with a star when
    there is no colour to dim. That question is asked of the stream being
    written to rather than of the program, because a run can be showing a
    browser colour while this terminal is having none.

    `hosts` is for the caller that renders this twice, once for each reader.
    Taken here it would be read twice, and a name the resolver discovered in
    between would appear in one copy and not the other.
    """
    out = out if out is not None else sys.stderr
    hosts = resolver.local_hosts() if hosts is None else hosts
    print(f"\n{C.BOLD}{C.BLUE}Local hosts seen{C.RESET}", file=out)
    if not hosts:
        print(f"  {C.GREY}none yet{C.RESET}", file=out)
        return
    for addr, names in hosts:
        current, older = names[0], names[1:]
        shown = [f"{C.GREEN}{current}{C.RESET}"]
        for name in older:
            shown.append(f"{C.DIM}{name}{C.RESET}"
                         if colour_on(out) else f"{name}*")
        print(f"  {C.CYAN}{addr:<18}{C.RESET} {'  '.join(shown)}", file=out)
    print(f"  {C.GREY}{len(hosts)} address{'' if len(hosts) == 1 else 'es'}"
          f"{'' if colour_on(out) else ', * marks a name that has been superseded'}"
          f"{C.RESET}", file=out)


def write_summary(stats, tally, resolver, sequences, sampling, args,
                  started, out=None):
    """Print what arrived, what it was, and what it implies about the link.

    Called on the way out, and from the s key while running, which is why it
    takes what it needs as arguments rather than reaching into the loop.
    """
    out = out if out is not None else sys.stderr

    # An address column is drawn as wide as its rows need and no narrower
    # than it always was, so that a name is shown whole wherever there is
    # room for it, and trimmed only where there is not. The room is the
    # window the report is going to: `out` when that is a terminal, and
    # stderr when `out` is the buffer `tee` renders into for the browser's
    # copy, since the same characters reach stderr from there. With neither,
    # a file or a pipe, nothing wraps and SUMMARY_WIDTH is the row that is
    # long enough.
    row_width = (qr_window(out)[0] or qr_window(sys.stderr)[0]
                 or SUMMARY_WIDTH)

    def table_width(needed, default, overhead):
        """How wide an address column is drawn, for one table.

        `needed` is what the widest row asks for, `default` the width the
        column was always drawn at, and `overhead` what else is on the row
        beside it: the margin, the figures, and the gaps between.
        """
        return max(default, min(needed, row_width - overhead))

    # Gathered before anything is printed, because the colour ramp below is
    # ranged over these rows and has to see the same figures the reader will.
    protocol_rows = tally.proto_bytes.most_common(8)
    service_rows = tally.service_bytes.most_common(8)
    pairs_by_bytes = tally.top_pairs_by_bytes()
    pairs_by_packets = tally.top_pairs_by_packets()
    longest = tally.longest_flows()
    talkers = tally.top_external(10)
    internal = tally.top_internal(10)

    # One ramp for the whole report, stretched over the figures it is about to
    # print, so a colour says how a number compares with its neighbours here.
    sizes = ([stats["bytes_rx"], tally.external_bytes, tally.inbound_bytes,
              tally.outbound_bytes]
             + [octets for _name, octets in protocol_rows]
             + [octets for _name, octets in service_rows]
             + [octets for _pair, octets in pairs_by_bytes]
             + [details[5] for _duration, details in longest]
             + [octets for _ip, octets, _in, _out in talkers]
             + [octets for _ip, octets, _in, _out in internal])
    ramp = SpanScale(sizes)

    def address(addr, port, name_at):
        """One endpoint: the address, its port, and the name it answers to.

        Three things worth telling apart at a glance, so they get three
        colours rather than one run of text. The address itself is coloured by
        what kind it is, the same distinction the flow display draws: cyan for
        somewhere out on the internet, blue for somewhere on this network.

        `name_at` is the column the name's bracket opens in, which
        `with_names` works out for a whole column of addresses at once, and
        it is the only caller: where a name goes is never one row's to say.
        """
        pieces = [(str(addr) if addr else "-", _address_colour(addr))]
        if port:
            pieces.append((f":{port}", C.GREY))
        host = resolver.lookup(addr) if addr else None
        if host:
            gap = name_at - sum(len(text) for text, _colour in pieces)
            pieces += [(" " * gap + "(", C.GREY), (host, C.GREEN), (")", C.GREY)]
        return pieces

    def with_names(column):
        """One column of endpoints, their names starting in one place.

        Every name opens three spaces past the widest address in the column
        that has one. Addresses on one network are twelve to fifteen
        characters wide, so a name set one space in from its own address sits
        a different distance in on every row, and the eye has to find each
        one afresh. An address with no name is left out of the measure: there
        is no bracket on its row for the others to clear, and a wide stranger
        with no name would otherwise push every name in the column out past
        it for nothing. `column` is the (addr, port) of every row, and what
        comes back is a painted cell per row in the same order.
        """
        widest = max((len(str(addr) if addr else "-")
                      + (len(f":{port}") if port else 0)
                      for addr, port in column
                      if addr and resolver.lookup(addr)), default=0)
        return [_painted(*address(addr, port, name_at=widest + 3))
                for addr, port in column]

    def row(label, value, width=18):
        print(f"  {C.GREY}{label:<{width}}{C.RESET} {C.CYAN}{value}{C.RESET}",
              file=out)

    def size_row(label, octets, width=18):
        print(f"  {C.GREY}{label:<{width}}{C.RESET} "
              f"{ramp.paint(human_bytes(octets), octets)}", file=out)

    def heading(text):
        print(f"\n{C.BOLD}{C.BLUE}{text}{C.RESET}", file=out)

    def columns(name, *headings, widths=(9, 7, 9), name_width=16):
        """A dim header row, so the numbers under it need no unit beside them."""
        cells = "  ".join(f"{text:>{width}}"
                          for text, width in zip(headings, widths))
        print(f"  {C.GREY}{name:<{name_width}}{cells}{C.RESET}", file=out)

    # The widest size human_bytes writes is seven characters, and the left
    # half is padded to that so the slashes fall in one column down the table.
    SIZE_WIDTH = 7

    def in_out(inbound, outbound):
        """Two sizes either side of a slash, each on the ramp for its own size.

        Padded on the plain text before the paint goes on, since an aligned
        cell is aligned by what the reader sees and not by the escape codes
        around it. The right half is the end of the row and is not padded.
        """
        left, right = human_bytes(inbound), human_bytes(outbound)
        pad = " " * (SIZE_WIDTH - len(left))
        return (f"{pad}{ramp.paint(left, inbound)}"
                f"{C.GREY}/{C.RESET}{ramp.paint(right, outbound)}")

    def address_table(title, rows):
        """The busiest addresses on one side of the network edge.

        The total is split by direction beside it. What "in" and "out" mean,
        and why they mean the same thing for both sides, is written once
        where the counters are declared, in `Tally.reset`.
        """
        heading(title)
        columns("", "bytes", f"{'in':>{SIZE_WIDTH}}/out", widths=(10, 0),
                name_width=49)
        cells = with_names([(ip, None) for ip, _n, _i, _o in rows])
        width = table_width(max(len(plain) for plain, _paint in cells), 48,
                            overhead=2 + 1 + 10 + 2 + 2 * SIZE_WIDTH + 1)
        for (_ip, nbytes, inbound, outbound), cell in zip(rows, cells):
            print(f"  {_column(cell, width)} "
                  f"{ramp.paint(f'{human_bytes(nbytes):>10}', nbytes)}  "
                  f"{in_out(inbound, outbound)}", file=out)

    elapsed = time.time() - started
    heading("Summary")
    row("runtime", human_clock(elapsed))
    row("datagrams received", stats["packets"])
    size_row("bytes received", stats["bytes_rx"])
    row("flows decoded", stats["flows"])
    row("templates learned", stats["templates_new"])
    if stats["option_records"]:
        row("option records", stats["option_records"])
    if stats["deferred"]:
        print(f"  {C.YELLOW}data sets with no template  "
              f"{stats['deferred']}{C.RESET}", file=out)
    for key in ("malformed", "parse_errors", "unsupported_version"):
        if stats[key]:
            print(f"  {C.RED}{key:<18} {stats[key]}{C.RESET}", file=out)

    if tally.proto_bytes:
        heading("Protocols")
        columns("", "bytes", "flows", "packets")
        for name, octets in protocol_rows:
            print(f"  {proto_colour(name)}{name:<16}{C.RESET}"
                  f"{ramp.paint(f'{human_bytes(octets):>9}', octets)}  "
                  f"{C.CYAN}{human_count(tally.proto_flows[name]):>7}{C.RESET}  "
                  f"{C.CYAN}{human_count(tally.proto_packets[name]):>9}{C.RESET}",
                  file=out)

    if tally.service_bytes:
        heading("Services")
        columns("", "bytes", "flows")
        for name, octets in service_rows:
            # "443/https" is a number and a convention; colour says which half
            # is which, and which half to trust.
            port, slash, named = name.partition("/")
            cell = (_painted((port, C.CYAN), (slash, C.GREY), (named, C.GREEN))
                    if slash else _painted((name, C.GREEN)))
            print(f"  {_column(cell, 16)}"
                  f"{ramp.paint(f'{human_bytes(octets):>9}', octets)}  "
                  f"{C.CYAN}{human_count(tally.service_flows[name]):>7}{C.RESET}",
                  file=out)

    def pair_halves(pairs):
        """Both ends of every row, built once so they can be measured once."""
        return list(zip(with_names([(pair[0], None) for pair, _figure in pairs]),
                        with_names([(pair[1], None) for pair, _figure in pairs])))

    def halves_width(halves, arrow, default, overhead):
        """The endpoint column of a two-ended table, sized to its rows.

        Measured as the widest first half plus the widest second half, and
        not as the widest row: every first half is padded out to the widest
        one so that the arrows line up, so a row whose name is on the right
        is as wide as the widest name on the left plus its own. A pair of
        flows in opposite directions, named at the local end of each, is
        exactly that shape, and measuring row by row trimmed both.
        """
        needed = (max(len(left) for (left, _lp), _right in halves)
                  + len(arrow[0])
                  + max(len(right) for _left, (right, _rp) in halves))
        return table_width(needed, default, overhead)

    if pairs_by_bytes:
        arrow = (PAIR_ARROW, C.MAGENTA)

        heading(f"Busiest {tally.top} pairs by volume")
        halves = pair_halves(pairs_by_bytes)
        width = halves_width(halves, arrow, 58, overhead=2 + 1 + 9)
        column = _arrow_column(halves, arrow, width)
        for (left, right), (_pair, octets) in zip(halves, pairs_by_bytes):
            cell = _endpoints(left, arrow, right, column)
            print(f"  {_column(cell, width)} "
                  f"{ramp.paint(f'{human_bytes(octets):>9}', octets)}", file=out)

        heading(f"Busiest {tally.top} pairs by packets")
        halves = pair_halves(pairs_by_packets)
        width = halves_width(halves, arrow, 58, overhead=2 + 1 + 9)
        column = _arrow_column(halves, arrow, width)
        for (left, right), (_pair, packets) in zip(halves, pairs_by_packets):
            cell = _endpoints(left, arrow, right, column)
            print(f"  {_column(cell, width)} "
                  f"{C.CYAN}{human_count(packets):>9}{C.RESET}", file=out)

    if longest:
        arrow = (FLOW_ARROW, C.MAGENTA)
        heading(f"Longest {tally.top} flows")
        halves = list(zip(with_names([(d[0], d[1]) for _duration, d in longest]),
                          with_names([(d[2], d[3]) for _duration, d in longest])))
        width = halves_width(halves, arrow, 56, overhead=2 + 7 + 2 + 6 + 1 + 1 + 9)
        column = _arrow_column(halves, arrow, width)
        for (left, right), (duration, details) in zip(halves, longest):
            proto_name, octets = details[4], details[5]
            cell = _endpoints(left, arrow, right, column)
            print(f"  {C.CYAN}{human_duration(duration):>7}{C.RESET}  "
                  f"{proto_colour(proto_name)}{proto_name:<6}{C.RESET} "
                  f"{_column(cell, width)} "
                  f"{ramp.paint(f'{human_bytes(octets):>9}', octets)}", file=out)

    if tally.external_flows:
        heading("External traffic")
        size_row("total", tally.external_bytes)
        size_row("inbound", tally.inbound_bytes)
        size_row("outbound", tally.outbound_bytes)
        row("flows", tally.external_flows)
        floor = tally.min_link_speed()
        if not floor:
            row("minimum link speed", "not enough timing data to say")
        else:
            row("minimum link speed", human_bits(floor))
            busiest = tally.busiest_moment()
            if busiest:
                row("concurrent demand", f"{human_bits(busiest)}  "
                                         f"if every flow sent evenly")
            if tally.events_dropped:
                print(f"  {C.GREY}that estimate covers the first "
                      f"{tally.rated_flows} timed external flows; "
                      f"{tally.events_dropped} later ones were not sampled"
                      f"{C.RESET}", file=out)

    if tally.pruned:
        print(f"  {C.GREY}{tally.pruned} rare conversations were dropped along "
              f"the way to bound memory; the busiest are unaffected{C.RESET}",
              file=out)

    if sequences.missed:
        heading("Export gaps")
        for label, missed, unit in sequences.report():
            print(f"  {C.CYAN}{label:<18}{C.RESET} "
                  f"{C.RED}{missed} {unit} never arrived{C.RESET}", file=out)
        if sequences.backwards:
            row("repeated or reordered", sequences.backwards)
        if sequences.resyncs:
            row("counter restarts", sequences.resyncs)
    elif sequences.watched():
        row("export gaps", "none")

    if sampling.rates:
        heading("Sampling")
        # A rate belongs to an observation domain, not to a box, and one
        # exporter can run several and sample them differently. The domain is
        # named only where the bare address would be ambiguous, which is the
        # same rule the export gap rows above follow.
        domains = Counter(exporter for exporter, _domain in sampling.rates)

        def by_exporter_then_domain(item):
            # A domain is a number, so it has to sort as one: as text, domain
            # 10 comes before domain 2. The middle element keeps a domain that
            # was never stated at the end rather than comparing None to an int.
            exporter, domain = item[0]
            return exporter, domain is None, domain or 0

        for key, rate in sorted(sampling.rates.items(),
                                key=by_exporter_then_domain):
            exporter, domain = key
            label = (f"{exporter} domain {domain}" if domains[exporter] > 1
                     else exporter)
            print(f"  {C.CYAN}{label:<18}{C.RESET} "
                  f"{C.YELLOW}1 in {rate}, so counts above are roughly "
                  f"{rate}x low{C.RESET}", file=out)

    rs = resolver.stats
    if args.resolve != "off":
        heading("Name resolution")
        row("names found", f"{rs['resolved']} (dns {rs['via_dns']}, "
                           f"mdns {rs['via_mdns']}, netbios {rs['via_netbios']})")
        row("unresolved", rs["missed"])
        if rs["dropped"]:
            print(f"  {C.YELLOW}lookups dropped, queue full  "
                  f"{rs['dropped']}{C.RESET}", file=out)
        if rs["evicted"]:
            row("cache evictions", f"{rs['evicted']} "
                                   f"(cache holds {RESOLVER_CACHE_MAX})")

    if talkers:
        address_table("Top external addresses by bytes", talkers)
    if internal:
        address_table("Top internal addresses by bytes", internal)


def web_bind_warning(bind, port, contained=None):
    """What to say when the web interface binds something other than loopback.

    On a host that bind is worth being loud about. It puts a live map of who
    on this network talked to whom on an address other machines can reach, over
    plain HTTP, guarded by a token that travels in the clear beside it.

    In a container the same bind means something else entirely. Loopback inside
    the container's own namespace is unreachable from a published port, so the
    image asks for 0.0.0.0 every time it starts, and the alarming version of
    this line would then be printed on every single start. A reader who learns
    to skip it there skips it on a host too, which is the case it exists for.

    What the container cannot see is how the port was published, and that is
    where the exposure is really decided. So it says that instead: quietly,
    without colour, and pointing at the thing the reader can actually check.

    `contained` is for the tests. Left as None it asks the environment.
    """
    if contained is None:
        contained = in_container()
    if contained:
        return (f"the web interface is bound to {bind}, which is how a "
                f"published port reaches it from outside this container. What "
                f"that exposes is settled by the publish, which cannot be seen "
                f"from in here: -p 127.0.0.1:{port}:{port} keeps it to the "
                f"host, while -p {port}:{port} puts this network's traffic on "
                f"every interface the host has, over plain HTTP.")
    return (f"{C.YELLOW}the web interface is bound to {bind}, not to "
            f"loopback. Anyone who can reach that address and guess nothing "
            f"worse than the token can read which machines on this network "
            f"talked to which, and the hostnames behind them. This is plain "
            f"HTTP, so the token in the URL travels in the clear and so does "
            f"everything it fetches.{C.RESET}")


def web_port_note(asked, port, contained=None):
    """What to say when a request named a port this is not listening on.

    The refusal itself is right and stays as it is: a `Host` naming another
    port is not something a browser sends by itself, and the answer it gets is
    the same 404 a bad token gets so that probing tells nobody which of the
    two they got right. What was missing is that the reader at the terminal
    could not tell those two apart either, and one of them is their own
    doing.

    A published port mapped to a different number is how this is nearly always
    reached, so in a container the line says so in those terms and hands over
    the flag that fixes it. On a host the same mismatch means a proxy in front,
    or a tunnel, and the same flag is still the answer, so the difference is
    only in which arrangement is named.

    `contained` is for the tests. Left as None it asks the environment.
    """
    if contained is None:
        contained = in_container()
    where = ("a published port mapped to a different number on the host"
             if contained else "a proxy or a tunnel in front of this")
    return (f"{C.YELLOW}a request asked for port {asked} and this is "
            f"listening on {port}, so it was refused, and it was refused with "
            f"the same 404 a wrong token gets. {where.capitalize()} is the "
            f"usual reason. The port in the address bar has to be the port "
            f"this was told to serve, so pass --web-port {asked} to match, or "
            f"move the other side to {port}. Said once.{C.RESET}")


def web_reach_note(bound_addr, hosts, contained=None):
    """What to say about reaching the view from another machine, or None.

    Under a wildcard bind the printed URL names 127.0.0.1, which is right for
    this machine and wrong for every other, and the reader has to know to
    substitute. This says so once, on the way in. A specific routable bind
    prints its own address, a name given with --web-host is what the URL
    carries, and a loopback bind is reachable from nowhere else, so none of
    those needs it.

    Nor does a container, where the image binds 0.0.0.0 on every start and
    the printed 127.0.0.1 is exactly right through a published port. The
    container line printed beside it already says what a publish decides.
    `contained` is for the tests, as it is for `web_bind_warning`.
    """
    if contained is None:
        contained = in_container()
    if bound_addr not in ("0.0.0.0", "", "::") or hosts or contained:
        return None
    return ("From another machine, put this machine's address or name in "
            "place of 127.0.0.1.")


def build_parser():
    """The command line, as a parser, built here rather than inside `main`.

    Pulled out so that the set of options this program accepts can be asked
    about without running it. `scripts/install.sh` assembles a command line
    for a program it does not import, out of choices it writes down a second
    time, and the two drifted once already: the installer offered a resolver
    mode that did not exist and a default install wrote a unit the collector
    refused to start, which survived from 0.2.1 to 0.5.0 because nothing
    compared them.

    Asking the program instead is what `test_installer` does with this. It
    has to be the parser rather than `--help`, because argparse reports an
    unknown argument only once it has finished parsing, and `--help` exits
    before that: `nettail --nonsense --help` succeeds, while the invalid
    choice that actually shipped does not.
    """
    # Named rather than taken from argv[0], which is the console script's full
    # path when installed and "__main__.py" under `python -m`. Neither is what
    # anyone types, and the usage line is quoted in the README.
    #
    # Python 3.14 gave argparse a colour scheme of its own, over the usage
    # line and the options beneath it, in the help and in the usage printed
    # above an error alike. It is chosen while parsing, which is before
    # --no-color has been read and so out of reach of the switch further down:
    # `nettail --no-color --help` came out in colour regardless, and so did a
    # plain `--help` at a terminal. What colour this program prints is settled
    # in one place, so argparse is asked to keep out of it. The keyword does
    # not exist before 3.14, hence the gate.
    plain_help = {"color": False} if sys.version_info >= (3, 14) else {}
    ap = argparse.ArgumentParser(
        prog="nettail",
        description="Listen for NetFlow v5/v9/IPFIX and print flows to the console.",
        **plain_help)
    ap.add_argument("--version", action="version",
                    version=f"nettail {__version__}",
                    help="print the version and exit")
    ap.add_argument("--bind", default="0.0.0.0",
                    help="address to bind (default 0.0.0.0)")
    ap.add_argument("--port", type=int, default=2055, help="UDP port (default 2055)")
    ap.add_argument("--external-only", action="store_true",
                    help="only show flows involving a public IP")
    ap.add_argument("--verbose", action="store_true",
                    help="print every decoded field under each flow")
    ap.add_argument("--json", action="store_true",
                    help="emit one JSON object per flow instead of a table")
    ap.add_argument("--colour", "--color", choices=("auto", "always", "never"),
                    default="auto", metavar="WHEN",
                    help="when to use ANSI colour on this terminal: auto (a "
                         "terminal gets it, a redirected stream does not), "
                         "always, or never. The browser view has its own "
                         "switch and is not decided by this one")
    ap.add_argument("--no-color", action="store_true",
                    help="the same as --colour never")
    ap.add_argument("--header-every", type=int, default=40,
                    help="reprint the column header every N lines (0 to disable)")
    ap.add_argument("--sticky-header", action="store_true",
                    help="pin the column header to the top of the window "
                         "(needs a terminal; costs scrollback)")
    ap.add_argument("--hide-status", action="store_true",
                    help="turn off the two-line status bar at the foot of the "
                         "window, which is shown by default on a terminal")
    ap.add_argument("--no-supplemental-services", action="store_true",
                    help="name ports from the system services database alone, "
                         "ignoring the list shipped with this program")

    web_grp = ap.add_argument_group(
        "web interface",
        "Off unless asked for. Serves the same flows, notices and summary to a "
        "browser over plain HTTP on the loopback address, reachable only "
        "through a one-time token printed at startup.")
    web_grp.add_argument("--web", action="store_true",
                         help="serve the display to a browser as well as to "
                              "this terminal")
    web_grp.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT,
                         metavar="PORT",
                         help=f"port for the web interface (default "
                              f"{DEFAULT_WEB_PORT})")
    web_grp.add_argument("--web-bind", default="127.0.0.1", metavar="ADDR",
                         help="address for the web interface (default "
                              "127.0.0.1, and anything else exposes this "
                              "network's traffic over cleartext HTTP)")
    web_grp.add_argument("--web-host", type=web_host_arg, action="append",
                         default=[], metavar="NAME",
                         help="a name the view answers to. Under the loopback "
                              "default it is added beside localhost; under "
                              "another --web-bind, which otherwise answers to "
                              "any name, it restricts the view to the names "
                              "given. May be repeated")
    web_grp.add_argument("--web-token", type=web_token_arg, default=None,
                         metavar="TOKEN",
                         help="use this token in the URL instead of a fresh "
                              "random one, so that a bookmark survives a "
                              "restart. Taken from %s in the environment when "
                              "the flag is not given, which is how the "
                              "installed service receives it without it "
                              "appearing in ps" % WEB_TOKEN_ENV)
    web_grp.add_argument("--web-colour", "--web-color", choices=("on", "off"),
                         default="on", metavar="WHEN",
                         help="colour in the browser view (default on). A "
                              "browser is a colour-capable reader whatever "
                              "stdout is, so a redirected run does not take "
                              "the colour out of it")
    web_grp.add_argument("--web-readonly", action="store_true",
                         help="serve the display but accept no keys from the "
                              "browser")

    size_grp = ap.add_argument_group("flow size colour")
    size_ex = size_grp.add_mutually_exclusive_group()
    size_ex.add_argument("--size-scale-max", type=size_scale_arg, default=None,
                         metavar="BYTES",
                         help="top of the byte colour scale, K/M/G/T suffixes "
                              "accepted (default 100K)")
    size_ex.add_argument("--size-scale-dynamic", action="store_true",
                         help="re-range the colour scale to the largest flow "
                              "seen so far instead of a fixed top")
    size_grp.add_argument("--size-scale-window", type=size_window_arg, default=0,
                          metavar="FLOWS",
                          help="scope the dynamic scale to the last N flows "
                               "rather than the whole run. Implies "
                               "--size-scale-dynamic")

    grp = ap.add_argument_group("hostname resolution")
    grp.add_argument("--resolve", choices=Resolver.MODES, default="all",
                     help="off: static entries only, nothing looked up. "
                          "dns: reverse DNS only, fully passive. "
                          "all: reverse DNS then mDNS then NetBIOS probes (default)")
    grp.add_argument("--hosts", action="append", default=[], metavar="FILE",
                     help="static name mappings in /etc/hosts format, repeatable. "
                          "Checked before any network lookup")
    grp.add_argument("--resolve-public", action="store_true",
                     help="also reverse-resolve public addresses (PTR only)")
    grp.add_argument("--fqdn", action="store_true",
                     help="show the full name instead of just the first label")
    grp.add_argument("--resolve-workers", type=int, default=4,
                     help="background lookup threads (default 4)")
    grp.add_argument("--resolve-timeout", type=float, default=1.0,
                     help="per-probe timeout in seconds for mDNS and NetBIOS")
    # Toggled by the n and p keys rather than asked for on the command line,
    # but they live on args like every other display setting, so that one
    # place says what the display is currently doing.
    ap.set_defaults(named_hosts=False, show_macs=False)
    return ap


def main():
    ap = build_parser()
    args = ap.parse_args()

    # The token, when the flag did not carry it. This is how the installed
    # service gets one: systemd reads `EnvironmentFile` and compose reads
    # `env_file`, so by the time this runs the value is already here, and
    # nothing has to put it on a command line where `ps` would show it.
    #
    # Read here rather than as the flag's `default` so that a bad one is
    # reported against the thing that actually carried it. Somebody whose env
    # file holds a token with a slash in it is not helped by being told that
    # `--web-token` is wrong when they never typed it.
    #
    # An empty value counts as absent rather than as an error. It is what an
    # exported-but-unset variable looks like, and the answer to it is the same
    # as to no variable at all: generate one.
    if args.web_token is None:
        carried = os.environ.get(WEB_TOKEN_ENV, "").strip()
        if carried:
            try:
                args.web_token = web_token_arg(carried)
            except argparse.ArgumentTypeError as exc:
                ap.error("%s in the environment cannot be used: %s"
                         % (WEB_TOKEN_ENV, exc))

    # Checked here rather than after the socket is up: argparse catches
    # --size-scale-max against --size-scale-dynamic for us, but this pair is
    # ours to check, and a bind failure would otherwise report first.
    if args.size_scale_window and args.size_scale_max is not None:
        ap.error("--size-scale-window scopes the dynamic scale and cannot be "
                 "combined with --size-scale-max")

    scale = SizeScale(
        top=DEFAULT_SIZE_SCALE_MAX if args.size_scale_max is None
        else args.size_scale_max,
        # Asking for a window is asking for a dynamic scale.
        dynamic=args.size_scale_dynamic or bool(args.size_scale_window),
        window=args.size_scale_window,
    )

    # The display reaches for a few characters that are not in every console
    # code page: the arrows between the endpoints, the ellipsis a trimmed name
    # ends in. Redirected into a file on Windows stdout comes up as cp1252,
    # where printing one of those raises instead of printing it, and the
    # collector would die on its first flow rather than on anything to do with
    # the network. Ask for UTF-8, and settle for replacing what still cannot be
    # encoded over stopping.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    # Colour, for each of the two readers there are. This has to come after
    # the reconfigure above, which wants the real streams, and after nothing
    # else: it reads stdout to find out whether it is a terminal, and then may
    # replace both streams with wrappers that take the colour back out.
    #
    # `--colour` is the terminal's switch and means what it always meant.
    # `--web-colour` is the browser's, on by default, because a browser is a
    # colour-capable reader however stdout was set up. One switch for both is
    # what this replaces, and the arrangement it got wrong was the one the
    # container image exists for: a detached container has no terminal, so
    # `auto` blanked the codes for the whole process and the browser view,
    # which is the only thing that image is for, came out white.
    global _WEB_COLOUR
    terminal_colour, _WEB_COLOUR = colour_choice(
        args, sys.stdout.isatty(), os.environ.get("NO_COLOR"))

    if not terminal_colour and not _WEB_COLOUR:
        # Nobody wants it, so nothing is painted in the first place. This is
        # the whole of what a run without --web does, which is what keeps such
        # a run exactly as it was.
        C.disable()
    elif not terminal_colour:
        # The browser is having colour and this terminal is not, so it is
        # painted at the source and taken out on the way here. Only the colour
        # comes out: the sticky header and the status bar write their margins,
        # their cursor moves and their erases to this same stream, and those
        # go through untouched.
        # Guarded so that wrapping is idempotent. Nothing calls main() twice
        # in one process today except a test, and a stream wrapped twice
        # would work while saying, to anything that looked, that it had
        # already been dealt with.
        if not isinstance(sys.stderr, PlainStream):
            sys.stderr = PlainStream(sys.stderr)
        if not args.json and not isinstance(sys.stdout, PlainStream):
            # Under --json stdout carries json.dumps output, which has never
            # had a colour code in it, so there is nothing to take out and no
            # reason to put a substitution in front of every flow.
            sys.stdout = PlainStream(sys.stdout)

    # Treat SIGTERM like Ctrl-C so the summary still prints under systemd.
    def _term(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _term)

    # Names for the ports the system database has no answer for, mdns among
    # them. Loaded before anything is decoded, so that one flow is never named
    # differently from the next, and skipped entirely when it was not wanted.
    if not args.no_supplemental_services:
        note = services.load()
        if note:
            print(f"{C.GREY}{note}{C.RESET}", file=sys.stderr)

    # lanname treats an unreadable hosts file as a warning on its own logger,
    # which is right for a library and useless here: this program configures no
    # logging, so the message would go nowhere. A bad path should still not
    # stop the collector, but a whole session of bare addresses because of a
    # typo has to be explained rather than left to be worked out.
    for path in args.hosts:
        try:
            with open(path, "r", encoding="utf-8", errors="replace"):
                pass
        except OSError as exc:
            print(f"{C.YELLOW}could not read hosts file {path}: {exc}"
                  f"{C.RESET}", file=sys.stderr)

    resolver = Resolver(
        mode=args.resolve,
        hosts_files=args.hosts,
        workers=args.resolve_workers,
        resolve_public=args.resolve_public,
        fqdn=args.fqdn,
        timeout=args.resolve_timeout,
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    except OSError:
        pass
    sock.bind((args.bind, args.port))

    # The bus everything a browser sees goes through. Created whatever the
    # flags say, because every publish site guards on whether anything is
    # actually attached and an unsubscribed bus answers that with an attribute
    # lookup. Nothing subscribes to it until the server below starts.
    bus = Feed()
    # Where a browser's key presses wait for the receive loop to notice them.
    # Bounded, because a script pressing keys faster than the loop turns is the
    # only thing that can fill it, and unbounded memory is a poor answer to
    # that. A person cannot reach the limit.
    key_queue = queue.Queue(maxsize=KEY_QUEUE_MAX)

    # Keys are read between datagrams, so how long the socket is allowed to
    # wait is also how long a keypress can sit unanswered on a quiet network.
    # A browser's key press waits the same way, which is why the quarter second
    # is asked for whenever either keyboard is live rather than only the local
    # one: without it a button pressed in a browser would sit unanswered for a
    # second at a time on a network with nothing on it.
    keyboard = Keyboard()
    keys_on = not args.json and keyboard.start()
    sock.settimeout(0.25 if (keys_on or args.web) else 1.0)

    # Everything about reading the wire belongs to the decoder: templates,
    # sequence numbers, advertised sampling rates and the counters over all
    # three. Held under the old names so the summary, the status bar and the
    # keys go on asking the same questions of the same objects.
    decoder = Decoder()
    stats = decoder.stats
    sampling = decoder.sampling
    sequences = decoder.sequence
    tally = Tally()

    sticky = StickyHeader()
    sticky_on = False
    rates = Rates()
    bar = StatusBar(sticky=sticky)

    controls = Controls(args, scale, resolver, sticky, stats, tally, sequences,
                        bar=bar, on_clear=bus.clear)
    # Attached rather than passed in: the report has to read the start time
    # from the controls themselves, since the c key moves it.
    #
    # Teed rather than printed, so that a browser watching gets the report and
    # the host list at the moment the terminal does. The listing the ? key
    # prints goes the same way, and is wired here rather than left inside
    # Controls because the tee is this module's to arrange.
    controls.summary = lambda: tee(
        bus, "summary",
        lambda out: write_summary(stats, tally, resolver, sequences, sampling,
                                  args, controls.started, out=out))
    def show_hosts():
        # The list is taken once and rendered from, because this is the block
        # that may be rendered for each reader separately and the two must be
        # looking at the same addresses.
        seen = resolver.local_hosts()
        tee(bus, "hosts",
            lambda out: write_hosts(resolver, out=out, hosts=seen),
            per_reader=True)

    controls.hosts = show_hosts
    def listing():
        """The ? listing, to each view with the keys that view can press.

        The one place the two are deliberately shown different text rather than
        the same characters. A browser cannot press the escape key, so a
        listing offering it would advertise something the control route then
        refuses. Everything else it can press, this key included, which has no
        button of its own precisely because the drawer is already the list.

        It goes straight at stderr rather than through `controls.out`, which is
        a tee: a listing written there would be published a line at a time,
        each line dressed as a reply to a key nobody pressed.
        """
        write_keys(sys.stderr)
        if bus.active:
            buffer = io.StringIO()
            write_keys(buffer, keys=web_keys())
            bus.prose("keys", for_web(buffer.getvalue()))

    controls.listing = listing
    controls.out = _ProseTee(bus, sys.stderr)

    def take_snapshot():
        return snapshot(stats, tally, resolver, sequences, sampling, scale,
                        args, controls, rates)

    # Whether the port mismatch above has been reported. In a one element list
    # for the reason `shown_flows` below is: the receive loop writes it and it
    # is read in the same nested scope.
    port_told = [False]

    # Flows that have passed the display filter. In a one element list rather
    # than a bare integer because `web_status` below reads it and the receive
    # loop writes it, and both are nested in this function: a plain name would
    # need `nonlocal` in one of them for no gain over a box that is simply
    # mutated.
    shown_flows = [0]

    # When the shared status clock last struck. Zero rather than now, so
    # that the first pass round the loop produces a snapshot and a browser
    # connecting immediately has figures to show rather than an empty
    # footer until the interval elapses.
    last_status = 0.0

    if not args.json:
        # Whether there is going to be a bar has to be settled before the
        # header starts, since the header writes the one scroll region the two
        # of them share and has to know how many rows are left for it.
        bar_on = not args.hide_status and bar.usable()
        sticky.bottom_reserved = STATUS_ROWS if bar_on else 0

        # Claim the top row before anything is printed: starting the sticky
        # header clears the screen, so a banner written first would be wiped.
        if args.sticky_header:
            sticky_on = sticky.start()
            if not sticky_on:
                # The rows the header itself needs, plus any the bar has
                # already spoken for, since that is the figure it was actually
                # measured against and the one the reader has to act on.
                needed = MIN_STICKY_ROWS + sticky.bottom_reserved
                print(f"{C.YELLOW}--sticky-header needs a terminal with scroll "
                      f"region support and at least {needed} rows; "
                      f"falling back to --header-every{C.RESET}", file=sys.stderr)

        # Nothing is said when this cannot start: unlike the header it was
        # never asked for, so its absence is not news.
        if bar_on and not bar.start():
            # The window changed size between being asked and being claimed.
            # Hand the rows back rather than leave the header holding two it
            # has no use for. Nothing has been printed yet, so the repaint
            # that proves it costs nothing.
            sticky.bottom_reserved = 0
            sticky.repaint()

    # Started after the sticky header and the bar have settled what they are
    # doing, because starting the header clears the screen and a URL printed
    # before that would be wiped off it. Anything this has to say is therefore
    # said in the banner below rather than on its own.
    web = None
    web_url = None
    web_warnings = []
    if args.web:
        web_keyset = set()
        if not args.web_readonly:
            allowed = {key for key, _doc in web_keys()}
            web_keyset = {
                KEY_CHARS.get(key, key) for key in allowed
            } & set(controls.actions())
        web = WebInterface(bus, key_queue, web_keyset, bind=args.web_bind,
                           port=args.web_port, token=args.web_token,
                           readonly=args.web_readonly, hosts=args.web_host)
        try:
            # Bound but not yet answering. The greeting a browser is met with
            # has to be in place before the first one can arrive, and it cannot
            # be built until the URL is known, which is what binding settles.
            web_url = web.bind()
        except OSError as exc:
            web = None
            web_warnings.append(
                f"{C.RED}the web interface could not bind "
                f"{args.web_bind}:{args.web_port}: {exc}. The collector is "
                f"running without it.{C.RESET}")
        else:
            # The address the socket actually got, rather than what was asked
            # for: `--web-bind localhost` is a loopback bind and should not be
            # warned about as though it were a routable one.
            if not is_loopback(web.bound_addr):
                web_warnings.append(
                    web_bind_warning(args.web_bind, args.web_port))

    def write_banner(out, qr_key=False):
        """What a session opens with: where it is listening, and how it is set.

        Gathered into a function so that the same characters can be printed
        here and handed to a browser, which needs them as much as this terminal
        does and cannot be given them by watching, since a browser that
        connects an hour in was not here when they were printed. They travel in
        the greeting instead, which is why this builds text rather than
        printing it directly.

        `qr_key` is the one line the two readers are not shown alike, and it is
        rendered twice for the reason `tee` renders the host list twice: the
        difference is in the words and not only in their dress. The q key is
        kept back from a browser, so offering it one would advertise something
        the control route then refuses, which is the objection `controls.listing`
        exists to answer for the escape key. Rendering the banner twice is free
        here in a way it is not there, because everything on it is settled
        before the first datagram and nothing in it reads live state.
        """
        print(f"{C.BOLD}Listening for NetFlow/IPFIX on "
              f"{args.bind}:{args.port}{C.RESET}", file=out)
        statics = (f"  |  static entries: {len(resolver.static)}"
                   if resolver.static else "")
        print(f"{C.GREY}Hostname resolution: {MODE_DESC[args.resolve]}"
              f"{statics}{C.RESET}", file=out)
        print(f"{C.GREY}v9 and IPFIX exporters resend templates periodically. "
              f"Data records before the first template are counted as deferred."
              f"{C.RESET}", file=out)
        if web_url:
            print(f"{C.BOLD}Web interface: {web_url}{C.RESET}", file=out)
            reach = web_reach_note(web.bound_addr, args.web_host)
            if reach:
                print(f"{C.GREY}{reach}{C.RESET}", file=out)
            if qr_key:
                print(f"{C.GREY}press {QR_KEY} for a QR code of that URL"
                      f"{C.RESET}", file=out)
            if args.web_readonly:
                print(f"{C.GREY}The browser is watching only; keys are not "
                      f"taken from it.{C.RESET}", file=out)
        if keys_on:
            print(f"{C.GREY}{KEY_HELP}{C.RESET}", file=out)
        for warning in web_warnings:
            print(warning, file=out)

    # The QR key is offered only where it can be answered: it needs a web
    # interface to point at and a keyboard to be pressed on. Whether the window
    # is wide enough for the symbol is not asked here and deliberately so, since
    # a window can be resized between this line and the keypress, and the key
    # answers a narrow one with the URL by itself rather than with nothing.
    qr_on = bool(web_url) and keys_on

    if web_url:
        def show_qr():
            """The QR block, to the terminal and to the terminal alone.

            Written straight at stderr rather than through `controls.out`,
            which is a tee: the key is kept back from the browser, so
            publishing the symbol would send it somewhere nobody asked for it,
            a line at a time and dressed as replies to a key nobody pressed.

            The window is measured on every press rather than once at startup,
            because it can be resized between the two. It is measured on the
            stream the block is going to, which is not the one
            `shutil.get_terminal_size` would have asked about, and against the
            scroll region rather than the whole terminal: a sticky header and
            a status bar have taken rows off either end, and a symbol whose
            top has scrolled out of the region is exactly as unreadable as one
            cut off at the side.
            """
            columns, lines = qr_window(sys.stderr)
            reserved = ((HEADER_ROWS if sticky.active else 0)
                        + (STATUS_ROWS if bar.active else 0))
            write_qr(web_url, size=(columns, lines - reserved))

        # Set from the URL rather than from `qr_on`, which also asks whether
        # the keyboard is live. The two differ only in a state no keypress can
        # reach, and the hook being None is how the key says there is no web
        # interface, which would be the wrong thing to say about a run that
        # has one.
        controls.qr = show_qr

    # Printed even under --json, where it goes to stderr on its own and the
    # flows have stdout to themselves. It is worth printing there too: the URL
    # is on it, and a run with the web interface up and no way to find out
    # where it is would be a poor joke.
    banner = io.StringIO()
    write_banner(banner, qr_key=qr_on)
    sys.stderr.write(banner.getvalue())
    sys.stderr.flush()
    # The browser's copy, which differs from the terminal's in the one line
    # above and in nothing else, so it is rendered a second time only when
    # there is a difference to render.
    if qr_on:
        web_banner = io.StringIO()
        write_banner(web_banner, qr_key=False)
        banner_text = web_banner.getvalue()
    else:
        banner_text = banner.getvalue()
    bus.set_hello({
        "nettail": __version__,
        "banner": for_web(banner_text),
        # The columns and the keys as the terminal knows them, so that the page
        # builds its table head and its buttons from what this program actually
        # does rather than from a second list written down in the page and left
        # to go stale. Nothing about the display is spelled out twice.
        "columns": [{"name": name, "align": align,
                     # A column wide enough on a terminal to hold an
                     # address, a port, a service and a hostname is the
                     # one that can be long enough to push a table wider
                     # than the window. Said here rather than worked out
                     # in the page, which would mean naming the columns
                     # over there and having two lists to keep in step.
                     "wrap": width >= ENDPOINT_WIDTH}
                    for name, width, align, _gap in COLUMNS],
        "keys": [{"key": key, "doc": doc} for key, doc in web_buttons()],
        # Every key the browser may press, as the characters a keypress
        # actually produces. A superset of the buttons above: the help key
        # is pressable and has no button, because the drawer is already
        # the answer it would print.
        "pressable": sorted(KEY_CHARS.get(key, key)
                            for key, _doc in web_keys()),
        "modes": dict(MODE_DESC),
        "readonly": bool(args.web_readonly),
        "json": bool(args.json),
    })

    # Only now does it start answering. Serving before the greeting was set
    # left a window, small but reachable by a bookmarked tab reconnecting the
    # instant the port came back, in which a browser was handed an empty
    # greeting: no columns, no buttons, and no second chance at either, since
    # the page takes the greeting once and builds its table from it.
    if web is not None:
        web.serve()

    if not args.json:
        print("", file=sys.stderr)
        if not sticky_on:
            print(C.BOLD + HEADER_LINE + C.RESET)

    def web_status(snap):
        """The status snapshot, with its figures already written out.

        The numbers are spelled here rather than in the page because
        `values.py` is where this program decides what a megabyte and a minute
        and a half look like, and a second opinion written in JavaScript would
        be a second thing to keep in step with it. The raw snapshot travels
        alongside, so anything the page wants to do arithmetic on it can.
        """
        talker = snap["top_talker"]
        return {
            # How many flows have passed the display filter since the run
            # started, which is precisely how many a browser would have been
            # shown. A tab that disconnects while it is hidden cannot count
            # what it missed, because nothing reached it, so it notes this
            # figure on the way out and subtracts on the way back. Not
            # `snap["flows"]`, which counts every flow decoded: under
            # --external-only that is a different and much larger number, and
            # the point of the count is to say what was missed rather than
            # what happened.
            "flows_shown": shown_flows[0],
            "snap": snap,
            "shown": {
                "elapsed": human_clock(snap["elapsed"]),
                "flows": human_count(snap["flows"]),
                "packets": human_count(snap["packets"]),
                "bytes_rx": human_bytes(snap["bytes_rx"]),
                "bit_rate": human_bits(snap["bit_rate"]),
                "flow_rate": human_count(snap["flow_rate"]),
                "peak": human_bits(snap["peak"]) if snap["peak"] else "",
                "scale_top": human_bytes(snap["scale_top"]),
                "external": human_bytes(snap["external_bytes"]),
                "inbound": human_bytes(snap["inbound"]),
                "outbound": human_bytes(snap["outbound"]),
                "resolve": MODE_DESC[snap["resolve"]],
                "talker": (f"{talker[0]}"
                           + (f" ({talker[1]})" if talker[1] else "")
                           + f"  {human_bytes(talker[2])}") if talker else "",
            },
        }

    def web_flow(rec, hdr, record=None):
        """A flow as a browser needs it: the cells to draw, and the record.

        The cells come from the same function the terminal row comes from,
        escape codes and all, which is what stops the two views drifting. A
        page could not work them out for itself in any case: a service name is
        whatever this machine's services database calls that port, and no
        amount of care in a browser reproduces that.

        The record rides along because it is the shape `--json` prints and it
        is what anything built on this page later will want. Both halves are
        assembled only when somebody is watching, and `record` lets the JSON
        branch hand over the one it has already built rather than have an
        identical second one made underneath it.
        """
        return {
            "cells": [for_web(unpad(painted)) for _plain, painted
                      in row_cells(rec, hdr, args, resolver, scale,
                                   endpoint_width=WEB_ENDPOINT_WIDTH)],
            "record": (record if record is not None
                       else flow_record(rec, hdr, resolver)),
        }

    def show(rec, hdr):
        """Put one flow on screen, with the header cadence around it."""
        if (not sticky.active and args.header_every and controls.lines
                and controls.lines % args.header_every == 0):
            print(C.BOLD + HEADER_LINE + C.RESET)
        render(rec, hdr, args, resolver, scale)
        # Published here rather than up beside should_show, because this is
        # the one point both a live flow and one replayed out of the pause
        # buffer pass through. Publishing at the filter instead would stream
        # flows to a browser while the terminal was still holding them, which
        # is the opposite of what the space key is for.
        if bus.active:
            bus.flow(web_flow(rec, hdr))
        controls.lines += 1
        # While the bar is up it does the re-measuring for both of them, on a
        # clock rather than a line count. Two pollers with two ideas of how
        # many rows are spoken for is how the margins come to disagree with
        # what is drawn inside them.
        if not bar.active:
            sticky.check_resize()

    try:
        while True:
            # Before the socket, so keys are answered on a silent network too.
            key = keyboard.poll()
            while key is not None:
                controls.handle(key, ask=keyboard.read_line)
                key = keyboard.poll()
            # Browser keys, answered on this thread and by the same dispatch as
            # the terminal's, so there goes on being one place a key means
            # anything. The value is bound as a default argument rather than
            # closed over: the lambda is called before the loop comes round
            # again, but ruff cannot know that and the B rules are selected.
            while True:
                try:
                    web_key, web_value = key_queue.get_nowait()
                except queue.Empty:
                    break
                controls.handle(web_key,
                                ask=lambda _prompt, v=web_value: v)
            # A request refused because its Host named another port, reported
            # on this thread for the reason browser keys are answered on it:
            # a line written from a request thread lands inside the scroll
            # region and takes the pinned header and the status bar with it.
            #
            # Once a run, and the flag is what makes it once. This is a fact
            # about how the collector was started rather than about the
            # request, so a second telling says nothing new, and saying it per
            # request would hand anybody who can reach the port a way to
            # scribble over the display for as long as they cared to.
            if web is not None and web.port_notice is not None:
                asked, web.port_notice = web.port_notice, None
                if not port_told[0]:
                    port_told[0] = True
                    print(web_port_note(asked, web.port), file=sys.stderr)
            if controls.quit:
                break
            if not controls.paused and controls.held:
                for held_rec, held_hdr in controls.drain():
                    if args.json:
                        # Under --json nothing was held back from stdout, only
                        # from the browser, so resuming owes the browser the
                        # flows and stdout nothing.
                        if bus.active:
                            bus.flow(web_flow(held_rec, held_hdr))
                    else:
                        show(held_rec, held_hdr)

            # Once round the loop is a quarter second on a silent network and
            # one datagram on a busy one.
            rates.observe(stats["packets"], stats["flows"], stats["bytes_rx"])
            # One clock, two consumers. The bar used to decide for itself when
            # it was worth redrawing, which was fine while it was the only
            # thing wanting a snapshot. It is not any more, and it is absent in
            # exactly the arrangement the web interface is most useful in:
            # --json never starts it, the b key takes it away, and redirected
            # output leaves no room for it. Hanging the browser's status on the
            # bar's decision would leave the footer dead in all three, so the
            # loop keeps the clock and each of them takes a snapshot or
            # declines one without being able to starve the other.
            now = time.time()
            snap = None
            if bar.active or bus.active:
                if now - last_status >= REPAINT_INTERVAL:
                    last_status = now
                    snap = take_snapshot()
                    if bus.active:
                        bus.status(web_status(snap))
            bar.update(lambda s=snap: s if s is not None else take_snapshot())

            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue

            exporter = addr[0]
            # One call covers the version check, the templates, the counters
            # and both watches. What it will not do is print, so whatever it
            # noticed is drained and said here.
            message = decoder.decode(data, exporter)
            # Teed rather than printed, because the running commentary about
            # sampling and lost exports is the part of this a browser most
            # needs. Bound as a default argument rather than closed over: the
            # lambda is called before the loop comes round again, but ruff
            # cannot know that and the B rules are selected here.
            noticed = decoder.take_events()
            if noticed:
                tee(bus, "notice",
                    lambda out, seen=noticed: report_events(seen, args, out=out))
            # None means the datagram held nothing this collector can read.
            # Testing the message itself would not do: one carrying only
            # templates or only option records is a normal thing to receive.
            if message is None:
                continue

            # The header carries the exporter and the version as well as the
            # timestamps, which is what lets a flow be turned into a record
            # from the header alone, here or minutes later on the way out of
            # the pause buffer.
            hdr = message.header

            for rec in message.flows:
                # Range a dynamic scale against every flow decoded, including
                # ones --external-only will hide, and before the flow is
                # painted so the biggest yet seen lands at the top of the ramp.
                scale.observe(rec.get("octets", rec.get("octets_total")))
                # Every decoded flow counts towards the report, shown or not.
                tally.add(rec, hdr)

                if not should_show(rec, args):
                    continue

                # Counted whether or not anybody is watching, which is the
                # whole point: a browser that disconnected while its tab was
                # hidden needs this to have gone on rising in its absence.
                shown_flows[0] += 1

                if args.json:
                    out = flow_record(rec, hdr, resolver)
                    print(json.dumps(out, default=str), flush=True)
                    # stdout is not pausable and should not become so. It is
                    # the part of this interface documented as parseable, and
                    # putting holds and drops into it would break the very
                    # consumers it exists for. The browser is the human view in
                    # this configuration, so it is the one the space key acts
                    # on, through the same buffer the terminal path uses.
                    if bus.active:
                        if controls.paused:
                            controls.hold(rec, hdr)
                        else:
                            bus.flow(web_flow(rec, hdr, record=out))
                elif controls.paused:
                    controls.hold(rec, hdr)
                else:
                    show(rec, hdr)

    except KeyboardInterrupt:
        pass
    finally:
        # Give the terminal back before anything else is printed: a shell left
        # in cbreak mode is as unwelcome as a stray scroll region.
        keyboard.stop()
        # The bar wipes its rows first and the header releases the margins
        # after, so the summary is written to a screen with nothing left
        # holding on to any part of it.
        bar.stop()
        sticky.stop()
        sock.close()
        resolver.shutdown()
        # Teed like the summary the s key prints, and for a better reason: a
        # browser that watched the whole session should not be missing the one
        # report that says how it went. Published before the server is told to
        # stop, so that the report is on its way out before anything starts
        # closing connections.
        tee(bus, "summary",
            lambda out: write_summary(stats, tally, resolver, sequences,
                                      sampling, args, controls.started,
                                      out=out))
        if web is not None:
            # Ordered rather than left to the daemon threads. server_close()
            # joins nothing, so without this the interpreter is free to exit
            # while a writer is halfway through the summary it was just handed.
            web.stop()
