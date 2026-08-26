"""The sample output the README quotes, against what the program prints.

Three blocks in the README are transcripts rather than prose: the flow table,
the `?` listing and the exit summary. Each was copied from a run at some point
and then went quietly out of date, which is the failure this suite exists to
stop. The column widths drifted, the key listing lost the ten keys added after
it was pasted, and the summary was missing three of its sections. None of that
broke anything a reader could see from the code, and none of it failed a test.

The three are pinned differently, because they are quotable to different
degrees:

- **The `?` listing** is pinned whole. It has nothing in it that varies, so
  anything less than the exact text would be leaving room for nothing.
- **The flow table** is pinned whole apart from the clock. The header line has
  to match exactly, which is what catches a column changing width, and each
  row has to match from the end of the TIME field onward. The clock itself is
  checked for shape only, and the reason is below.
- **The exit summary** is pinned as a subset: every heading the report prints
  has to appear, and every line the README quotes has to be a line the report
  really prints. It is not pinned whole because the README abridges it,
  showing three rows under a heading that says five. That is a readable choice
  for a document and not this suite's to overrule, so what is checked is that
  nothing quoted is invented and no whole section has gone missing.

Why the clock is not pinned: the TIME column is local, `datetime.fromtimestamp`
rather than UTC, so the same fixture renders differently on this machine and on
a build runner. The sub-second part cannot be reproduced either, because
netflume rebuilds it from the header's uptime and rejects the result when it
lands more than a day from now, and the fixture's export time is a fixed date
receding into the past. Pinning either would be pinning the reader's timezone
and the calendar. The shape is checked instead, and `test_flow_display` covers
the formatting itself.

Every flow table the README quotes is checked, not the first one found. The
same table appears twice, in the opening sample and under Output format, and
the pair had drifted apart from each other before the audit that prompted this.
A claim is checked everywhere it is made.
"""
import argparse
import io
import os
import re
import sys
import time
from collections import Counter

from harness import ROOT, check, finish, plain
from netflume import SamplingWatch, SequenceWatch

import nettail as main
from nettail import cli, services
from nettail.display import HEADER_LINE, TIME_WIDTH, render

# A failing check here prints the row it disagreed about, and a row carries
# the arrows between the endpoints. Piped into a file or into run.py on
# Windows, stdout comes up as cp1252, where printing one of those raises and
# the suite dies reporting its first failure instead of listing them all. The
# collector reconfigures its own streams for this reason; a suite that quotes
# its output needs the same footing to report on it.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        pass

# The names the flow rows show for ports come from the system services
# database first, so they are the one part of a rendered row that a machine
# can disagree about. Loaded here for the same reason the collector loads it
# at startup: without it a Windows box renders 5353 and 853 as bare numbers.
services.load()

with io.open(os.path.join(ROOT, "README.md"), encoding="utf-8") as handle:
    README = handle.read()

# Every fenced block in the README, its fence and trailing newline removed.
BLOCKS = [block.strip("\n") for block in
          re.findall(r"(?ms)^```[a-z]*\n(.*?)^```", README)]
check("the README has fenced blocks to check", len(BLOCKS) > 3, str(len(BLOCKS)))

# --- the ports these fixtures lean on ---------------------------------------
# Asked before anything is rendered, so that a machine whose services database
# disagrees says so plainly here rather than turning up as a mismatched row
# further down and reading like the README has gone stale. 5353 and 853 come
# from the shipped supplemental list on a system that names neither.
for port, proto, expected in ((443, 6, "https"), (853, 6, "domain-s"),
                              (5353, 17, "mdns"), (445, 6, "microsoft-ds"),
                              (22, 6, "ssh"), (53, 17, "domain")):
    got = services.service_name(port, proto)
    check("this machine names %d/%s as %s" % (port, proto, expected),
          got == expected,
          "got %r; the README quotes %r, so the rows below will not match"
          % (got, expected))


class Names:
    """Just enough resolver to reproduce the README's annotated addresses.

    The real one asks the network. What the samples need is that a fixed set
    of addresses answer to fixed names, which is a dictionary.
    """

    KNOWN = {
        "192.168.1.42": "macbook-pro",
        "140.82.114.4": "github",
        "10.0.1.5": "nas",
        "104.244.42.1": "twitter-edge",
        "192.168.1.77": "hue",
        "192.168.1.10": "laptop",
        "192.168.1.12": "buildbox",
    }

    # What the summary's name resolution section reports. Counted rather than
    # derived, since nothing here does any looking up.
    stats = Counter({"resolved": 4, "via_dns": 1, "via_mdns": 2,
                     "via_netbios": 1, "missed": 2, "hits": 51,
                     "dropped": 0, "evicted": 0})

    def lookup(self, addr):
        return self.KNOWN.get(addr)


def flow(src, sport, dst, dport, proto, pkts, octets, flags, first, last):
    """One decoded flow, in the shape netflume hands over."""
    return {"src_addr": src, "src_port": sport, "dst_addr": dst,
            "dst_port": dport, "proto": proto, "packets": pkts,
            "octets": octets, "tcp_flags": flags,
            "first_switched": first, "last_switched": last}


BASE = 1755780003
HDR = {"exporter": "10.0.0.1", "unix_secs": BASE}

