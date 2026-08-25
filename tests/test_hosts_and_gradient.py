"""Host observation, the l key, the report gradient, and named report rows."""
import argparse
import io
import re
import socket
import sys
import time
from collections import Counter

from harness import check, finish, plain
from lanname import Resolver
from lanname.resolver import MAX_NAMES_PER_HOST, MAX_OBSERVED_HOSTS
from netflume import SamplingWatch, SequenceWatch

import nettail as main
from nettail import cli


def resolver_with(*pairs):
    r = Resolver(mode="off", workers=1)
    for addr, name in pairs:
        r._observe(addr, name)
    return r


# --- what the resolver remembers -------------------------------------------
r = resolver_with(("192.168.1.10", "nas"))
check("a local name is remembered", r.local_hosts() == [("192.168.1.10", ["nas"])],
      str(r.local_hosts()))
r._observe("192.168.1.10", "nas")
check("the same name again is not a second entry",
      r.local_hosts() == [("192.168.1.10", ["nas"])], str(r.local_hosts()))
r._observe("192.168.1.10", "nas2")
check("a new name joins the old one, most recent first",
      r.local_hosts() == [("192.168.1.10", ["nas2", "nas"])], str(r.local_hosts()))
r._observe("192.168.1.10", "nas")
check("a name seen again moves back to the front",
      r.local_hosts() == [("192.168.1.10", ["nas", "nas2"])], str(r.local_hosts()))
r.shutdown()

r = resolver_with(("8.8.8.8", "dns.google"), ("127.0.0.1", "localhost"),
                  ("224.0.0.251", "mdns"))
check("only local addresses are listed", r.local_hosts() == [], str(r.local_hosts()))
r.shutdown()

r = resolver_with(("10.0.0.1", "a"))
r._observe("10.0.0.1", None)
r._observe("10.0.0.2", "")
check("an empty name is not remembered",
      r.local_hosts() == [("10.0.0.1", ["a"])], str(r.local_hosts()))
r.shutdown()

r = Resolver(mode="off", workers=1)
for i in range(MAX_NAMES_PER_HOST + 3):
    r._observe("192.168.1.9", "name%d" % i)
check("names per address are bounded",
      len(r.local_hosts()[0][1]) == MAX_NAMES_PER_HOST,
      str(r.local_hosts()))
check("and the oldest are the ones dropped",
      r.local_hosts()[0][1][0] == "name%d" % (MAX_NAMES_PER_HOST + 2),
      str(r.local_hosts()))
r.shutdown()

