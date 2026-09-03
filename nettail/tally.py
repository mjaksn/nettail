"""Everything the traffic summary reports, accumulated one flow at a time.

Fed every decoded flow, shown or hidden, so the report describes what the
exporter sent rather than what happened to fit on screen.
"""

import heapq
from collections import Counter

from netflume import (
    PROTO_NAMES,
    addr_kind,
    flow_duration,
    flow_endpoints,
    flow_timestamp,
)

from .services import service_name
from .traffic import MAX_TRACKED_KEYS, Traffic

TOP_N = 5                    # rows in the busiest-pairs and longest-flows tables
MAX_SPEED_EVENTS = 100000    # rate changes kept for the concurrency estimate

__all__ = ["MAX_SPEED_EVENTS", "MAX_TRACKED_KEYS", "TOP_N", "Tally"]


class Tally:
    """Running totals behind the traffic summary."""

    def __init__(self, top=TOP_N):
        self.top = top
        self.reset()

    def reset(self):
        """Forget everything. What the c key does."""
        self.flows = 0

        # "In" is what entered this network and "out" what left it, which is
        # the reading the external traffic section uses, and every direction
        # counter below splits its total that way. A flow from a public
        # address arrived, a flow to one departed, and a flow between two
        # public addresses did both and is counted in each half.
        #
        # A flow that never left this network crossed nothing, so it adds to
        # neither half of a protocol or service row: there the two halves are
        # what touched the edge, and the total less them is what stayed
        # inside, bar a flow public at both ends, which is in both halves and
        # so comes off twice. An address is the one thing with a side of its
        # own to be read from, which is why its counters below say more.
        self.proto_flows = Counter()
        self.proto_bytes = Counter()
        self.proto_packets = Counter()
        self.proto_in = Counter()
        self.proto_out = Counter()

        self.service_bytes = Counter()
        self.service_flows = Counter()
        self.service_in = Counter()
        self.service_out = Counter()

        # Per address and per pair, which is what the flow details dialog
        # reports and where the busiest-pairs lists now read from. It used to
        # be two Counters here, keyed alike and pruned together; folding them
        # in leaves one object holding everything known about a conversation
        # rather than a pair of tables beside a growing set of others.
        self.traffic = Traffic()

        # Bytes by address, one trio for each side of the network edge, with
        # the direction counters beside the total they split. The same words
        # as above, read from whichever end the address is on: bytes from a
        # public address arrived and bytes to one departed, while a private
        # address received what was sent to it and sent what it was the
        # source of. So an internal conversation, which is in neither half of
        # a protocol row, is in both halves here, once on each of the two rows
        # it has. That is not the tables disagreeing: a protocol is asked what
        # crossed the edge, an address what it sent and received.
        self.talkers = Counter()
        self.talkers_in = Counter()
        self.talkers_out = Counter()
        self.internal = Counter()
        self.internal_in = Counter()
        self.internal_out = Counter()

        self.longest = []            # a heap of the `top` longest lived flows
        self._tick = 0               # breaks ties without comparing addresses

        self.external_bytes = 0
        self.external_flows = 0
        self.inbound_bytes = 0
        self.outbound_bytes = 0

        self.timed_flows = 0
        self.rated_flows = 0          # external timed flows in the estimate
        self._events = []
        self.events_dropped = 0
        self.pruned = 0

        # A floor needs no assumptions: a flow's own average over its own
        # lifetime, and what whole flows delivered inside a single second.
        self.peak_flow_bits = 0.0
        self.second_bits = Counter()

    def clear(self):
        """Alias for reset(), so the key that clears counters can call clear()
        on this the same way it does on the Counters it replaced."""
        self.reset()

    # -- collecting ---------------------------------------------------------

    def add(self, rec, hdr):
        """Fold one decoded flow in."""
        octets = rec.get("octets", rec.get("octets_total")) or 0
        packets = rec.get("packets", rec.get("packets_total")) or 0
        proto = rec.get("proto")
        proto_name = PROTO_NAMES.get(proto, str(proto) if proto is not None else "?")
        src, dst = flow_endpoints(rec)

        # Which side of the edge each end is on, worked out before anything is
        # counted because the protocol and service rows are split by it too.
        src_kind = addr_kind(src) if src else "unknown"
        dst_kind = addr_kind(dst) if dst else "unknown"
        src_public = src_kind == "public"
        dst_public = dst_kind == "public"

        self.flows += 1
        self.proto_flows[proto_name] += 1
        self.proto_bytes[proto_name] += octets
        self.proto_packets[proto_name] += packets
        if src_public:
            self.proto_in[proto_name] += octets
        if dst_public:
            self.proto_out[proto_name] += octets

        service = self.service_of(rec, proto)
        self.service_bytes[service] += octets
        self.service_flows[service] += 1
        if src_public:
            self.service_in[service] += octets
        if dst_public:
            self.service_out[service] += octets

        # Direction is collapsed for the pair, which is what makes a flow and
        # its reply one conversation, and kept for each address, where it is
        # the endpoint's own reading rather than the network edge's. Both are
        # `traffic`'s to explain and the reasoning is written there.
        #
        # Fed here so that there goes on being one place a flow is counted,
        # and its prune reports into the same total the summary already prints
        # a line about.
        self.pruned += self.traffic.add(src, dst, octets, packets, proto_name,
                                        service, hdr.get("received"))

        if dst_public:
            self.talkers[dst] += octets
            self.talkers_out[dst] += octets
        if src_public:
            self.talkers[src] += octets
            self.talkers_in[src] += octets

        # Internal means private, the same test the display uses to colour an
        # address as somewhere on this network. Multicast and the special
        # ranges are left out: a table of machines topped by 224.0.0.251,
        # which is every mDNS query on the LAN and not a machine at all,
        # would answer a question nobody asked. A subnet broadcast address
        # stays, since it is private like any other and nothing in a flow
        # record says what prefix length the network uses.
        if src_kind == "private":
            self.internal[src] += octets
            self.internal_out[src] += octets
        if dst_kind == "private":
            self.internal[dst] += octets
            self.internal_in[dst] += octets

        if src_public or dst_public:
            self.external_bytes += octets
            self.external_flows += 1
            # A flow between two public addresses is both arriving and leaving,
            # and is counted in each direction rather than assigned to one.
            if src_public:
                self.inbound_bytes += octets
            if dst_public:
                self.outbound_bytes += octets

        self._prune((self.service_bytes, self.service_flows),
                    (self.service_in, self.service_out))
        self._prune((self.talkers,), (self.talkers_in, self.talkers_out))
        self._prune((self.internal,), (self.internal_in, self.internal_out))

        duration = flow_duration(rec, hdr)
        if not duration:
            # A flow with no duration says nothing about how long anything took
            # or how fast it went, so it stays out of both of those answers.
            return
        self.timed_flows += 1
        self._remember_longest(duration, rec, src, dst, proto_name, octets)
        if not (src_public or dst_public):
            return

        start = flow_timestamp(rec, hdr)
        bits = octets * 8.0
        rate = bits / duration
        # Whatever else the link did, it carried this flow's bytes inside this
        # flow's lifetime, so its average is a rate the link certainly reached.
        self.peak_flow_bits = max(self.peak_flow_bits, rate)
        if int(start) == int(start + duration):
            # Begun and ended inside one second, so all of it crossed in that
            # second and several such flows add up without any assuming.
            self.second_bits[int(start)] += bits
            self._prune((self.second_bits,))
        self._add_rate(start, start + duration, rate)

    @staticmethod
    def service_of(rec, proto):
        """The port a flow is filed under, with its name where it has one.

        Whichever port has a name wins, destination first, since that is the
        one being connected to in the ordinary case. The number is always
        shown even when a name is known: a name is a convention and the number
        is the fact, and it is the number you reach for when writing a firewall
        rule or searching a capture.
        """
        for port in (rec.get("dst_port"), rec.get("src_port")):
            named = service_name(port, proto)
            if named:
                return f"{port}/{named}"
        ports = [p for p in (rec.get("dst_port"), rec.get("src_port")) if p]
        if not ports:
            return PROTO_NAMES.get(proto, "other").lower()
        return f"{min(ports)}/{PROTO_NAMES.get(proto, 'ip').lower()}"

    def _remember_longest(self, duration, rec, src, dst, proto_name, octets):
        # A heap of `top` entries, so a busy run costs five slots and not one
        # per flow. The counter breaks ties, which keeps addresses out of the
        # comparison where a None would raise.
        entry = (duration, self._tick,
                 (src, rec.get("src_port"), dst, rec.get("dst_port"),
                  proto_name, octets))
        self._tick += 1
        if len(self.longest) < self.top:
            heapq.heappush(self.longest, entry)
        else:
            heapq.heappushpop(self.longest, entry)

    def _add_rate(self, start, end, rate):
        if len(self._events) + 2 > MAX_SPEED_EVENTS:
            self.events_dropped += 1
            return
        self.rated_flows += 1
        self._events.append((start, rate))
        self._events.append((end, -rate))

    def _prune(self, counters, companions=()):
        """Drop the small fry from counters that would otherwise grow forever.

        Only the busiest handful is ever reported, so holding every
        conversation a long run has ever seen costs memory to no purpose. What
        survives is whatever ranks highest in any of the counters given, since
        a pair can be large by bytes or by packets and either earns its keep.

        A companion ranks nothing of its own and only decorates a row the
        counters chose, as the two direction halves beside an address total
        do. It loses whatever they dropped and has no say in what that is,
        which matters more than it sounds: ranking by a half would hold on to
        addresses no table can ever show, and every pass would give back less
        for it. Where each address is seen in one direction only, the two
        halves between them cover the whole table, a pass reclaims a single
        key, and the counter then sits at the cap paying for a full pass on
        every flow that brings a new address.
        """
        primary = counters[0]
        if len(primary) <= MAX_TRACKED_KEYS:
            return
        keep = set()
        for counter in counters:
            keep.update(key for key, _ in counter.most_common(MAX_TRACKED_KEYS // 2))
        dropped = [key for key in primary if key not in keep]
        for counter in (*counters, *companions):
            for key in dropped:
                counter.pop(key, None)
        self.pruned += len(dropped)

    # -- reporting ----------------------------------------------------------

    def longest_flows(self):
        """The longest lived flows, longest first, as (duration, details)."""
        return [(duration, details)
                for duration, _tick, details in sorted(self.longest, reverse=True)]

    def top_pairs_by_bytes(self):
        return self.traffic.top_pairs(self.top)

    def top_pairs_by_packets(self):
        return self.traffic.top_pairs(self.top, by_packets=True)

    def top_protocols(self, n):
        """The busiest protocols, as (name, bytes, in, out).

        Flows and packets are not in the tuple: they are looked up by name
        where the row is drawn, as they always were, and only the figures the
        ramp is stretched over need gathering here.
        """
        return self._by_bytes(self.proto_bytes, self.proto_in,
                              self.proto_out, n)

    def top_services(self, n):
        """The busiest services, in the same shape."""
        return self._by_bytes(self.service_bytes, self.service_in,
                              self.service_out, n)

    def top_external(self, n):
        """The busiest public addresses, as (address, bytes, in, out)."""
        return self._by_bytes(self.talkers, self.talkers_in, self.talkers_out, n)

    def top_internal(self, n):
        """The busiest private addresses, in the same shape."""
        return self._by_bytes(self.internal, self.internal_in,
                              self.internal_out, n)

    @staticmethod
    def _by_bytes(total, inbound, outbound, n):
        return [(addr, octets, inbound[addr], outbound[addr])
                for addr, octets in total.most_common(n)]

    def min_link_speed(self):
        """Bits per second the external link certainly carried at some point.

        Two things are true without assuming anything about how a flow spread
        its bytes. A flow delivered all of them inside its own lifetime, so the
        link reached at least that flow's average at some instant within it.
        And flows that began and ended inside the same second delivered all of
        their bytes during that second, so those add up. The larger of the two
        is the answer, and it is a floor in the strict sense: the link cannot
        have been slower.
        """
        busiest_second = max(self.second_bits.values(), default=0.0)
        return max(self.peak_flow_bits, busiest_second)

    def busiest_moment(self):
        """Concurrent demand, if every flow delivered its bytes evenly.

        Each flow is spread across its own lifetime and the rates are laid on a
        timeline; the tallest point is the answer. This is an estimate and not
        a bound, which is a distinction worth keeping: two flows that merely
        overlap need not have been sending at the same instant, so the sum of
        their averages can exceed anything the link was actually required to
        do. Real traffic is also burstier than an even spread, which pushes the
        other way. It is the shape of the traffic, not a measurement.

        Where a flow ends exactly as another begins the ending is counted
        first, which keeps the number on the conservative side.
        """
        if not self._events:
            return 0.0
        peak = running = 0.0
        for _when, delta in sorted(self._events):
            running += delta
            peak = max(peak, running)
        return peak
