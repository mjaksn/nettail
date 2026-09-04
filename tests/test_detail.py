"""The report behind the flow details dialog.

Two of these hold this module to netflume rather than to itself: every TCP
flag the decoder knows has a word here, and every information element it can
name has a label. Both fail loudly when the decoder gains one, which is what
`test_services` does for the ephemeral floor and for the same reason: an
upstream addition that arrives as a bare key is exactly the sort of thing
nobody notices until they go looking for it.

Every value in the report is painted, so the checks about what it *says* read
it through `plain`, which is what `cli.detail_for_web` does for a browser that
refused colour. What it is *painted* is held separately at the foot of the
file, against the report as it really goes out.
"""
import time

from harness import check, finish
from lanname import Resolver
from netflume import IE, TCP_FLAG_BITS

import nettail as main
from nettail import detail
from nettail.colour import C, strip_payload
from nettail.detail import (
    DETAIL_ROWS,
    FIELD_LABELS,
    FLOW_KEYS_SHOWN,
    TCP_FLAG_NAMES,
)
from nettail.display import address_colour, proto_colour, way

NOW = 1700000000.0
HDR = {"exporter": "10.0.0.1", "version": 9, "domain": 3, "sequence": 4242,
       "unix_secs": int(NOW), "sys_uptime": 123456, "received": NOW + 0.5,
       "sampling_rate": 10, "gap": 7, "datagram_bytes": 240, "flow_count": 2}

# Everything a v9 exporter can reasonably put in one record, so that the check
# that nothing is skipped has something to be thorough about.
WIDE = {
    "src_addr": "192.168.1.10", "dst_addr": "8.8.8.8",
    "src_port": 51000, "dst_port": 443, "proto": 6,
    "octets": 1500, "packets": 12, "tcp_flags": 0x12,
    "first_switched": 90000, "last_switched": 100000,
    "in_if": 1, "out_if": 2, "tos": 0, "src_mask": 24, "dst_mask": 24,
    "src_as": 0, "dst_as": 15169, "next_hop": "192.168.1.1",
    "flow_end_reason": 3, "src_mac": "aa:bb:cc:dd:ee:ff",
    "min_ttl": 63, "max_ttl": 64, "vlan": 7, "flow_id": 99,
    "ie999": 5, "e9.42": "an enterprise field",
}

resolver = Resolver(mode="off")


class Named:
    """A resolver that answers, for the two checks that need one to.

    `Resolver(mode="off")` never answers, which is what every other check
    here wants: what a name is, and how it was found, is lanname's business.
    What is this module's business is where a name goes when there is one and
    what colour it is written in.
    """

    def lookup(self, addr):
        return "dns.google" if str(addr) == "8.8.8.8" else None


def plain(built):
    """A section or a whole report with the colour taken back out."""
    return strip_payload(built)


def escapes(built):
    """Every string in a payload run together, escapes and all.

    `repr` of the payload will not do: it spells an escape \\x1b, so
    a check for one in it is a check that passes whatever the payload holds.
    """
    if isinstance(built, str):
        return built
    if isinstance(built, dict):
        return "".join(escapes(item) for item in built.values())
    if isinstance(built, (list, tuple)):
        return "".join(escapes(item) for item in built)
    return ""


def flags(value):
    """The flags spelled out, as a reader without colour sees them."""
    return strip_payload(detail.spell_flags(value))


def facts_of(section):
    return {label: value for label, value in section["facts"]}


def tally_of(flows, hdr=HDR):
    t = main.Tally()
    for rec in flows:
        t.add(rec, hdr)
    return t


# -- the two tables held to the decoder ------------------------------------

letters = {letter for _bit, letter in TCP_FLAG_BITS}
check("every flag the decoder knows has a word",
      letters <= set(TCP_FLAG_NAMES),
      str(sorted(letters - set(TCP_FLAG_NAMES))))
