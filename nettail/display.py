"""Laying one flow out as a line of text.
"""

from datetime import datetime

from netflume import (
    FLOW_END_REASON,
    PROTO_NAMES,
    addr_kind,
    flow_duration,
    flow_endpoints,
    flow_timestamp,
    tcp_flags_str,
)

from .colour import C
from .services import service_name
from .values import human_bytes, human_count


def proto_colour(name):
    """The colour a protocol is shown in.

    Built when it is asked for rather than at import, so that disabling colour
    later still takes effect: C.disable() blanks the codes on the class, and a
    dictionary built early would still be holding the originals.
    """
    return {"TCP": C.GREEN, "UDP": C.YELLOW,
            "ICMP": C.MAGENTA, "ICMP6": C.MAGENTA}.get(name, C.GREY)


def endpoint(addr, port, proto, width, resolver=None, named=False):
    """Render "ip:port/service (hostname)", trimmed to fit width.

    The service name rides on the port it describes, so the parentheses always
    mean "resolved hostname" and nothing else. The hostname is kept in
    preference to the service name when space is tight.

    With `named` set, which is what the n key turns on, a host that answered
    to a name is shown by that name instead of by its address, and the
    parentheses go with the address they were explaining. An address that answered to
    nothing is still an address, so a column under the n key is a mixture, and
    deliberately so: the alternative is hiding the hosts nothing is known about
    behind a blank, which are the ones worth noticing.
    """
    if addr is None:
        return "-".ljust(width)
    stem = f"[{addr}]" if ":" in str(addr) else str(addr)
    if port:
        stem = f"{stem}:{port}"

    svc = service_name(port, proto)
    base = f"{stem}/{svc}" if svc else stem

    host = resolver.lookup(addr) if resolver else None

    if named and host:
        stem = f"{host}:{port}" if port else host
        base = f"{stem}/{svc}" if svc else stem
        if len(base) > width:
            base = f"{host}:{port}" if port else host
    elif host:
        candidate = f"{base} ({host})"
        if len(candidate) > width:
            # Drop the service name first, the hostname matters more.
            base = stem
            candidate = f"{base} ({host})"
        if len(candidate) <= width:
            base = candidate
        else:
            # Still does not fit. Trim the hostname rather than lose it,
            # provided enough room remains to stay readable.
            room = width - len(base) - 3
            if room >= 6:
                base = f"{base} ({host[:room - 1]}…)"

    if len(base) > width:
        base = base[:width - 1] + "…"
    return base.ljust(width)


TIME_WIDTH = 12
EXPORTER_WIDTH = 15
PROTO_WIDTH = 6
ENDPOINT_WIDTH = 40
WAY_WIDTH = 1            # the arrow between the two endpoints
PKTS_WIDTH = 7
BYTES_WIDTH = 8
DUR_WIDTH = 7

# Where the SOURCE column begins, which is where a continuation line has to
# start to sit under it. Counted rather than written down, so that widening a
# column above cannot leave the line beneath it one place out.
ENDPOINT_INDENT = TIME_WIDTH + 1 + EXPORTER_WIDTH + 1 + PROTO_WIDTH + 1

HEADER_LINE = (
    f"{'TIME':<{TIME_WIDTH}} {'EXPORTER':<{EXPORTER_WIDTH}} "
    f"{'PROTO':<{PROTO_WIDTH}} "
    f"{'SOURCE':<{ENDPOINT_WIDTH}} {'':<{WAY_WIDTH}} "
    f"{'DESTINATION':<{ENDPOINT_WIDTH}} "
    f"{'PKTS':>{PKTS_WIDTH}} {'BYTES':>{BYTES_WIDTH}} {'DUR':>{DUR_WIDTH}}  FLAGS"
)

# Multicast and link-local count as being on this side of the router. A flow to
# 224.0.0.251 never went anywhere near the internet, whatever else is true of
# the address, and the arrow is about which side of the boundary each end sat.
LOCAL_KINDS = ("private", "multicast", "special")


