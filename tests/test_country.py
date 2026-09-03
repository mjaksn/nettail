"""Country marking: the database reader, the flag, and where it shows up.

There is no committed fixture and there could not usefully be one. A real
country database is nine megabytes, is licensed rather than free to
redistribute, and would say something different every month; and a hand
trimmed one would be a file this suite wrote in the end anyway. So the builder
below writes real MaxMind format files into `tempfile`, a few hundred bytes
each, and the reader is run against them.

That makes the builder the thing under test as much as the reader is, which is
worth saying out loud: two halves of one misunderstanding agree with each
other perfectly. The parts where that would matter are pinned against the
format rather than against the other half.

- **Every database is built at all three record widths**, 24, 28 and 32 bits,
  and asked the same questions. The 28 bit case packs the top four bits of
  each record into a byte between them, high nibble to the left record and low
  to the right, and swapping those two produces a tree that walks perfectly
  and answers with the wrong country. The three widths agreeing is what says
  the packing is right, because 24 and 32 have no nibble to get wrong.
- **An IPv4 address is looked up in an IPv6 tree**, which is ninety six zero
  bits and then the address, and separately in an IPv4 tree, which is the
  address alone. A reader that forgot the ninety six would answer from
  whatever sits at the top of the v6 tree.
- **The pointer, the extended size and the types a City database carries** are
  all put in the file deliberately. A country database uses none of the last
  two, so a reader can be wrong about them for as long as nobody points it at
  a City file, which is a thing this program invites people to do.

The rest of the suite is about where a country ends up once it is known: in a
flow row, in the summary, on the status bar, in `--json`, and never on an
address on this network.
"""
import argparse
import gzip
import hashlib
import io
import ipaddress
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
from collections import Counter

from harness import ROOT, SCRIPT, FakeTTY, check, finish, plain
from lanname import Resolver
from netflume import SequenceWatch

import nettail as main
from nettail import country
from nettail.colour import C, PlainStream, colour_on
from nettail.display import ENDPOINT_WIDTH, endpoint
from nettail.statusbar import wire_line
from nettail.values import human_bytes
from nettail.web import WEB_ENDPOINT_WIDTH, unpad
from nettail.web import load_font as web_font

# ---------------------------------------------------------------------------
# Writing a MaxMind format file
# ---------------------------------------------------------------------------

MARKER = b"\xab\xcd\xefMaxMind.com"


def field(kind, size):
    """A field's control byte, its type extension and its size extension.

    In that order, which is the order a reader takes them in: the type is
    settled before the size is asked for. A type above seven does not fit the
    three bits it has, so it is written as zero and the real number, less
    seven, follows in a byte of its own.
    """
    first, extension = (kind << 5, b"") if kind < 8 else (0, bytes([kind - 7]))
    if size < 29:
        return bytes([first | size]) + extension
    if size < 285:
        return bytes([first | 29]) + extension + bytes([size - 29])
    return (bytes([first | 30]) + extension
            + struct.pack(">H", size - 285))


def encode(value):
    """One value, in the format's own encoding."""
    if isinstance(value, bool):
        # Before the integer test, which a bool would otherwise pass. The size
        # field is the value and there is no payload at all.
        return field(14, 1 if value else 0)
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return field(2, len(raw)) + raw
    if isinstance(value, float):
        return field(3, 8) + struct.pack(">d", value)
    if isinstance(value, int):
        raw = struct.pack(">I", value).lstrip(b"\x00")
        return field(6, len(raw)) + raw
    if isinstance(value, dict):
        out = field(7, len(value))
        for key, item in value.items():
            out += encode(key) + encode(item)
        return out
    if isinstance(value, list):
        out = field(11, len(value))
        for item in value:
            out += encode(item)
        return out
    raise TypeError(value)


def pointer(offset):
    """A pointer to somewhere in the data section.

    Written at the widest of the four forms, which is the one that spends its
    three spare bits on nothing and takes four bytes for the address. The
    narrower three are what a real database uses and are exercised by the
    metadata being read through the same decoder, but a builder that chose
    between them by size would be a second thing to be wrong about.
    """
    return bytes([(1 << 5) | (3 << 3)]) + struct.pack(">I", offset)


class Tree:
    """A binary trie of network prefixes, ready to be written out."""

    def __init__(self):
        # Each node is a two item list. An entry is None for nothing here, an
        # int for another node, or a ("data", key) pair.
        self.nodes = [[None, None]]

    def insert(self, bits, key):
        node = 0
        for index, bit in enumerate(bits):
            if index == len(bits) - 1:
                self.nodes[node][bit] = ("data", key)
                return
            child = self.nodes[node][bit]
            if not isinstance(child, int):
                self.nodes.append([None, None])
                child = len(self.nodes) - 1
                self.nodes[node][bit] = child
            node = child


def build(entries, record_size=24, ip_version=6, extras=None, built=None,
          kind="GeoLite2-Country"):
    """A whole database, as bytes, for a list of (network, country code).

    `extras` is folded into every record, which is how a City database's
    doubles and arrays are put in front of the reader without a second
    builder.
    """
    tree = Tree()
    for network, code in entries:
        net = ipaddress.ip_network(network)
        value = int(net.network_address)
        if net.version == 4 and ip_version == 6:
            # At ::/96, which is ninety six zero bits in front of it. The same
            # number read as 128 bits wide has them already.
            width, depth = 128, 96 + net.prefixlen
        else:
            width = 32 if net.version == 4 else 128
            depth = net.prefixlen
        tree.insert([(value >> (width - 1 - i)) & 1 for i in range(depth)], code)

    # The data section, one record per country and a pointer for a repeat.
    data, offsets = bytearray(), {}
    for _network, code in entries:
        if code in offsets:
            continue
        record = {
            "continent": {"code": "EU", "geoname_id": 6255148},
            "country": {"iso_code": code, "geoname_id": 2635167,
                        "names": {"en": "The country called " + code}},
            "registered_country": {"iso_code": code},
        }
        if extras:
            record.update(extras)
        offsets[code] = len(data)
        data += encode(record)
    # Every code after the first occurrence is written again as a pointer, so
    # that a file with a repeat has one in it to be followed.
    repeats = {}
    for _network, code in entries:
        if code not in repeats and list(c for _n, c in entries).count(code) > 1:
            repeats[code] = len(data)
            data += pointer(offsets[code])

    node_count = len(tree.nodes)
    node_bytes = record_size // 4

    def value_of(entry):
        if entry is None:
            return node_count
        if isinstance(entry, int):
            return entry
        code = entry[1]
        at = repeats.get(code, offsets[code])
        return node_count + 16 + at

    out = bytearray()
    for left, right in tree.nodes:
        first, second = value_of(left), value_of(right)
        if record_size == 24:
            out += first.to_bytes(3, "big") + second.to_bytes(3, "big")
        elif record_size == 32:
            out += first.to_bytes(4, "big") + second.to_bytes(4, "big")
        else:
            out += first.to_bytes(4, "big")[1:]
            out += bytes([((first >> 24) << 4) | (second >> 24)])
            out += second.to_bytes(4, "big")[1:]
    assert len(out) == node_count * node_bytes
    out += b"\x00" * 16
    out += data
    out += MARKER
    out += encode({
        "node_count": node_count,
        "record_size": record_size,
        "ip_version": ip_version,
        "database_type": kind,
        "binary_format_major_version": 2,
        "binary_format_minor_version": 0,
        "build_epoch": int(time.time()) if built is None else built,
        "languages": ["en"],
        "description": {"en": "a database this suite wrote"},
    })
    return bytes(out)


