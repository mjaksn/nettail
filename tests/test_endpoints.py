"""Display, filter and summary must agree on where a flow's ends are."""
import argparse
import io
import sys

from harness import check, finish
from netflume import flow_endpoints

import nettail as main
from nettail import cli, display
from nettail.services import service_name

HDR = {"exporter": "10.0.0.1"}

# An exporter that reports only post-NAT addresses. The display has always read
# these; the filter used to ignore them, so such a flow was shown with real
# addresses, counted as external in the report, and hidden by --external-only.
post_nat = {"post_nat_src_addr": "192.168.1.10", "post_nat_dst_addr": "8.8.8.8",
            "proto": 6, "octets": 1000, "packets": 10, "src_port": 51000,
            "dst_port": 443}
plain = {"src_addr": "192.168.1.10", "dst_addr": "8.8.8.8", "proto": 6,
         "octets": 1000, "packets": 10}
internal = {"src_addr": "192.168.1.10", "dst_addr": "192.168.1.11", "proto": 6,
            "octets": 1000, "packets": 10}
nat_internal = {"post_nat_src_addr": "192.168.1.10",
                "post_nat_dst_addr": "192.168.1.11", "proto": 6, "octets": 1000}

check("the helper reads pre-NAT addresses",
      flow_endpoints(plain) == ("192.168.1.10", "8.8.8.8"))
check("and falls back to post-NAT ones",
      flow_endpoints(post_nat) == ("192.168.1.10", "8.8.8.8"))
check("a flow with neither has no ends",
      flow_endpoints({"proto": 6}) == (None, None))
check("pre-NAT wins when both are present",
      flow_endpoints(dict(plain, post_nat_src_addr="203.0.113.9",
                               post_nat_dst_addr="203.0.113.10"))
      == ("192.168.1.10", "8.8.8.8"))

external_only = argparse.Namespace(external_only=True)
everything = argparse.Namespace(external_only=False)

for label, rec, expected in (("a plain external flow", plain, True),
                             ("a post-NAT external flow", post_nat, True),
                             ("an internal flow", internal, False),
                             ("a post-NAT internal flow", nat_internal, False)):
    shown = cli.should_show(rec, external_only)
    tally = main.Tally()
    tally.add(rec, HDR)
    counted = tally.external_flows == 1
    check("%s: filter and summary agree" % label,
          shown == counted == expected,
          "shown=%s counted=%s expected=%s" % (shown, counted, expected))

check("without the filter everything is shown",
      all(cli.should_show(rec, everything)
          for rec in (plain, post_nat, internal, nat_internal)))

# The display reads the same ends, so a post-NAT flow is not rendered blank.
out = io.StringIO()
real, sys.stdout = sys.stdout, out
try:
    main.render(post_nat, dict(HDR, unix_secs=1700000000, sys_uptime=None),
                argparse.Namespace(verbose=False), None, main.SizeScale())
finally:
    sys.stdout = real
check("the display shows the post-NAT addresses",
      "192.168.1.10" in out.getvalue() and "8.8.8.8" in out.getvalue(),
      repr(out.getvalue()))

# And the pair table files it under the same two ends.
tally = main.Tally()
tally.add(post_nat, HDR)
check("the pair table uses the same ends",
      ("192.168.1.10", "8.8.8.8") in tally.traffic.pairs,
      str(sorted(tally.traffic.pairs)))
check("and the talkers table", tally.talkers["8.8.8.8"] == 1000,
      str(dict(tally.talkers)))

# And what the browser's filter box may match a flow on is read off the same
# ends, so a post-NAT flow can be found by the addresses its row shows.


class Named:
    """A resolver that knows one name, as a real one knows some and not others."""

    def lookup(self, addr):
        return {"8.8.8.8": "dns.google"}.get(addr)


terms = display.filter_terms(post_nat, Named())
check("the filter's terms carry the post-NAT addresses",
      "192.168.1.10" in terms and "8.8.8.8" in terms, str(terms))
check("and both ports, as the row writes them",
      "51000" in terms and "443" in terms, str(terms))
check("and the hostname of the end that has one",
      "dns.google" in terms, str(terms))
expected = [name for name in (service_name(51000, 6), service_name(443, 6)) if name]
check("and the service names this machine gives those ports",
      all(name in terms for name in expected), "%s against %s" % (terms, expected))
check("and nothing else, with no repeats",
      len(terms) == len(set(terms)) == 5 + len(set(expected)), str(terms))
check("unfolded, since the page folds case on both sides at once",
      display.filter_terms({"src_addr": "10.0.0.1", "proto": 6},
                           type("R", (), {"lookup": lambda s, a: "NAS.Local"})())
      == ["10.0.0.1", "NAS.Local"])
check("a port of 0 is not a term, since no row prints one",
      display.filter_terms({"src_addr": "10.0.0.1", "src_port": 0, "proto": 1})
      == ["10.0.0.1"])
check("and a flow with no ends has nothing to match",
      display.filter_terms({"proto": 6}, Named()) == [])

finish("endpoint agreement")
