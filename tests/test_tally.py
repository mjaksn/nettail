"""The traffic tally: breakdowns, busiest pairs, longest flows, link floor."""
import argparse
import io
import random
import time
from collections import Counter

from harness import check, finish, plain
from lanname import Resolver
from netflume import SamplingWatch, SequenceWatch

import nettail as main
from nettail import cli

NOW = 1700000000.0
HDR = {"exporter": "10.0.0.1", "unix_secs": int(NOW)}


def flow(src, dst, octets=1000, packets=10, proto=6, sport=None, dport=None,
         start=0.0, duration=None):
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


def tally_of(flows):
    t = main.Tally()
    for rec in flows:
        t.add(rec, HDR)
    return t


# --- per protocol -----------------------------------------------------------
t = tally_of([
    flow("192.168.1.1", "8.8.8.8", octets=1000, packets=10, proto=6),
    flow("192.168.1.1", "8.8.8.8", octets=500, packets=5, proto=6),
    flow("192.168.1.1", "8.8.8.8", octets=200, packets=2, proto=17),
    flow("192.168.1.1", "8.8.8.8", octets=64, packets=1, proto=1),
])
check("flows are counted", t.flows == 4, str(t.flows))
check("bytes are split by protocol",
      dict(t.proto_bytes) == {"TCP": 1500, "UDP": 200, "ICMP": 64},
      str(dict(t.proto_bytes)))
check("flows are split by protocol",
      dict(t.proto_flows) == {"TCP": 2, "UDP": 1, "ICMP": 1}, str(dict(t.proto_flows)))
check("packets are split by protocol",
      dict(t.proto_packets) == {"TCP": 15, "UDP": 2, "ICMP": 1},
      str(dict(t.proto_packets)))
check("a known protocol number becomes its name",
      "GRE" in tally_of([flow("10.0.0.1", "10.0.0.2", proto=47)]).proto_bytes)
check("an unknown protocol number is kept as itself",
      "253" in tally_of([flow("10.0.0.1", "10.0.0.2", proto=253)]).proto_bytes)
check("a missing protocol is not lost",
      "?" in tally_of([{"src_addr": "10.0.0.1", "dst_addr": "10.0.0.2"}]).proto_flows)

# --- per service ------------------------------------------------------------
t = tally_of([
    flow("192.168.1.1", "8.8.8.8", octets=1000, sport=51000, dport=443),
    flow("8.8.8.8", "192.168.1.1", octets=800, sport=443, dport=51000),
    flow("192.168.1.1", "9.9.9.9", octets=90, proto=17, sport=51001, dport=53),
    flow("192.168.1.1", "192.168.1.2", octets=50, sport=51002, dport=51003),
    flow("192.168.1.1", "192.168.1.2", octets=64, proto=1),
])
check("the destination port names the service",
      t.service_bytes["443/https"] == 1800,
      str(dict(t.service_bytes)))
check("a reply is filed under the same service", t.service_flows["443/https"] == 2)
check("udp services are named too", t.service_bytes["53/domain"] == 90)
check("two ephemeral ports fall back to the lower number",
      t.service_bytes["51002/tcp"] == 50, str(dict(t.service_bytes)))
check("a named service still shows its number",
      all("/" in k or k == "icmp" for k in t.service_bytes),
      str(dict(t.service_bytes)))
check("a flow with no ports falls back to its protocol",
      t.service_bytes["icmp"] == 64, str(dict(t.service_bytes)))

# --- busiest pairs ----------------------------------------------------------
t = tally_of([
    flow("192.168.1.1", "8.8.8.8", octets=1000, packets=2),
    flow("8.8.8.8", "192.168.1.1", octets=500, packets=90),      # same pair, reversed
    flow("192.168.1.2", "1.1.1.1", octets=9000, packets=3),
])
check("direction is collapsed into one pair",
      t.pair_bytes[("192.168.1.1", "8.8.8.8")] == 1500, str(dict(t.pair_bytes)))
check("the busiest pair by volume comes first",
      t.top_pairs_by_bytes()[0] == (("1.1.1.1", "192.168.1.2"), 9000),
      str(t.top_pairs_by_bytes()))