check("and nothing here names a flag it does not",
      set(TCP_FLAG_NAMES) <= letters,
      str(sorted(set(TCP_FLAG_NAMES) - letters)))

names = {name for name, _kind in IE.values()}
check("every element the decoder can name has a label",
      names <= set(FIELD_LABELS), str(sorted(names - set(FIELD_LABELS))))
check("and nothing here labels one it cannot",
      set(FIELD_LABELS) <= names, str(sorted(set(FIELD_LABELS) - names)))

# -- flags, spelled out ----------------------------------------------------

# In the decoder's bit order, which is the wire's, so the words run in the
# same order as the letters in the FLAGS column of the row above the dialog.
check("the flags are named, not drawn",
      flags(0x12) == "ACK, SYN (0x12)", flags(0x12))
check("in the order the decoder lists the bits",
      flags(0xFF) == "CWR, ECE, URG, ACK, PSH, RST, SYN, FIN (0xff)",
      flags(0xFF))
check("no flags at all still says the byte",
      flags(0) == "none (0x00)", flags(0))
check("and a flow whose exporter sent none says nothing about them",
      flags(None) == "-")
for bit, letter in TCP_FLAG_BITS:
    check("bit 0x%02x is spelled %s" % (bit, TCP_FLAG_NAMES[letter]),
          flags(bit) == "%s (0x%02x)" % (TCP_FLAG_NAMES[letter], bit))

# -- the flow section ------------------------------------------------------

section = plain(detail.flow_section(WIDE, HDR, resolver))
labels = [label for label, _value in section["facts"]]
values = [value for _label, value in section["facts"]]

check("the flow section is titled", section["title"] == "Flow")
missing = [key for key in WIDE
           if key not in FLOW_KEYS_SHOWN
           and FIELD_LABELS.get(key, key) not in labels]
check("every field of a wide record reaches the dialog", missing == [],
      str(missing))
check("including one the decoder had no name for", "ie999" in labels)
check("and an enterprise field", "e9.42" in labels)
check("a labelled field carries the units that make it readable",
      "First switched (ms of exporter uptime)" in labels)

facts = facts_of(section)
check("both ends are named", facts["Source"] == "192.168.1.10"
      and facts["Destination"] == "8.8.8.8", str(facts.get("Source")))
check("the destination port carries its service name",
      facts["Destination port"] == "443/https", facts["Destination port"])
check("each end says which side of the boundary it is on",
      facts["Source kind"] == "private" and facts["Destination kind"] == "public")
check("the direction is in words rather than an arrow",
      facts["Direction"] == "leaving for the internet", facts["Direction"])
check("the protocol is named and numbered", facts["Protocol"] == "TCP (6)")
check("the flags are spelled out", facts["TCP flags"] == "ACK, SYN (0x12)")
check("and the end reason is named rather than numbered",
      facts["Flow end reason"] == "eof", facts["Flow end reason"])
check("the size is exact as well as short",
      facts["Bytes"] == "1,500 bytes (1.5K)", facts["Bytes"])
check("the start is an absolute time", facts["Started"].startswith("20"),
      facts["Started"])
check("and none of it is a bare arrow or a raw flag byte",
      "↑" not in " ".join(values) and "0x12" in facts["TCP flags"])

# The other direction, so that the arrow is not merely being echoed.
inbound = plain(detail.flow_section(
    dict(WIDE, src_addr="8.8.8.8", dst_addr="192.168.1.10"), HDR, resolver))
check("a flow the other way says so",
      facts_of(inbound)["Direction"] == "arriving from the internet")
local = plain(detail.flow_section(
    dict(WIDE, dst_addr="192.168.1.20"), HDR, resolver))
check("and one that never left the network says that",
      facts_of(local)["Direction"] == "between two addresses on this network")

# -- the datagram section --------------------------------------------------

facts = facts_of(plain(detail.datagram_section(HDR)))
check("the exporter is named", facts["Exporter"] == "10.0.0.1")
check("the version is spelled out", facts["Version"] == "NetFlow v9")
check("the sequence number survives the receive loop",
      facts["Sequence"] == "4242", facts["Sequence"])