# --- the flow table ---------------------------------------------------------
SAMPLE = [
    (flow("192.168.1.42", 51234, "140.82.114.4", 443, 6, 23, 4180, 0x1b,
          95100, 100000), BASE),
    (flow("192.168.1.77", 5353, "224.0.0.251", 5353, 17, 2, 180, 0,
          95100, 100000), BASE),
    (flow("10.0.1.5", 44321, "104.244.42.1", 443, 6, 412, 58880, 0x18,
          87500, 100000), BASE - 7),
    (flow("1.1.1.1", 853, "10.0.1.5", 39012, 6, 4, 320, 0x02,
          99850, 100000), BASE + 5),
]


def rendered_rows():
    """The sample flows as the program would print them, colour stripped.

    render() writes to stdout and returns nothing, which is right for what it
    does and means capturing it here rather than asking it for a string.
    """
    args = argparse.Namespace(verbose=False, json=False, named_hosts=False,
                              show_macs=False)
    buf = io.StringIO()
    real, sys.stdout = sys.stdout, buf
    try:
        for rec, when in SAMPLE:
            hdr = dict(HDR)
            hdr["unix_secs"] = when
            render(rec, hdr, args, Names(), main.SizeScale())
    finally:
        sys.stdout = real
    return plain(buf.getvalue()).rstrip("\n").splitlines()


CLOCK = re.compile(r"^\d\d:\d\d:\d\d\.\d\d\d$")

tables = [block for block in BLOCKS
          if block.splitlines()[0].startswith("TIME ")]
check("the README quotes the flow table", len(tables) >= 1, str(len(tables)))

produced = rendered_rows()
for number, block in enumerate(tables, 1):
    where = "flow table %d of %d" % (number, len(tables))
    lines = block.splitlines()
    check("%s: the column header is the one the program prints" % where,
          lines[0] == HEADER_LINE.rstrip(), repr(lines[0]))

    quoted = lines[1:]
    check("%s: quotes as many rows as the sample has" % where,
          len(quoted) == len(produced),
          "README %d, sample %d" % (len(quoted), len(produced)))

    for index, row in enumerate(quoted):
        made = produced[index] if index < len(produced) else ""
        check("%s: row %d has a well formed clock" % (where, index + 1),
              bool(CLOCK.match(row[:TIME_WIDTH])), repr(row[:TIME_WIDTH]))
        check("%s: row %d matches after the clock" % (where, index + 1),
              row[TIME_WIDTH:].rstrip() == made[TIME_WIDTH:].rstrip(),
              "README %r\n         program %r"
              % (row[TIME_WIDTH:], made[TIME_WIDTH:]))

# --- the ? listing ----------------------------------------------------------
# Pinned whole, and the one block here that can be. test_key_help already holds
# KEYS and the listing to each other, so this closes the last link in the chain
# from the table in keys.py to the block a reader sees in the README.
buf = io.StringIO()
main.write_keys(buf)
listing = plain(buf.getvalue()).strip("\n")
check("the README quotes the key listing exactly as the ? key prints it",
      any(listing in block for block in BLOCKS),
      "%d lines, first %r" % (len(listing.splitlines()),
                              listing.splitlines()[0]))

# --- the exit summary -------------------------------------------------------
TRAFFIC = [
    ("192.168.1.10", 51000, "93.184.216.34", 443, 6, 4100, 5_400_000, 1000, 4000),
    ("192.168.1.10", 51001, "93.184.216.34", 443, 6, 40, 40_000, 1000, 4000),
    ("192.168.1.12", 51004, "140.82.121.4", 22, 6, 17000, 900_000, 1000, 3_601_000),
    ("192.168.1.11", 51002, "9.9.9.9", 53, 17, 40, 5_000, 1000, 2000),
    ("192.168.1.11", 51003, "1.1.1.1", 53, 17, 42, 4_800, 1000, 2000),
    ("192.168.1.13", 51005, "192.168.1.20", 445, 6, 400, 21_000_000, 1000, 9000),
]

tally = main.Tally()
for src, sport, dst, dport, proto, pkts, octets, first, last in TRAFFIC:
    tally.add(flow(src, sport, dst, dport, proto, pkts, octets, 0x1b,
                   first, last),
              {"exporter": "10.0.0.1", "unix_secs": BASE, "sys_uptime": 100000})

stats = Counter({"packets": 61, "bytes_rx": 41_000, "flows": len(TRAFFIC),
                 "templates_new": 3, "option_records": 2})
summary_args = argparse.Namespace(resolve="all", json=False,
                                  external_only=False, fqdn=False,
                                  header_every=40, verbose=False)
out = io.StringIO()
# An hour back, which is what the runtime line reports. Elapsed is measured
# from this against the clock at the moment of printing, so it can only ever
# be an hour and a few microseconds, and the line is cut to whole seconds.
cli.write_summary(stats, tally, Names(), SequenceWatch(), SamplingWatch(),
                  summary_args, time.time() - 3600, out=out)
report = plain(out.getvalue()).strip("\n")
report_lines = set(line.rstrip() for line in report.splitlines() if line.strip())
# A heading sits hard against the margin; everything under one is indented.
headings = [line for line in report.splitlines()
            if line.strip() and not line.startswith(" ")]

summaries = [block for block in BLOCKS
             if block.splitlines()[0].strip() == "Summary"]
check("the README quotes the exit summary", len(summaries) == 1, str(len(summaries)))

if summaries:
    block = summaries[0]
    absent = [head for head in headings if head not in block]
    check("every section of the report is quoted", not absent, repr(absent))

    invented = [line.rstrip() for line in block.splitlines()
                if line.strip() and line.rstrip() not in report_lines]
    check("every line quoted is a line the report really prints",
          not invented, repr(invented[:3]))

finish("README samples")
