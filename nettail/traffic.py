"""What each address did, and what each pair of them did together.

The exit summary wants five busiest pairs and nothing more, and for most of
this program's life a pair of counters was all that needed keeping. The flow
details dialog asks a different question: given one address, what has it been
doing, with whom, over what protocols and services, and since when. Nothing
else here can answer that, so this is where the answer accumulates.

Fed from inside `Tally.add`, so there goes on being one place a flow is
counted, and emptied by `Tally.reset`, so the c key covers it.

**Direction here is relative to the endpoint, and that is the one trap in the
module.** Everywhere else in this program "in" means what entered this network
and "out" what left it, because the tables it feeds are about the edge. An
endpoint has a side of its own, so here *in* means the endpoint was the flow's
destination and *out* means it was the source. Read from a machine on this
network the two readings agree; read from a public address they are opposites,
and a report that mixed them would say a server had downloaded what it served.
A pair has no side at all, so its two halves are named after its own two
addresses rather than after a direction.

Bounded the way `Tally._prune` is bounded and for the reason its docstring
gives: what survives a prune is the union of the top half by bytes and the top
half by packets, so a chatty low-volume pair, which is what DNS and ICMP look
like, is not quietly dropped out of the list that ranks by packets. The same
rule applies again inside an endpoint, to its own service table: a host being
port scanned files one service key per port it was asked about, and an
otherwise unbounded map inside a bounded one is still unbounded.
"""

import heapq
import time

MAX_TRACKED_KEYS = 50000     # pairs or endpoints held at once
MAX_ENDPOINT_KEYS = 2000     # services or protocols held for one endpoint


class Leg:
    """Flows, bytes and packets going one way, or added up over both."""

    __slots__ = ("flows", "bytes", "packets")

    def __init__(self):
        self.flows = 0
        self.bytes = 0
        self.packets = 0

    def add(self, octets, packets):
        self.flows += 1
        self.bytes += octets
        self.packets += packets


class Counts:
    """A total with the endpoint's own two halves beside it.

    `inward` is what the endpoint received and `outward` what it sent, which
    is the reading written on the module and not the one the summary's tables
    use.
    """

    __slots__ = ("total", "inward", "outward")

    def __init__(self):
        self.total = Leg()
        self.inward = Leg()
        self.outward = Leg()

    def add(self, octets, packets, inbound):
        self.total.add(octets, packets)
        half = self.inward if inbound else self.outward
        half.add(octets, packets)


class Endpoint:
    """One address, and everything seen of it.

    `first` and `last` are when this collector received a datagram carrying a
    flow with this address on it, rather than anything the flow says about
    itself. "First seen" therefore means first seen here, which is the only
    thing a collector started an hour ago can honestly claim.
    """

    __slots__ = ("addr", "first", "last", "counts", "protos", "services",
                 "peers", "dropped")

    def __init__(self, addr, when):
        self.addr = addr
        self.first = when
        self.last = when
        self.counts = Counts()
        self.protos = {}
        self.services = {}
        # The other end of every pair this address is in, which is the index
        # into the pair table. Bounded by that table rather than on its own: a
        # pair a prune drops is taken out of both of its endpoints' sets.
        self.peers = set()
        # What its own tables lost to a prune, so that a report can say the
        # figures are the busiest rather than all of them.
        self.dropped = 0

    def add(self, octets, packets, inbound, proto, service, when):
        self.last = when
        self.counts.add(octets, packets, inbound)
        _bump(self.protos, proto, octets, packets, inbound)
        _bump(self.services, service, octets, packets, inbound)
        if len(self.services) > MAX_ENDPOINT_KEYS:
            self.dropped += _prune(self.services, MAX_ENDPOINT_KEYS,
                                   _counts_bytes, _counts_packets)
        if len(self.protos) > MAX_ENDPOINT_KEYS:
            self.dropped += _prune(self.protos, MAX_ENDPOINT_KEYS,
                                   _counts_bytes, _counts_packets)


class Pair:
    """Two addresses and what passed between them, whoever started it.

    The halves are `a_to_b` and `b_to_a`, named after the pair's own two
    addresses rather than after a direction, because a conversation has no
    side to be read from. `a` is whichever address sorts first, which is what
    makes one key out of a flow and its reply.
    """

    __slots__ = ("a", "b", "first", "last", "total", "a_to_b", "b_to_a",
                 "protos", "services", "dropped")

    def __init__(self, a, b, when):
        self.a = a
        self.b = b
        self.first = when
        self.last = when
        self.total = Leg()
        self.a_to_b = Leg()
        self.b_to_a = Leg()
        self.protos = {}
        self.services = {}
        self.dropped = 0

    def add(self, src, octets, packets, proto, service, when):
        self.last = when
        self.total.add(octets, packets)
        half = self.a_to_b if src == self.a else self.b_to_a
        half.add(octets, packets)
        # Direction independent, which is what was asked for: a conversation's
        # protocols and services are the same list whichever end named them.
        _leg(self.protos, proto).add(octets, packets)
        _leg(self.services, service).add(octets, packets)
        if len(self.services) > MAX_ENDPOINT_KEYS:
            self.dropped += _prune(self.services, MAX_ENDPOINT_KEYS,
                                   _leg_bytes, _leg_packets)


def _leg(table, key):
    leg = table.get(key)
    if leg is None:
        leg = table[key] = Leg()
    return leg


def _bump(table, key, octets, packets, inbound):
    counts = table.get(key)
    if counts is None:
        counts = table[key] = Counts()
    counts.add(octets, packets, inbound)


def _leg_bytes(leg):
    return leg.bytes


