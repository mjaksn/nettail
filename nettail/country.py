"""What country an external address sits in, and the flag it prints as.

Off unless `--country` asks for it, and silent unless there is a database to
read. No country data ships with this program and none is fetched: what a flag
says is whatever the file the reader pointed at says, which is the only honest
arrangement for a fact this program cannot work out for itself. An address on
this network is never marked, because the question is about the far end of a
flow and a private address has no far end to be in.

A database is a MaxMind format file, `.mmdb`. That is what both free country
databases are distributed as, DB-IP's lite build and MaxMind's GeoLite2, and
it is what a distribution's `geoipupdate` writes into `/usr/share/GeoIP`, so
one machine in three already has a usable file on it. A City database answers
the country question too and is read the same way.

Reading one is about two hundred lines against a format that has not changed
since 2015, and the case for writing them rather than depending on
`maxminddb` is the case `qr.py` makes for its encoder. This program installs
three pure Python packages and nothing else, the suite has no dependencies,
and the container image pins every byte it fetches by hash; a dependency here
would make all three statements false at once, for a file the reader has to
supply regardless. The whole of the format is decoded rather than the part a
country database happens to use, because a reader who has a City database
already installed should be able to point at it and be answered rather than
be told to fetch a second file.

The flag itself is a pair of regional indicator letters, which is not a
picture and cannot be one: a terminal draws it as a flag if its font has that
flag and as two boxed letters if not, and no escape sequence asks which. So
this module paints the flag everywhere and `CodeStream` spells it back out as
the two ascii letters on the way to a terminal that was judged unable to draw
it. That is exactly the arrangement `PlainStream` uses for colour, and for the
same reason: the summary is rendered once and read by a terminal and a browser
together, so anything the two are shown differently has to be decided at the
boundary rather than where the text is built.

Both forms are two characters wide, on screen and to `len`, which is what lets
every column in this program go on measuring its contents the way it always
did. A marker is three characters wherever it appears, the space in front of
it included, whichever way the reader ends up seeing it.
"""

import ipaddress
import mmap
import os
import re
import struct
import time

from netflume import addr_kind

from .colour import FilterStream

# The metadata section begins after the last of these in the file.
_MARKER = b"\xab\xcd\xefMaxMind.com"

# The format caps the metadata at 128 KiB, so the marker is looked for in the
# tail of the file rather than the whole of it. A search over the whole file
# is not merely slower: those twelve bytes could occur inside the data section
# of a large database, and the last occurrence there would be found in
# preference to the real one only if the real one were missing, but bounding
# the search says what is meant.
_METADATA_MAX = 128 * 1024

# Where a database is looked for when --country-db named none. The first three
# are where geoipupdate and the distribution packages put one; the first is
# where the installer would put one on a machine running this as a service.
SEARCH_PATHS = (
    "/etc/nettail/country.mmdb",
    "/usr/share/GeoIP/GeoLite2-Country.mmdb",
    "/var/lib/GeoIP/GeoLite2-Country.mmdb",
    "/usr/local/share/GeoIP/GeoLite2-Country.mmdb",
    "/usr/share/GeoIP/dbip-country-lite.mmdb",
    "/usr/share/GeoIP/GeoLite2-City.mmdb",
    "/var/lib/GeoIP/GeoLite2-City.mmdb",
)

# Addresses whose answer is remembered. A lookup walks up to 128 nodes and
# then decodes a record that carries a continent and a country in a dozen
# languages, and a busy link asks about the same few thousand addresses over
# and over. Emptied rather than aged when it fills, which costs a run its warm
# cache occasionally and keeps this to a dictionary.
CACHE_MAX = 8192

# A database older than this is still read, and still said to be old. Country
# assignments move: a block sold between registries answers with the seller
# for as long as the file is not refreshed, and nothing about a stale answer
# looks stale on screen.
STALE_AFTER = 365 * 24 * 3600

# The first regional indicator, which stands for A. A flag is the two letters
# of the country code written in these.
_INDICATOR_A = 0x1F1E6

_INDICATORS = re.compile("[\U0001f1e6-\U0001f1ff]")


