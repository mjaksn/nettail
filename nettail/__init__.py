"""A NetFlow v5 / v9 / IPFIX collector that prints flows to a console.

Installing this package puts a ``nettail`` command on the path; ``python -m
nettail`` runs the same thing from a checkout. Either way the work starts at
``cli.main``, and this package holds the pieces it drives. Names used across
modules are re-exported here so that ``from nettail import X`` works
regardless of which module X lives in.

Reading the wire is not one of those pieces, and neither is discovering a
hostname. Decoding v5, v9 and IPFIX, the template store behind them, sequence
gaps and advertised sampling rates live in netflume; turning an address into a
name over reverse DNS, mDNS and NetBIOS lives in lanname. Both were lifted out
of this program and are now packages in their own right. What is left here is
the display: the part that decides what a flow should look like. Mostly that
means a terminal, which is what this is for and where all of it works. It can
also mirror the same display into a browser, which is off unless asked for and
draws what the terminal draws rather than deciding anything of its own.

    colour      ANSI codes and the switch that disables them
    values      sizes, rates and durations, written for a column
    sizescale   the colour ramp behind the BYTES column
    services    port names, the system database first and a shipped list after
    display     laying one flow out as a line of text
    sticky      pinning the column header to the top of the window
    statusbar   the two-line bar along the foot of the window
    tally       the running totals behind the exit summary
    keys        the keyboard and what each key does
    qr          the web interface URL, encoded and drawn as a QR code
    feed        the events a browser watches, and the queues they wait in
    web         serving those events over HTTP, and taking keys back
    cli         argument parsing, the receive loop, and the exit summary

Nothing from either dependency is re-exported below. A program wanting the
decoder should import netflume, and one wanting the resolver should import
lanname, and get the version it pinned rather than whichever one this package
happens to be sitting on. The one apparent exception is ``service_name``,
which is not netflume's function but this package's wrapper around it.

Loading and parsing the supplemental service list are left on the module rather
than lifted out of it: ``services.load()`` says what it loads and a bare
``load`` in this namespace would not.

``qr.render`` is the one name below that could not be brought up, because
``display.render`` is already here and means something else entirely: one lays
a flow out as a line, the other turns a matrix into rows of half blocks. A
caller wanting the second asks ``qr.render`` for it. Nothing else in that
module collides.
"""

__version__ = "0.8.0"

from .cli import (
    build_parser,
    flow_record,
    main,
    report_events,
    should_show,
    tee,
    write_hosts,
    write_summary,
)
from .colour import C
from .display import (
    COLUMNS,
    ENDPOINT_INDENT,
    ENDPOINT_WIDTH,
    HEADER_LINE,
    WAY_WIDTH,
    endpoint,
    flow_macs,
    proto_colour,
    render,
    row_cells,
    way,
)
from .feed import CLIENT_BACKLOG, EVENTS, PROSE_KINDS, Feed
from .keys import (
    HELP_KEY,
    KEY_HELP,
    KEY_WIDTH,
    KEYS,
    PAUSE_BUFFER,
    QR_KEY,
    WEB_EXCLUDED,
    Controls,
    Keyboard,
    web_keys,
    write_keys,
)
from .qr import MAX_BYTES, QUIET_ZONE, encode, fits, window, write_qr
from .services import EPHEMERAL_FLOOR, SUPPLEMENTAL_SERVICES, service_name
from .sizescale import (
    DEFAULT_SIZE_SCALE_MAX,
    MIN_DYNAMIC_SCALE_MAX,
    SIZE_RAMP,
    SIZE_SCALE_FLOOR,
    SizeScale,
    SpanScale,
    size_scale_arg,
    size_window_arg,
)
from .statusbar import (
    MIN_STATUS_ROWS,
    RATE_WINDOW,
    REPAINT_INTERVAL,
    STATUS_ROWS,
    Rates,
    StatusBar,
    run_line,
    snapshot,
    status_lines,
    wire_line,
)
from .sticky import (
    HEADER_ROWS,
    MIN_STICKY_ROWS,
    RESIZE_POLL_LINES,
    StickyHeader,
    enable_windows_vt,
    scroll_region,
)
from .tally import MAX_SPEED_EVENTS, MAX_TRACKED_KEYS, TOP_N, Tally
from .values import human_bits, human_bytes, human_clock, human_count, human_duration
from .web import (
    DEFAULT_WEB_PORT,
    HEARTBEAT,
    MAX_CLIENTS,
    WEB_ENDPOINT_WIDTH,
    WebInterface,
    content_policy,
    host_allowed,
    is_loopback,
    origin_allowed,
)

__all__ = [
    "C", "Controls", "DEFAULT_SIZE_SCALE_MAX", "ENDPOINT_INDENT",
    "ENDPOINT_WIDTH", "WAY_WIDTH", "flow_macs", "human_clock", "way",
    "HELP_KEY", "KEY_HELP", "KEY_WIDTH", "KEYS", "Keyboard",
    "PAUSE_BUFFER", "QR_KEY",
    "write_keys",
    "MAX_BYTES", "QUIET_ZONE", "encode", "fits", "window", "write_qr",
    "HEADER_LINE", "HEADER_ROWS", "MIN_DYNAMIC_SCALE_MAX", "MIN_STATUS_ROWS",
    "MIN_STICKY_ROWS", "RATE_WINDOW", "REPAINT_INTERVAL",
    "STATUS_ROWS", "Rates", "StatusBar", "run_line", "scroll_region",
    "snapshot", "status_lines", "wire_line",
    "RESIZE_POLL_LINES", "SIZE_RAMP", "SIZE_SCALE_FLOOR",
    "SizeScale", "StickyHeader",
    "MAX_SPEED_EVENTS", "MAX_TRACKED_KEYS", "TOP_N", "Tally",
    "human_bits", "human_duration",
    "EPHEMERAL_FLOOR", "SUPPLEMENTAL_SERVICES", "service_name",
    "proto_colour", "write_summary", "write_hosts", "SpanScale",
    "enable_windows_vt", "endpoint",
    "build_parser",
    "human_bytes", "human_count", "main", "render", "report_events",
    "should_show", "size_scale_arg", "size_window_arg",
    "COLUMNS", "row_cells", "tee", "flow_record",
    "CLIENT_BACKLOG", "EVENTS", "PROSE_KINDS", "Feed",
    "DEFAULT_WEB_PORT", "HEARTBEAT", "MAX_CLIENTS", "WEB_ENDPOINT_WIDTH",
    "WEB_EXCLUDED", "WebInterface", "content_policy", "host_allowed",
    "is_loopback", "origin_allowed", "web_keys",
]