def _leg_packets(leg):
    return leg.packets


def _counts_bytes(counts):
    return counts.total.bytes


def _counts_packets(counts):
    return counts.total.packets


def _prune(table, cap, by_bytes, by_packets):
    """Drop the small fry from a table that would otherwise grow forever.

    The same policy as `Tally._prune`: what survives is the union of the top
    half by bytes and the top half by packets, because a key can earn its
    place either way and ranking on one of them alone would quietly empty the
    list that ranks on the other. Returns how many keys went, so that a caller
    can add it to the count the summary already reports.
    """
    half = cap // 2
    keep = set(heapq.nlargest(half, table, key=lambda k: by_bytes(table[k])))
    keep.update(heapq.nlargest(half, table, key=lambda k: by_packets(table[k])))
    dropped = [key for key in table if key not in keep]
    for key in dropped:
        del table[key]
    return len(dropped)


class Traffic:
    """Per address and per pair statistics, for the flow details dialog.

    Held by `Tally` and fed from inside its `add`, so a flow is folded in
    exactly once and the c key clears this with everything else.
    """

    def __init__(self, cap=MAX_TRACKED_KEYS):
        self.cap = cap
        self.endpoints = {}
        self.pairs = {}
        # Every byte and packet handed to this, counted once per flow rather
        # than once per end. It is what an endpoint's share of the traffic is
        # a share of, and there is nothing else in the program that holds it:
        # the protocol and service totals count a flow once each, but they are
        # pruned, so summing one of them would give a share of a shrinking
        # denominator.
        self.total = Leg()

    def clear(self):
        self.endpoints = {}
        self.pairs = {}
        self.total = Leg()

    def add(self, src, dst, octets, packets, proto, service, when=None):
        """Fold one flow in. Returns how many keys a prune dropped.

        The count goes back to `Tally.pruned` rather than being kept here, so
        that the one line the summary prints about pruning covers every table
        this program bounds rather than some of them.

        `when` is when the datagram carrying this flow arrived, which the
        receive loop stamps on the header beside `recvfrom`. It falls back to
        now for a caller that has no such stamp, which is every suite that
        builds a header by hand and is also what a replayed record deserves:
        the alternative is a first-seen time of zero, dated 1970.
        """
        if when is None:
            when = time.time()
        self.total.add(octets, packets)

        # An endpoint is recorded for whichever ends are there, and a pair
        # only for a flow that has both. A record missing one address still
        # says what the other one did, and dropping it from the endpoint
        # tables as well would lose that for no gain.
        if src:
            self._endpoint(src, when).add(octets, packets, False, proto,
                                          service, when)
        if dst:
            self._endpoint(dst, when).add(octets, packets, True, proto,
                                          service, when)

        dropped = 0
        if src and dst:
            a, b = (src, dst) if src <= dst else (dst, src)
            pair = self.pairs.get((a, b))
            if pair is None:
                pair = self.pairs[(a, b)] = Pair(a, b, when)
            pair.add(src, octets, packets, proto, service, when)
            self.endpoints[src].peers.add(dst)
            self.endpoints[dst].peers.add(src)
            if len(self.pairs) > self.cap:
                dropped += self._prune_pairs()
        if len(self.endpoints) > self.cap:
            dropped += self._prune_endpoints()
        return dropped

    def _endpoint(self, addr, when):
        end = self.endpoints.get(addr)
        if end is None:
            end = self.endpoints[addr] = Endpoint(addr, when)
        return end

    def _prune_pairs(self):
        """Drop the quietest pairs, and unhook them from their two endpoints.

        The unhooking is the part that is easy to leave out. A peer set is an
        index into the pair table, so a name left in one after its pair has
        gone is a peer row a report would go looking for and not find.
        """
        keep = set(heapq.nlargest(self.cap // 2, self.pairs,
                                 key=lambda k: self.pairs[k].total.bytes))
        keep.update(heapq.nlargest(self.cap // 2, self.pairs,
                                   key=lambda k: self.pairs[k].total.packets))
        dropped = [key for key in self.pairs if key not in keep]
        for key in dropped:
            del self.pairs[key]
            a, b = key
            if a in self.endpoints:
                self.endpoints[a].peers.discard(b)
            if b in self.endpoints:
                self.endpoints[b].peers.discard(a)
        return len(dropped)

    def _prune_endpoints(self):
        """Drop the quietest addresses.

        Pairs naming a dropped address are left where they are. They are
        ranked on their own traffic and may well be among the busiest, and an
        address that has gone from this table is one no report will be asked
        about anyway: `endpoint_report` answers for it by saying it is no
        longer held, which is the truth and is what a pruned run owes a
        reader.
        """
        return _prune(self.endpoints, self.cap,
                      lambda end: end.counts.total.bytes,
                      lambda end: end.counts.total.packets)

    # -- reporting ----------------------------------------------------------

    def pair_of(self, a, b):
        """The pair holding these two addresses, whichever way round they came."""
        if not a or not b:
            return None
        key = (a, b) if a <= b else (b, a)
        return self.pairs.get(key)

    def top_pairs(self, n, by_packets=False):
        """The busiest pairs, as the (pair, value) the summary expects.

        Two rankings out of one table, which is the whole reason the pair
        counters were folded in here: they were two Counters holding the same
        keys, and every table this program keeps about a pair now hangs off
        one object instead.
        """
        if by_packets:
            def value(key):
                return self.pairs[key].total.packets
        else:
            def value(key):
                return self.pairs[key].total.bytes
        best = heapq.nlargest(n, self.pairs, key=value)
        return [(key, value(key)) for key in best]