class BadDatabase(ValueError):
    """A file that is not a MaxMind database, or is one and is unreadable."""


def flag(code):
    """The two letter country code as a pair of regional indicators."""
    return "".join(chr(_INDICATOR_A + ord(letter) - ord("A")) for letter in code)


def spell_flags(text):
    """`text` with every regional indicator written back as its letter.

    Each indicator is turned back on its own rather than in pairs. Nothing
    here ever emits a lone one, so pairing would be arithmetic in aid of a
    case that cannot arise, and a lone one arriving from somewhere else is
    better spelled than left as a character the reader was judged unable to
    see.
    """
    return _INDICATORS.sub(
        lambda match: chr(ord("A") + ord(match.group()) - _INDICATOR_A), text)


class CodeStream(FilterStream):
    """A stream that spells a flag out as the two letters it is made of.

    Wrapped around stdout and stderr for a terminal that was judged unable to
    draw a flag, while the browser watching the same collector goes on being
    shown one. Everything upstream paints the flag as it always did and this
    takes it back out on the way past, which is why a second reader with a
    different answer did not mean threading a setting through every function
    that prints an address.
    """

    def transform(self, text):
        return spell_flags(text)


def terminal_flags(choice, stream, env=None, platform=None):
    """Whether this terminal is shown a flag or the two letters.

    `auto` is a guess and cannot be anything else. There is no query for "can
    you draw a flag": the sequence is two ordinary letters in a font, so a
    terminal that cannot renders them as letters in boxes and says nothing
    about it. What can be known is where a flag is certainly not drawn, and
    those are the cases below. Everything else is given the flag, since being
    shown boxed letters on a terminal that could have managed one is a worse
    default than being shown plain ones everywhere.

    Windows has no flag in any of its shipped fonts, by a deliberate decision
    that has held for a decade, so its consoles are answered no whatever they
    are. macOS Terminal draws the letters rather than the flag and names
    itself in the environment, which is as close to an answer as this gets.
    The Linux console and a dumb terminal have no emoji at all. A stream that
    is not a terminal is not one this program can reason about, and letters
    survive a file, a pipe and a paste into anything.

    An encoding that cannot carry the characters settles it before any of
    that, and is a fact rather than a guess. `main` asks for UTF-8 on both
    streams before this is called, so it is the answer for a stream that
    refused.

    The environment and the platform are arguments with the real ones as
    their default, so that every branch can be asked about from anywhere. The
    alternative is a suite that can only check the answer this machine
    happens to give, and the Windows branch is exactly the one a Linux runner
    would never reach.
    """
    if choice == "flag":
        return True
    if choice == "code":
        return False
    env = os.environ if env is None else env
    platform = os.name if platform is None else platform
    encoding = (getattr(stream, "encoding", "") or "").lower()
    if "utf" not in encoding:
        return False
    if platform == "nt":
        return False
    try:
        if not stream.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    if env.get("TERM", "") in ("", "linux", "dumb"):
        return False
    if env.get("TERM_PROGRAM", "") == "Apple_Terminal":
        return False
    return True


