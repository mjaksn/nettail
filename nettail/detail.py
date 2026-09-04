"""Everything known about one flow, written out for the details dialog.

Pure functions with no I/O, called on the receive thread because that is the
only thread allowed to read collector state: a request thread may read a feed
queue and put an ask on a queue, and that is the whole of its authority. The
report they build goes back to the browser as a `detail` event with the ask's
id echoed on it.

**Every value here is formatted in Python, and painted here too.** The page
names no field, no flag and no protocol, for the reason it hardcodes no column
and no key: a service name is whatever this machine's services database calls
that port, a country is whatever the reader's database says, and a second
opinion written in JavaScript would be a second thing to keep in step with
`values.py`. So a section is a title and a list of (label, value) pairs, a
table is a head and rows of finished strings, and the page has exactly two
renderers. The colour rides to the browser as escape codes inside those
finished strings, which `web.html` turns back into spans exactly as it does
for a flow row; how a colour is chosen is set out in the comment above
`_paint` below.

Nothing is reimplemented that netflume or the display already answers.
`flow_endpoints`, `flow_timestamp`, `flow_duration`, `PROTO_NAMES`,
`FLOW_END_REASON` and `TCP_FLAG_BITS` come from the decoder; `display.way`
decides which way a flow crossed the boundary and this only puts words to it;
`services.service_name`, `country.country_of` and `resolver.lookup` answer for
an address and a port exactly as they do for a terminal row.
"""

import heapq
import time
from datetime import datetime

from netflume import (
    FLOW_END_REASON,
    PROTO_NAMES,
    TCP_FLAG_BITS,
    addr_kind,
    flow_duration,
    flow_endpoints,
    flow_timestamp,
)

from . import country
from .colour import C
from .display import address_colour, proto_colour, way
from .services import service_name
from .values import human_bytes, human_count, human_duration

# How many rows a table in the dialog shows before it says how many it left
# out. It bounds the peer list, which is the one that can really run long,
# and the protocol and service tables with it: an endpoint's service table is
# capped in `traffic.py` at a figure meant to keep the memory honest rather
# than to be read, and two thousand rows is not a table anybody reads.
DETAIL_ROWS = 20

# The name of each TCP flag, keyed by the letter netflume writes it as. Keyed
# that way on purpose: `test_detail` holds this table and `TCP_FLAG_BITS` to
# each other in both directions, so a flag gained or renamed upstream fails
# here rather than arriving as a letter with no word beside it.
TCP_FLAG_NAMES = {
    "C": "CWR",
    "E": "ECE",
    "U": "URG",
    "A": "ACK",
    "P": "PSH",
    "R": "RST",
    "S": "SYN",
    "F": "FIN",
}

