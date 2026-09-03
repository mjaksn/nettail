"""Per address and per pair accounting: the tables the details dialog reads.

The one thing worth reading this suite for is the direction. Everywhere else
in the program "in" means what entered this network; here it means what the
endpoint received, so the same flow is outbound on its source's panel and
inbound on its destination's. Half of these checks exist to hold that.
"""
from harness import check, finish

import nettail as main
from nettail.traffic import MAX_ENDPOINT_KEYS, MAX_TRACKED_KEYS, Traffic

NOW = 1700000000.0
HDR = {"exporter": "10.0.0.1", "unix_secs": int(NOW), "received": NOW}


def flow(src, dst, octets=1000, packets=10, proto=6, sport=None, dport=None):
    rec = {"src_addr": src, "dst_addr": dst, "proto": proto,
           "octets": octets, "packets": packets}
    if sport:
        rec["src_port"] = sport
    if dport:
        rec["dst_port"] = dport
    return rec


def tally_of(flows, hdr=None):
    t = main.Tally()
    for rec in flows:
        t.add(rec, hdr if hdr is not None else HDR)
    return t


# -- direction is read from the endpoint, not from the network edge ---------

t = tally_of([
    flow("192.168.1.10", "8.8.8.8", octets=1000, packets=10),
    flow("8.8.8.8", "192.168.1.10", octets=9000, packets=20),
])
local = t.traffic.endpoints["192.168.1.10"]
remote = t.traffic.endpoints["8.8.8.8"]

check("both ends of a flow are recorded", len(t.traffic.endpoints) == 2,
      str(sorted(t.traffic.endpoints)))
check("an endpoint counts every flow it was on either end of",
      local.counts.total.flows == 2, str(local.counts.total.flows))
check("what it was the source of is what it sent",
      local.counts.outward.bytes == 1000, str(local.counts.outward.bytes))
check("and what it was the destination of is what it received",
      local.counts.inward.bytes == 9000, str(local.counts.inward.bytes))
check("the other end reads the same two flows the opposite way",
      remote.counts.outward.bytes == 9000
      and remote.counts.inward.bytes == 1000,
      "%d out, %d in" % (remote.counts.outward.bytes,
                         remote.counts.inward.bytes))
check("the halves add up to the total",
      local.counts.inward.bytes + local.counts.outward.bytes
      == local.counts.total.bytes == 10000)
check("and so do the packet halves",
      local.counts.inward.packets + local.counts.outward.packets
      == local.counts.total.packets == 30)

# The external table in the summary is asked a different question about the
# same bytes and answers the other way round, which is not a disagreement and
# is exactly why this module writes its definition down.
check("the summary's table still reads from the network edge",
      t.talkers_in["8.8.8.8"] == 9000 and t.talkers_out["8.8.8.8"] == 1000,
      "%r" % (dict(t.talkers_in),))

# -- protocols and services, per endpoint ----------------------------------

t = tally_of([
    flow("192.168.1.10", "8.8.8.8", octets=1000, packets=10, dport=443),
    flow("192.168.1.10", "9.9.9.9", octets=90, packets=1, proto=17, dport=53),
    flow("9.9.9.9", "192.168.1.10", octets=200, packets=2, proto=17, sport=53),
])
local = t.traffic.endpoints["192.168.1.10"]
check("an endpoint splits its traffic by protocol",
      sorted(local.protos) == ["TCP", "UDP"], str(sorted(local.protos)))
check("with the same two halves on each row",
      local.protos["UDP"].outward.bytes == 90
      and local.protos["UDP"].inward.bytes == 200,
      "%d out, %d in" % (local.protos["UDP"].outward.bytes,
                         local.protos["UDP"].inward.bytes))
check("and by service, under the key the summary files it under",
      local.services["53/domain"].total.bytes == 290,
      str({k: v.total.bytes for k, v in local.services.items()}))
check("a service row counts its flows too",
      local.services["53/domain"].total.flows == 2)

# -- peers ------------------------------------------------------------------

check("an endpoint knows who it talked to",
      local.peers == {"8.8.8.8", "9.9.9.9"}, str(sorted(local.peers)))
check("and each of them knows about it",
      t.traffic.endpoints["9.9.9.9"].peers == {"192.168.1.10"})
check("a peer is the index into the pair table",
      all(t.traffic.pair_of("192.168.1.10", peer) is not None
          for peer in local.peers))