class Database:
    """One MaxMind format file, open and ready to be asked about an address.

    The file is memory mapped rather than read, so a nine megabyte country
    database costs a mapping and the pages a lookup actually touches, and a
    collector that is handed a City database is not paying eighty megabytes of
    resident memory for a two letter answer.
    """

    def __init__(self, path):
        self.path = path
        handle = open(path, "rb")
        try:
            self._map = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
        except (OSError, ValueError) as exc:
            raise BadDatabase(str(exc)) from exc
        finally:
            # The mapping holds its own reference to the file, so the
            # descriptor has done its job as soon as it is made.
            handle.close()

        start = self._map.rfind(
            _MARKER, max(0, len(self._map) - _METADATA_MAX - len(_MARKER)))
        if start < 0:
            raise BadDatabase("no MaxMind metadata marker in the last "
                              "128 KiB of the file")
        meta = self._decode(start + len(_MARKER), start + len(_MARKER))[0]
        if not isinstance(meta, dict):
            raise BadDatabase("the metadata is not a map")

        self.node_count = meta.get("node_count")
        self.record_size = meta.get("record_size")
        self.ip_version = meta.get("ip_version")
        self.database_type = meta.get("database_type", "")
        self.build_epoch = meta.get("build_epoch")
        if self.record_size not in (24, 28, 32):
            raise BadDatabase("record size %r, which the format does not "
                              "allow" % (self.record_size,))
        if not isinstance(self.node_count, int) or self.node_count <= 0:
            raise BadDatabase("node count %r" % (self.node_count,))
        if self.ip_version not in (4, 6):
            raise BadDatabase("ip version %r" % (self.ip_version,))

        self._node_bytes = self.record_size // 4
        # Sixteen zero bytes sit between the tree and the data, and are part
        # of neither. Every offset a record holds is counted from the start of
        # the data section, which is where they end.
        self._data = self.node_count * self._node_bytes + 16
        if self._data > len(self._map):
            raise BadDatabase("the search tree runs past the end of the file")

        # Every IPv4 lookup in an IPv6 tree begins by walking the same ninety
        # six zero bits, which is three quarters of the walk and gives the same
        # answer every time. Walked once here, so a lookup costs the thirty two
        # bits that actually differ. It can land on a record or on the empty
        # marker rather than on a node, in a database that says one thing about
        # the whole of ::/96, and `find` reads it back the same way it reads
        # any other stopping place.
        self._v4_root = 0
        if self.ip_version == 6:
            for _ in range(96):
                if self._v4_root >= self.node_count:
                    break
                self._v4_root = self._record(self._v4_root, 0)

    def close(self):
        self._map.close()

    # -- the search tree ----------------------------------------------------

    def _record(self, node, side):
        """One of a node's two records, as the number it holds.

        The 28 bit case is the only one that is not two plain big endian
        integers: a middle byte carries the top four bits of each record, the
        high nibble belonging to the left and the low nibble to the right.
        Getting those two the wrong way round produces a tree that walks
        perfectly well and answers with somebody else's country, which is why
        the suite builds a database at every one of the three widths.
        """
        at = node * self._node_bytes
        raw = self._map[at:at + self._node_bytes]
        if self.record_size == 24:
            half = raw[0:3] if side == 0 else raw[3:6]
            return int.from_bytes(half, "big")
        if self.record_size == 32:
            half = raw[0:4] if side == 0 else raw[4:8]
            return int.from_bytes(half, "big")
        if side == 0:
            return ((raw[3] >> 4) << 24) | int.from_bytes(raw[0:3], "big")
        return ((raw[3] & 0x0F) << 24) | int.from_bytes(raw[4:7], "big")

    def _start(self, addr):
        """Where a lookup begins: (node, value, bits), or None for neither.

        An IPv4 address in an IPv6 database is looked up at ::/96, and the
        ninety six zeros in front of it were walked at open, so what is left is
        the thirty two bits of the address from the node they led to. An IPv6
        address is asked of an IPv4 database not at all: there is nowhere in
        that tree for it to be.
        """
        try:
            ip = ipaddress.ip_address(str(addr))
        except ValueError:
            return None
        if ip.version == 4:
            return (0 if self.ip_version == 4 else self._v4_root), int(ip), 32
        if self.ip_version == 4:
            return None
        return 0, int(ip), 128

    def find(self, addr):
        """The record for an address, or None where the tree holds none."""
        start = self._start(addr)
        if start is None:
            return None
        node, value, bits = start
        record, count = self._record, self.node_count
        for shift in range(bits - 1, -1, -1):
            if node >= count:
                break
            node = record(node, (value >> shift) & 1)
        if node <= count:
            # The empty marker, or a walk that ran out of address while still
            # inside the tree, which is a malformed file rather than an answer.
            return None
        return self._decode(self._data + node - count - 16, self._data)[0]

    def country(self, addr):
        """The two letter code for an address, or None.

        `country` is where a database says who an address is assigned to and
        is the answer wanted here. `registered_country` is who the registry
        has it down to, which differs for a block one country's provider
        routes in another, and it is what the file offers when the first is
        absent. `represented_country` is the last resort and is how a military
        base abroad is described.
        """
        record = self.find(addr)
        if not isinstance(record, dict):
            return None
        for key in ("country", "registered_country", "represented_country"):
            section = record.get(key)
            if not isinstance(section, dict):
                continue
            code = section.get("iso_code")
            if (isinstance(code, str) and len(code) == 2
                    and code.isascii() and code.isalpha()):
                return code.upper()
        return None

    # -- the data section ---------------------------------------------------

    def _size(self, control, at):
        """A field's payload size, and where the payload starts.

        Twenty nine and above are not sizes but escapes, each adding a byte to
        the field and the whole of the range below it to the count, so the
        three of them together reach sixteen megabytes without spending two
        bytes on a country name.
        """
        size = control & 0x1F
        if size < 29:
            return size, at
        if size == 29:
            return 29 + self._map[at], at + 1
        if size == 30:
            return 285 + int.from_bytes(self._map[at:at + 2], "big"), at + 2
        return 65821 + int.from_bytes(self._map[at:at + 3], "big"), at + 3

    def _decode(self, at, base):
        """One field, as (value, where the next field starts).

        `base` is what a pointer in this field is counted from, which is the
        start of the data section everywhere except in the metadata, where it
        is the start of the metadata itself. Handing it down rather than
        holding it on the instance is what lets the metadata be read by this
        same decoder before the data section's whereabouts are known.
        """
        control = self._map[at]
        at += 1
        kind = control >> 5
        if kind == 0:
            # An extended type, whose number is in the byte that follows and
            # counted from where the three bit field ran out.
            kind = self._map[at] + 7
            at += 1

        if kind == 1:
            # A pointer spends its size field on the value instead: two bits
            # say how many bytes follow and three are the top of the number.
            # Each width starts where the one below it stopped, so no value is
            # spelled two ways.
            width = (control >> 3) & 0x03
            if width == 3:
                target = int.from_bytes(self._map[at:at + 4], "big")
                at += 4
            else:
                count = width + 1
                target = ((control & 0x07) << (8 * count)) | int.from_bytes(
                    self._map[at:at + count], "big")
                target += (0, 2048, 526336)[width]
                at += count
            # The format forbids a pointer to a pointer, so this recurses once
            # and no deeper. What comes back is the value; where the caller
            # goes next is after the pointer, not after what it pointed at.
            return self._decode(base + target, base)[0], at

        size, at = self._size(control, at)

        if kind == 2:
            return self._map[at:at + size].decode("utf-8", "replace"), at + size
        if kind == 7:
            out = {}
            for _ in range(size):
                key, at = self._decode(at, base)
                value, at = self._decode(at, base)
                out[key] = value
            return out, at
        if kind == 11:
            out = []
            for _ in range(size):
                value, at = self._decode(at, base)
                out.append(value)
            return out, at
        if kind in (5, 6, 9, 10):
            return int.from_bytes(self._map[at:at + size], "big"), at + size
        if kind == 8:
            return (int.from_bytes(self._map[at:at + size], "big", signed=True),
                    at + size)
        if kind == 14:
            # The size field is the value, and there is no payload.
            return bool(size), at
        if kind == 4:
            return bytes(self._map[at:at + size]), at + size
        if kind == 3:
            return struct.unpack(">d", self._map[at:at + 8])[0], at + 8
        if kind == 15:
            return struct.unpack(">f", self._map[at:at + 4])[0], at + 4
        if kind in (12, 13):
            # A cache container and an end marker, neither of which a reader
            # is meant to meet in a finished file. Skipped rather than raised
            # over, since one turning up says nothing about the record beside
            # it.
            return None, at + size
        raise BadDatabase("data type %d, which the format does not define"
                          % kind)