check("packets rank separately from bytes",
      t.top_pairs_by_packets()[0] == (("192.168.1.1", "8.8.8.8"), 92),
      str(t.top_pairs_by_packets()))
check("only five pairs are reported",
      len(tally_of([flow("10.0.0.%d" % i, "8.8.8.8") for i in range(20)])
          .top_pairs_by_bytes()) == 5)
check("a flow missing an address makes no pair",
      tally_of([{"src_addr": "10.0.0.1", "proto": 6}]).pair_bytes == Counter())

# --- longest flows ----------------------------------------------------------
t = tally_of([flow("10.0.0.%d" % i, "8.8.8.8", duration=float(i), start=0)
              for i in range(1, 12)])
longest = t.longest_flows()
check("only five longest flows are kept", len(longest) == 5, str(len(longest)))
check("they come back longest first",
      [d for d, _ in longest] == [11.0, 10.0, 9.0, 8.0, 7.0], str(longest))
check("the details travel with them", longest[0][1][0] == "10.0.0.11", str(longest[0]))
check("a flow with no duration is not in the running",
      tally_of([flow("10.0.0.1", "8.8.8.8")]).longest == [])
t = tally_of([flow("10.0.0.1", "8.8.8.8", proto=1, duration=5.0),
              flow("10.0.0.2", "8.8.8.8", proto=1, duration=6.0)])
check("portless flows rank without raising",
      [d for d, _ in t.longest_flows()] == [6.0, 5.0], str(t.longest_flows()))

# --- external traffic -------------------------------------------------------
t = tally_of([
    flow("192.168.1.1", "8.8.8.8", octets=1000),          # outbound
    flow("9.9.9.9", "192.168.1.1", octets=400),           # inbound
    flow("192.168.1.1", "192.168.1.2", octets=50_000),    # internal, excluded
    flow("8.8.8.8", "1.1.1.1", octets=70),                # public both ways
])
check("only flows touching a public address count",
      t.external_bytes == 1470, str(t.external_bytes))
check("external flows are counted", t.external_flows == 3, str(t.external_flows))
check("outbound is what left", t.outbound_bytes == 1070, str(t.outbound_bytes))
check("inbound is what arrived", t.inbound_bytes == 470, str(t.inbound_bytes))
check("the internal flow is absent from both",
      t.outbound_bytes + t.inbound_bytes == 1540, "public-to-public counts twice")
check("top talkers still work", t.talkers["8.8.8.8"] == 1070, str(dict(t.talkers)))

# --- addresses split by direction -------------------------------------------
# "In" is what entered this network and "out" what left it, whichever end the
# address is on, so the two tables read the way the external section does.
check("bytes to a public address are out",
      t.talkers_out["8.8.8.8"] == 1000, str(dict(t.talkers_out)))
check("bytes from a public address are in",
      t.talkers_in["9.9.9.9"] == 400, str(dict(t.talkers_in)))
check("a public-to-public flow is in at one end and out at the other",
      t.talkers_in["8.8.8.8"] == 70 and t.talkers_out["1.1.1.1"] == 70,
      str((dict(t.talkers_in), dict(t.talkers_out))))
check("the split adds up to the total",
      all(t.talkers_in[a] + t.talkers_out[a] == n for a, n in t.talkers.items()))
check("a private address is ranked by everything it touched",
      t.internal["192.168.1.1"] == 51_400, str(dict(t.internal)))
check("what a private address sent is out",
      t.internal_out["192.168.1.1"] == 51_000, str(dict(t.internal_out)))
check("what a private address received is in",
      t.internal_in["192.168.1.1"] == 400 and t.internal_in["192.168.1.2"] == 50_000,
      str(dict(t.internal_in)))
check("a public address is not an internal one",
      not set(t.internal) & set(t.talkers), str(dict(t.internal)))
check("the external report carries the split",
      t.top_external(10)[0] == ("8.8.8.8", 1070, 70, 1000), str(t.top_external(10)))
check("and so does the internal one",
      t.top_internal(10)[0] == ("192.168.1.1", 51_400, 400, 51_000),
      str(t.top_internal(10)))
check("the internal report is bounded like the rest",
      len(t.top_internal(1)) == 1, str(t.top_internal(1)))