# -- the pair ---------------------------------------------------------------

t = tally_of([
    flow("192.168.1.1", "8.8.8.8", octets=1000, packets=2),
    flow("8.8.8.8", "192.168.1.1", octets=500, packets=90),
])
pair = t.traffic.pair_of("8.8.8.8", "192.168.1.1")
check("a pair is found whichever way round it is asked for",
      pair is t.traffic.pair_of("192.168.1.1", "8.8.8.8"))
check("its key is the two addresses sorted",
      (pair.a, pair.b) == ("192.168.1.1", "8.8.8.8"),
      "%r" % ((pair.a, pair.b),))
check("the total collapses the direction",
      pair.total.bytes == 1500 and pair.total.flows == 2,
      "%d bytes over %d flows" % (pair.total.bytes, pair.total.flows))
check("the halves are named after its own two addresses",
      pair.a_to_b.bytes == 1000 and pair.b_to_a.bytes == 500,
      "%d and %d" % (pair.a_to_b.bytes, pair.b_to_a.bytes))
check("and the total is their sum",
      pair.a_to_b.bytes + pair.b_to_a.bytes == pair.total.bytes)
check("packets too",
      pair.a_to_b.packets + pair.b_to_a.packets == pair.total.packets == 92)
check("its protocol table is direction independent",
      pair.protos["TCP"].bytes == 1500, str(pair.protos["TCP"].bytes))
check("and so is its service table",
      sum(leg.bytes for leg in pair.services.values()) == 1500)

check("a flow missing an address makes no pair",
      tally_of([{"src_addr": "10.0.0.1", "proto": 6}]).traffic.pairs == {})
check("but the address it does carry is still recorded",
      "10.0.0.1" in tally_of([{"src_addr": "10.0.0.1", "octets": 5,
                               "proto": 6}]).traffic.endpoints)

# -- first and last seen come off the datagram's arrival --------------------
#
# Not off the flow's own timestamps, which are the exporter's clock and can be
# hours out. "First seen" means first seen by this collector, which is the only
# thing a collector started ten minutes ago can honestly claim.

t = main.Tally()
t.add(flow("192.168.1.1", "8.8.8.8"), dict(HDR, received=NOW))
t.add(flow("192.168.1.1", "8.8.8.8"), dict(HDR, received=NOW + 30))
end = t.traffic.endpoints["192.168.1.1"]
check("first seen is the first arrival", end.first == NOW, str(end.first))
check("last seen is the most recent one", end.last == NOW + 30, str(end.last))
check("and the pair keeps the same two",
      t.traffic.pair_of("192.168.1.1", "8.8.8.8").first == NOW
      and t.traffic.pair_of("192.168.1.1", "8.8.8.8").last == NOW + 30)

# A header built by hand, which is what half the suite hands `Tally.add`, has
# no arrival stamp on it at all. That has to be a time rather than a zero,
# since a report would otherwise date every endpoint to 1970.
t = tally_of([flow("192.168.1.1", "8.8.8.8")],
             hdr={"exporter": "10.0.0.1", "unix_secs": int(NOW)})
check("a header with no arrival stamp still gets a real time",
      t.traffic.endpoints["192.168.1.1"].first > 1600000000,
      str(t.traffic.endpoints["192.168.1.1"].first))

# -- the totals everything else is a share of -------------------------------

t = tally_of([
    flow("192.168.1.1", "8.8.8.8", octets=1000, packets=10),
    flow("192.168.1.2", "8.8.8.8", octets=3000, packets=30),
])
check("the grand total counts a flow once, not once per end",
      t.traffic.total.bytes == 4000 and t.traffic.total.flows == 2,
      "%d bytes over %d flows" % (t.traffic.total.bytes,
                                  t.traffic.total.flows))

# -- the busiest pairs still answer in the summary's shape ------------------

t = tally_of([
    flow("192.168.1.1", "8.8.8.8", octets=1000, packets=2),
    flow("8.8.8.8", "192.168.1.1", octets=500, packets=90),
    flow("192.168.1.2", "1.1.1.1", octets=9000, packets=3),
])
check("the busiest pair by volume comes first",
      t.top_pairs_by_bytes()[0] == (("1.1.1.1", "192.168.1.2"), 9000),
      str(t.top_pairs_by_bytes()))
