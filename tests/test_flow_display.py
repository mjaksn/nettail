"""The direction arrow, names in place of addresses, and the mac line.

All three are decisions render() makes about one flow, so they are checked by
rendering one and reading the result rather than through the receive loop.
"""
import argparse
import io
import sys

from harness import check, finish, plain
from netflume import tcp_flags_str

import nettail as main
from nettail.display import (
    ENDPOINT_INDENT,
    ENDPOINT_WIDTH,
    FLAGS_WIDTH,
    HEADER_LINE,
    flow_macs,
    render,
    way,
)

HDR = {"exporter": "10.0.0.1", "unix_secs": 1755780000}


class Names:
    """A resolver that knows some of the addresses and none of the rest."""

    KNOWN = {"192.168.1.42": "macbook-pro", "140.82.114.4": "github"}

    def lookup(self, addr):
        return self.KNOWN.get(addr)


def flow(**over):
    rec = {"src_addr": "192.168.1.42", "dst_addr": "140.82.114.4",
           "src_port": 51234, "dst_port": 443, "proto": 6, "packets": 23,
           "octets": 4180, "tcp_flags": 0x1b}
    rec.update(over)
    return rec


def rows(rec, resolver=None, **flags):
    """The lines one flow prints, with the colour taken back off."""
    args = argparse.Namespace(verbose=False, json=False, named_hosts=False,
                              show_macs=False)
    for key, value in flags.items():
        setattr(args, key, value)
    buffer = io.StringIO()
    real, sys.stdout = sys.stdout, buffer
    try:
        render(rec, HDR, args, resolver, main.SizeScale())
    finally:
        sys.stdout = real
    return [plain(line) for line in buffer.getvalue().splitlines()]


# --- which way the flow went ------------------------------------------------
check("out to the internet points up", way("192.168.1.42", "140.82.114.4")[0] == "↑")
check("in from the internet points down", way("140.82.114.4", "10.0.1.5")[0] == "↓")
check("across the network gets the opposed pair",
      way("10.0.1.5", "192.168.1.42")[0] == "⇄")
check("multicast counts as being on this side",
      way("192.168.1.77", "224.0.0.251")[0] == "⇄")
check("so does link-local", way("169.254.1.1", "192.168.1.42")[0] == "⇄")
check("a local mark is not the same as no mark at all",
      way("10.0.1.5", "192.168.1.42")[0] != way("8.8.8.8", "1.1.1.1")[0])
check("two public addresses get no arrow rather than a wrong one",
      way("8.8.8.8", "1.1.1.1")[0] == " ")
check("nor does a flow with an end missing", way(None, "8.8.8.8")[0] == " "
      and way("8.8.8.8", None)[0] == " ")
check("an unreadable address gets no arrow", way("not-an-address", "8.8.8.8")[0] == " ")
check("the two internet arrows are coloured alike",
      way("192.168.1.42", "8.8.8.8")[1] == way("8.8.8.8", "192.168.1.42")[1])
check("and a local conversation is quieter than either",
      way("10.0.1.5", "192.168.1.42")[1] != way("10.0.1.5", "8.8.8.8")[1])

# --- the arrow reaches the rendered row -------------------------------------
line = rows(flow())[0]
check("the arrow is on the row", "↑" in line, repr(line))
check("the header leaves it a column with no name",
      "SOURCE" in HEADER_LINE and "DESTINATION" in HEADER_LINE
      and HEADER_LINE.index("DESTINATION") - HEADER_LINE.index("SOURCE")
      == ENDPOINT_WIDTH + 3, HEADER_LINE)

# --- header, flow row and mac row agree where the columns start -------------
macs = rows(flow(src_mac="a4:83:e7:1c:9d:02", dst_mac="24:5a:4c:88:10:ff"),
            show_macs=True)