# What each information element is called in words, keyed by the name netflume
# decodes it under. `test_detail` holds this to `netflume.IE`, so an element
# added upstream fails here rather than reaching a reader as a bare key, which
# is what `test_services` does for the ephemeral floor.
#
# Units are in the label wherever the number alone would be read wrongly. The
# switched pair is the worst of them: they are milliseconds of the exporter's
# own uptime rather than any kind of clock, and a reader who takes them for a
# timestamp gets 1970.
FIELD_LABELS = {
    "active_timeout": "Active timeout (seconds)",
    "bgp_next_as": "BGP next AS",
    "direction": "Direction, as the exporter reported it",
    "dot1q_cust_vlan": "802.1Q customer VLAN",
    "dot1q_prio": "802.1Q priority",
    "dot1q_vlan": "802.1Q VLAN",
    "dst_addr": "Destination address",
    "dst_as": "Destination AS",
    "dst_mac": "Destination MAC",
    "dst_mask": "Destination prefix length",
    "dst_port": "Destination port",
    "dst_tos": "Destination type of service",
    "egress_vrf": "Egress VRF",
    "enterprise_id": "Enterprise ID",
    "exported_flows": "Flows this exporter has exported",
    "exported_octets": "Bytes this exporter has exported",
    "exported_packets": "Packets this exporter has exported",
    "first_switched": "First switched (ms of exporter uptime)",
    "flow_end_ms": "Flow end (ms since the epoch)",
    "flow_end_ns": "Flow end (ns since the epoch)",
    "flow_end_reason": "Flow end reason",
    "flow_end_s": "Flow end (seconds since the epoch)",
    "flow_end_us": "Flow end (microseconds since the epoch)",
    "flow_id": "Flow ID",
    "flow_label": "IPv6 flow label",
    "flow_start_ms": "Flow start (ms since the epoch)",
    "flow_start_ns": "Flow start (ns since the epoch)",
    "flow_start_s": "Flow start (seconds since the epoch)",
    "flow_start_us": "Flow start (microseconds since the epoch)",
    "flows": "Flows this record stands for",
    "forwarding_status": "Forwarding status",
    "icmp_code": "ICMP code",
    "icmp_type": "ICMP type",
    "icmp_type_code": "ICMP type and code",
    "icmp_type_code_v6": "ICMPv6 type and code",
    "idle_timeout": "Idle timeout (seconds)",
    "if_desc": "Interface description",
    "if_name": "Interface name",
    "in_if": "Ingress interface",
    "ingress_vrf": "Ingress VRF",
    "ip_version": "IP version",
    "last_switched": "Last switched (ms of exporter uptime)",
    "max_ttl": "Largest TTL seen",
    "min_ttl": "Smallest TTL seen",
    "mpls_label_1": "MPLS label 1",
    "next_hop": "Next hop",
    "observation_point_id": "Observation point ID",
    "observation_time_ms": "Observation time (ms since the epoch)",
    "octets": "Bytes",
    "octets_total": "Bytes, as a running total for the flow",
    "out_if": "Egress interface",
    "out_octets": "Bytes out of the egress interface",
    "out_packets": "Packets out of the egress interface",
    "packets": "Packets",
    "packets_total": "Packets, as a running total for the flow",
    "post_dst_mac": "Destination MAC after forwarding",
    "post_nat_dst_addr": "Destination address after NAT",
    "post_nat_dst_port": "Destination port after NAT",
    "post_nat_src_addr": "Source address after NAT",
    "post_nat_src_port": "Source port after NAT",
    "post_src_mac": "Source MAC after forwarding",
    "post_vlan": "VLAN after forwarding",
    "proto": "Protocol",
    "sampler_id": "Sampler ID",
    "sampler_interval": "Sampler interval",
    "sampler_mode": "Sampler mode",
    "sampler_name": "Sampler name",
    "sampling_algorithm": "Sampling algorithm",
    "sampling_interval": "Sampling interval",
    "sampling_packet_interval": "Packets sampled per interval",
    "sampling_packet_space": "Packets skipped between samples",
    "sampling_population": "Sampling population",
    "sampling_size": "Sampling size",
    "selector_algorithm": "Selector algorithm",
    "selector_id": "Selector ID",
    "src_addr": "Source address",
    "src_as": "Source AS",
    "src_mac": "Source MAC",
    "src_mask": "Source prefix length",
    "src_port": "Source port",
    "srh_flags": "Segment routing header flags",
    "tcp_flags": "TCP flags",
    "template_id": "Template ID",
    "tos": "Type of service",
    "vlan": "VLAN",
}

# The record keys the flow section spells out in rows of its own, and so does
# not repeat further down. Everything else in the record is listed through the
# label table, which is what makes the dialog a superset of the `--verbose`
# dump rather than a differently arranged half of it.
FLOW_KEYS_SHOWN = frozenset((
    "src_addr", "dst_addr", "src_port", "dst_port", "proto",
    "packets", "packets_total", "octets", "octets_total",
    "tcp_flags", "flow_end_reason",
))

# What the two ends of a flow are called in the report, in the order
# `flow_endpoints` hands them back.
END_TITLES = ("Source", "Destination")

# What a flow's arrow means in words. Keyed by the arrow `display.way` chose,
# so which way round a crossing is drawn goes on being decided in exactly one
# place and this only says it out loud.
WAY_WORDS = {
    "↓": "arriving from the internet",
    "↑": "leaving for the internet",
    "⇄": "between two addresses on this network",
}