check("packets rank separately from bytes",
      t.top_pairs_by_packets()[0] == (("192.168.1.1", "8.8.8.8"), 92),
      str(t.top_pairs_by_packets()))
check("and both come back as (pair, value) pairs",
      all(len(row) == 2 and isinstance(row[0], tuple)
          for row in t.top_pairs_by_bytes() + t.top_pairs_by_packets()))
check("only `top` of them are reported",
      len(tally_of([flow("10.0.0.%d" % i, "8.8.8.8") for i in range(20)])
          .top_pairs_by_bytes()) == main.TOP_N)

# -- the caps ---------------------------------------------------------------
#
# The same policy as `Tally._prune` and for the reason its docstring gives:
# what survives is the union of the top half by bytes and the top half by
# packets, so a pair that is busy by packets and small by bytes, which is what
# DNS and ICMP look like, is still there for the list that ranks by packets.

small = Traffic(cap=200)
# One conversation that is large by bytes and one that is large by packets,
# both a long way past the noise below them.
small.add("10.9.9.9", "8.8.8.8", 10 ** 9, 1, "TCP", "443/https", NOW)
small.add("10.9.9.8", "8.8.8.8", 1, 10 ** 6, "UDP", "53/domain", NOW)
for i in range(600):
    small.add("10.0.%d.%d" % (i // 256, i % 256), "9.9.9.9", 1, 1, "TCP",
              "80/http", NOW)
check("the pair table is bounded", len(small.pairs) <= 200, str(len(small.pairs)))
check("the endpoint table is bounded too", len(small.endpoints) <= 200,
      str(len(small.endpoints)))
check("the pair that was large by bytes survived",
      small.pair_of("10.9.9.9", "8.8.8.8") is not None)
check("and so did the one that was large by packets",
      small.pair_of("10.9.9.8", "8.8.8.8") is not None)

# A pair a prune drops has to come out of both of its endpoints' peer sets. A
# peer set is an index into the pair table, so a name left in one is a peer
# row a report goes looking for and does not find.
dangling = [(addr, peer) for addr, end in small.endpoints.items()
            for peer in end.peers if small.pair_of(addr, peer) is None]
check("no peer set names a pair that has gone", dangling == [],
      str(dangling[:4]))

# What went is counted, through the same total the summary already prints a
# line about rather than through one of its own.
t = main.Tally()
t.traffic = Traffic(cap=200)
for i in range(600):
    t.add(flow("10.0.%d.%d" % (i // 256, i % 256), "8.8.8.8", octets=1), HDR)
check("what a prune dropped reaches the summary's count", t.pruned > 0,
      str(t.pruned))

# An endpoint's own service table is a map inside a bounded map, and a host
# being port scanned files one key per port it was asked about. Bounded on the
# same terms, and what it lost is on the endpoint so a report can say so.
scanned = Traffic()
for port in range(MAX_ENDPOINT_KEYS + 500):
    scanned.add("10.0.0.1", "10.0.0.2", 1, 1, "TCP", "%d/tcp" % port, NOW)
end = scanned.endpoints["10.0.0.2"]
check("an endpoint's service table is bounded",
      len(end.services) <= MAX_ENDPOINT_KEYS, str(len(end.services)))
check("and says how much of it went", end.dropped > 0, str(end.dropped))
check("while the endpoint's own totals are untouched by that",
      end.counts.total.flows == MAX_ENDPOINT_KEYS + 500,
      str(end.counts.total.flows))

check("the cap is the one the tally reports under",
      MAX_TRACKED_KEYS == main.MAX_TRACKED_KEYS)

# -- clearing ---------------------------------------------------------------

t = tally_of([flow("192.168.1.1", "8.8.8.8")])
t.clear()
check("the c key empties the endpoints",
      t.traffic.endpoints == {} and t.traffic.pairs == {},
      str((len(t.traffic.endpoints), len(t.traffic.pairs))))
check("and the grand total with them", t.traffic.total.bytes == 0)
check("and it goes on counting afterwards",
      tally_of([flow("192.168.1.1", "8.8.8.8")]).traffic.total.flows == 1)

standalone = Traffic()
standalone.add("10.0.0.1", "10.0.0.2", 5, 1, "TCP", "80/http", NOW)
standalone.clear()
check("clearing one directly empties it as well",
      standalone.endpoints == {} and standalone.pairs == {}
      and standalone.total.flows == 0)

finish("traffic")