# The database in hand, and whether the display is currently marking with it.
#
# Module state rather than something carried on `args`, which is where the
# other display switches live, because three modules with no arguments in
# common ask this question: a flow row is built in `display`, the summary in
# `cli`, and the status bar's top talker in `statusbar`. It is the arrangement
# `services` uses for the same reason, and for the same reason again nothing
# is loaded until a caller asks: a run without --country behaves exactly as it
# did before this module existed.
_database = None
_showing = False
_cache = {}


def load(path=None):
    """Open a country database, replacing whatever was open before.

    Hands back a line to tell the reader, or None when the file opened and
    reads as a database. A file that is missing or malformed is not an error
    that stops anything: the collector runs perfectly well without countries
    and the whole consequence is that no address is marked, which is worth one
    line at startup and no more.

    With no path given the usual places are searched, which is what makes the
    flag useful on a machine whose `geoipupdate` already keeps a file current.
    Being told where this looked matters more than usual here, because a
    reader who asked for countries and got none has no other way to tell an
    absent file from an unreadable one.
    """
    global _database, _showing
    _cache.clear()
    if _database is not None:
        _database.close()
    _database = None
    _showing = False

    if path is None:
        found = [candidate for candidate in SEARCH_PATHS
                 if os.path.exists(candidate)]
        if not found:
            where = dict.fromkeys(os.path.dirname(candidate) or candidate
                                  for candidate in SEARCH_PATHS)
            return ("no country database found, so no address will be "
                    "marked. Looked in %s. Point --country-db at a MaxMind "
                    "format file to have one read." % ", ".join(where))
        path = found[0]

    try:
        _database = Database(path)
    except OSError as exc:
        return ("no countries from %s: %s. No address will be marked."
                % (path, exc.strerror or exc))
    except BadDatabase as exc:
        return ("no countries from %s: %s. A country database is a MaxMind "
                "format file, which is what both of the free ones are "
                "distributed as. No address will be marked." % (path, exc))
    except (IndexError, RecursionError, ValueError, struct.error) as exc:
        # A file that is the right shape and truncated, or whose metadata
        # points somewhere it should not. Nothing here trusts the file, since
        # it is neither this program's nor written by it, and refusing to
        # start over one would be the wrong answer to a thing the collector
        # can run perfectly well without.
        return ("no countries from %s: it reads as a MaxMind database and "
                "then stops making sense (%s: %s). No address will be marked."
                % (path, type(exc).__name__, exc))
    _showing = True
    return None