check("the mac line is printed under the flow", len(macs) == 2, repr(macs))
flow_row, mac_row = macs
for name, text, first, second in (
        ("the header", HEADER_LINE, "SOURCE", "DESTINATION"),
        ("the flow row", flow_row, "192.168.1.42", "140.82.114.4"),
        ("the mac row", mac_row, "a4:83", "24:5a")):
    check("%s starts source where the others do" % name,
          text.index(first) == ENDPOINT_INDENT,
          "%d, wanted %d" % (text.index(first), ENDPOINT_INDENT))
    check("%s starts destination where the others do" % name,
          text.index(second) == ENDPOINT_INDENT + ENDPOINT_WIDTH + 3,
          "%d, wanted %d" % (text.index(second),
                             ENDPOINT_INDENT + ENDPOINT_WIDTH + 3))

# --- p: the mac line only where there is a mac to show ----------------------
check("no mac line until the key is pressed",
      len(rows(flow(src_mac="a4:83:e7:1c:9d:02"))) == 1)
check("no mac line for an exporter that sends none",
      len(rows(flow(), show_macs=True)) == 1, repr(rows(flow(), show_macs=True)))
one_sided = rows(flow(src_mac="a4:83:e7:1c:9d:02"), show_macs=True)
check("one mac is enough to draw the line", len(one_sided) == 2)
check("and the end without one is marked rather than left blank",
      one_sided[1].rstrip().endswith("-"), repr(one_sided[1]))
check("the post-nat elements are used when the plain ones are absent",
      flow_macs({"post_src_mac": "aa:bb", "post_dst_mac": "cc:dd"})
      == ("aa:bb", "cc:dd"))
check("the plain elements win when both arrived",
      flow_macs({"src_mac": "11:22", "post_src_mac": "aa:bb"})[0] == "11:22")

# --- n: a name in place of an address ---------------------------------------
plain_row = rows(flow(), Names())[0]
check("the address leads and the name follows it",
      "192.168.1.42:51234 (macbook-pro)" in plain_row, repr(plain_row))

named_row = rows(flow(), Names(), named_hosts=True)[0]
check("the name stands in for the address", "macbook-pro:51234" in named_row,
      repr(named_row))
check("and is not repeated in brackets after itself",
      "(macbook-pro)" not in named_row, repr(named_row))
check("the service name is still carried", "github:443/https" in named_row,
      repr(named_row))

unknown = rows(flow(src_addr="10.9.9.9"), Names(), named_hosts=True)[0]
check("an address that answered to nothing is still an address",
      "10.9.9.9:51234" in unknown, repr(unknown))

check("with no resolver at all the row is unchanged",
      rows(flow(), None, named_hosts=True)[0] == rows(flow(), None)[0])

# --- the columns hold their width whatever is in them -----------------------
widths = {len(rows(rec, Names(), named_hosts=named)[0].rstrip())
          for named in (False, True)
          for rec in (flow(), flow(src_addr="10.9.9.9"),
                      flow(dst_addr="224.0.0.251"))}
check("every row is laid out on the same grid", len(widths) <= 2, str(widths))

# --- the flags width is netflume's, and this is what holds it there --------
#
# COLUMNS gives FLAGS no width, because on a terminal it is last and nothing
# is padded against it. The browser's table has to size every column it
# draws, so FLAGS_WIDTH stands in, and it is taken from the string netflume
# actually produces rather than counted off a screen. A release that added a
# flag or dropped one would otherwise leave the column a character out with
# nothing failing to say so: the cells would still be right and the heading
# above them would sit over the wrong place.
#
# Every flag set and none of them, because the string is meant to be the
# same width either way. That is what makes it sortable, and it is the whole
# reason a single width is the right thing to send.
check("the flags width is what netflume writes with nothing set",
      FLAGS_WIDTH == len(tcp_flags_str(0)), str(FLAGS_WIDTH))
check("and with every flag set, the string being fixed width",
      FLAGS_WIDTH == len(tcp_flags_str(0xFF)),
      repr(tcp_flags_str(0xFF)))
check("and it is a width, not a zero standing in for one", FLAGS_WIDTH > 0,
      str(FLAGS_WIDTH))

# An exporter that sent no flags at all gets an empty cell rather than a row
# of dots, which is a different thing from every flag being clear, so the
# width above is what the column is sized to and not what every cell holds.
check("an exporter that sent no flags at all says nothing",
      tcp_flags_str(None) == "", repr(tcp_flags_str(None)))

finish("flow display")