# -- how a value is painted ------------------------------------------------
#
# This dialog is the browser's alone, and a browser is a colour-capable reader
# whatever stdout happens to be, so every value below is painted at full
# strength here. A browser that refused colour has it taken out at the
# boundary, by `cli.detail_for_web`, rather than by a setting threaded down
# through every function that builds a row.
#
# The vocabulary is the one the flow rows and the traffic summary already use,
# so that the dialog reads as part of the same program:
#
# - **A figure is cyan, and whatever restates or measures it is grey**: the
#   units after a size, the short form in brackets, the "3m20s ago" after a
#   clock reading. That is what the summary's own rows do, and it is what lets
#   a panel of a dozen figures be scanned for the one somebody came for.
# - **An identity is coloured by what it is.** An address takes
#   `display.address_colour`, the three colours the summary's tables give one;
#   a hostname is green, as it is there; a protocol takes
#   `display.proto_colour`, so it matches the PROTO column of the row the
#   dialog was opened from; a service is split at its slash, the port cyan and
#   the name green, exactly as the summary's services table splits one.
# - **A direction takes the colour `display.way` chose for the arrow**, so the
#   sentence here and the arrow in the row above it agree without the question
#   being asked twice.
# - **Prose and raw record fields are left alone.** Grey arrives in the page as
#   the ink the label column is drawn in, so a whole value greyed stops looking
#   like a value at all, and a sentence saying there is nothing to report is
#   not a fact to be picked out of a list.


def _paint(*pieces):
    """One value built from (text, colour) pieces.

    `cli._painted` is the same idea for a terminal row and hands back the
    plain text as well, because a column there is padded to what a reader sees
    and escape codes are not seen. Nothing in a dialog is padded, so only the
    painted half is wanted here.

    An empty piece is dropped and an uncoloured one is left bare, so a run with
    the codes blanked comes out as the text and nothing around it.
    """
    return "".join("%s%s%s" % (colour, text, C.RESET) if colour else text
                   for text, colour in pieces if text)


def _nothing():
    """The stand-in for a field the exporter did not send."""
    return _paint(("-", C.GREY))


def _figure(text, aside=""):
    """A number, with whatever restates or measures it beside it."""
    return _paint((text, C.CYAN), (aside, C.GREY))


def _stat(value):
    """One figure the exporter sent, with nothing to restate it."""
    return _nothing() if value is None else _figure(str(value))


def _address(addr, bracket=False):
    """One address, in the colour its kind is drawn in everywhere else.

    `bracket` is for the flow's own two ends, which put an IPv6 address in
    square brackets exactly as the SOURCE and DESTINATION columns of a row do.
    Nothing else here does, because nothing else has a port after it.
    """
    text = str(addr)
    if bracket and ":" in text:
        text = "[%s]" % text
    return _paint((text, address_colour(addr)))


def _hostname(host):
    """A name something on this network answered to."""
    return _paint((host, C.GREEN))


def _kind(addr):
    """Which side of the boundary an address is on, in that side's colour.

    The word and the address above it come out the same colour, which is the
    cheapest way of saying that the one explains the other.
    """
    return _paint((addr_kind(addr), address_colour(addr)))


def _protocol(name):
    """A protocol, in the colour the PROTO column of a row gives it."""
    return _paint((name, proto_colour(name)))


def _service(name):
    """A service key, split at its slash the way the summary splits one.

    "443/https" is a number the exporter sent and a convention this machine
    happens to hold, and which half is which is worth seeing at a glance. A
    key with no slash in it is all convention.
    """
    port, slash, named = name.partition("/")
    if not slash:
        return _paint((name, C.GREEN))
    return _paint((port, C.CYAN), (slash, C.GREY), (named, C.GREEN))