t = tally_of([flow("192.168.1.1", "224.0.0.251", octets=10),
              flow("192.168.1.1", "255.255.255.255", octets=20),
              flow("192.168.1.1", "8.8.8.8", octets=30)])
check("a multicast or reserved destination is not an internal address",
      set(t.internal) == {"192.168.1.1"}, str(dict(t.internal)))
check("but what was sent to it still counts as sent",
      t.internal_out["192.168.1.1"] == 60, str(dict(t.internal_out)))
# A subnet broadcast is private like any other address and no flow record says
# what prefix length the network uses, so it is ranked as a machine. The README
# says as much rather than promising an exclusion that cannot be made.
t = tally_of([flow("192.168.1.1", "192.168.1.255", octets=40)])
check("a subnet broadcast address is one, since nothing says it is not",
      dict(t.internal) == {"192.168.1.1": 40, "192.168.1.255": 40}
      and t.internal_in["192.168.1.255"] == 40, str(dict(t.internal)))


# --- protocols and services split by direction -------------------------------
# The same words as above, but a protocol has no side of its own to be read
# from, so its halves are only what crossed the edge. A flow that stayed
# inside this network crossed nothing and is in neither of them, which is what
# makes the total less the two halves the traffic that never left. The two
# rows asserted on by key below use ephemeral ports at both ends, so the key
# is the lower port and this machine's services database is not consulted.
t = tally_of([
    flow("192.168.1.10", "93.184.216.34", octets=5000, sport=51000, dport=443),
    flow("9.9.9.9", "192.168.1.10", octets=400, proto=17, sport=53, dport=51001),
    flow("192.168.1.13", "192.168.1.20", octets=70_000, sport=51005, dport=51006),
    flow("140.82.121.4", "8.8.8.8", octets=900, sport=51007, dport=51008),
])
check("bytes to a public address are out for a protocol too",
      t.proto_out["TCP"] == 5900, str(dict(t.proto_out)))
check("and bytes from one are in",
      t.proto_in["TCP"] == 900 and t.proto_in["UDP"] == 400,
      str((dict(t.proto_in), dict(t.proto_out))))
check("a flow that never left this network is in neither half",
      t.service_bytes["51005/tcp"] == 70_000
      and t.service_in["51005/tcp"] == 0 and t.service_out["51005/tcp"] == 0,
      str(dict(t.service_bytes)))
# So the total less the two halves is what stayed inside, with one correction:
# a flow public at both ends is in both halves and comes off twice. Here that
# is the 900 byte one, and the arithmetic is pinned exactly rather than
# roughly, because "roughly" is how a wrong reading of it would survive.
check("the total less both halves is the traffic that stayed inside",
      t.proto_bytes["TCP"] - t.proto_in["TCP"] - t.proto_out["TCP"]
      == 70_000 - 900,
      str((t.proto_bytes["TCP"], t.proto_in["TCP"], t.proto_out["TCP"])))
inside = tally_of([
    flow("192.168.1.10", "93.184.216.34", octets=5000, sport=51000, dport=443),
    flow("192.168.1.13", "192.168.1.20", octets=70_000, sport=51005, dport=51006),
])
check("and it comes out exact where nothing is public at both ends",
      inside.proto_bytes["TCP"] - inside.proto_in["TCP"]
      - inside.proto_out["TCP"] == 70_000,
      str((dict(inside.proto_bytes), dict(inside.proto_in),
           dict(inside.proto_out))))
check("a public-to-public flow is counted in both halves of one row",
      t.service_in["51007/tcp"] == 900 and t.service_out["51007/tcp"] == 900,
      str((dict(t.service_in), dict(t.service_out))))
check("a service is split the same way as a protocol",
      t.service_out["443/https"] == 5000 and t.service_in["53/domain"] == 400,
      str((dict(t.service_in), dict(t.service_out))))
# Every flow is filed under exactly one protocol and one service, so these are
# the same bytes counted a different way and the sums are equal rather than
# close. This is what ties the new column to the External traffic section the
# README points a reader at, and it is the check that would fail if a flow
# were ever counted into a half without the edge test agreeing.
check("what arrived is the same total however it is grouped",
      sum(t.proto_in.values()) == t.inbound_bytes
      and sum(t.service_in.values()) == t.inbound_bytes,
      "%d, %d, %d" % (sum(t.proto_in.values()), sum(t.service_in.values()),
                      t.inbound_bytes))