r = Resolver(mode="off", workers=1)
for i in range(MAX_OBSERVED_HOSTS + 50):
    r._observe("10.%d.%d.%d" % (i // 65536, (i // 256) % 256, i % 256), "h%d" % i)
check("the address list is bounded",
      len(r.local_hosts()) == MAX_OBSERVED_HOSTS, str(len(r.local_hosts())))
r.shutdown()

r = resolver_with(("192.168.1.20", "b"), ("192.168.1.3", "a"), ("10.0.0.7", "c"))
check("the list is sorted by address, numerically",
      [addr for addr, _ in r.local_hosts()]
      == ["10.0.0.7", "192.168.1.3", "192.168.1.20"], str(r.local_hosts()))
r.shutdown()

# a static hosts entry counts as an observation the moment it is used
r = Resolver(mode="dns", workers=1)
r.static["192.168.1.50"] = "gateway"
check("a static name is returned", r.lookup("192.168.1.50") == "gateway")
check("and remembered", r.local_hosts() == [("192.168.1.50", ["gateway"])],
      str(r.local_hosts()))
r.shutdown()


# --- how the list reads -----------------------------------------------------
def hosts_text(resolver, colour=True):
    saved = {n: getattr(main.C, n) for n in dir(main.C) if n.isupper()}
    if not colour:
        main.C.disable()
    out = io.StringIO()
    try:
        cli.write_hosts(resolver, out=out)
    finally:
        for n, v in saved.items():
            setattr(main.C, n, v)
    return out.getvalue()


r = resolver_with(("192.168.1.20", "nas-old"), ("192.168.1.20", "nas"),
                  ("192.168.1.5", "tv"))
text = hosts_text(r)
check("the list has a heading", "Local hosts seen" in plain(text))
check("both names share one row",
      len([ln for ln in plain(text).splitlines() if "192.168.1.20" in ln]) == 1,
      repr(plain(text)))
check("the row carries both names",
      "nas" in plain(text) and "nas-old" in plain(text))
check("the current name is highlighted",
      f"{main.C.GREEN}nas{main.C.RESET}" in text, repr(text))
check("the address beside it is not the same colour",
      f"{main.C.CYAN}192.168.1.20" in text, repr(text))
check("the superseded name is dimmed", f"{main.C.DIM}nas-old{main.C.RESET}" in text,
      repr(text))
check("no star is needed when there is colour",
      "*" not in plain(text), repr(plain(text)))
check("the count is reported", "2 addresses" in plain(text))

text = hosts_text(r, colour=False)
check("without colour there are no escapes", "\033[" not in text, repr(text[:80]))
check("a superseded name gets a star instead", "nas-old*" in text, repr(text))
check("the current name gets no star", "nas  nas-old*" in text, repr(text))
check("and the star is explained", "superseded" in text)
r.shutdown()

empty = Resolver(mode="off", workers=1)
check("an empty list says so", "none yet" in plain(hosts_text(empty)))
empty.shutdown()

one = resolver_with(("192.168.1.1", "router"))
check("one address reads as singular", "1 address" in plain(hosts_text(one))
      and "1 addresses" not in plain(hosts_text(one)))
one.shutdown()


# --- the l key --------------------------------------------------------------
def controls_with(**kwargs):
    a = argparse.Namespace(json=False, external_only=False, fqdn=False,
                           resolve="off", header_every=40, verbose=False)
    r = Resolver(mode="off", workers=1)
    return main.Controls(a, main.SizeScale(), r, None, Counter(), main.Tally(),
                         SequenceWatch(), out=io.StringIO(),
                         **kwargs), r


printed = []
c, r = controls_with(hosts=lambda: printed.append("list"))
check("l prints the list", c.handle("l") is None and printed == ["list"], str(printed))
check("l says nothing on top of it", c.out.getvalue() == "", repr(c.out.getvalue()))
check("uppercase L works", c.handle("L") is None and len(printed) == 2)
check("l does not quit or pause", c.quit is False and c.paused is False)
r.shutdown()

c, r = controls_with()
check("l with nothing to print is harmless", c.handle("l") is None)
r.shutdown()
# Against the key table rather than this run's banner: the reminder line is a
# pointer at the ? listing now, and the listing is where a key is advertised.
check("the key listing describes it",
      "local addresses" in dict(main.KEYS)["l"], dict(main.KEYS).get("l"))


# --- the report gradient ----------------------------------------------------
ramp = main.SpanScale([1000, 10_000, 100_000])
check("the smallest value sits at the cold end",
      ramp.fraction(1000) == 0.0, str(ramp.fraction(1000)))
check("the largest sits at the hot end", ramp.fraction(100_000) == 1.0)
check("the middle sits in the middle", abs(ramp.fraction(10_000) - 0.5) < 0.01,
      str(ramp.fraction(10_000)))
check("a value below the range clamps cold", ramp.fraction(1) == 0.0)
check("a value above it clamps hot", ramp.fraction(10 ** 9) == 1.0)
check("one value alone sits mid-ramp", main.SpanScale([5000]).fraction(5000) == 0.5)
check("no values at all is not an error", main.SpanScale([]).fraction(10) == 0.0)
check("zero and None are cold",
      ramp.fraction(0) == 0.0 and ramp.fraction(None) == 0.0)
check("painting wraps in a ramp colour", "\033[38;5;" in ramp.paint("x", 50_000))
check("an unknown size is left alone", ramp.paint("x", None) == "x")
check("a zero is cold even beside larger figures",
      main.SpanScale([0, 1000, 100_000]).fraction(0) == 0.0)
check("a report of nothing but zeros is cold throughout",
      all(main.SpanScale([0, 0, 0]).fraction(v) == 0.0 for v in (0, 0, 0)))
check("equal positive figures share the middle",
      main.SpanScale([4096, 4096]).fraction(4096) == 0.5)


def report_text(tally, resolver, colour=True):
    saved = {n: getattr(main.C, n) for n in dir(main.C) if n.isupper()}
    if not colour:
        main.C.disable()
    out = io.StringIO()
    try:
        cli.write_summary(Counter({"packets": 3, "bytes_rx": 500,
                                   "flows": tally.flows}), tally, resolver,
                          SequenceWatch(),
                          SamplingWatch(),
                          argparse.Namespace(resolve="off", json=False),
                          time.time() - 60, out=out)
    finally:
        for n, v in saved.items():
            setattr(main.C, n, v)
    return out.getvalue()


NOW = 1700000000.0
HDR = {"exporter": "10.0.0.1"}


def flow(src, dst, octets, packets=10, proto=6, sport=None, dport=None,
         duration=None, start=0.0):
    rec = {"src_addr": src, "dst_addr": dst, "proto": proto,
           "octets": octets, "packets": packets}
    if sport:
        rec["src_port"] = sport
    if dport:
        rec["dst_port"] = dport
    if duration is not None:
        rec["flow_start_ms"] = int((NOW + start) * 1000)
        rec["flow_end_ms"] = int((NOW + start + duration) * 1000)
    return rec


tally = main.Tally()
tally.add(flow("192.168.1.10", "93.184.216.34", 5_000_000, 3000,
               sport=51000, dport=443, duration=30), HDR)
tally.add(flow("192.168.1.11", "9.9.9.9", 500, 5, proto=17,
               sport=51001, dport=53, duration=1), HDR)

r = resolver_with(("192.168.1.10", "laptop"), ("192.168.1.11", "phone"))
r.mode = "dns"
r._cache["192.168.1.10"] = ("laptop", time.monotonic() + 999)
r._cache["192.168.1.11"] = ("phone", time.monotonic() + 999)

text = report_text(tally, r)
bare = plain(text)
check("the biggest figure is at the hot end of the ramp",
      f"\033[38;5;{main.SIZE_RAMP[-1]}m" in text, repr(text[:80]))
check("the smallest is at the cold end",
      f"\033[38;5;{main.SIZE_RAMP[0]}m" in text)
packet_rows = []
section = False
for line in text.splitlines():
    if "by packets" in plain(line):
        section = True
        continue
    if section:
        if not line.strip() or plain(line).startswith(("Longest", "External")):
            break
        packet_rows.append(line)
check("packet figures are not painted with the size ramp",
      packet_rows and all("[38;5;" not in ln for ln in packet_rows),
      repr(packet_rows[:1]))

check("pair rows carry hostnames",
      "192.168.1.10 (laptop) <-> 93.184.216.34" in bare,
      repr([ln for ln in bare.splitlines() if "93.184" in ln]))
check("packet pair rows carry them too",
      len([ln for ln in bare.splitlines() if "(laptop)" in ln]) >= 2, repr(bare))
check("longest flow rows carry them",
      "192.168.1.10:51000 (laptop)" in bare,
      repr([ln for ln in bare.splitlines() if "51000" in ln]))
check("services carry their port number",
      "443/https" in bare and "53/domain" in bare,
      repr([ln for ln in bare.splitlines() if "https" in ln]))

plainer = plain(report_text(tally, r, colour=False))
check("without colour the report still names hosts",
      "(laptop)" in plainer and "443/https" in plainer)
check("and carries no escapes at all",
      "\033[" not in report_text(tally, r, colour=False))
r.shutdown()

# a very long label is trimmed rather than wrapped
long_r = resolver_with(("192.168.1.10", "a-very-long-hostname-for-one-machine"))
long_r.mode = "dns"
long_r._cache["192.168.1.10"] = ("a-very-long-hostname-for-one-machine",
                                 time.monotonic() + 999)
wide = main.Tally()
wide.add(flow("192.168.1.10", "93.184.216.34", 1000, sport=51000, dport=443,
              duration=5), HDR)
rows = [ln for ln in plain(report_text(wide, long_r)).splitlines()
        if "192.168.1.10" in ln]
check("no report row runs away with a long name",
      all(len(ln) < 100 for ln in rows), str([len(ln) for ln in rows]))
check("a trimmed label says it was trimmed",
      any("..." in ln for ln in rows), repr(rows))
long_r.shutdown()


# --- the ramp spans the rows that are printed, not every row counted --------
many = main.Tally()
# One large conversation, then more distinct services than the report shows,
# each smaller than the last. The rarest never reach the page.
many.add(flow("192.168.1.1", "8.8.8.8", 5_000_000, sport=51000, dport=443), HDR)
for i in range(20):
    many.add(flow("192.168.1.1", "8.8.8.8", 1000 - i * 40,
                  sport=51000, dport=9000 + i), HDR)

quiet = Resolver(mode="off", workers=1)
text = report_text(many, quiet)
service_lines = []
seen_heading = False
for line in text.splitlines():
    if plain(line).strip() == "Services":
        seen_heading = True
        continue
    if seen_heading:
        if not plain(line).strip() or plain(line).startswith("Busiest"):
            break
        if "bytes" in plain(line) and "flows" in plain(line):
            continue
        service_lines.append(line)

check("the services table is capped at eight rows", len(service_lines) == 8,
      str(len(service_lines)))
check("the largest service is at the hot end",
      f"\033[38;5;{main.SIZE_RAMP[-1]}m" in service_lines[0],
      repr(plain(service_lines[0])))
check("rarer services were counted even though they are not shown",
      len(many.service_bytes) > 8, str(len(many.service_bytes)))

# The rarest services counted here are 240B and up, all smaller than the 500
# byte "bytes received" figure and none of them printed. If they reached the
# ramp they would set its cold end and 500 would be painted warmer than it.
smallest_printed = [ln for ln in text.splitlines()
                    if "bytes received" in plain(ln)]
check("the coldest colour belongs to the smallest figure actually printed",
      smallest_printed
      and f"\033[38;5;{main.SIZE_RAMP[0]}m" in smallest_printed[0],
      repr(plain(smallest_printed[0]) if smallest_printed else None))
check("which is smaller than every service row shown",
      all(int(plain(ln).split()[1].rstrip("B")) > 500
          for ln in service_lines if plain(ln).split()[1].endswith("B")),
      repr([plain(ln).split()[1] for ln in service_lines]))
quiet.shutdown()

# --- the report leaves nothing in the terminal's plain text ------------------
mixed = main.Tally()
mixed.add(flow("192.168.1.10", "93.184.216.34", 5_000_000, 3000,
               sport=51000, dport=443, duration=30), HDR)
known = resolver_with(("192.168.1.10", "laptop"))
known.mode = "dns"
known._cache["192.168.1.10"] = ("laptop", time.monotonic() + 999)
text = report_text(mixed, known)

pair_row = [ln for ln in text.splitlines()
            if "93.184.216.34" in plain(ln) and "<->" in plain(ln)][0]
check("a local address and a public one are told apart by colour",
      f"{main.C.BLUE}192.168.1.10" in pair_row
      and f"{main.C.CYAN}93.184.216.34" in pair_row, repr(pair_row))
check("the arrow between them is neither of those colours",
      f"{main.C.MAGENTA} <-> " in pair_row, repr(pair_row))
check("the hostname is its own colour again",
      f"{main.C.GREEN}laptop" in pair_row, repr(pair_row))

heading_rows = [ln for ln in text.splitlines()
                if plain(ln).strip() in ("Protocols", "Services",
                                         "External traffic")]
check("headings are coloured, not just bold",
      heading_rows and all(main.C.BLUE in ln for ln in heading_rows),
      repr(heading_rows[:1]))

labelled = [ln for ln in text.splitlines() if "flows decoded" in plain(ln)][0]
check("a label and its value are different colours",
      main.C.GREY in labelled and main.C.CYAN in labelled, repr(labelled))


def uncoloured_words(line):
    """The words in a line that no escape sequence is wrapping."""
    remainder = re.sub(r"(?:\033\[[0-9;]*m)+[^\033]*\033\[0m", "", line)
    return [word for word in remainder.split() if word not in ("<->", "->")]


body = [ln for ln in text.splitlines() if plain(ln).strip()]
bare = [ln for ln in body if uncoloured_words(ln)]
check("no line of the report is left in plain terminal text", not bare,
      repr([plain(ln) for ln in bare[:3]]))

check("and none of it survives --no-color",
      "\033[" not in report_text(mixed, known, colour=False))
known.shutdown()


# --- a hosts file that cannot be read must say so ---------------------------
# lanname logs this and nothing else, on a logger this program does not
# configure, so without a word from here the run would look ordinary while
# every static mapping was quietly missing.
class _NoSocket:
    def __init__(self, *a, **kw):
        pass

    def setsockopt(self, *a):
        pass

    def bind(self, *a):
        pass

    def settimeout(self, *a):
        pass

    def close(self):
        pass

    def recvfrom(self, _n):
        raise KeyboardInterrupt


socket.socket = _NoSocket
err = io.StringIO()
real_out, real_err = sys.stdout, sys.stderr
sys.stdout, sys.stderr = io.StringIO(), err
sys.argv = ["nettail", "--resolve", "off", "--no-color",
            "--hosts", "no-such-hosts-file.txt"]
try:
    main.main()
finally:
    sys.stdout, sys.stderr = real_out, real_err

check("an unreadable hosts file is reported",
      "could not read hosts file no-such-hosts-file.txt" in plain(err.getvalue()),
      repr([ln for ln in err.getvalue().splitlines() if "hosts file" in ln]))
check("and the collector still runs", "Listening for NetFlow" in err.getvalue())

finish("host list and gradient")
