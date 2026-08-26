"""Argument parsing, the receive loop, and the exit summary.
"""

import argparse
import json
import os
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
from .colour import C
from .display import HEADER_LINE, proto_colour, render
from .keys import KEY_HELP, Controls, Keyboard
from .sizescale import (
    DEFAULT_SIZE_SCALE_MAX,
    SizeScale,
    SpanScale,
    size_scale_arg,
    size_window_arg,
)
from .statusbar import STATUS_ROWS, Rates, StatusBar, snapshot
from .sticky import MIN_STICKY_ROWS, StickyHeader
from .tally import Tally
from .values import human_bits, human_bytes, human_clock, human_count, human_duration


def should_show(rec, args):
    if not args.external_only:
        return True
    # The same two ends the display and the summary use, so that what is hidden
    # here and what is counted as external there cannot disagree.
    src, dst = flow_endpoints(rec)
    return (bool(dst) and addr_kind(dst) == "public") or (
        bool(src) and addr_kind(src) == "public")


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


def _column(cell, width):
    """Pad a cell to width, or trim it saying so rather than let a row wrap."""
    plain_text, painted = cell
    if len(plain_text) > width:
        return f"{C.CYAN}{plain_text[:width - 3]}...{C.RESET}"
    return painted + " " * (width - len(plain_text))


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


def write_hosts(resolver, out=None):
    """List the local addresses seen this session and the names they answered to.

    Everything discovered at any point, not what happens to be cached: names
    expire and the cache evicts, and the useful question hours later is still
    "what did you see". Where an address answered to more than one name the
    most recent leads and the rest follow, dimmed, or marked with a star when
    there is no colour to dim.
    """
    out = out if out is not None else sys.stderr
    hosts = resolver.local_hosts()
    print(f"\n{C.BOLD}{C.BLUE}Local hosts seen{C.RESET}", file=out)
    if not hosts:
        print(f"  {C.GREY}none yet{C.RESET}", file=out)
        return
    for addr, names in hosts:
        current, older = names[0], names[1:]
        shown = [f"{C.GREEN}{current}{C.RESET}"]
        for name in older:
            shown.append(f"{C.DIM}{name}{C.RESET}" if C.enabled() else f"{name}*")
        print(f"  {C.CYAN}{addr:<18}{C.RESET} {'  '.join(shown)}", file=out)
    print(f"  {C.GREY}{len(hosts)} address{'' if len(hosts) == 1 else 'es'}"
          f"{'' if C.enabled() else ', * marks a name that has been superseded'}"
          f"{C.RESET}", file=out)