check("and so is what left",
      sum(t.proto_out.values()) == t.outbound_bytes
      and sum(t.service_out.values()) == t.outbound_bytes,
      "%d, %d, %d" % (sum(t.proto_out.values()), sum(t.service_out.values()),
                      t.outbound_bytes))
check("the protocol report carries the split",
      t.top_protocols(10)[0] == ("TCP", 75_900, 900, 5900), str(t.top_protocols(10)))
check("and the service report too",
      ("51007/tcp", 900, 900, 900) in t.top_services(10), str(t.top_services(10)))
check("both reports are bounded like the rest",
      len(t.top_protocols(1)) == 1 and len(t.top_services(1)) == 1)


# --- the link speed floor, against a brute force sweep ----------------------
def brute_force_peak(intervals, step=0.01):
    """Sample the timeline and take the busiest instant."""
    if not intervals:
        return 0.0
    peak = 0.0
    start = min(s for s, _e, _r in intervals)
    end = max(e for _s, e, _r in intervals)
    when = start
    while when <= end:
        total = sum(r for s, e, r in intervals if s <= when < e)
        peak = max(peak, total)
        when += step
    return peak


rng = random.Random(20260821)
for trial in range(12):
    specs, intervals = [], []
    for _ in range(rng.randint(1, 8)):
        start = rng.uniform(0, 20)
        duration = rng.uniform(0.5, 10)
        octets = rng.choice([1000, 50_000, 1_000_000])
        specs.append(flow("192.168.1.1", "8.8.8.8", octets=octets,
                          start=start, duration=duration))
        intervals.append((NOW + start, NOW + start + duration,
                          octets * 8.0 / duration))
    got = tally_of(specs).busiest_moment()
    want = brute_force_peak(intervals)
    check("trial %d matches a brute force sweep" % trial,
          abs(got - want) < max(want * 0.02, 1.0),
          "got %.1f want %.1f" % (got, want))

# --- the shape of the estimate ----------------------------------------------
overlapping = tally_of([
    flow("192.168.1.1", "8.8.8.8", octets=1_250_000, start=0, duration=10),
    flow("192.168.1.2", "8.8.8.8", octets=1_250_000, start=0, duration=10),
])
check("concurrent flows add up",
      abs(overlapping.busiest_moment() - 2_000_000) < 1,
      str(overlapping.busiest_moment()))

sequential = tally_of([
    flow("192.168.1.1", "8.8.8.8", octets=1_250_000, start=0, duration=10),
    flow("192.168.1.2", "8.8.8.8", octets=1_250_000, start=10, duration=10),
])
check("flows one after another do not",
      abs(sequential.busiest_moment() - 1_000_000) < 1,
      str(sequential.busiest_moment()))
check("an ending is counted before a start at the same instant",
      sequential.busiest_moment() < overlapping.busiest_moment())

check("a flow with no duration is in neither answer",
      tally_of([flow("192.168.1.1", "8.8.8.8", octets=10 ** 9)]).busiest_moment() == 0.0
      and tally_of([flow("192.168.1.1", "8.8.8.8", octets=10 ** 9)]
                   ).min_link_speed() == 0.0)
check("nor is an internal flow",
      tally_of([flow("192.168.1.1", "192.168.1.2", octets=10 ** 9, duration=1)]
               ).busiest_moment() == 0.0)
check("timed flows are counted",
      tally_of([flow("192.168.1.1", "8.8.8.8", duration=1),
                flow("192.168.1.1", "8.8.8.8")]).timed_flows == 1)

# --- the floor claims only what it can prove --------------------------------
# One flow, 1000 bytes across ten seconds: the link reached 800 bps at some
# point inside that window, whatever it did with the rest of the time.
t = tally_of([flow("192.168.1.1", "8.8.8.8", octets=1000, start=0, duration=10)])
check("a single flow's own average is the floor",
      abs(t.min_link_speed() - 800) < 1, str(t.min_link_speed()))