def spell_flags(value):
    """The TCP flags as words, with the byte that carried them.

    "ACK, SYN (0x12)", which is the same fact the FLAGS column shows as
    ".A....S." and nobody reads at a glance. In the decoder's own bit order,
    which is the wire's, so that the words run left to right in the same order
    as the letters in the column above them: a reader comparing the two should
    not have to reorder one of them in their head. That is why it is written
    "ACK, SYN" and not the "SYN, ACK" a handshake is usually described as.

    Absent flags are left out rather than listed as absent. The column already
    answers which of the eight were set, and this is the row for reading
    rather than for scanning.
    """
    if value is None:
        return _nothing()
    names = [TCP_FLAG_NAMES[letter] for bit, letter in TCP_FLAG_BITS
             if value & bit]
    # The words are left plain and the byte is the grey aside behind them,
    # which is the shape every figure in the dialog takes. "none" is grey
    # instead, being the absence of the thing the row is about.
    return _paint((", ".join(names) if names else "none",
                   None if names else C.GREY),
                  (" (0x%02x)" % value, C.GREY))


def _clock(when):
    """A wall clock time, to the millisecond, in this machine's own zone."""
    return datetime.fromtimestamp(when).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def at(when):
    """That reading as a value in the report."""
    return _paint((_clock(when), C.CYAN)) if when else _nothing()


def since(when, now):
    """"14:02:11.500 (3m20s ago)", or the time alone when it is in the future.

    A clock reading answers "when", and how long ago answers "is this still
    going on", which is the question somebody clicking a row usually has.
    """
    if not when:
        return _nothing()
    gap = now - when
    if gap < 0:
        return at(when)
    return _figure(_clock(when), " (%s ago)" % human_duration(gap))


def _count(n):
    """A number with its thousands marked, and the short form beside it.

    Both, because the dialog is where the exact figure belongs, and the short
    form is what the rest of the interface shows: a reader comparing this
    against the BYTES column should not have to convert one to the other.
    """
    if n is None:
        return _nothing()
    short = human_count(n)
    return _figure("{:,}".format(n),
                   "" if short == str(n) else " ({})".format(short))


def _size(n):
    if n is None:
        return _nothing()
    return _figure("{:,}".format(n), " bytes ({})".format(human_bytes(n)))


def _halves(total, inward, outward):
    """A total with the endpoint's two halves spelled out after it.

    Words rather than a second pair of brackets, because the total already
    carries one: "1,500 bytes (1.5K) (0B received, 1.5K sent)" is two nested
    parentheticals and reads as neither.
    """
    return _paint((total, None),
                  (", of which ", C.GREY), (inward, C.CYAN),
                  (" received and ", C.GREY), (outward, C.CYAN),
                  (" sent", C.GREY))


def _addr_facts(title, addr, port, proto, resolver):
    """The rows describing one end of a flow.

    A row is left out rather than filled with a dash when there is nothing to
    say: a hostname nobody looked up and a country nobody asked for are not
    facts about this flow, and a panel of empty rows hides the ones that are.
    """
    facts = []
    if not addr:
        return [[title, "the exporter sent no address for this end"]]
    facts.append([title, _address(addr, bracket=True)])
    if port:
        named = service_name(port, proto)
        facts.append(["%s port" % title,
                      _service("%d/%s" % (port, named)) if named
                      else _figure(str(port))])
    host = resolver.lookup(addr) if resolver else None
    if host:
        facts.append(["%s name" % title, _hostname(host)])
    facts.append(["%s kind" % title, _kind(addr)])
    code = country.country_of(addr)
    if code:
        # Grey, as the summary paints a country marker, and it is the one
        # value here whose colour hardly matters: what reaches a browser is a
        # flag, which brings its own.
        facts.append(["%s country" % title, _paint((code, C.GREY))])
    return facts