def close():
    """Forget the database. The state a run without --country is already in."""
    global _database, _showing
    if _database is not None:
        _database.close()
    _database = None
    _showing = False
    _cache.clear()


def loaded():
    """Whether there is a database to ask at all."""
    return _database is not None


def showing():
    """Whether an external address is being marked with its country."""
    return _showing and _database is not None


def show(on):
    """Start or stop marking. What the g key calls."""
    global _showing
    _showing = bool(on) and _database is not None
    return _showing


def describe():
    """A line naming the database in hand, for the startup notice."""
    if _database is None:
        return "no country database"
    built = ""
    if _database.build_epoch:
        age = time.time() - _database.build_epoch
        built = ", built %s" % time.strftime(
            "%Y-%m-%d", time.gmtime(_database.build_epoch))
        if age > STALE_AFTER:
            built += (", which is old enough that some of what it says has "
                      "since moved")
    return "countries from %s%s" % (_database.path, built)


def country_of(addr):
    """The two letter code for a public address, or None.

    Only a public address is asked about. A private one has no country to be
    in whatever a database says about the range it belongs to, and skipping it
    saves the walk on the majority of what a home network sees.
    """
    if _database is None or not addr:
        return None
    if addr_kind(addr) != "public":
        return None
    key = str(addr)
    if key in _cache:
        return _cache[key]
    try:
        code = _database.country(key)
    except (IndexError, RecursionError, ValueError, struct.error):
        # A truncated or corrupt file, met halfway through a run. One bad
        # record should cost its own answer rather than the collector.
        code = None
    if len(_cache) >= CACHE_MAX:
        _cache.clear()
    _cache[key] = code
    return code


def mark(addr):
    """The country marker for an address: a space and a flag, or nothing.

    Always the flag, never the letters. Which of the two a reader ends up
    seeing is decided at the stream the text is written to, so that one
    rendering of the summary can go to a terminal spelling them out and to a
    browser drawing them.
    """
    if not _showing:
        return ""
    code = country_of(addr)
    return " " + flag(code) if code else ""