def write_summary(stats, tally, resolver, sequences, sampling, args,
                  started, out=None):
    """Print what arrived, what it was, and what it implies about the link.

    Called on the way out, and from the s key while running, which is why it
    takes what it needs as arguments rather than reaching into the loop.
    """
    out = out if out is not None else sys.stderr

    # Gathered before anything is printed, because the colour ramp below is
    # ranged over these rows and has to see the same figures the reader will.
    protocol_rows = tally.proto_bytes.most_common(8)
    service_rows = tally.service_bytes.most_common(8)
    pairs_by_bytes = tally.top_pairs_by_bytes()
    pairs_by_packets = tally.top_pairs_by_packets()
    longest = tally.longest_flows()
    talkers = tally.talkers.most_common(10)

    # One ramp for the whole report, stretched over the figures it is about to
    # print, so a colour says how a number compares with its neighbours here.
    sizes = ([stats["bytes_rx"], tally.external_bytes, tally.inbound_bytes,
              tally.outbound_bytes]
             + [octets for _name, octets in protocol_rows]
             + [octets for _name, octets in service_rows]
             + [octets for _pair, octets in pairs_by_bytes]
             + [details[5] for _duration, details in longest]
             + [octets for _ip, octets in talkers])
    ramp = SpanScale(sizes)

    def address(addr, port=None):
        """One endpoint: the address, its port, and the name it answers to.

        Three things worth telling apart at a glance, so they get three
        colours rather than one run of text. The address itself is coloured by
        what kind it is, the same distinction the flow display draws: cyan for
        somewhere out on the internet, blue for somewhere on this network.
        """
        pieces = [(str(addr) if addr else "-", _address_colour(addr))]
        if port:
            pieces.append((f":{port}", C.GREY))
        host = resolver.lookup(addr) if addr else None
        if host:
            pieces += [(" (", C.GREY), (host, C.GREEN), (")", C.GREY)]
        return pieces

    def row(label, value, width=18):
        print(f"  {C.GREY}{label:<{width}}{C.RESET} {C.CYAN}{value}{C.RESET}",
              file=out)

    def size_row(label, octets, width=18):
        print(f"  {C.GREY}{label:<{width}}{C.RESET} "
              f"{ramp.paint(human_bytes(octets), octets)}", file=out)

    def heading(text):
        print(f"\n{C.BOLD}{C.BLUE}{text}{C.RESET}", file=out)

    def columns(name, *headings):
        """A dim header row, so the numbers under it need no unit beside them."""
        widths = (9, 7, 9)
        cells = "  ".join(f"{text:>{width}}"
                          for text, width in zip(headings, widths))
        print(f"  {C.GREY}{name:<16}{cells}{C.RESET}", file=out)

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

    if pairs_by_bytes:
        heading(f"Busiest {tally.top} pairs by volume")
        for pair, octets in pairs_by_bytes:
            cell = _painted(*address(pair[0]), (" <-> ", C.MAGENTA),
                            *address(pair[1]))
            print(f"  {_column(cell, 58)} "
                  f"{ramp.paint(f'{human_bytes(octets):>9}', octets)}", file=out)

        heading(f"Busiest {tally.top} pairs by packets")
        for pair, packets in pairs_by_packets:
            cell = _painted(*address(pair[0]), (" <-> ", C.MAGENTA),
                            *address(pair[1]))
            print(f"  {_column(cell, 58)} "
                  f"{C.CYAN}{human_count(packets):>9}{C.RESET}", file=out)

    if longest:
        heading(f"Longest {tally.top} flows")
        for duration, (src, sport, dst, dport, proto_name, octets) in longest:
            cell = _painted(*address(src, sport), (" -> ", C.MAGENTA),
                            *address(dst, dport))
            print(f"  {C.CYAN}{human_duration(duration):>7}{C.RESET}  "
                  f"{proto_colour(proto_name)}{proto_name:<6}{C.RESET} "
                  f"{_column(cell, 56)} "
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
        heading("Top external addresses by bytes")
        for ip, nbytes in talkers:
            print(f"  {_column(_painted(*address(ip)), 48)} "
                  f"{ramp.paint(f'{human_bytes(nbytes):>10}', nbytes)}", file=out)


def main():
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
    ap.add_argument("--no-color", action="store_true", help="disable ANSI colour")
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
    args = ap.parse_args()

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

    if args.no_color or not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        C.disable()

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

    # Keys are read between datagrams, so how long the socket is allowed to
    # wait is also how long a keypress can sit unanswered on a quiet network.
    keyboard = Keyboard()
    keys_on = not args.json and keyboard.start()
    sock.settimeout(0.25 if keys_on else 1.0)

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
                        bar=bar)
    # Attached rather than passed in: the report has to read the start time
    # from the controls themselves, since the c key moves it.
    controls.summary = lambda: write_summary(stats, tally, resolver, sequences,
                                             sampling, args, controls.started)
    controls.hosts = lambda: write_hosts(resolver)

    def take_snapshot():
        return snapshot(stats, tally, resolver, sequences, sampling, scale,
                        args, controls, rates)

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

        print(f"{C.BOLD}Listening for NetFlow/IPFIX on "
              f"{args.bind}:{args.port}{C.RESET}", file=sys.stderr)
        statics = (f"  |  static entries: {len(resolver.static)}"
                   if resolver.static else "")
        print(f"{C.GREY}Hostname resolution: {MODE_DESC[args.resolve]}"
              f"{statics}{C.RESET}", file=sys.stderr)
        print(f"{C.GREY}v9 and IPFIX exporters resend templates periodically. "
              f"Data records before the first template are counted as deferred."
              f"{C.RESET}", file=sys.stderr)
        if keys_on:
            print(f"{C.GREY}{KEY_HELP}{C.RESET}", file=sys.stderr)
        print("", file=sys.stderr)
        if not sticky_on:
            print(C.BOLD + HEADER_LINE + C.RESET)

    def show(rec, hdr):
        """Put one flow on screen, with the header cadence around it."""
        if (not sticky.active and args.header_every and controls.lines
                and controls.lines % args.header_every == 0):
            print(C.BOLD + HEADER_LINE + C.RESET)
        render(rec, hdr, args, resolver, scale)
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
            if controls.quit:
                break
            if not controls.paused and controls.held:
                for held_rec, held_hdr in controls.drain():
                    show(held_rec, held_hdr)

            # Once round the loop is a quarter second on a silent network and
            # one datagram on a busy one; the bar decides for itself which of
            # those are too soon to bother redrawing for.
            rates.observe(stats["packets"], stats["flows"], stats["bytes_rx"])
            bar.update(take_snapshot)

            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue

            exporter = addr[0]
            # One call covers the version check, the templates, the counters
            # and both watches. What it will not do is print, so whatever it
            # noticed is drained and said here.
            message = decoder.decode(data, exporter)
            report_events(decoder.take_events(), args)
            # None means the datagram held nothing this collector can read.
            # Testing the message itself would not do: one carrying only
            # templates or only option records is a normal thing to receive.
            if message is None:
                continue

            hdr, version = message.header, message.version

            for rec in message.flows:
                # Range a dynamic scale against every flow decoded, including
                # ones --external-only will hide, and before the flow is
                # painted so the biggest yet seen lands at the top of the ramp.
                scale.observe(rec.get("octets", rec.get("octets_total")))
                # Every decoded flow counts towards the report, shown or not.
                tally.add(rec, hdr)

                if not should_show(rec, args):
                    continue

                if args.json:
                    out = dict(rec)
                    out["_exporter"] = exporter
                    out["_version"] = version
                    out["_timestamp"] = flow_timestamp(rec, hdr)
                    src_host = resolver.lookup(rec.get("src_addr"))
                    dst_host = resolver.lookup(rec.get("dst_addr"))
                    if src_host:
                        out["src_host"] = src_host
                    if dst_host:
                        out["dst_host"] = dst_host
                    print(json.dumps(out, default=str), flush=True)
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
        write_summary(stats, tally, resolver, sequences, sampling,
                      args, controls.started)
