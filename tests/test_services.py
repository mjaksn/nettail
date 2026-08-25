"""The supplemental service list: parsing it, and where it sits in the order.

The point of the list is that it fills gaps and never covers anything the
system database already answers, so precedence is what most of these check.
The system lookup is replaced for that: what /etc/services says differs from
one machine to the next, which is the very problem the list exists to solve
and no basis for an assertion.
"""
import os
import socket
import tempfile

import netflume.values as netflume_values
from harness import check, finish

import nettail as main
from nettail import services
from nettail.display import ENDPOINT_WIDTH, endpoint

HERE = os.path.dirname(os.path.abspath(__file__))
MISSING = os.path.join(HERE, "no-such-services-file")

TCP, UDP = 6, 17


def system_says(answers):
    """Stand in for the system services database, saying only what is given."""
    services.system_service_name = lambda port, proto: answers.get((port, proto))


real_system = services.system_service_name

# --- the ephemeral floor is netflume's, and this is what holds it there -----
#
# EPHEMERAL_FLOOR repeats a number netflume writes inline and exports no
# constant for, so there is nothing to import and the two can only ever drift
# apart. This finds where netflume actually stops naming ports and pins ours to
# it, so a release that moved the floor fails a check here rather than leaving
# one rule with two different ideas of where the ephemeral range begins.
#
# Every port is given a name for the duration. netflume answers None both for a
# port it declines to name and for a port the database has never heard of, and
# without that there is no telling the two apart.
real_getservbyport = socket.getservbyport
socket.getservbyport = lambda port, proto: "named"
netflume_values._service_cache.clear()
try:
    below = real_system(services.EPHEMERAL_FLOOR - 1, TCP)
    at = real_system(services.EPHEMERAL_FLOOR, TCP)
finally:
    socket.getservbyport = real_getservbyport
    netflume_values._service_cache.clear()

check("netflume still names the port below our floor", below == "named",
      repr(below))
check("and still declines to name the one at it", at is None, repr(at))

# --- the shipped file -------------------------------------------------------
note = services.load()
check("the shipped list loads", services.loaded() > 0, str(services.loaded()))
check("and says nothing when it is there", note is None, repr(note))
check("mdns is in it over udp", services._supplemental[(5353, UDP)] == "mdns")
check("and over tcp", services._supplemental[(5353, TCP)] == "mdns")
check("the path points beside the package",
      os.path.isfile(services.SUPPLEMENTAL_SERVICES),
      services.SUPPLEMENTAL_SERVICES)

# --- the system database wins -----------------------------------------------
system_says({(5353, UDP): "system-name"})
check("a port the system knows keeps the system's name",
      services.service_name(5353, UDP) == "system-name")

system_says({})
check("and only falls through when the system has nothing",
      services.service_name(5353, UDP) == "mdns")
check("a port in neither is still nameless",
      services.service_name(4711, UDP) is None)

# --- what is never looked up ------------------------------------------------
services._supplemental[(50000, UDP)] = "would-be"
check("an ephemeral port is not named from the list",
      services.service_name(50000, UDP) is None)
check("nor is port 0", services.service_name(0, UDP) is None)
check("nor is a missing port", services.service_name(None, UDP) is None)
del services._supplemental[(50000, UDP)]

check("a protocol that is neither tcp nor udp finds nothing",
      services.service_name(5353, 1) is None)

# --- parsing ----------------------------------------------------------------
table = services.parse([
    "# a comment line",
    "",
    "   ",
    "mdns   5353/udp   # trailing comment",
    "named  1234/tcp   alias1 alias2",
    "upper  4321/UDP",
    "junk",
    "noport thing/udp",
    "badproto 99/sctp",
    "ephemeral 49152/udp",
    "zero 0/udp",
    "negative -1/udp",
])
check("comments and blank lines are skipped", (5353, UDP) in table and len(table) == 3,
      repr(sorted(table)))
check("aliases are read and dropped", table[(1234, TCP)] == "named")
check("a protocol is matched whatever its case", table[(4321, UDP)] == "upper")
check("a trailing comment does not reach the name", table[(5353, UDP)] == "mdns")
check("a line with no port/proto pair is skipped", "junk" not in table.values())
check("a port that is not a number is skipped", "noport" not in table.values())
check("a protocol that cannot be named is skipped", "badproto" not in table.values())
check("the ephemeral floor is applied at parse time too",
      (49152, UDP) not in table)