# Whole flows inside one second add up, because all of those bytes crossed
# during that second.
# Each on its own averages 8889 bps; together in one second they are 16000,
# and the floor takes whichever measure proves more.
t = tally_of([
    flow("192.168.1.1", "8.8.8.8", octets=1000, start=0.0, duration=0.9),
    flow("192.168.1.2", "8.8.8.8", octets=1000, start=0.05, duration=0.9),
])
check("flows contained in one second are added",
      abs(t.min_link_speed() - 16000) < 1, str(t.min_link_speed()))
t = tally_of([
    flow("192.168.1.1", "8.8.8.8", octets=1000, start=0.1, duration=0.2),
    flow("192.168.1.2", "8.8.8.8", octets=2000, start=1.4, duration=0.3),
])
check("flows in different seconds are not",
      abs(t.min_link_speed() - 2000 * 8 / 0.3) < 1, str(t.min_link_speed()))

# The case that made the old claim false: two flows whose windows overlap can
# both be satisfied without ever sending at the same time.
t = tally_of([
    flow("192.168.1.1", "8.8.8.8", octets=1000, start=0, duration=10),
    flow("192.168.1.2", "8.8.8.8", octets=1000, start=9, duration=10),
])
schedulable_peak = 1000 * 8 / 9      # each sent in a nine second half, no overlap
check("the floor is not above what a real schedule could achieve",
      t.min_link_speed() <= schedulable_peak,
      "%.0f > %.0f" % (t.min_link_speed(), schedulable_peak))
check("the estimate still shows the overlap",
      t.busiest_moment() > schedulable_peak, str(t.busiest_moment()))
check("the floor is never above the estimate here",
      t.min_link_speed() <= t.busiest_moment())