def way(src, dst):
    """Which way a flow crossed the boundary: the arrow, and its colour.

    Down for something arriving from the internet, up for something leaving
    for it, and a pair of opposed arrows for a conversation that stayed on the
    network.

    The pair rather than a single left-right arrow: one arrow has to fit two
    heads and a shaft across a single cell, and a console that gives it no more
    width than a letter renders it as a smudge. Stacking two spreads the same
    detail down the cell instead, where there is room, which is the same reason
    the up and down arrows read cleanly. It is drawn dimmed, since local
    chatter is most of what crosses the screen and should recede far enough to
    let a crossing stand out of it.

    That is the way round a router's own dashboard draws it, where a download
    points down, and it is also the way round a network diagram is drawn, with
    the internet above and everything here below it. Read the column as
    pointing at where the flow went: out and up to the internet, or down off it
    and on to something here. Whichever way it is drawn, it wants to be the
    same way round everywhere, so this is the one place the mapping is written
    down.

    Anything that fits none of the three gets a blank rather than a guess,
    whether that is a flow between two public addresses or an end that could
    not be read. An arrow pointing the wrong way is worse than no arrow.
    """
    if not src or not dst:
        return " ", C.GREY
    src_kind, dst_kind = addr_kind(src), addr_kind(dst)
    src_local, dst_local = src_kind in LOCAL_KINDS, dst_kind in LOCAL_KINDS
    if src_kind == "public" and dst_local:
        return "↓", C.CYAN
    if src_local and dst_kind == "public":
        return "↑", C.CYAN
    if src_local and dst_local:
        return "⇄", C.DIM
    return " ", C.GREY


def flow_macs(rec):
    """The two hardware addresses on a flow, either of which may be missing.

    An exporter that reports MACs at all may report them under the plain
    elements or the post-NAT ones, and a UniFi gateway is as likely to send
    one pair as the other. Whichever arrived is the one worth showing.
    """
    src = rec.get("src_mac") or rec.get("post_src_mac")
    dst = rec.get("dst_mac") or rec.get("post_dst_mac")
    return src, dst


def render(rec, hdr, args, resolver, scale):
    proto = rec.get("proto")
    proto_name = PROTO_NAMES.get(proto, str(proto) if proto is not None else "?")

    src, dst = flow_endpoints(rec)
    sport = rec.get("src_port")
    dport = rec.get("dst_port")

    ts = flow_timestamp(rec, hdr)
    tstr = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]

    pkts = rec.get("packets", rec.get("packets_total"))
    octets = rec.get("octets", rec.get("octets_total"))

    # The width has to be applied before the colour: escape codes are not
    # printable, but str.format counts them.
    bytes_cell = scale.paint(f"{human_bytes(octets):>{BYTES_WIDTH}}", octets)

    dur = flow_duration(rec, hdr)
    dur_str = f"{dur:.2f}s" if dur is not None else "-"

    flags = tcp_flags_str(rec.get("tcp_flags")) if proto == 6 else ""

    dkind = addr_kind(dst) if dst else "unknown"
    if dkind == "public":
        dst_col = C.CYAN
    elif dkind == "multicast":
        dst_col = C.GREY
    else:
        dst_col = C.DIM

    proto_col = proto_colour(proto_name)
    named = bool(getattr(args, "named_hosts", False))
    arrow, arrow_col = way(src, dst)

    line = (
        f"{C.GREY}{tstr:<{TIME_WIDTH}}{C.RESET} "
        f"{C.GREY}{hdr['exporter']:<{EXPORTER_WIDTH}}{C.RESET} "
        f"{proto_col}{proto_name:<{PROTO_WIDTH}}{C.RESET} "
        f"{endpoint(src, sport, proto, ENDPOINT_WIDTH, resolver, named)} "
        f"{arrow_col}{arrow}{C.RESET} "
        f"{dst_col}{endpoint(dst, dport, proto, ENDPOINT_WIDTH, resolver, named)}"
        f"{C.RESET} "
        f"{human_count(pkts):>{PKTS_WIDTH}} {bytes_cell} {dur_str:>{DUR_WIDTH}}  "
        f"{C.DIM}{flags}{C.RESET}"
    )
    print(line)

    if getattr(args, "show_macs", False):
        src_mac, dst_mac = flow_macs(rec)
        # Nothing at all rather than a row of dashes: an exporter that does not
        # send MACs would otherwise double the height of the display to say so
        # once per flow. v5 never carries them, and plenty of v9 exporters
        # leave the elements out.
        if src_mac or dst_mac:
            print(f"{' ' * ENDPOINT_INDENT}"
                  f"{C.DIM}{src_mac or '-':<{ENDPOINT_WIDTH}}{C.RESET} "
                  f"{' ' * WAY_WIDTH} "
                  f"{C.DIM}{dst_mac or '-'}{C.RESET}")

    if args.verbose:
        skip = {"src_addr", "dst_addr", "src_port", "dst_port", "proto",
                "packets", "octets", "tcp_flags"}
        extras = []
        for k, v in sorted(rec.items()):
            if k in skip or v in (None, 0, ""):
                continue
            if k == "flow_end_reason":
                v = FLOW_END_REASON.get(v, v)
            extras.append(f"{k}={v}")
        if extras:
            print(f"    {C.GREY}{'  '.join(extras)}{C.RESET}")