def flow_section(rec, hdr, resolver):
    """The flow proper: its two ends, its size, its timing, and every field.

    The named rows come first because they are what somebody clicked the row
    to see, and every remaining key of the record follows them in sorted
    order through the label table. Nothing is skipped down there, not even a
    zero: a field the exporter took the trouble to send is a fact about the
    flow, and `--verbose` leaving the empty ones out is a concession to a
    console line rather than a judgement about what matters.
    """
    src, dst = flow_endpoints(rec)
    proto = rec.get("proto")
    proto_name = PROTO_NAMES.get(proto, str(proto) if proto is not None else "?")

    facts = []
    facts.extend(_addr_facts(END_TITLES[0], src, rec.get("src_port"), proto,
                             resolver))
    facts.extend(_addr_facts(END_TITLES[1], dst, rec.get("dst_port"), proto,
                             resolver))

    # The arrow's own colour, so the sentence here and the arrow in the row
    # the dialog was opened from say the same thing twice over.
    arrow, arrow_colour = way(src, dst)
    facts.append(["Direction",
                  _paint((WAY_WORDS.get(arrow,
                                        "neither end is on this network"),
                          arrow_colour))])
    proto_pieces = [(proto_name, proto_colour(proto_name))]
    if proto is not None:
        proto_pieces.append((" (%s)" % proto, C.GREY))
    facts.append(["Protocol", _paint(*proto_pieces)])

    start = flow_timestamp(rec, hdr)
    duration = flow_duration(rec, hdr)
    facts.append(["Started", at(start)])
    if duration is not None:
        facts.append(["Ended", at(start + duration)])
        facts.append(["Duration",
                      _figure(human_duration(duration),
                              " (%.3f seconds)" % duration)])
    else:
        facts.append(["Duration",
                      "the exporter sent no start and end for this flow"])

    octets = rec.get("octets", rec.get("octets_total"))
    packets = rec.get("packets", rec.get("packets_total"))
    facts.append(["Bytes", _size(octets)])
    facts.append(["Packets", _count(packets)])
    if octets and packets:
        facts.append(["Mean packet size", _figure(human_bytes(octets / packets))])
    if duration and octets:
        facts.append(["Mean rate over its lifetime",
                      _figure(human_bytes(octets / duration), " per second")])

    if proto == 6 or rec.get("tcp_flags") is not None:
        facts.append(["TCP flags", spell_flags(rec.get("tcp_flags"))])
    reason = rec.get("flow_end_reason")
    if reason is not None:
        facts.append(["Flow end reason",
                      FLOW_END_REASON.get(reason, str(reason))])

    for key in sorted(rec):
        if key in FLOW_KEYS_SHOWN:
            continue
        facts.append([FIELD_LABELS.get(key, key), _value(rec[key])])
    return {"title": "Flow", "facts": facts, "tables": []}


def _value(value):
    """One raw field, as text. Nothing clever, because nothing here knows more.

    An element this build has no name for arrives under a key of its own
    making, `ie123` or `e9.42`, and its value is whatever `decode_value` made
    of the bytes. Rendering it as itself is the only honest answer.
    """
    return "-" if value is None else str(value)


def datagram_section(hdr):
    """The export message the flow arrived in.

    None of this survives the receive loop today: the header is used to
    timestamp a flow and then dropped. What it says is about the exporter and
    the path rather than about the traffic, which is exactly what is wanted
    when a figure looks wrong. The sequence number and the export gap say
    whether anything was lost getting here, and the arrival time beside the
    export time says how far behind the exporter's clock is.
    """
    version = hdr.get("version")
    named = {5: "NetFlow v5", 9: "NetFlow v9", 10: "IPFIX (version 10)"}
    exporter = hdr.get("exporter")
    facts = [
        ["Exporter", _address(exporter) if exporter else _nothing()],
        ["Version", named.get(version, str(version))],
        # v5 has no observation domain and puts the engine id in the same
        # field, which is what netflume decodes it into, so it is named for
        # what it actually is on each version rather than for one of them.
        ["Engine ID" if version == 5 else "Observation domain",
         _stat(hdr.get("domain"))],
        ["Sequence", _stat(hdr.get("sequence"))],
        ["Export time", at(hdr.get("unix_secs"))],
    ]
    uptime = hdr.get("sys_uptime")
    if uptime is not None:
        # Milliseconds since the exporter booted, which is also the clock the
        # switched pair above is measured against.
        facts.append(["Exporter uptime",
                      _figure(human_duration(uptime / 1000.0),
                              " (%s ms)" % "{:,}".format(uptime))])
    facts.append(["Received here", at(hdr.get("received"))])
    # The export time is whole seconds, so the sub-second part of the arrival
    # is not a difference between two clocks, it is the resolution of one of
    # them. A row is only worth printing once the gap is a second or more, and
    # then it says which way round it is: `abs` would call an exporter that is
    # running fast one that is running slow.
    if hdr.get("unix_secs") and hdr.get("received"):
        drift = hdr["received"] - hdr["unix_secs"]
        if abs(drift) >= 1:
            facts.append(["Exporter's clock",
                          _figure(human_duration(abs(drift)),
                                  " %s this machine's"
                                  % ("behind" if drift > 0 else "ahead of"))])
    size = hdr.get("datagram_bytes")
    if size is not None:
        facts.append(["Datagram size", _size(size)])
    count = hdr.get("flow_count")
    if count is not None:
        facts.append(["Flows in the datagram", _count(count)])
    rate = hdr.get("sampling_rate")
    if rate is not None:
        facts.append(["Sampling in force",
                      _figure("1 in %s" % "{:,}".format(rate),
                              ", as the exporter advertised it")
                      if rate and rate > 1
                      else _paint(("unsampled", C.GREY))])
    gap = hdr.get("gap")
    if gap is not None:
        facts.append(["Exports missed before this one", _count(gap)])
    return {"title": "Datagram", "facts": facts, "tables": []}