check("and the observation domain", facts["Observation domain"] == "3")
check("the export time is a time", facts["Export time"].startswith("20"))
check("so is the arrival time here", facts["Received here"].startswith("20"))
check("the datagram's own size is there",
      facts["Datagram size"] == "240 bytes (240B)", facts["Datagram size"])
check("and how many flows it carried",
      facts["Flows in the datagram"] == "2")
check("the sampling rate in force is reported",
      "1 in 10" in facts["Sampling in force"], facts["Sampling in force"])
check("and the export gap on this datagram",
      facts["Exports missed before this one"] == "7")
check("the exporter's uptime is a duration and the raw milliseconds",
      "123,456 ms" in facts["Exporter uptime"], facts["Exporter uptime"])

# The export time is whole seconds, so the sub-second part of an arrival is
# the resolution of one clock rather than a difference between two. A row for
# it on every datagram would be noise, and `abs` would call an exporter
# running fast one that is running slow.
check("a half second between the two clocks is not worth a row",
      "Exporter's clock" not in facts, str(facts.get("Exporter's clock")))
late = facts_of(plain(detail.datagram_section(dict(HDR,
                                                  received=NOW + 90))))
check("a real gap is reported",
      late["Exporter's clock"] == "1m30s behind this machine's",
      late["Exporter's clock"])
early = facts_of(plain(detail.datagram_section(dict(HDR,
                                                   received=NOW - 90))))
check("and an exporter running fast is said to be ahead, not behind",
      early["Exporter's clock"] == "1m30s ahead of this machine's",
      early["Exporter's clock"])

# v5 puts an engine id where v9 puts an observation domain, and netflume
# decodes both into the same key, so the label says which it is.
v5 = facts_of(plain(detail.datagram_section(dict(HDR, version=5))))
check("v5 calls the same field an engine id", "Engine ID" in v5)
check("and is named as v5", v5["Version"] == "NetFlow v5")
ipfix = facts_of(plain(detail.datagram_section(
    {k: v for k, v in HDR.items() if k != "sys_uptime"})))
check("IPFIX, which has no exporter uptime, says nothing about one",
      "Exporter uptime" not in ipfix)
check("an unsampled datagram says so",
      facts_of(plain(detail.datagram_section(dict(HDR, sampling_rate=1))))
      ["Sampling in force"] == "unsampled")

# -- the whole payload -----------------------------------------------------

t = tally_of([WIDE, WIDE,
              dict(WIDE, src_addr="8.8.8.8", dst_addr="192.168.1.10",
                   octets=9000, packets=20),
              dict(WIDE, dst_addr="1.1.1.1", proto=17, dst_port=53,
                   octets=90, packets=1)])
ring = {7: (WIDE, HDR)}
payload = plain(detail.report((5, 7, ("192.168.1.10", "8.8.8.8")), ring, t,
                              resolver, now=NOW + 60))

check("the ask's own id comes back untouched", payload["ask"] == 5)
check("along with the serial that was asked about", payload["n"] == 7)
check("a flow still in the ring is held", payload["held"] is True)
check("the dialog is given a title of its own",
      payload["title"] == "192.168.1.10 to 8.8.8.8", payload["title"])
check("the flow and the datagram are both there",
      [s["title"] for s in payload["sections"]] == ["Flow", "Datagram"],
      str([s["title"] for s in payload["sections"]]))
check("there are two endpoint panels", len(payload["ends"]) == 2)
check("the source panel first",
      payload["ends"][0]["title"] == "Source: 192.168.1.10",
      payload["ends"][0]["title"])
check("and a pair panel under them",
      payload["pair"]["title"] == "Between the two")

src_panel = payload["ends"][0]
facts = facts_of(src_panel)
check("an endpoint says when it was first seen and how long ago",
      "ago" in facts["First seen"], facts["First seen"])