# --- rare conversations are dropped rather than kept forever ----------------
t = main.Tally()
for i in range(main.MAX_TRACKED_KEYS + 500):
    t.add(flow("10.%d.%d.%d" % (i // 65536, (i // 256) % 256, i % 256),
               "8.8.8.8", octets=1), HDR)
check("the pair table is bounded", len(t.pair_bytes) <= main.MAX_TRACKED_KEYS,
      str(len(t.pair_bytes)))
check("its companion is pruned in step", set(t.pair_bytes) == set(t.pair_packets),
      "%d vs %d" % (len(t.pair_bytes), len(t.pair_packets)))
check("what was dropped is counted", t.pruned > 0, str(t.pruned))
check("the talkers table is bounded too",
      len(t.talkers) <= main.MAX_TRACKED_KEYS, str(len(t.talkers)))

# A direction half ranks no table of its own, so it must not decide what a
# prune keeps. Every address below is seen in one direction only, which is
# where letting the halves decide reclaims a single key per pass and leaves
# the table pinned at the cap, paying for a full pass on every new address.
t = main.Tally()
for i in range(main.MAX_TRACKED_KEYS + 500):
    addr = "8.%d.%d.%d" % (i // 65536, (i // 256) % 256, i % 256)
    if i % 2:
        t.add(flow(addr, "192.168.1.1", octets=1 + i, packets=1 + i), HDR)
    else:
        t.add(flow("192.168.1.1", addr, octets=1 + i, packets=1 + i), HDR)
check("a prune still gives back half the table when the halves disagree",
      len(t.talkers) <= main.MAX_TRACKED_KEYS // 2 + 500, str(len(t.talkers)))
check("and the halves lose exactly what the total lost",
      set(t.talkers) == set(t.talkers_in) | set(t.talkers_out),
      "%d vs %d and %d" % (len(t.talkers), len(t.talkers_in), len(t.talkers_out)))

# The busiest survive a prune, which is the whole point.
t = main.Tally()
t.add(flow("192.168.1.1", "8.8.8.8", octets=10 ** 9, packets=10 ** 6), HDR)
for i in range(main.MAX_TRACKED_KEYS + 500):
    t.add(flow("10.%d.%d.%d" % (i // 65536, (i // 256) % 256, i % 256),
               "9.9.9.9", octets=1), HDR)
check("the biggest pair is still there after pruning",
      t.top_pairs_by_bytes()[0][1] == 10 ** 9, str(t.top_pairs_by_bytes()[:1]))
check("and the biggest by packets too",
      t.top_pairs_by_packets()[0][1] == 10 ** 6, str(t.top_pairs_by_packets()[:1]))

# --- the event cap ----------------------------------------------------------
t = main.Tally()
for i in range(main.MAX_SPEED_EVENTS // 2 + 40):
    t.add(flow("192.168.1.1", "8.8.8.8", octets=1000, start=i, duration=1), HDR)
check("the event list is capped",
      len(t._events) <= main.MAX_SPEED_EVENTS, str(len(t._events)))
check("what could not be kept is counted", t.events_dropped == 40,
      str(t.events_dropped))
check("the estimate survives truncation", t.busiest_moment() > 0)
check("accepted flows are counted apart from dropped ones",
      t.rated_flows == main.MAX_SPEED_EVENTS // 2 and t.events_dropped == 40,
      "%d rated, %d dropped" % (t.rated_flows, t.events_dropped))

# --- reset ------------------------------------------------------------------
t = tally_of([flow("192.168.1.1", "8.8.8.8", duration=1)])
t.clear()
check("clear() empties everything",
      t.flows == 0 and not t.proto_bytes and not t.pair_bytes and not t.talkers
      and not t.talkers_in and not t.talkers_out and not t.internal
      and not t.internal_in and not t.internal_out
      and t.longest == [] and t.external_bytes == 0 and t._events == []
      and t.peak_flow_bits == 0.0 and not t.second_bits,
      str(t.__dict__))
check("and it keeps working afterwards",
      tally_of([flow("192.168.1.1", "8.8.8.8")]).flows == 1)


# --- the report itself ------------------------------------------------------
def report(tally, colour=True, resolve="off"):
    saved = {n: getattr(main.C, n) for n in dir(main.C) if n.isupper()}
    if not colour:
        main.C.disable()
    out = io.StringIO()
    resolver = Resolver(mode="off", workers=1)
    try:
        cli.write_summary(Counter({"packets": 3, "flows": tally.flows}), tally,
                          resolver, SequenceWatch(),
                          SamplingWatch(),
                          argparse.Namespace(resolve=resolve, json=False),
                          time.time() - 60, out=out)
    finally:
        resolver.shutdown()
        for n, v in saved.items():
            setattr(main.C, n, v)
    return out.getvalue()


busy = tally_of([
    flow("192.168.1.1", "8.8.8.8", octets=1_250_000, packets=900,
         sport=51000, dport=443, start=0, duration=10),
    flow("192.168.1.2", "9.9.9.9", octets=4000, packets=40, proto=17,
         sport=51001, dport=53, start=1, duration=2),
    flow("192.168.1.3", "192.168.1.4", octets=64, packets=1, proto=1,
         start=2, duration=30),
])
text = report(busy)
for section in ("Protocols", "Services", "Busiest 5 pairs by volume",
                "Busiest 5 pairs by packets", "Longest 5 flows",
                "External traffic"):
    check("the report has a %s section" % section, section in text,
          repr(text[:120]))
check("the report names a service",
      "443/https" in plain(text) and "53/domain" in plain(text),
      repr([ln for ln in plain(text).splitlines() if "https" in ln]))
check("the report shows the link floor", "minimum link speed" in text,
      repr([ln for ln in text.splitlines() if "link" in ln]))
check("and labels the estimate as an assumption",
      "concurrent demand" in text and "if every flow sent evenly" in text,
      repr([ln for ln in text.splitlines() if "concurrent" in ln]))
check("the report splits inbound from outbound",
      "inbound" in text and "outbound" in text)
check("the report is coloured", "\033[" in text)

plain = report(busy, colour=False)
check("--no-color leaves no escapes anywhere in the report", "\033[" not in plain,
      repr([ln for ln in plain.splitlines() if "\033[" in ln][:2]))
check("and says the same things", "Protocols" in plain and "https" in plain)

quiet = report(main.Tally())
check("an empty run reports no breakdowns",
      "Protocols" not in quiet and "Busiest" not in quiet, repr(quiet))
check("an empty run still reports the basics", "Summary" in quiet)

untimed = tally_of([flow("192.168.1.1", "8.8.8.8", octets=1000)])
text = report(untimed)
check("without timings the floor says so rather than zero",
      "not enough timing data" in text,
      repr([ln for ln in text.splitlines() if "link" in ln]))

finish("tally and report")