def _label(addr, resolver):
    """An address with its name after it, where one is known."""
    host = resolver.lookup(addr) if resolver else None
    return _paint((str(addr), address_colour(addr)),
                  (" (" if host else "", C.GREY),
                  (host or "", C.GREEN),
                  (")" if host else "", C.GREY))


def _table(title, head, keys, rank, cells):
    """One table, cut to `DETAIL_ROWS` with a count of what did not fit.

    `rank` is what the rows are ordered on, largest first, and `cells` turns
    one key into its row. `nlargest` rather than a sort, because the tables
    behind this are capped in the thousands and only twenty of them are shown.
    """
    keys = list(keys)
    best = heapq.nlargest(DETAIL_ROWS, keys, key=rank)
    return {"title": title, "head": head,
            "rows": [cells(key) for key in best],
            "more": max(len(keys) - len(best), 0)}


# The two shapes of table this module builds. An endpoint's tables carry its
# own two halves, which is why they have two more columns than a pair's: a
# conversation has no side to be read from and its protocol and service totals
# are direction independent, exactly as asked for.
END_HEAD = (["Flows", ">"], ["Bytes", ">"], ["Received", ">"], ["Sent", ">"],
            ["Packets", ">"])
PAIR_HEAD = (["Flows", ">"], ["Bytes", ">"], ["Packets", ">"])


def _counts_cells(counts):
    """The figure columns of an endpoint's table.

    The totals are cyan and the two halves grey, which is how `_halves` splits
    the same figures in a facts list and for the same reason: received and
    sent are the total beside them broken up rather than three figures of
    equal standing. Five columns in one colour would say nothing about any of
    them.
    """
    return [_figure(human_count(counts.total.flows)),
            _figure(human_bytes(counts.total.bytes)),
            _paint((human_bytes(counts.inward.bytes), C.GREY)),
            _paint((human_bytes(counts.outward.bytes), C.GREY)),
            _figure(human_count(counts.total.packets))]


def _leg_cells(leg):
    return [_figure(human_count(leg.flows)), _figure(human_bytes(leg.bytes)),
            _figure(human_count(leg.packets))]


def _counts_table(title, first, table, paint):
    return _table(title, [[first, "<"]] + list(END_HEAD), table,
                  lambda key: table[key].total.bytes,
                  lambda key: [paint(key)] + _counts_cells(table[key]))


def _leg_table(title, first, table, paint):
    return _table(title, [[first, "<"]] + list(PAIR_HEAD), table,
                  lambda key: table[key].bytes,
                  lambda key: [paint(key)] + _leg_cells(table[key]))