check("its flows are split into the two halves",
      "received" in facts["Flows"] and "sent" in facts["Flows"], facts["Flows"])
check("it counts the addresses it talked to",
      facts["Distinct peers"] == "2", facts["Distinct peers"])
check("and says what share of everything seen it accounts for",
      facts["Share of all bytes seen"].endswith("%"),
      facts["Share of all bytes seen"])
check("and its mean flow size", "Mean flow size" in facts)
check("an endpoint gets three tables",
      [table["title"] for table in src_panel["tables"]]
      == ["By protocol", "By service", "Peers"],
      str([table["title"] for table in src_panel["tables"]]))
peers = [table for table in src_panel["tables"] if table["title"] == "Peers"][0]
check("every table names its own columns",
      peers["head"][0] == ["Peer", "<"], str(peers["head"][:1]))
check("with an alignment for each of them",
      all(col[1] in ("<", ">") for col in peers["head"]))
check("a peer row has a cell for every column",
      all(len(row) == len(peers["head"]) for row in peers["rows"]))
check("and every cell is finished text, never a number",
      all(isinstance(cell, str) for row in peers["rows"] for cell in row))
check("both peers are listed", len(peers["rows"]) == 2, str(peers["rows"]))
check("busiest first", peers["rows"][0][0] == "8.8.8.8", str(peers["rows"][0]))
check("nothing was left out of a short table", peers["more"] == 0)

pair = payload["pair"]
facts = facts_of(pair)
check("the pair names both of its ends",
      "192.168.1.10" in facts["Between"] and "8.8.8.8" in facts["Between"],
      facts["Between"])
check("and reports totals rather than halves",
      facts["Bytes"] == "12,000 bytes (11.7K)", facts["Bytes"])
check("with two tables under it",
      [table["title"] for table in pair["tables"]]
      == ["By protocol", "By service"])
check("which are direction independent, so they have three figure columns",
      [col[0] for col in pair["tables"][0]["head"]]
      == ["Protocol", "Flows", "Bytes", "Packets"],
      str([col[0] for col in pair["tables"][0]["head"]]))

# -- a table longer than the dialog shows ----------------------------------