HELD = []


def written(data):
    """A database on disk, kept for the run so the mapping stays valid."""
    handle = tempfile.NamedTemporaryFile(suffix=".mmdb", delete=False)
    handle.write(data)
    handle.close()
    HELD.append(handle.name)
    return handle.name


NETWORKS = [
    ("93.184.216.0/24", "US"),
    ("140.82.114.0/24", "US"),     # the address the README quotes
    ("9.9.9.0/24", "CH"),
    ("8.8.8.0/24", "US"),          # the repeat, which is written as a pointer
    ("2606:2800::/32", "US"),
    ("192.168.1.0/24", "GB"),      # never marked, whatever the file says
]

# ---------------------------------------------------------------------------
# The reader
# ---------------------------------------------------------------------------

for size in (24, 28, 32):
    db = country.Database(written(build(NETWORKS, record_size=size)))
    label = "%d bit records" % size
    check("%s: the metadata reads back" % label,
          (db.record_size, db.ip_version, db.database_type)
          == (size, 6, "GeoLite2-Country"),
          repr((db.record_size, db.ip_version, db.database_type)))
    check("%s: an IPv4 address is found under ninety six zero bits" % label,
          db.country("93.184.216.34") == "US", repr(db.country("93.184.216.34")))
    check("%s: a second network answers with its own country" % label,
          db.country("9.9.9.9") == "CH", repr(db.country("9.9.9.9")))
    check("%s: a record reached through a pointer reads the same" % label,
          db.country("8.8.8.8") == "US", repr(db.country("8.8.8.8")))
    check("%s: an IPv6 address is found too" % label,
          db.country("2606:2800:220:1:248:1893:25c8:1946") == "US",
          repr(db.country("2606:2800:220:1:248:1893:25c8:1946")))
    check("%s: an address in no network answers nothing" % label,
          db.country("1.1.1.1") is None, repr(db.country("1.1.1.1")))
    db.close()

# The pointer above is only a check of the pointer decoder if the file really
# has one in it, and a builder that quietly wrote the record twice would pass
# every line above.
raw = build(NETWORKS)
check("the file the checks above read has a pointer in it",
      bytes([(1 << 5) | (3 << 3)]) in raw)

# An IPv4 tree, where there is no ::/96 to walk through first.
db = country.Database(written(build(
    [("93.184.216.0/24", "US")], ip_version=4)))
check("an IPv4 database is walked at thirty two bits",
      db.country("93.184.216.34") == "US", repr(db.country("93.184.216.34")))
check("and is asked nothing about an IPv6 address",
      db.country("2606:2800::1") is None)
db.close()

# A database with nothing at all under ::/96, where the ninety six zeros walked
# at open run into the empty marker rather than into a node.
db = country.Database(written(build([("2606:2800::/32", "US")])))
check("a database with no IPv4 space answers nothing for an IPv4 address",
      db.country("93.184.216.34") is None)
check("and still answers for the IPv6 space it has",
      db.country("2606:2800::1") == "US")
db.close()

# What a City database carries and a country database does not. Somebody with
# one already installed should be able to point at it.
city = country.Database(written(build(NETWORKS, extras={
    "location": {"latitude": 51.4964, "longitude": -0.1224,
                 "accuracy_radius": 100},
    "subdivisions": [{"iso_code": "ENG", "names": {"en": "England"}}],
    "traits": {"is_anonymous_proxy": False},
    "postal": {"code": "a code long enough to need a size byte of its own"},
})))
check("a record carrying doubles, arrays and a boolean still reads",
      city.country("93.184.216.34") == "US")
record = city.find("93.184.216.34")
check("the double came back as itself",
      abs(record["location"]["latitude"] - 51.4964) < 1e-9,
      repr(record.get("location")))
check("the array came back as itself",
      record["subdivisions"][0]["iso_code"] == "ENG", repr(record.get("subdivisions")))
check("the boolean came back as itself",
      record["traits"]["is_anonymous_proxy"] is False, repr(record.get("traits")))
check("a string too long for the five bit size field came back whole",
      record["postal"]["code"].endswith("of its own"), repr(record.get("postal")))
city.close()

# A file of the right shape whose tree points into nowhere. One bad record
# should cost its own answer rather than the collector, since the file is
# neither this program's nor written by it.
raw = bytearray(build(NETWORKS))
raw[0:3] = bytes([0xFF, 0xFF, 0xFF])
country.load(written(bytes(raw)))
check("a record pointing past the end of the file answers nothing",
      country.mark("93.184.216.34") == "", repr(country.mark("93.184.216.34")))
check("and the database is still open for the addresses it can answer",
      country.loaded())
country.close()

# A file that is not one of these at all.
try:
    country.Database(written(b"not a database, not even a little"))
    check("a file with no metadata marker is refused", False)
except country.BadDatabase as exc:
    check("a file with no metadata marker is refused", True, str(exc))

# ---------------------------------------------------------------------------
# Loading, and what the reader is told
# ---------------------------------------------------------------------------

note = country.load(os.path.join(tempfile.gettempdir(), "nothing-is-here.mmdb"))
check("a missing file is a line and not an exception", bool(note), repr(note))
check("and the line names the file", "nothing-is-here.mmdb" in (note or ""))
check("and nothing is marked after it", country.mark("93.184.216.34") == "",
      repr(country.mark("93.184.216.34")))

note = country.load(written(b"nonsense" * 40))
check("a file that is not a database is a line too", bool(note), repr(note))
check("and says what one is",
      "MaxMind" in (note or ""), repr(note))

PATH = written(build(NETWORKS))
check("a real one loads with nothing to say", country.load(PATH) is None)
check("and says which file it read", PATH in country.describe(), country.describe())
check("and dates it", time.strftime("%Y-%m-%d", time.gmtime()) in country.describe(),
      country.describe())
check("and says nothing about the age of a fresh one",
      "old enough" not in country.describe(), country.describe())

country.load(written(build(NETWORKS, built=int(time.time()) - 400 * 86400)))
check("a database old enough for countries to have moved says so",
      "old enough" in country.describe(), country.describe())
country.load(PATH)

# ---------------------------------------------------------------------------
# The marker itself
# ---------------------------------------------------------------------------

check("a flag is the two letters as regional indicators",
      country.flag("GB") == "\U0001f1ec\U0001f1e7", repr(country.flag("GB")))
check("which is two characters, as the two letters are",
      len(country.flag("GB")) == len("GB"))
check("a public address is marked",
      country.mark("93.184.216.34") == " " + country.flag("US"),
      repr(country.mark("93.184.216.34")))
check("an address on this network is not, whatever the database says",
      country.mark("192.168.1.10") == "", repr(country.mark("192.168.1.10")))
check("nor is one the database has nothing for",
      country.mark("1.1.1.1") == "", repr(country.mark("1.1.1.1")))
check("nor is nothing at all", country.mark(None) == "")

country.show(False)
check("the g key stops the marking without closing the database",
      country.mark("93.184.216.34") == "" and country.loaded())
check("and the code is still there to be asked for",
      country.country_of("93.184.216.34") == "US")
country.show(True)
check("and puts it back", country.mark("93.184.216.34") != "")