def endpoint_report(traffic, addr, resolver, title, now):
    """One address: what it is, what it has done, and who with.

    *Received* and *sent* are read from this address rather than from the
    network edge, which is the definition written on `traffic.py` and the one
    trap in the whole feature. A public web server's panel says it sent what
    it served; the summary's external table, asked a different question about
    the same bytes, calls the same traffic inbound.
    """
    if not addr:
        return {"title": title,
                "facts": [["Address",
                           "the exporter sent no address for this end"]],
                "tables": []}
    end = traffic.endpoints.get(addr)
    if end is None:
        return {"title": "%s: %s" % (title, addr),
                "facts": [["Address", str(addr)],
                          ["Seen", "this address is not in the collector's "
                                   "tables. It was either cleared with the c "
                                   "key or dropped to keep the tables "
                                   "bounded."]],
                "tables": []}

    counts = end.counts
    facts = [["Address", _address(addr)]]
    host = resolver.lookup(addr) if resolver else None
    if host:
        facts.append(["Name", _hostname(host)])
    facts.append(["Kind", _kind(addr)])
    code = country.country_of(addr)
    if code:
        facts.append(["Country", _paint((code, C.GREY))])
    facts.append(["First seen", since(end.first, now)])
    facts.append(["Last seen", since(end.last, now)])
    facts.append(["Flows", _halves(_count(counts.total.flows),
                                   human_count(counts.inward.flows),
                                   human_count(counts.outward.flows))])
    facts.append(["Bytes", _halves(_size(counts.total.bytes),
                                   human_bytes(counts.inward.bytes),
                                   human_bytes(counts.outward.bytes))])
    facts.append(["Packets", _halves(_count(counts.total.packets),
                                     human_count(counts.inward.packets),
                                     human_count(counts.outward.packets))])
    facts.append(["Distinct peers", _count(len(end.peers))])
    if traffic.total.bytes:
        facts.append(["Share of all bytes seen",
                      _figure("%.1f%%" % (100.0 * counts.total.bytes
                                          / traffic.total.bytes))])
    if counts.total.flows:
        facts.append(["Mean flow size",
                      _figure(human_bytes(counts.total.bytes
                                          / counts.total.flows))])
    if end.dropped:
        facts.append(["Dropped from its own tables",
                      _paint((_count(end.dropped), None),
                             (" quiet protocols or services, to keep them "
                              "bounded", C.GREY))])

    peers = sorted(end.peers)
    pairs = [(peer, traffic.pair_of(addr, peer)) for peer in peers]
    pairs = [(peer, pair) for peer, pair in pairs if pair is not None]
    by_peer = dict(pairs)

    def peer_row(peer):
        pair = by_peer[peer]
        # The pair's halves are named after its own two addresses, so which of
        # them this endpoint received is decided here, where there is an
        # endpoint to read it from.
        sent = pair.a_to_b if pair.a == addr else pair.b_to_a
        got = pair.b_to_a if pair.a == addr else pair.a_to_b
        return [_label(peer, resolver),
                _figure(human_count(pair.total.flows)),
                _figure(human_bytes(pair.total.bytes)),
                _paint((human_bytes(got.bytes), C.GREY)),
                _paint((human_bytes(sent.bytes), C.GREY)),
                _figure(human_count(pair.total.packets))]

    tables = [
        _counts_table("By protocol", "Protocol", end.protos, _protocol),
        _counts_table("By service", "Service", end.services, _service),
        _table("Peers", [["Peer", "<"]] + list(END_HEAD), by_peer,
               lambda peer: by_peer[peer].total.bytes, peer_row),
    ]
    return {"title": "%s: %s" % (title, addr), "facts": facts, "tables": tables}


def pair_report(traffic, a, b, resolver, now):
    """The two ends together: one conversation, whichever end opened it.

    Direction independent throughout, which is what was asked for and what a
    pair can honestly say: the halves are kept, and where they are worth
    reading is in each endpoint's peer row, where there is a side to read them
    from.
    """
    title = "Between the two"
    if not a or not b:
        return {"title": title,
                "facts": [["Both ends", "this flow has only one address, so "
                                        "there is no pair to report on"]],
                "tables": []}
    pair = traffic.pair_of(a, b)
    if pair is None:
        return {"title": title,
                "facts": [["Between",
                           _paint((_address(a), None), (" and ", C.GREY),
                                  (_address(b), None))],
                          ["Seen", "this pair is not in the collector's "
                                   "tables. It was either cleared with the c "
                                   "key or dropped to keep them bounded."]],
                "tables": []}
    facts = [
        ["Between", _paint((_label(pair.a, resolver), None),
                           (" and ", C.GREY),
                           (_label(pair.b, resolver), None))],
        ["First seen", since(pair.first, now)],
        ["Last seen", since(pair.last, now)],
        ["Flows", _count(pair.total.flows)],
        ["Bytes", _size(pair.total.bytes)],
        ["Packets", _count(pair.total.packets)],
    ]
    tables = [_leg_table("By protocol", "Protocol", pair.protos, _protocol),
              _leg_table("By service", "Service", pair.services, _service)]
    return {"title": title, "facts": facts, "tables": tables}