busy = main.Tally()
for i in range(DETAIL_ROWS + 15):
    busy.add({"src_addr": "192.168.1.10", "dst_addr": "10.5.%d.%d"
              % (i // 256, i % 256), "proto": 6, "dst_port": 443,
              "octets": 10 * (i + 1), "packets": 1}, HDR)
panel = plain(detail.report((1, None, ("192.168.1.10", None)), {}, busy,
                            resolver, now=NOW))["ends"][0]
peers = [table for table in panel["tables"] if table["title"] == "Peers"][0]
check("a long table is cut to the dialog's row count",
      len(peers["rows"]) == DETAIL_ROWS, str(len(peers["rows"])))
check("and says how many did not fit", peers["more"] == 15, str(peers["more"]))
check("keeping the busiest of them",
      peers["rows"][0][0].startswith("10.5.0.%d" % (DETAIL_ROWS + 14)),
      str(peers["rows"][0]))

# -- a flow the ring no longer holds ---------------------------------------

gone = plain(detail.report((9, 4000, ("192.168.1.10", "8.8.8.8")), ring, t,
                           resolver, now=NOW + 60))
check("a serial the ring has lost is not held", gone["held"] is False)
check("and the answer says so in words rather than an empty panel",
      "not holding this flow" in facts_of(gone["sections"][0])["This flow"],
      str(gone["sections"]))
check("but both endpoint panels are still filled",
      all(len(panel["facts"]) > 3 for panel in gone["ends"]),
      str([len(panel["facts"]) for panel in gone["ends"]]))
check("and so is the pair", len(gone["pair"]["tables"]) == 2)
check("which is the point of the ends riding on the ask",
      gone["ends"][1]["title"] == "Destination: 8.8.8.8")

# A serial the ring does hold, but for a different flow. Serials start again
# at 1 every run, and `--web-token` exists so that a bookmark survives a
# restart, so a reconnecting tab has a page full of the previous run's serials
# on its rows. Answering one of those from the new run's ring would show a
# reader a completely different flow under a title naming the one they clicked.
stale = plain(detail.report((9, 7, ("10.1.2.3", "10.4.5.6")), ring, t,
                            resolver, now=NOW))
check("a serial whose flow has different ends is not held",
      stale["held"] is False, str(stale["held"]))
check("and the report is about the addresses the row actually carried",
      stale["title"] == "10.1.2.3 to 10.4.5.6", stale["title"])
check("rather than about whatever the ring had under that number",
      all("192.168.1.10" not in str(panel) for panel in stale["ends"]),
      str([panel["title"] for panel in stale["ends"]]))
check("while the matching serial is still held",
      detail.report((9, 7, ("192.168.1.10", "8.8.8.8")), ring, t, resolver,
                    now=NOW)["held"] is True)

nameless = plain(detail.report((9, None, ("192.168.1.10", "8.8.8.8")), ring, t,
                               resolver, now=NOW))
check("a row with no serial at all says that instead",
      "carries no serial" in facts_of(nameless["sections"][0])["This flow"],
      str(nameless["sections"][0]))

# -- an address the tally never saw, or has dropped ------------------------

unknown = plain(detail.report((1, None, ("203.0.113.9", None)), {}, t,
                              resolver, now=NOW))
facts = facts_of(unknown["ends"][0])
check("an address the collector has no figures for says so",
      "not in the collector's tables" in facts["Seen"], facts["Seen"])
check("rather than reporting zeros as though they were facts",
      unknown["ends"][0]["tables"] == [])
check("an end the exporter never sent is a fact of its own",
      "no address" in facts_of(unknown["ends"][1])["Address"],
      str(unknown["ends"][1]["facts"]))
check("and a flow with one end has no pair to report on",
      "no pair to report" in facts_of(unknown["pair"])["Both ends"],
      str(unknown["pair"]["facts"]))

alone = plain(detail.report((1, None, ("203.0.113.9", "203.0.113.10")), {}, t,
                            resolver, now=NOW))
check("a pair the collector has no figures for says so too",
      "not in the collector's tables" in facts_of(alone["pair"])["Seen"],
      str(alone["pair"]["facts"]))

# -- the clock -------------------------------------------------------------
#
# `report` takes its own when it is not given one, so the receive loop calls it
# with three arguments and nothing has to thread a time through the queue.

live = plain(detail.report((1, None, ("192.168.1.10", "8.8.8.8")), {}, t,
                           resolver))
check("a report with no clock given still dates itself",
      "ago" in facts_of(live["ends"][0])["First seen"],
      facts_of(live["ends"][0])["First seen"])
check("against now rather than against nothing",
      abs(time.time() - NOW) > 0)

# -- what it is painted ----------------------------------------------------
#
# The dialog is the browser's alone, and the page renders what it is handed
# rather than deciding anything: a value that reaches it unpainted arrives in
# the reader's ordinary ink and nothing at either end fails. So the colour is
# worth pinning, and what is pinned is that it is the vocabulary the flow rows
# and the traffic summary already use rather than a second one invented here.
# Held against the report as it really goes out, not through `plain`.

lit = facts_of(detail.flow_section(WIDE, HDR, Named()))

check("an address is painted the colour its kind is drawn in everywhere else",
      lit["Source"] == C.BLUE + "192.168.1.10" + C.RESET, repr(lit["Source"]))
check("which is the summary's own mapping and not a second one",
      lit["Destination"]
      == address_colour("8.8.8.8") + "8.8.8.8" + C.RESET,
      repr(lit["Destination"]))
check("and the word for its kind takes the same colour as the address",
      lit["Destination kind"] == address_colour("8.8.8.8") + "public" + C.RESET,
      repr(lit["Destination kind"]))
check("a protocol takes the colour the PROTO column gives it",
      lit["Protocol"].startswith(proto_colour("TCP") + "TCP"),
      repr(lit["Protocol"]))
check("with its number as the grey aside behind it",
      lit["Protocol"].endswith(C.GREY + " (6)" + C.RESET), repr(lit["Protocol"]))
check("a direction takes the colour the arrow it puts words to was given",
      lit["Direction"]
      == way("192.168.1.10", "8.8.8.8")[1] + "leaving for the internet"
      + C.RESET, repr(lit["Direction"]))
check("a service is split at its slash as the summary's table splits one",
      lit["Destination port"]
      == C.CYAN + "443" + C.RESET + C.GREY + "/" + C.RESET
      + C.GREEN + "https" + C.RESET, repr(lit["Destination port"]))
check("a hostname is green, as it is in the summary",
      lit["Destination name"] == C.GREEN + "dns.google" + C.RESET,
      repr(lit["Destination name"]))
check("a figure is cyan and what restates it is grey",
      lit["Bytes"] == C.CYAN + "1,500" + C.RESET
      + C.GREY + " bytes (1.5K)" + C.RESET, repr(lit["Bytes"]))

# Prose is not a figure and a raw field is nobody's to interpret, so neither
# is painted. Grey arrives in the page as the ink the label column is drawn
# in, and a whole value greyed stops looking like a value at all.
check("a raw record field is left as itself", lit["ie999"] == "5",
      repr(lit["ie999"]))
missed = detail.report((9, 4000, ("192.168.1.10", "8.8.8.8")), ring, t,
                       resolver, now=NOW + 60)
check("and a sentence saying there is nothing to report carries no colour",
      "\033" not in facts_of(missed["sections"][0])["This flow"],
      repr(facts_of(missed["sections"][0])["This flow"]))

# The tables carry it too, since a peer list is where an address is hardest to
# pick out, and the halves are grey for the reason `_halves` greys them in a
# facts row: they are the total beside them broken up.
lit_panel = detail.report((1, 7, ("192.168.1.10", "8.8.8.8")), ring, t,
                          Named(), now=NOW)["ends"][0]
lit_peers = [table for table in lit_panel["tables"]
             if table["title"] == "Peers"][0]
check("a peer is its address in its own colour, and its name in green",
      lit_peers["rows"][0][0]
      == C.CYAN + "8.8.8.8" + C.RESET + C.GREY + " (" + C.RESET
      + C.GREEN + "dns.google" + C.RESET + C.GREY + ")" + C.RESET,
      repr(lit_peers["rows"][0][0]))
check("a table's totals are cyan and its two halves grey",
      lit_peers["rows"][0][2].startswith(C.CYAN)
      and lit_peers["rows"][0][3].startswith(C.GREY),
      repr(lit_peers["rows"][0][2:5]))
lit_protos = [table for table in lit_panel["tables"]
              if table["title"] == "By protocol"][0]
check("a protocol in a table is painted as it is in a row",
      lit_protos["rows"][0][0].startswith(proto_colour("TCP")),
      repr(lit_protos["rows"][0][0]))

# And the other half of the deal: everything above reads the text through
# `plain`, which is only worth anything if it really takes all of it out.
full = detail.report((1, 7, ("192.168.1.10", "8.8.8.8")), ring, t, Named(),
                    now=NOW)
check("stripping a whole report leaves no escape anywhere in it",
      "\033" not in escapes(plain(full)))
check("while the report as it goes out has plenty",
      escapes(full).count("\033") > 50,
      str(escapes(full).count("\033")))
check("and stripping leaves a serial a number rather than a string",
      plain(detail.report((1, 7, ("192.168.1.10", "8.8.8.8")), ring, t,
                          Named(), now=NOW))["n"] == 7)

resolver.shutdown()
finish("detail")