check("and so is a port no flow can carry",
      (0, UDP) not in table and (-1, UDP) not in table)

# --- a missing file says so and carries on ----------------------------------
note = services.load(MISSING)
check("a missing file reads nothing", services.loaded() == 0)
check("and hands back a line to print", bool(note), repr(note))
check("which names the file", MISSING in note, repr(note))
check("and says what the reader loses", "mdns" in note, repr(note))
check("the lookup still works on the system database alone",
      services.service_name(5353, UDP) is None)
system_says({(443, TCP): "https"})
check("and still answers for a port the system knows",
      services.service_name(443, TCP) == "https")

# --- a file that opens and yields nothing says so too -----------------------
#
# One line that cannot be read costs its own name and stays quiet, which is the
# bargain parse() strikes. A file where every line fails that way has lost the
# whole list and looks from the outside exactly like one that worked, so it is
# the case that has to speak up. Saving as UTF-16 is how it happens in real
# life: read back as UTF-8 every line comes through interleaved with NULs, no
# protocol field matches, and all fourteen names are skipped one at a time.
with tempfile.TemporaryDirectory() as folder:
    shipped = open(services.SUPPLEMENTAL_SERVICES, encoding="utf-8").read()

    wide = os.path.join(folder, "utf-16-services")
    with open(wide, "w", encoding="utf-16") as handle:
        handle.write(shipped)
    note = services.load(wide)
    check("a list saved as UTF-16 reads as no entries at all",
          services.loaded() == 0)
    check("and is reported rather than passed over", bool(note), repr(note))
    check("the note names the file", wide in note, repr(note))
    check("and names the likely cause", "UTF-16" in note, repr(note))
    check("and says what the reader loses", "mdns" in note, repr(note))

    barren = os.path.join(folder, "comments-only")
    with open(barren, "w", encoding="utf-8") as handle:
        handle.writelines(["# every line a comment\n", "\n", "   \n"])
    note = services.load(barren)
    check("a file with no entries in it is reported the same way",
          bool(note) and services.loaded() == 0, repr(note))

    # The same file saved the way it ships reads back whole, which is what
    # says the checks above caught the encoding and not the copying.
    narrow = os.path.join(folder, "utf-8-services")
    with open(narrow, "w", encoding="utf-8") as handle:
        handle.write(shipped)
    note = services.load(narrow)
    check("the same list saved as UTF-8 loads and says nothing",
          note is None and services.loaded() > 0, repr(note))

# --- clear() is what the flag leaves behind ---------------------------------
services.load()
with open(services.SUPPLEMENTAL_SERVICES, encoding="utf-8") as handle:
    entries = [line for line in handle
               if line.strip() and not line.lstrip().startswith("#")]
check("loaded() counts every entry in the file",
      services.loaded() == len(entries),
      f"{services.loaded()} of {len(entries)}")
services.clear()
check("clear() empties the table", services.loaded() == 0)
system_says({})
check("and the supplemental names go with it",
      services.service_name(5353, UDP) is None)

# --- it reaches the rendered column -----------------------------------------
services.load()
system_says({})
cell = endpoint("192.168.1.77", 5353, UDP, ENDPOINT_WIDTH)
check("a flow column shows the supplemental name", "5353/mdns" in cell, repr(cell))

services.clear()
cell = endpoint("192.168.1.77", 5353, UDP, ENDPOINT_WIDTH)
check("and shows the bare port without it", "5353/mdns" not in cell, repr(cell))

# --- and the summary files a flow under it ----------------------------------
services.load()
rec = {"src_addr": "192.168.1.77", "dst_addr": "224.0.0.251",
       "src_port": 5353, "dst_port": 5353, "proto": UDP,
       "packets": 2, "octets": 180}
check("the services table names it too",
      main.Tally.service_of(rec, UDP) == "5353/mdns",
      main.Tally.service_of(rec, UDP))

services.system_service_name = real_system
finish("service name")