def report(ask, ring, tally, resolver, now=None):
    """The whole payload for one ask.

    `ask` is what a request thread put on the queue: the browser's own id, the
    serial of the row it clicked, and the two addresses that row was drawn
    from. The id is echoed back untouched, because every client receives every
    reply and matching them up is the page's job.

    A serial the ring no longer holds is the ordinary case rather than an
    error: the ring is bounded and a reader can click a row that has scrolled
    a long way up. `held` says so, the flow and datagram sections are left
    out, and the endpoint and pair reports still come from the addresses the
    ask carried, which is most of what was worth clicking for.

    A serial the ring holds is checked against the ends the ask carried before
    it is believed, and that is not belt and braces. Serials start again at 1
    every run, and a bookmarked tab reconnects across a restart with a page
    full of rows from the run before, since `--web-token` exists precisely so
    that a bookmark survives one. Without the check, clicking one of those
    rows answers with whatever flow the new run happens to have given that
    number, under a title that confidently names it. Not held is the truth,
    and the addresses on the row are still worth reporting on.
    """
    ask_id, serial, ends = ask
    if now is None:
        now = time.time()
    held = False
    if serial is not None and serial in ring:
        rec, hdr = ring[serial]
        held = tuple(flow_endpoints(rec)) == tuple(ends)
    if held:
        # The ring's own ends rather than the ask's. They agree, by the check
        # above, and reading them off the record keeps one source for them.
        ends = flow_endpoints(rec)
        sections = [flow_section(rec, hdr, resolver), datagram_section(hdr)]
    else:
        sections = [_gone(serial, len(ring))]
    src = ends[0] if ends else None
    dst = ends[1] if len(ends) > 1 else None
    return {
        "ask": ask_id,
        "n": serial,
        "held": held,
        # What the dialog calls itself. Written here rather than assembled in
        # the page, for the reason every other string in this payload is: the
        # page lays out what it is handed and names nothing.
        "title": _title(src, dst),
        "sections": sections,
        "ends": [endpoint_report(tally.traffic, src, resolver, END_TITLES[0], now),
                 endpoint_report(tally.traffic, dst, resolver, END_TITLES[1], now)],
        "pair": pair_report(tally.traffic, src, dst, resolver, now),
    }


def _title(src, dst):
    if src and dst:
        return "%s to %s" % (src, dst)
    return str(src or dst or "one flow")


def _gone(serial, kept):
    """The stand-in for a flow the ring no longer holds.

    Two ways to arrive and they are worth telling apart. A serial the ring
    does not answer for is the ordinary one, and it covers two cases that read
    the same to a reader: a row that has scrolled far enough up that the ring
    has dropped its flow, and a row left over from before a restart, whose
    serial this run has either not reached or has given to something else. A
    row carrying no serial at all is one published by a collector that was not
    stamping them.

    Said here rather than in the page, so that the words about what this
    program keeps live where the keeping is decided.
    """
    if serial is None:
        return {"title": "Flow",
                "facts": [["This flow",
                           "this row carries no serial, so the collector "
                           "cannot look it up. The figures below are for its "
                           "two addresses."]],
                "tables": []}
    return {"title": "Flow",
            "facts": [["This flow",
                       "the collector is not holding this flow. It keeps the "
                       "most recent %s it published, and this row is not "
                       "among them, either because it has scrolled that far "
                       "up or because it is left over from before a restart. "
                       "The figures below are for its two addresses, which "
                       "are kept for as long as the run."
                       % "{:,}".format(kept)]],
            "tables": []}