# The cache is what stops a busy link walking the same tree over and over, and
# the bound is what stops it being a leak. Every one of these answers nothing,
# and an answer of nothing is worth remembering exactly as much as any other.
for n in range(country.CACHE_MAX + 500):
    country.country_of("100.%d.%d.%d" % (n // 65536 % 64, n // 256 % 256, n % 256))
check("the cache is bounded", len(country._cache) <= country.CACHE_MAX,
      str(len(country._cache)))

# ---------------------------------------------------------------------------
# Spelling a flag out for a terminal that cannot draw one
# ---------------------------------------------------------------------------

check("a flag is spelled back as its letters",
      country.spell_flags(country.flag("GB")) == "GB")
check("and is the same width either way",
      len(country.spell_flags(country.flag("GB"))) == len(country.flag("GB")))
check("colour is left alone",
      country.spell_flags(f"{C.CYAN}9.9.9.9{C.RESET}")
      == f"{C.CYAN}9.9.9.9{C.RESET}")
check("and so is every other escape",
      country.spell_flags("\033[2;80r\033[1;1H") == "\033[2;80r\033[1;1H")

buffer = io.StringIO()
stream = country.CodeStream(buffer)
stream.write("an address " + country.flag("GB") + "\n")
check("the stream writes the letters", buffer.getvalue() == "an address GB\n",
      repr(buffer.getvalue()))
check("and reports what it was handed, not what it wrote",
      country.CodeStream(io.StringIO()).write(country.flag("GB")) == 2)

# Two wrappers can end up around one terminal, and the colour question has to
# see through whichever is on the outside.
check("colour_on sees a colourless stream through a flag wrapper",
      colour_on(country.CodeStream(PlainStream(io.StringIO()))) is False)
check("and the other way round",
      colour_on(PlainStream(country.CodeStream(io.StringIO()))) is False)
check("and says yes when neither takes it out",
      colour_on(country.CodeStream(io.StringIO())) is True)


class Redirected(io.StringIO):
    """A stream that could carry the characters and is not a terminal."""

    encoding = "utf-8"


class Console(Redirected):
    """The same, and a terminal."""

    def isatty(self):
        return True


console = Console()
check("--country-style flag overrides every guess",
      country.terminal_flags("flag", io.StringIO(), env={}, platform="nt") is True)
check("--country-style code does too",
      country.terminal_flags("code", console, env={"TERM": "xterm"},
                             platform="posix") is False)
check("auto draws a flag on an ordinary terminal",
      country.terminal_flags("auto", console, env={"TERM": "xterm-256color"},
                             platform="posix") is True)
check("auto spells it out on Windows, which ships no flag",
      country.terminal_flags("auto", console, env={"TERM": "xterm"},
                             platform="nt") is False)
check("and on macOS Terminal, which draws the letters itself",
      country.terminal_flags("auto", console,
                             env={"TERM": "xterm-256color",
                                  "TERM_PROGRAM": "Apple_Terminal"},
                             platform="posix") is False)
check("and on the Linux console",
      country.terminal_flags("auto", console, env={"TERM": "linux"},
                             platform="posix") is False)
check("and with no TERM at all",
      country.terminal_flags("auto", console, env={}, platform="posix") is False)
check("and into a file, which is not a terminal to reason about",
      country.terminal_flags("auto", Redirected(), env={"TERM": "xterm"},
                             platform="posix") is False)


class Latin(Console):
    encoding = "cp1252"


check("an encoding that cannot carry them settles it first",
      country.terminal_flags("auto", Latin(), env={"TERM": "xterm-256color"},
                             platform="posix") is False)

# ---------------------------------------------------------------------------
# Where it shows up
# ---------------------------------------------------------------------------

cell = endpoint("93.184.216.34", 443, 6, ENDPOINT_WIDTH)
check("a flow row carries the flag after the service name",
      cell.startswith("93.184.216.34:443/https " + country.flag("US")), repr(cell))
check("and the column is still exactly as wide as it was",
      len(cell) == ENDPOINT_WIDTH, str(len(cell)))
local = endpoint("192.168.1.10", 51000, 6, ENDPOINT_WIDTH)
check("a local address in the same row carries nothing",
      local.strip() == "192.168.1.10:51000", repr(local))


class Named:
    """A resolver that knows one name, so the brackets are in play."""

    def lookup(self, addr):
        return "edge" if str(addr) == "93.184.216.34" else None


cell = endpoint("93.184.216.34", 443, 6, ENDPOINT_WIDTH, Named())
check("the flag goes in front of the brackets, which mean a name",
      cell.strip() == "93.184.216.34:443/https " + country.flag("US") + " (edge)",
      repr(cell))
check("and the cell is still its column's width", len(cell) == ENDPOINT_WIDTH)

cell = endpoint("93.184.216.34", 443, 6, ENDPOINT_WIDTH, Named(), named=True)
check("under the n key it rides on the name that replaced the address",
      cell.strip() == "edge:443/https " + country.flag("US"), repr(cell))

narrow = endpoint("93.184.216.34", 443, 6, 30, Named())
check("dropping the service name to make room keeps the country",
      narrow.strip() == "93.184.216.34:443 " + country.flag("US") + " (edge)",
      repr(narrow))
check("and that cell fits its width too", len(narrow) == 30)

# What the README quotes, against what the program builds. The two forms of
# one cell are the whole of what a reader is shown of this feature, and a
# sample that has gone stale is exactly as misleading here as a stale column
# width elsewhere.
with io.open(os.path.join(ROOT, "README.md"), encoding="utf-8") as handle:
    README = handle.read()


class Github:
    def lookup(self, addr):
        return "github" if str(addr) == "140.82.114.4" else None


quoted = endpoint("140.82.114.4", 443, 6, ENDPOINT_WIDTH, Github()).rstrip()
check("the README quotes the cell the program builds", quoted in README,
      repr(quoted))
check("and the spelled out form of the same cell",
      country.spell_flags(quoted) in README, repr(country.spell_flags(quoted)))
check("and the flag it quotes is the one this program draws",
      country.flag("US") in README)
for path in country.UNIX_PATHS:
    check("the README lists %s among the places looked in" % path,
          path in README)
for _variable, tail in country.WINDOWS_PATHS:
    check("the README lists the Windows %s too" % tail, tail in README)

# Where a database is looked for, on a platform that is not the one running
# this. A list of Unix paths on Windows is a --country that cannot work and
# an answer naming four directories that could not have existed, which is
# what shipped for an afternoon.
check("the Unix places are the Unix ones",
      country.search_paths(platform="posix", env={}) == country.UNIX_PATHS)
windows = country.search_paths(
    platform="nt", env={"LOCALAPPDATA": r"C:\Users\x\AppData\Local",
                        "PROGRAMDATA": r"C:\ProgramData"})
check("Windows gets Windows ones, per user first",
      windows == (r"C:\Users\x\AppData\Local\nettail\country.mmdb",
                  r"C:\ProgramData\nettail\country.mmdb"), str(windows))
check("and none of the Unix ones, which cannot exist there",
      not any(path.startswith("/") for path in windows), str(windows))
check("a Windows machine with neither variable set is looked at nowhere",
      country.search_paths(platform="nt", env={}) == ())

# What a run that finds nothing says, without depending on what this machine
# happens to have. `load` reads the list through the module, so replacing it
# is enough.
real_paths = country.search_paths
# Takes whatever it is handed, because `destination` asks the same function
# for a platform and an environment and `load` asks it for neither.
country.search_paths = lambda *_: ("/nowhere/at/all/country.mmdb",)
try:
    note = country.load()
finally:
    country.search_paths = real_paths
check("finding none says where it looked", "/nowhere/at/all" in (note or ""),
      repr(note))
check("and names somewhere to put one", "country.mmdb" in (note or ""),
      repr(note))
check("and says a free one can be had", "DB-IP" in (note or ""), repr(note))
country.load(PATH)

country.show(False)
check("with the marking off a row is exactly what it always was",
      endpoint("93.184.216.34", 443, 6, ENDPOINT_WIDTH)
      == "93.184.216.34:443/https".ljust(ENDPOINT_WIDTH))
country.show(True)

# ---------------------------------------------------------------------------
# End to end: a collector run with a database in front of it
# ---------------------------------------------------------------------------

V5_HDR = struct.Struct("!HHIIIIBBH")
V5_REC = struct.Struct("!4s4s4sHHIIIIHHBBBBHHBBH")

FLOWS = [((192, 168, 1, 10), (93, 184, 216, 34), 5000),
         ((9, 9, 9, 9), (192, 168, 1, 20), 9000)]


def v5_packet():
    now = int(time.time())
    packet = V5_HDR.pack(5, len(FLOWS), 100000, now, 0, 0, 0, 0, 0)
    for index, (src, dst, octets) in enumerate(FLOWS):
        packet += V5_REC.pack(
            bytes(src), bytes(dst), bytes([192, 168, 1, 1]),
            1, 2, 12, octets, 90000, 100000, 51000 + index, 443, 0,
            0x18, 6, 0, 0, 0, 24, 24, 0)
    return packet


def run(argv):
    queue = [v5_packet()]

    class FakeSocket:
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
            if not queue:
                raise KeyboardInterrupt
            return queue.pop(0), ("10.0.0.1", 2055)

    real_socket = socket.socket
    socket.socket = FakeSocket
    out, err = FakeTTY(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    sys.argv = ["nettail", "--resolve", "off", "--no-color",
                "--hide-status"] + argv
    try:
        main.main()
    finally:
        socket.socket = real_socket
        sys.stdout, sys.stderr = real_out, real_err
    return out.getvalue(), plain(err.getvalue())


US, CH = country.flag("US"), country.flag("CH")

out, err = run(["--country-db", PATH, "--country-style", "flag"])
check("the run says which database it read", PATH in err, err[:400])
check("the public destination is flagged in the flow row", US in out, repr(out))
check("the public source is flagged too", CH in out, repr(out))
check("the private end of the same flow is not",
      out.count(US) == 1 and out.count(CH) == 1, repr(out))
check("the summary's external table is flagged",
      any(US in line for line in err.splitlines()
          if line.strip().startswith("93.184.216.34")), err[err.find("Top external"):])
internal = err[err.find("Top internal addresses"):]
check("the internal table is not",
      US not in internal and CH not in internal, internal)
ends = err.find("Longest")
pairs = err[err.find("Busiest"):ends if ends > 0 else len(err)]
check("and so are the busiest pairs, which are made of the same addresses",
      US in pairs, pairs)

# The same run, told this terminal cannot draw one.
out, err = run(["--country-db", PATH, "--country-style", "code"])
check("code style spells the flag out on stdout",
      "93.184.216.34:443/https US" in out, repr(out))
check("and in the summary on stderr",
      any("US" in line for line in err.splitlines()
          if line.strip().startswith("93.184.216.34")), err[err.find("Top external"):])
check("and no flag survives to either", US not in out and US not in err)

# --country on its own goes looking, and says where it looked when it finds
# nothing. The search paths are absolute and Unix, so on a machine that has
# none of them this is the whole of what happens.
if not any(os.path.exists(path) for path in country.search_paths()):
    out, err = run(["--country"])
    check("--country with nothing to read says where it looked",
          "Looked in" in err, err[:400])
    check("and the collector runs on regardless",
          "93.184.216.34:443/https" in out, repr(out))

# --country-db implies the marking, so nobody has to ask twice.
out, err = run(["--country-db", PATH, "--country-style", "flag"])
check("naming a database is asking for the marking", US in out)

# What a browser is handed. Its cells are built by the same `row_cells` the
# terminal row is built by and never pass a terminal stream, which is the whole
# of why the two views can differ at all: the run above spelled the flag out on
# stdout, and this is the same row, from the same function, with the flag still
# on it.
cells = [unpad(painted) for _plain, painted in main.row_cells(
    {"src_addr": "192.168.1.10", "src_port": 51000, "dst_addr": "140.82.114.4",
     "dst_port": 443, "proto": 6, "packets": 12, "octets": 4180,
     "first_switched": 90000, "last_switched": 100000},
    {"exporter": "10.0.0.1", "unix_secs": 1755780003, "version": 5},
    argparse.Namespace(named_hosts=False, show_macs=False, verbose=False),
    Github(), main.SizeScale(), endpoint_width=WEB_ENDPOINT_WIDTH)]
check("the cells a browser is handed carry the flag itself",
      any(US in cell for cell in cells), repr(cells))
check("and the hostname beside it, which is the same cell",
      any("github" in cell for cell in cells), repr(cells))

# A browser draws a flag with whatever font it can find, and no monospace font
# has one, so the page names the emoji families that can behind its own stack.
# Dropping them is invisible on a Mac and on Linux and takes every flag away
# from a Windows browser that had one, which is the sort of quiet loss a grep
# is for. Segoe UI Emoji is what Windows always has and is the one of them that
# draws the two letters instead, so anything installed beside it has to come
# first.
with io.open(os.path.join(ROOT, "nettail", "web.html"), encoding="utf-8") as h:
    PAGE = h.read()

# The declaration itself, not the page, since the comment above it names the
# same fonts in the order it explains them rather than the order they are in.
STACK = PAGE[PAGE.index("font: 13px"):]
STACK = STACK[:STACK.index(";")]

for family in ("Twemoji Mozilla", "Noto Color Emoji", "Apple Color Emoji",
               "Segoe UI Emoji"):
    check("the page names %s among its fonts" % family, family in STACK, STACK)
check("and names the one that cannot draw a flag last of the four",
      STACK.index("Segoe UI Emoji") > STACK.index("Noto Color Emoji"), STACK)
check("with the whole lot behind the monospace fonts",
      STACK.index("Twemoji Mozilla") > STACK.index("DejaVu Sans Mono"), STACK)

# The font this collector ships, and the file that says whose it is. The
# artwork is CC BY 4.0, which asks for the credit to travel with the material,
# so the licence file has to be in the package beside the font and has to be
# describing the font that is actually there. A hash written down once and
# never checked again is the same as no hash at all.
FONT = os.path.join(ROOT, "nettail", "flags.woff2")
with io.open(FONT, "rb") as handle:
    FONT_BYTES = handle.read()
with io.open(os.path.join(ROOT, "nettail", "flags-licence"),
             encoding="utf-8") as handle:
    LICENCE = handle.read()

check("the font ships with the package", len(FONT_BYTES) > 20000,
      str(len(FONT_BYTES)))
check("and is a woff2", FONT_BYTES[:4] == b"wOF2", repr(FONT_BYTES[:4]))
check("the licence beside it records this font and not another",
      hashlib.sha256(FONT_BYTES).hexdigest() in LICENCE,
      hashlib.sha256(FONT_BYTES).hexdigest())
for credit in ("Twemoji", "CC BY 4.0", "creativecommons.org/licenses/by/4.0",
               "country-flag-emoji-polyfill"):
    check("and credits %s" % credit, credit in LICENCE)
check("the page asks for the file that is shipped",
      "flags.woff2" in PAGE and web_font() == FONT_BYTES)

# The status bar's top talker is a public address by definition, and is the one
# field on the bar that can carry a country.
bar = plain(wire_line({
    "elapsed": 252.0, "packets": 3100, "flows": 12400, "bytes_rx": 48 << 20,
    "pkt_rate": 12.0, "flow_rate": 1200.0, "bit_rate": 9.4e6,
    "external_bytes": 41 << 20, "inbound": 28 << 20, "outbound": 12 << 20,
    "counted_bytes": 67 << 20, "peak": 142e6,
    "top_talker": ("93.184.216.34", "edge", 5.4 * (1 << 20)),
}, 200))
check("the status bar flags its top talker",
      "top 93.184.216.34 " + US + " (edge)" in bar, repr(bar))

# ---------------------------------------------------------------------------
# The parseable half carries the code and not the picture
# ---------------------------------------------------------------------------

out, err = run(["--json", "--country-db", PATH])
check("--json carries the destination's country as two letters",
      '"dst_country": "US"' in out, repr(out[:600]))
check("and the source's", '"src_country": "CH"' in out, repr(out[:600]))
check("and no flag anywhere in it", US not in out and CH not in out)
check("and nothing at all for the private end",
      out.count("_country") == 2, repr(out))

out, err = run(["--json"])
check("a run that asked for no countries says nothing about them",
      "_country" not in out, repr(out[:600]))

# ---------------------------------------------------------------------------
# The key
# ---------------------------------------------------------------------------

check("the g key is listed", any(key == "g" for key, _doc in main.KEYS))
check("and a browser may press it", "g" in dict(main.web_keys()))

args = argparse.Namespace(json=False, external_only=False, fqdn=False,
                          resolve="off", header_every=40, verbose=False)
resolver = Resolver(mode="off", workers=1)
controls = main.Controls(args, main.SizeScale(), resolver, None, Counter(),
                         Counter(), SequenceWatch(), out=io.StringIO())

country.close()
check("with no database the g key says so, rather than doing nothing quietly",
      "no country database" in controls.handle("g"), controls.handle("g"))
country.load(PATH)
check("with one it turns the marking off",
      "no longer" in controls.handle("g"))
check("and on again",
      "marking external addresses" in controls.handle("g"))
resolver.shutdown()

# ---------------------------------------------------------------------------
# Being offered a database, and fetching one
# ---------------------------------------------------------------------------
#
# Nothing here touches the network. `download` takes the opener it uses, so
# the suite hands it one that answers out of memory, and every shape a real
# fetch can come back in is written out here: the file, a month whose build is
# not up yet, an HTTP error that is not that, a stream that is not gzip, and
# gzip of something that is not a database.
#
# The other half is the offer, which is the part that decides whether a
# program fetches anything at all. Both ends being a terminal is the whole of
# that guard, and a run under systemd or cron or docker has neither, so the
# checks below are as much about the runs that must not ask as the one that
# does.


class Fetched:
    """What urlopen hands back, as much of it as gzip touches."""

    def __init__(self, payload):
        self.body = io.BytesIO(payload)
        self.closed = False

    def read(self, size=-1):
        return self.body.read(size)

    def close(self):
        self.closed = True


class Headed:
    """What urlopen hands back for a HEAD: the headers and no body at all."""

    def __init__(self, size=None):
        self.headers = {} if size is None else {"Content-Length": str(size)}
        self.closed = False

    def close(self):
        self.closed = True


class Opener:
    """A urlopen that answers from a table of URLs, and logs what it was asked.

    A URL with no entry is a 404, which is what DB-IP answers for a month
    whose build has not gone up, and is the case the second URL exists for.
    The real server was checked by hand and answers a HEAD for a month that is
    there with a 200 and a Content-Length, and one that is not with a plain
    404, which is what this stands in for.
    """

    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def __call__(self, request, timeout=None):
        self.asked.append(request)
        self.timeout = timeout
        payload = self.answers.get(request.full_url)
        if payload is None:
            raise urllib.error.HTTPError(
                request.full_url, 404, "Not Found", {}, None)
        if isinstance(payload, Exception):
            raise payload
        if request.get_method() == "HEAD":
            return Headed(len(payload))
        return Fetched(payload)


def gzipped(payload):
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb") as handle:
        handle.write(payload)
    return out.getvalue()


JANUARY = time.struct_time((2026, 1, 5, 0, 0, 0, 0, 5, 0))
SEPTEMBER = time.struct_time((2026, 9, 2, 0, 0, 0, 0, 5, 0))

urls = country.download_urls(SEPTEMBER)
check("the current month's build is asked for first",
      urls[0].endswith("dbip-country-lite-2026-09.mmdb.gz"), urls[0])
check("and last month's after it",
      urls[1].endswith("dbip-country-lite-2026-08.mmdb.gz"), urls[1])
check("January falls back to December of the year before",
      country.download_urls(JANUARY)[1].endswith("2025-12.mmdb.gz"),
      country.download_urls(JANUARY)[1])
check("both are db-ip's own https address",
      all(url.startswith("https://download.db-ip.com/free/") for url in urls),
      str(urls))

FETCH_DIR = tempfile.mkdtemp()
HELD.append(FETCH_DIR)
DEST = os.path.join(FETCH_DIR, "fetched", "country.mmdb")
HELD.append(DEST)
REAL = gzipped(build(NETWORKS, kind="DBIP-Country-Lite"))

opener = Opener({urls[0]: REAL})
trouble = country.download(DEST, opener=opener, when=SEPTEMBER)
check("a fetch that works says nothing", trouble is None, repr(trouble))
check("and leaves a database where it was told to", os.path.exists(DEST))
check("and the directory it needed was made", os.path.isdir(
    os.path.dirname(DEST)))
check("and nothing is left half written",
      not os.path.exists(DEST + ".part"))
check("and the response was closed", opener.asked and True)
check("the request names this program and its version",
      opener.asked[0].get_header("User-agent") == "nettail/" + main.__version__,
      opener.asked[0].get_header("User-agent"))
check("and a timeout was asked for", opener.timeout == country.DOWNLOAD_TIMEOUT,
      repr(opener.timeout))
check("what was fetched reads as a database", country.load(DEST) is None)
check("and answers about an address in it",
      country.country_of("140.82.114.4") == "US")

# A month whose build is not up yet. The first URL is a 404 and the second is
# the newest there is, which is the ordinary state of things on the first of
# the month and the reason there are two.
# The mapping has to be let go of before the file can be replaced, on Windows
# at least, and a real run has nothing open here: it fetches because it found
# nothing to open.
country.close()
opener = Opener({urls[1]: REAL})
os.unlink(DEST)
trouble = country.download(DEST, opener=opener, when=SEPTEMBER)
check("a 404 on this month falls back to last month", trouble is None,
      repr(trouble))
check("and both were tried, in that order",
      [request.full_url for request in opener.asked] == list(urls),
      str([request.full_url for request in opener.asked]))
check("and the fallback is what landed", os.path.exists(DEST))

# Anything that is not a 404 is reported rather than retried. A 403 or a 500
# says something about the server, and asking it for a second file it is
# equally unable to serve is noise.
opener = Opener({urls[0]: urllib.error.HTTPError(
    urls[0], 503, "Service Unavailable", {}, None)})
os.unlink(DEST)
trouble = country.download(DEST, opener=opener, when=SEPTEMBER)
check("an error that is not a 404 is reported", "503" in (trouble or ""),
      repr(trouble))
check("and stops there rather than trying the month before",
      len(opener.asked) == 1, str(len(opener.asked)))
check("and nothing was written", not os.path.exists(DEST))

# A stream that is not gzip at all, which is what a captive portal or a proxy
# with an opinion hands back.
opener = Opener({urls[0]: b"<html>not what you asked for</html>"})
trouble = country.download(DEST, opener=opener, when=SEPTEMBER)
check("something that is not gzip is reported", trouble is not None,
      repr(trouble))
check("and leaves nothing behind", not os.path.exists(DEST))
check("and no part file either", not os.path.exists(DEST + ".part"))

# Gzip of something that is not a database. This is the check the move exists
# for: a file left under a name the search looks in would be found by every
# later run and refused by every one of them.
opener = Opener({urls[0]: gzipped(b"nonsense" * 200)})
trouble = country.download(DEST, opener=opener, when=SEPTEMBER)
check("gzip of something that is not a database is reported",
      "not a database" in (trouble or ""), repr(trouble))
check("and is not put where a later run would find it",
      not os.path.exists(DEST))
check("and the part file went with it", not os.path.exists(DEST + ".part"))

# ---------------------------------------------------------------------------
# The question asked before the question
# ---------------------------------------------------------------------------
#
# Nobody is asked to agree to a download that cannot happen, so a HEAD goes
# first and settles whether there is anything to offer. The real server was
# checked by hand while this was written: a month that is there answers 200
# with a Content-Length, and a month that is not answers a plain 404, which is
# what the fake above stands in for.

opener = Opener({urls[0]: REAL})
url, size, trouble = country.probe(opener=opener, when=SEPTEMBER)
check("the probe asks about the current month first", url == urls[0], str(url))
check("with a HEAD rather than by fetching the file",
      [request.get_method() for request in opener.asked] == ["HEAD"],
      str([request.get_method() for request in opener.asked]))
check("and reports the size the server named", size == len(REAL), str(size))
check("and has nothing to report going wrong", trouble is None, repr(trouble))
check("and waits a good deal less than a download would",
      opener.timeout == country.PROBE_TIMEOUT
      and country.PROBE_TIMEOUT < country.DOWNLOAD_TIMEOUT,
      repr(opener.timeout))
check("and names this program to the server it asks",
      opener.asked[0].get_header("User-agent")
      == "nettail/" + main.__version__)

opener = Opener({urls[1]: REAL})
url, size, trouble = country.probe(opener=opener, when=SEPTEMBER)
check("a month whose build is not up yet falls to the one before",
      url == urls[1], str(url))
check("and both were asked with HEAD",
      [request.get_method() for request in opener.asked] == ["HEAD", "HEAD"])

opener = Opener({})
url, size, trouble = country.probe(opener=opener, when=SEPTEMBER)
check("neither month there is nothing to offer", url is None, str(url))
check("and no size to go with it", size is None)
check("and it says what it was told", "404" in (trouble or ""), repr(trouble))

opener = Opener({urls[0]: urllib.error.HTTPError(
    urls[0], 503, "Service Unavailable", {}, None)})
url, _size, trouble = country.probe(opener=opener, when=SEPTEMBER)
check("an answer that is not a 404 stops the probe there",
      url is None and len(opener.asked) == 1, str(len(opener.asked)))
check("and is reported as it came", "503" in (trouble or ""), repr(trouble))

opener = Opener({urls[0]: urllib.error.URLError("no route to host")})
url, _size, trouble = country.probe(opener=opener, when=SEPTEMBER)
check("a machine with no way out is answered at once", url is None)
check("and told why", "no route to host" in (trouble or ""), repr(trouble))


def unmeasured(request, timeout=None):
    """A server that answers but names no length, which is allowed."""
    return Headed()


url, size, trouble = country.probe(opener=unmeasured, when=SEPTEMBER)
check("a server that names no size is still a yes", url == urls[0])
check("and the size is simply not known", size is None, str(size))

# ---------------------------------------------------------------------------
# Where a fetched file is allowed to land
# ---------------------------------------------------------------------------

home = country.search_paths(platform="posix", env={"HOME": "/home/somebody"})
check("a Unix home adds a place under it",
      home[-1] == "/home/somebody/.local/share/nettail/country.mmdb", home[-1])
check("and it goes last, so a system database still wins",
      home[:len(country.UNIX_PATHS)] == country.UNIX_PATHS)
check("XDG_DATA_HOME moves it",
      country.search_paths(platform="posix",
                           env={"HOME": "/home/somebody",
                                "XDG_DATA_HOME": "/elsewhere"})[-1]
      == "/elsewhere/nettail/country.mmdb")
check("and a machine with neither is the list it always was",
      country.search_paths(platform="posix", env={}) == country.UNIX_PATHS)

real_paths = country.search_paths
blocker = os.path.join(FETCH_DIR, "a-file-not-a-directory")
io.open(blocker, "w").close()
HELD.append(blocker)
try:
    country.search_paths = lambda *_: ()
    check("nowhere to write is answered with nothing rather than a guess",
          country.destination() is None)
    country.search_paths = lambda *_: (
        os.path.join(blocker, "country.mmdb"),
        os.path.join(FETCH_DIR, "chosen", "country.mmdb"))
    check("a path leading through a file is passed over",
          country.destination()
          == os.path.join(FETCH_DIR, "chosen", "country.mmdb"),
          repr(country.destination()))
    check("and a directory that does not exist yet is fine, since it is made",
          not os.path.isdir(os.path.join(FETCH_DIR, "chosen")))
finally:
    country.search_paths = real_paths

# ---------------------------------------------------------------------------
# The offer
# ---------------------------------------------------------------------------


class Typed(io.StringIO):
    """A stdin with somebody at it, holding whatever they typed."""

    def isatty(self):
        return True


class Probe:
    """A probe that logs rather than asking, and can be told to fail."""

    def __init__(self, trouble=None, size=...):
        self.trouble = trouble
        # Ellipsis for "say nothing", so that None stays available and means
        # what the real probe means by it: a server that named no length.
        self.size = len(REAL) if size is ... else size
        self.asked = 0

    def __call__(self):
        self.asked += 1
        if self.trouble:
            return None, None, self.trouble
        return urls[0], self.size, None


def offered(typed, fetch=None, stdin=None, probe=None):
    """Put the offer, and hand back what was printed and what came back."""
    out = FakeTTY()
    stdin = Typed(typed) if stdin is None else stdin
    note = main.offer_country_db("the old note", stream=out, stdin=stdin,
                                 fetch=fetch,
                                 probe=Probe() if probe is None else probe)
    return plain(out.getvalue()), note


class Counted:
    """A download that logs rather than fetching, and can be told to fail."""

    def __init__(self, trouble=None):
        self.trouble = trouble
        self.asked = []
        self.urls = None

    def __call__(self, where, urls=None):
        self.asked.append(where)
        self.urls = urls
        if self.trouble:
            return self.trouble
        # What a real fetch leaves behind: a database at the place asked for.
        with io.open(where, "wb") as handle:
            handle.write(build(NETWORKS, kind="DBIP-Country-Lite"))
        return None


country.close()
country.search_paths = lambda *_: (DEST,)
try:
    fetch = Counted()
    probe = Probe()
    printed, note = offered("", fetch=fetch, stdin=io.StringIO("y\n"),
                            probe=probe)
    check("a stdin that is not a terminal is never asked", printed == "",
          repr(printed))
    check("and nothing is fetched on its behalf", fetch.asked == [])
    check("and the note it already had is what it keeps", note == "the old note")
    # The important half of that: a run with nobody at it does not reach out
    # either. The terminal check is what stops the network as well as the
    # question, which is why the probe sits after it rather than before it.
    check("and nothing whatever is asked of the network", probe.asked == 0,
          str(probe.asked))

    fetch = Counted()
    printed, note = offered("y\n", fetch=fetch)
    check("a person is told where this looked", "Looked in" in printed, printed)
    check("and which licence the file comes under",
          country.DBIP_LICENCE in printed, printed)
    check("and the address it would be fetched from",
          "db-ip.com" in printed, printed)
    check("and where it would be put", DEST in printed, printed)
    check("and that no address goes anywhere either way",
          "No address goes anywhere" in printed, printed)
    check("the question defaults to no, out loud", "[y/N]" in printed, printed)
    check("yes fetches it", fetch.asked == [DEST], str(fetch.asked))
    check("and fetches the URL the probe just confirmed",
          fetch.urls == (urls[0],), str(fetch.urls))
    check("and the size the server named is what the reader was told",
          human_bytes(len(REAL)) in printed, printed)
    check("and what came back is a database", note is None, repr(note))
    check("which is the one that was fetched", country.loaded())

    for answer, what in (("n\n", "no"), ("\n", "a bare return"),
                         ("", "end of input"), ("yes please\n", "anything else")):
        country.close()
        fetch = Counted()
        printed, note = offered(answer, fetch=fetch)
        check("%s fetches nothing" % what, fetch.asked == [], str(fetch.asked))
        check("and says how to get one by hand instead",
              "--country-db" in (note or ""), repr(note))

    country.close()
    fetch = Counted()
    printed, note = offered("yes\n", fetch=fetch)
    check("a spelled out yes is a yes too", fetch.asked == [DEST])

    # A machine that cannot reach db-ip.com is not asked a question whose yes
    # was never going to work. It is told why, and told where to go and find a
    # database for itself, and the collector runs on unmarked.
    country.close()
    fetch = Counted()
    probe = Probe(trouble="no route to host")
    printed, note = offered("y\n", fetch=fetch, probe=probe)
    check("a probe that fails is still a probe that was made",
          probe.asked == 1, str(probe.asked))
    check("but nothing is asked of the reader after it",
          "[y/N]" not in printed, printed)
    check("and nothing is fetched, whatever was on stdin", fetch.asked == [])
    check("the reader is told why", "no route to host" in (note or ""),
          repr(note))
    check("and that no address will be marked",
          "no address will be marked" in (note or ""), repr(note))
    check("and where to find one themselves", country.DBIP_PAGE in (note or ""),
          repr(note))
    check("both of them, in fact", country.MAXMIND_PAGE in (note or ""),
          repr(note))
    check("and what kind of file to come back with",
          ".mmdb" in (note or ""), repr(note))
    check("and where to put it", DEST in (note or ""), repr(note))
    check("and the other way of pointing this program at it",
          "--country-db" in (note or ""), repr(note))
    check("and the run goes on without one", not country.loaded())

    # A server that answers but names no size. The offer is still made; it
    # simply says nothing about how big the file is, rather than guessing.
    country.close()
    fetch = Counted()
    printed, note = offered("n\n", probe=Probe(size=None), fetch=fetch)
    check("no size named is no size claimed", "to fetch and" not in printed,
          printed)
    check("and nothing is said to be twice a number nobody was given",
          "twice that" not in printed, printed)
    check("but where it would go is still said", DEST in printed, printed)
    check("and the offer is put all the same", "[y/N]" in printed, printed)

    country.close()
    fetch = Counted(trouble="db-ip.com: timed out")
    printed, note = offered("y\n", fetch=fetch)
    check("a fetch that fails says what went wrong",
          "timed out" in (note or ""), repr(note))
    check("and says how to get one by hand after that",
          "--country-db" in (note or ""), repr(note))
    check("and leaves the run without a database", not country.loaded())
finally:
    country.search_paths = real_paths

# Ctrl-C at the question ends the run rather than being read as an answer.


class Interrupted(io.StringIO):
    def isatty(self):
        return True

    def readline(self, *args):
        raise KeyboardInterrupt


try:
    main.ask_yes_no("well?", FakeTTY(), Interrupted())
    check("Ctrl-C at the question does not fall through as a no", False)
except SystemExit as exc:
    check("Ctrl-C at the question ends the run", exc.code == 130, repr(exc.code))

# ---------------------------------------------------------------------------
# The refresh, asked for outright
# ---------------------------------------------------------------------------
#
# `--update-country-db` is the offer's other half: the same file from the same
# publisher, fetched because somebody typed a flag rather than because they
# answered a prompt. Every guard the offer carries is about not mistaking an
# empty pipe for a yes, and none of them applies here, so what is left to pin
# is the one thing that can go quietly wrong: where it is allowed to write. A
# copy put behind the file the search already reads is a fetch that changes
# nothing at all and then says it worked.

UPDATE_DIR = tempfile.mkdtemp()
HELD.append(UPDATE_DIR)
FIRST = os.path.join(UPDATE_DIR, "first", "country.mmdb")
SECOND = os.path.join(UPDATE_DIR, "second", "country.mmdb")
os.makedirs(os.path.dirname(FIRST))
os.makedirs(os.path.dirname(SECOND))

real_destination = country.destination
try:
    country.search_paths = lambda *_: (FIRST, SECOND)
    # Every directory in a temporary tree is writable, so the real
    # `destination` would answer FIRST here and the case worth testing, a
    # database sitting above the only place this may write, could never come
    # up. Standing in for it is how the suite says "root owns the one above",
    # which is what a Unix machine means by it and what no runner can arrange
    # for itself.
    country.destination = lambda *_: SECOND

    where, trouble = country.update_target()
    check("with no database anywhere the fetch goes where this may write",
          where == SECOND and trouble is None, "%s %s" % (where, trouble))

    named = os.path.join(UPDATE_DIR, "named.mmdb")
    where, trouble = country.update_target(named)
    check("a named file is where a refresh goes, whatever the search says",
          where == named and trouble is None, "%s %s" % (where, trouble))

    with io.open(SECOND, "wb") as handle:
        handle.write(build(NETWORKS, kind="DBIP-Country-Lite"))
    where, trouble = country.update_target()
    check("a database already at that place is refreshed in place",
          where == SECOND and trouble is None, "%s %s" % (where, trouble))

    with io.open(FIRST, "wb") as handle:
        handle.write(build(NETWORKS, kind="DBIP-Country-Lite"))
    where, trouble = country.update_target()
    check("a database searched before it is refused, not shadowed",
          where is None, str(where))
    check("and the file that would have gone on being read is named",
          FIRST in (trouble or ""), repr(trouble))
    check("and so is the one that would have been written and never opened",
          SECOND in (trouble or ""), repr(trouble))
    check("and the reader is told what to do instead",
          "--country-db" in (trouble or ""), repr(trouble))

    os.unlink(FIRST)
    country.destination = lambda *_: None
    where, trouble = country.update_target()
    check("a machine with nowhere writable is refused too", where is None,
          str(where))
    check("and sent to both of the pages that publish a database",
          country.DBIP_PAGE in (trouble or "")
          and country.MAXMIND_PAGE in (trouble or ""), repr(trouble))
finally:
    country.search_paths = real_paths
    country.destination = real_destination

# And the errand itself. It answers with a status rather than a note, because
# nothing else in the run depends on how it went and a script that asked for a
# current database wants to know whether it got one.

UPDATE_DEST = os.path.join(UPDATE_DIR, "run", "country.mmdb")
HELD.append(UPDATE_DEST)
os.makedirs(os.path.dirname(UPDATE_DEST))

country.close()
try:
    country.search_paths = lambda *_: (UPDATE_DEST,)

    fetch = Counted()
    out = FakeTTY()
    status = main.update_country_db(stream=out, fetch=fetch)
    printed = plain(out.getvalue())
    check("an errand with no database anywhere fetches one", status == 0,
          str(status))
    check("into the place the next run will look", fetch.asked == [UPDATE_DEST],
          str(fetch.asked))
    check("and is left free to try either month, having asked about neither",
          fetch.urls is None, str(fetch.urls))
    check("the reader is told whose data it is and on what terms",
          country.DBIP_LICENCE in printed, printed)
    check("and that no address goes anywhere either way",
          "No address goes anywhere" in printed, printed)
    check("and what landed, credit and all", country.DBIP_CREDIT in printed,
          printed)
    check("no question is put, since typing the flag was the answer",
          "[y/N]" not in printed, printed)
    check("and nothing is said about replacing a file that was not there",
          "replacing" not in printed, printed)

    # Again, over the file the first one left. This is the case the flag
    # exists for: a database that is there and is old.
    country.close()
    fetch = Counted()
    out = FakeTTY()
    status = main.update_country_db(stream=out, fetch=fetch)
    printed = plain(out.getvalue())
    check("a second errand replaces what the first one left", status == 0,
          str(status))
    line = next((one for one in printed.splitlines()
                 if one.startswith("replacing")), "")
    check("and says so before it goes", line != "", printed)
    check("naming the file", UPDATE_DEST in line, repr(line))
    check("and whose build it was", "DBIP-Country-Lite" in line, repr(line))

    # A file somebody put there by hand from the other publisher. Swapping one
    # for the other without saying which is going is the wrong shape for a
    # feature this careful about whose terms are being agreed to.
    country.close()
    with io.open(UPDATE_DEST, "wb") as handle:
        handle.write(build(NETWORKS, kind="GeoLite2-Country"))
    fetch = Counted()
    out = FakeTTY()
    main.update_country_db(stream=out, fetch=fetch)
    line = next((one for one in plain(out.getvalue()).splitlines()
                 if one.startswith("replacing")), "")
    check("a file from the other publisher is named before it is swapped out",
          "GeoLite2-Country" in line, repr(line))

    country.close()
    fetch = Counted(trouble="db-ip.com: timed out")
    out = FakeTTY()
    status = main.update_country_db(stream=out, fetch=fetch)
    printed = plain(out.getvalue())
    check("a fetch that fails answers with a status a script can read",
          status == 1, str(status))
    check("and says what went wrong", "timed out" in printed, printed)
    check("and where to go and find one instead",
          country.DBIP_PAGE in printed, printed)

    # What lands is opened before this says it worked, the way every later run
    # will open it. A fetch that wrote something unreadable and reported
    # success would be the one failure this errand cannot afford.
    country.close()


    class Nonsense:
        """A fetch that leaves a file which is not a database."""

        def __init__(self):
            self.asked = []
            self.urls = None

        def __call__(self, where, urls=None):
            self.asked.append(where)
            with io.open(where, "wb") as handle:
                handle.write(b"nonsense" * 40)
            return None

    out = FakeTTY()
    status = main.update_country_db(stream=out, fetch=Nonsense())
    check("a fetch that lands something unreadable is not a success",
          status == 1, str(status))
    check("and says which file it could not read",
          UPDATE_DEST in plain(out.getvalue()), plain(out.getvalue()))

    country.close()
    fetch = Counted()
    out = FakeTTY()
    elsewhere = os.path.join(UPDATE_DIR, "elsewhere.mmdb")
    status = main.update_country_db(elsewhere, stream=out, fetch=fetch)
    check("a named file is fetched into rather than the searched one",
          fetch.asked == [elsewhere], str(fetch.asked))
    check("and that is a success like any other", status == 0, str(status))
finally:
    country.search_paths = real_paths
    country.close()

# The two errands cannot both be run, and argparse cannot say so for them:
# --save-config is already in a mutually exclusive group with --config, and an
# option may only be in one. Checked through a real process, which also says
# that a status this program answers with reaches the shell.

spare = os.path.join(UPDATE_DIR, "spare.conf")
proc = subprocess.run([sys.executable, *SCRIPT, "--save-config", spare,
                       "--update-country-db"],
                      capture_output=True, text=True)
check("asking for both errands at once is refused", proc.returncode == 2,
      str(proc.returncode))
check("and both are named in the refusal",
      "--save-config" in proc.stderr and "--update-country-db" in proc.stderr,
      proc.stderr)
check("and nothing was written on the way to refusing",
      not os.path.exists(spare))

check("a config file cannot hold the errand either",
      "update_country_db" not in
      [action.dest for action in main.settable(main.build_parser())],
      "update_country_db is settable from a file")


# ---------------------------------------------------------------------------
# The credit the licence asks for
# ---------------------------------------------------------------------------
#
# Owed by whoever shows the data, which is this program, and decided from what
# the file says it is rather than from how it got here: a DB-IP file somebody
# fetched by hand is under the terms one this program fetched is under.

country.load(written(build(NETWORKS, kind="DBIP-Country-Lite")))
owed = country.credit()
check("a DB-IP database asks for a credit", owed is not None)
check("which names them", owed and owed[0] == country.DBIP_CREDIT, str(owed))
check("and links to them", owed and owed[1] == country.DBIP_HOME, str(owed))
check("and the startup line carries it", country.DBIP_CREDIT in
      country.describe(), country.describe())
check("with the address in it too", country.DBIP_HOME in country.describe(),
      country.describe())

country.load(written(build(NETWORKS, kind="DBIP-City-Lite")))
check("their city build asks for the same one",
      country.credit() == (country.DBIP_CREDIT, country.DBIP_HOME))

country.load(PATH)
check("a database from anywhere else asks for nothing",
      country.credit() is None, str(country.credit()))
check("and its startup line says nothing about db-ip",
      "DB-IP" not in country.describe(), country.describe())

# The browser is the reader whose licence terms name a link, and it is handed
# the words and the address rather than writing either down. A page with
# db-ip in it would go on crediting them for a database somebody else
# published the moment --country-db pointed somewhere else.
PAGE = io.open(os.path.join(ROOT, "nettail", "web.html"),
               encoding="utf-8").read()
check("the page reads the credit off the status frame",
      "payload.credit" in PAGE)
check("the README sends a reader to the same DB-IP page the fallback does",
      country.DBIP_PAGE in README)
check("and the same MaxMind one", country.MAXMIND_PAGE in README)
check("and writes down neither the words it shows",
      country.DBIP_CREDIT not in PAGE)
check("nor the address it points them at", country.DBIP_HOME not in PAGE)

# ---------------------------------------------------------------------------
# Which failure is worth offering a fetch for
# ---------------------------------------------------------------------------

country.search_paths = lambda *_: ("/nowhere/at/all/country.mmdb",)
try:
    country.load()
    check("a search that found nothing is what the offer is for",
          country.missing())
finally:
    country.search_paths = real_paths

country.load(os.path.join(tempfile.gettempdir(), "nothing-is-here.mmdb"))
check("a --country-db that names nothing is a typo, not a missing database",
      not country.missing())
country.load(written(b"nonsense" * 40))
check("and a file that was found and would not read is neither",
      not country.missing())
country.load(PATH)
check("nor is a database that loaded", not country.missing())


# The mapping has to go before the files can, on Windows at least, where an
# open mapping is a file that cannot be deleted.
country.close()
for path in HELD:
    try:
        os.unlink(path)
    except OSError:
        pass
# The fetch directory has files under it that the loop above cannot take, so
# it goes whole and last.
shutil.rmtree(FETCH_DIR, ignore_errors=True)

finish("country")
