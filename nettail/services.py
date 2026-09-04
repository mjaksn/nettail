"""Service names, the system database first and a shipped list after.

netflume answers "what is this port called" out of the system services
database, which is the right source and an inconsistent one: /etc/services on
a Linux box names port 5353 and the Windows services file does not, so the same
capture reads `5353/mdns` on one machine and `5353` on another. A collector
watching a home network sees mDNS constantly, and a column that names it on
some machines and not others is worse than one that always names it.

So a small list ships beside this module and is consulted when, and only when,
the system database had nothing to say. Precedence that way round means a
machine that already knows a port keeps its own answer, and the list can only
ever fill a gap.

The table is loaded once at startup rather than on demand. Nothing is loaded
until `load` is called, so a caller that never asks for the supplemental names
behaves exactly as it did before this module existed.
"""

import os

from netflume import service_name as system_service_name
from netflume.values import EPHEMERAL_FLOOR

# Where the shipped list lives. Beside this module, so it travels with the
# package however the package was fetched.
SUPPLEMENTAL_SERVICES = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "supplemental-services")

# The bottom of the ephemeral range. netflume declines to name a port at or
# above this and so does the table below: a name up there would describe
# whichever port the kernel happened to hand a client, not a service.
#
# Imported rather than repeated. It was written out here while netflume had
# the number inline and exported nothing to point at, with a test that found
# where netflume actually stopped naming ports and held this copy to it, which
# was the best that could be done about two halves of one rule sitting in two
# places. netflume 0.2.0 published the constant, so the copy went and the test
# that reverse engineered it went with it.
#
# It comes from `netflume.values` rather than from the package, which is the
# line netflume draws rather than an accident: the package exports what a
# consumer of flows needs, and a module exports what a caller drawing the same
# line as that module needs. This is the second kind. `service_name` above is
# the first, and the two arriving by different routes is the rule working.

PROTO_NUMBERS = {"tcp": 6, "udp": 17}

# (port, protocol number) -> name. Empty until load() fills it.
_supplemental = {}


def parse(lines):
    """Read /etc/services format into a {(port, proto): name} table.

    A line is a name, a `port/protocol` pair, and any number of aliases the
    column has no room for. Comments run from a # to the end of the line and a
    blank line is nothing. Anything that does not fit that shape is skipped:
    this file is meant to be edited, and one bad line should cost its own name
    rather than every name after it.

    A protocol other than tcp or udp is skipped for the same reason netflume
    will not name one, and so is a port in the ephemeral range, which nothing
    would ever look up.
    """
    table = {}
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        name = fields[0]
        number, slash, protocol = fields[1].partition("/")
        if not slash:
            continue
        proto = PROTO_NUMBERS.get(protocol.lower())
        if proto is None:
            continue
        try:
            port = int(number)
        except ValueError:
            continue
        if port <= 0 or port >= EPHEMERAL_FLOOR:
            continue
        table[(port, proto)] = name
    return table


# What the reader loses, worded the same way whichever way the list went
# missing, since from a column's point of view the two are the same event.
_LOST = ("Ports the system database does not know, mdns among them, will "
         "show as bare numbers")


def load(path=SUPPLEMENTAL_SERVICES):
    """Read the supplemental list, replacing whatever was loaded before.

    Hands back a line to tell the reader, or None when there is nothing worth
    saying. How many names came back is `loaded()` and is not news: a file that
    is missing or cannot be read is not an error either, since the collector
    runs perfectly well on the system database alone and the only consequence
    is a handful of ports printing as bare numbers. That is worth one line at
    startup and no more.

    A file that opens and yields nothing is reported too, and is the case
    worth having a message for at all. One unreadable line costs its own name
    silently, which is the bargain `parse` is meant to strike, but a file where
    every line fails that way has lost the lot and looks from the outside
    exactly like one that worked. Saving it as UTF-16 does precisely that:
    every line comes back interleaved with NULs, no protocol field matches,
    and the whole list is skipped a line at a time without anything to show
    for it.
    """
    global _supplemental
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            _supplemental = parse(handle)
    except OSError as exc:
        _supplemental = {}
        return (f"no supplemental service names: {exc.strerror or exc} "
                f"({path}). {_LOST}")
    if not _supplemental:
        return (f"no supplemental service names: nothing in {path} reads as "
                f"an entry, which is what a file saved as UTF-16 rather than "
                f"UTF-8 looks like as well as what an empty one looks like. "
                f"{_LOST}")
    return None


def clear():
    """Forget the supplemental list, system database only from here.

    The same state a run started with --no-supplemental-services is in, which
    reaches it by never loading rather than by calling this.
    """
    global _supplemental
    _supplemental = {}


def loaded():
    """How many supplemental names are in hand."""
    return len(_supplemental)


def service_name(port, proto):
    """A well known name for a port, or None.

    The system database is asked first and its answer is final. The shipped
    list is only reached for a port the system had no name for, which is what
    keeps a machine that already names a port naming it the same way it always
    did.
    """
    named = system_service_name(port, proto)
    if named:
        return named
    if not port or port >= EPHEMERAL_FLOOR:
        return None
    return _supplemental.get((port, proto))
