"""What country an external address sits in, and the flag it prints as.

Off unless `--country` asks for it, and silent unless there is a database to
read. No country data ships with this program and none is fetched unless a
person at the keyboard says so: what a flag says is whatever the file the
reader pointed at says, which is the only honest arrangement for a fact this
program cannot work out for itself. One request goes out before that yes, and
only one: `probe` asks db-ip.com whether there is a file to fetch at all, so
that nobody is put a question whose yes could not have been carried out. It is
announced on the line above itself for the same reason the config module
prints which file it read. An address on this network is never marked, because
the question is about the far end of a flow and a private address has no far
end to be in.

A database is a MaxMind format file, `.mmdb`. That is what both free country
databases are distributed as, DB-IP's lite build and MaxMind's GeoLite2, and
it is what a distribution's `geoipupdate` writes into `/usr/share/GeoIP`, so
a machine that already syncs one needs nothing fetched. A City database
answers the country question too and is read the same way.

A run that searches and finds nothing offers to fetch one. `probe` is what
settles whether there is an offer to make, `download` is that offer carried
out, and `find_online` is what a reader is told instead when the first of
those says no. Only DB-IP's file can be offered, for the reason set out beside
`DBIP_URL`, and only after somebody has said yes at a terminal.

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
# is not merely slower: those fourteen bytes could occur inside the data
# section of a large database, and the last occurrence there would be found in
# preference to the real one only if the real one were missing, but bounding
# the search says what is meant.
_METADATA_MAX = 128 * 1024

# Where a database is looked for when --country-db named none.
#
# Two lists, because there are two platforms and neither one's places exist on
# the other. A Windows machine has no /usr/share/GeoIP and never will, so a
# list of Unix paths alone is a --country that cannot work there at all, and
# that answers a reader who asked where it looked by naming four directories
# which could not have existed. That is what it did.
#
# The Unix places are where geoipupdate and the distribution packages put one,
# with the installer's own directory first. Windows has no convention for this
# whatever, so these are this program's own, per-user first because that is
# the one somebody can create without being an administrator. They are
# assembled with a literal backslash rather than through os.path.join, so that
# what this answers for a platform does not depend on the platform asking.
UNIX_PATHS = (
    "/etc/nettail/country.mmdb",
    "/usr/share/GeoIP/GeoLite2-Country.mmdb",
    "/var/lib/GeoIP/GeoLite2-Country.mmdb",
    "/usr/local/share/GeoIP/GeoLite2-Country.mmdb",
    "/usr/share/GeoIP/dbip-country-lite.mmdb",
    "/usr/share/GeoIP/GeoLite2-City.mmdb",
    "/var/lib/GeoIP/GeoLite2-City.mmdb",
)

WINDOWS_PATHS = (
    ("LOCALAPPDATA", "nettail\\country.mmdb"),
    ("PROGRAMDATA", "nettail\\country.mmdb"),
)


# The one place on the Unix list somebody who is not root can write.
#
# Every path above it belongs to the system, which is right for a file
# geoipupdate or a package manager keeps current and useless for a file this
# program fetches on behalf of whoever ran it. Without this a person says yes
# to the offer, the write into /etc/nettail is refused, and there would have
# been nowhere the next run looked in any case. XDG_DATA_HOME first because
# that is the variable that moves it, then the default the specification gives
# for an unset one.
#
# Last rather than first, so that a machine already syncing a database into
# /usr/share/GeoIP goes on reading the file something else keeps current. A
# fetched copy is only ever reached by a machine that had none, which is the
# only situation it is offered in.
UNIX_USER_TAIL = "nettail/country.mmdb"


def search_paths(platform=None, env=None):
    """Where a database is looked for on this machine, in order.

    The platform and the environment are arguments with the real ones as their
    default, for the reason `terminal_flags` takes them: the list a Windows
    machine gets is exactly the one a Linux runner can never see for itself.
    """
    platform = os.name if platform is None else platform
    env = os.environ if env is None else env
    if platform == "nt":
        return tuple(env[variable].rstrip("\\/") + "\\" + tail
                     for variable, tail in WINDOWS_PATHS if env.get(variable))
    data = env.get("XDG_DATA_HOME") or (
        env["HOME"].rstrip("/") + "/.local/share" if env.get("HOME") else "")
    if not data:
        return UNIX_PATHS
    return UNIX_PATHS + (data.rstrip("/") + "/" + UNIX_USER_TAIL,)


def destination(platform=None, env=None):
    """The first searched path a database could actually be written to.

    None when there is no such place, which is what a machine with nothing
    writable anywhere on its list gets. Nothing here creates a directory: this
    answers the "put one at" hint as well as the download, and a hint that made
    /etc/nettail on its way past would be doing something nobody asked for. A
    directory that does not exist counts as writable when the nearest parent
    that does exist is, since that is the one a download would make. What is
    walked past is a name with nothing at it: the walk stops at the first
    thing that exists, so a path leading through a regular file is refused
    here rather than at the makedirs that would fail on it.
    """
    for candidate in search_paths(platform, env):
        directory = os.path.dirname(candidate) or "."
        while directory and not os.path.exists(directory):
            parent = os.path.dirname(directory)
            if parent == directory:
                break
            directory = parent
        if os.path.isdir(directory) and os.access(directory, os.W_OK):
            return candidate
    return None


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

# The free database this program offers to fetch, and the terms it comes on.
#
# One publisher rather than the choice of two the prose elsewhere offers,
# because only one of the two can be fetched at all. DB-IP's lite build is a
# plain URL under the Creative Commons Attribution 4.0 licence, which asks for
# a credit and nothing else, and their terms of service put the free files
# outside their own terms and under that licence expressly. MaxMind's GeoLite2
# wants an account and a licence key before a byte moves, and obliges whoever
# holds a copy to delete it within thirty days of a newer one; neither of those
# is a thing a yes or no at a prompt can stand in for. Somebody who wants
# GeoLite2 fetches it themselves and points --country-db at it, which is what
# the declined message goes on saying.
#
# The credit is that licence's price, and this program pays it rather than
# leaving it to the reader: nothing was asked for by hand, so nobody but this
# program knows whose data is on the screen. `credit` is where that is decided.
DBIP_URL = "https://download.db-ip.com/free/dbip-country-lite-%04d-%02d.mmdb.gz"
DBIP_LICENCE = "Creative Commons Attribution 4.0"
DBIP_CREDIT = "IP Geolocation by DB-IP"
DBIP_HOME = "https://db-ip.com"

# The two pages a reader is sent to when the offer cannot be made, which is
# every run that cannot reach db-ip.com and every run with nobody to ask.
# Named here rather than written into the message, because the README lists
# the same two and `test_country` holds the two lists to each other.
DBIP_PAGE = "https://db-ip.com/db/download/ip-to-country-lite"
MAXMIND_PAGE = "https://dev.maxmind.com/geoip/geolite2-free-geolocation-data"

# What a DB-IP file's metadata calls it. Their country build says
# DBIP-Country-Lite and their city one DBIP-City-Lite, so what is matched is
# the prefix rather than either name.
DBIP_TYPE = "dbip"

# How long to wait on a fetch, and how much of it to take.
#
# Nine megabytes unpacked is what the country file actually is, so the cap is
# not a limit anybody meets. It is there because the decompressor is pointed
# at a socket rather than at a file, and a gzip stream that never ends would
# otherwise fill a disk quietly. The timeout is the one urlopen takes, which
# bounds each read rather than the transfer.
DOWNLOAD_TIMEOUT = 30
DOWNLOAD_MAX = 64 * 1024 * 1024

# And how long to wait on the question that comes before it. Shorter than the
# download's own on purpose: a HEAD is a few hundred bytes either way, and the
# machine this matters on is the one that drops the packets rather than
# refusing them, where the whole wait is spent finding out there is nobody
# there. Thirty seconds of that in front of a collector that was going to run
# perfectly well anyway is a program that looks hung at startup.
PROBE_TIMEOUT = 10


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
    are. macOS Terminal does not draw one dependably, and is the one terminal
    that says who it is in the environment, so it is taken as unable rather
    than guessed about. The Linux console and a dumb terminal have no emoji at
    all. A stream that is not a terminal is not one this program can reason
    about, and letters survive a file, a pipe and a paste into anything. A
    terminal with no TERM to speak of is in the same position.

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
        try:
            self._read_metadata()
        except BaseException:
            # A file that mapped and then made no sense, which is most of the
            # ways one goes wrong. The mapping has to be let go of before the
            # exception leaves, because on Windows a mapped file is one that
            # cannot be deleted or replaced, and the caller with the worst
            # need of both is `download`: it opens what it has just fetched
            # precisely to find out whether to throw it away.
            self._map.close()
            raise

    def _read_metadata(self):
        """Everything after the mapping, which is everything that can raise."""
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

# Whether the last load searched the usual places and came back empty handed.
#
# Only that case is worth offering a download for, and it is not the same
# question as `loaded`. A file that was found and would not read leaves no
# database in hand either, and answering that with a fetch into some other
# directory would leave the broken file still first in the search order and
# still what every later run reads. The right answer to a bad file is to say
# which file, which is what happens now.
_missing = False


def download_urls(when=None):
    """The files to try, this month's build and last month's.

    DB-IP names each build for its month and puts it up early in that month,
    so between the first and whenever it appears the current name answers 404
    and the month before it is the newest there is. Trying both is the
    difference between a fetch that works every day and one that works most
    days. The clock is an argument for the reason `search_paths` takes the
    platform: a suite cannot otherwise say what it expects.
    """
    when = time.gmtime() if when is None else when
    before = ((when.tm_year - 1, 12) if when.tm_mon == 1
              else (when.tm_year, when.tm_mon - 1))
    return (DBIP_URL % (when.tm_year, when.tm_mon), DBIP_URL % before)


def probe(opener=None, when=None):
    """Ask db-ip.com whether there is a database to fetch, without fetching.

    Hands back three things: the URL that answered, how large it says the file
    is, and what went wrong. The first is None exactly when the third is set,
    and the size is None on its own where the server named none.

    A HEAD rather than a GET, because the only two questions are whether the
    file is there and how big it is and both are answered in the headers.

    It exists so that nobody is asked to agree to a download that cannot
    happen. A reader with no route out, behind a proxy that refuses, or on a
    network that drops the request would otherwise say yes, wait, and be told
    it failed, when the useful answer was always going to be where to fetch a
    file by hand. Knowing first turns that into one line of advice.

    Both months are tried, for the reason `download` tries both: from the
    first of a month until DB-IP puts the new build up, the current name is a
    404 and the month before is the newest there is, and probing only the
    first would send everybody in that window to the fallback.
    """
    # Lazily, for the reason `download` imports lazily: this is the only part
    # of the program that speaks HTTP as a client.
    import urllib.error
    import urllib.request

    from . import __version__

    opener = urllib.request.urlopen if opener is None else opener
    trouble = "nothing to fetch"
    for url in download_urls(when):
        request = urllib.request.Request(
            url, method="HEAD",
            headers={"User-Agent": "nettail/" + __version__})
        try:
            response = opener(request, timeout=PROBE_TIMEOUT)
        except urllib.error.HTTPError as exc:
            trouble = "%s: HTTP %s" % (url, exc.code)
            if exc.code == 404:
                continue
            return None, None, trouble
        except OSError as exc:
            # No route, no name, a refused connection, a certificate that
            # would not verify, or the timeout above. All of them mean the
            # same thing to a reader: not from here, not now.
            return None, None, "%s: %s" % (
                url, getattr(exc, "reason", None) or exc)
        try:
            size = response.headers.get("Content-Length")
        finally:
            response.close()
        return url, (int(size) if size and size.isdigit() else None), None
    return None, None, trouble


def download(dest, opener=None, when=None, urls=None):
    """Fetch DB-IP's free country database to `dest`, unpacked.

    Hands back None when there is a readable database at `dest` afterwards,
    and a line saying what went wrong otherwise. Nothing here raises: a fetch
    that fails costs a run its flags and nothing else, which is exactly what
    finding no file at all costs it.

    The bytes are written beside the destination and moved into place, and
    they are opened as a database before the move. Both halves of that matter.
    What is being unpacked came off the network, and a half written or plainly
    wrong file left under a name the search looks in would be found by every
    later run and refused by every one of them, which is a worse state than
    the one this started in.

    `opener` is urlopen unless a caller says otherwise, which is how the suite
    exercises all of this without touching the network. `urls` is both months
    unless a caller says otherwise, and the offer says otherwise: `probe` has
    just been told which of the two is there, and asking for the other one
    again would be spending a round trip on a 404 already seen.
    """
    # Imported here rather than at the top of the module because this is the
    # only thing in the program that speaks HTTP as a client, and
    # urllib.request brings http.client, ssl and email in behind it. At the
    # top every run pays for that at import, for something almost no run does.
    import gzip
    import urllib.error
    import urllib.request

    from . import __version__

    opener = urllib.request.urlopen if opener is None else opener
    directory = os.path.dirname(dest) or "."
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        return "cannot make %s: %s" % (directory, exc.strerror or exc)

    part = dest + ".part"
    last = "nothing to fetch"
    for url in (download_urls(when) if urls is None else urls):
        request = urllib.request.Request(
            url, headers={"User-Agent": "nettail/" + __version__})
        try:
            response = opener(request, timeout=DOWNLOAD_TIMEOUT)
        except urllib.error.HTTPError as exc:
            last = "%s: HTTP %s" % (url, exc.code)
            if exc.code == 404:
                # The month whose build is not up yet, which is the whole
                # reason there is a second URL to try.
                continue
            return last
        except OSError as exc:
            # URLError is one of these, and so are a read that timed out and a
            # certificate that would not verify.
            return "%s: %s" % (url, getattr(exc, "reason", None) or exc)

        try:
            taken = 0
            with gzip.GzipFile(fileobj=response) as unpacked:
                with open(part, "wb") as out:
                    while True:
                        chunk = unpacked.read(65536)
                        if not chunk:
                            break
                        taken += len(chunk)
                        if taken > DOWNLOAD_MAX:
                            raise ValueError(
                                "more than %d bytes, which no country "
                                "database is" % DOWNLOAD_MAX)
                        out.write(chunk)
        except (OSError, EOFError, ValueError) as exc:
            # A truncated stream, something that was not gzip at all, a disk
            # that filled, or the cap above.
            _discard(part)
            return "%s: %s" % (url, exc)
        finally:
            response.close()

        try:
            Database(part).close()
        except (BadDatabase, OSError, IndexError, RecursionError, ValueError,
                struct.error) as exc:
            _discard(part)
            return ("%s unpacked to something that is not a database (%s)"
                    % (url, exc))
        try:
            os.replace(part, dest)
        except OSError as exc:
            _discard(part)
            return "cannot put %s in place: %s" % (dest, exc.strerror or exc)
        return None
    return last


def _discard(path):
    """Take a part file away, and say nothing if it will not go.

    A failed fetch is already being reported, and a second line about the
    scrap it left would be reporting the same thing twice. The file is not
    where anything looks in any case.
    """
    try:
        os.remove(path)
    except OSError:
        pass


def looked_in(places=None):
    """The directories a search covers, written for a line about it.

    Directories rather than files, and each named once however many files in
    it were tried, because a reader who is about to go and put a database
    somewhere wants the places rather than every file name this looked for.
    """
    places = search_paths() if places is None else places
    where = dict.fromkeys(os.path.dirname(candidate) or candidate
                          for candidate in places)
    return ", ".join(where) or ("nowhere, since this platform has no usual "
                                "place for one")


def somewhere_to_put_one():
    """The place to tell a reader to put a database they fetched themselves.

    `destination` and not the head of the search list: the head belongs to
    root on every Unix machine, and advice that fails when the reader follows
    it is worse than none.
    """
    return destination() or (search_paths() or UNIX_PATHS)[0]


def by_hand():
    """How to get a database without this program's help.

    The whole of what a run that cannot ask says, and the tail of what a
    declined offer says. Short, because in both of those cases the line it
    ends has already explained itself.
    """
    return ("DB-IP and MaxMind both publish a free one: put it at %s, or "
            "point --country-db at it wherever it is." % somewhere_to_put_one())


def find_online():
    """How to go and find a database, for a reader who has to do it alone.

    Longer than `by_hand` on purpose. That one is a tail on a line which has
    already named a way out; this is the whole of what somebody is told when
    the offer could not even be made, so it says what kind of file to come
    back with, where both of the free ones live, which of them wants an
    account, and both ways of pointing this program at what they end up with.
    """
    return ("A country database is a MaxMind format .mmdb file, and either of "
            "the free ones does: DB-IP's IP to Country Lite at %s, which needs "
            "no account, or MaxMind's GeoLite2 Country at %s, which needs a "
            "free one. Put it at %s, or point --country-db at it wherever it "
            "is." % (DBIP_PAGE, MAXMIND_PAGE, somewhere_to_put_one()))


def update_target(named=None, platform=None, env=None):
    """Where an asked-for refresh may write, or why it may not.

    A pair, and exactly one half of it is set: the path to fetch into, or the
    line saying there is no such path and what to do instead.

    A reader who named a file has named the file. That is where a refresh
    goes, whatever the search would have said, and whether it can be written
    to is the download's answer to give rather than a guess to make here.

    Otherwise the search order decides it, and the rule is that a refresh
    writes only where the next run will read. `destination` is the first
    searched path this program could write to, and every path above it on Unix
    belongs to root; so a machine whose `geoipupdate` keeps `/usr/share/GeoIP`
    current would take a fetched copy into the writable path below it and go
    on reading the old one for ever, having been told it had just been given a
    new one. That is the trap `missing` avoids one door along, and the answer
    is the same: name the file that is winning rather than put a second one
    behind it.

    The two are compared by their place in the list rather than as paths,
    which needs no rule about case, separators or symbolic links: whatever
    `destination` answers came out of `search_paths` in the first place. A
    file found below the destination is no obstacle, since the fetched one
    lands above it and is what every later run reads.

    The platform and the environment are arguments for the reason
    `search_paths` takes them, and are passed down to both halves of the
    question so that the answer cannot be drawn from two different machines.
    """
    if named:
        return named, None
    places = search_paths(platform, env)
    where = destination(platform, env)
    if where is None:
        return None, ("there is nowhere to put a country database: none of %s "
                      "can be written to. %s"
                      % (looked_in(places), find_online()))
    found = next((c for c in places if os.path.exists(c)), None)
    if found is not None and places.index(found) < places.index(where):
        return None, ("%s is searched before %s and is what every run here "
                      "reads, so a database fetched into the second would "
                      "never be looked at. Refresh that file however it is "
                      "kept, or point --country-db at the one to replace."
                      % (found, where))
    return where, None


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
    global _database, _showing, _missing
    _cache.clear()
    if _database is not None:
        _database.close()
    _database = None
    _showing = False
    _missing = False

    if path is None:
        places = search_paths()
        found = [candidate for candidate in places if os.path.exists(candidate)]
        if not found:
            _missing = True
            return ("no country database found, so no address will be "
                    "marked. Looked in %s. %s"
                    % (looked_in(places), by_hand()))
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
    global _database, _showing, _missing
    if _database is not None:
        _database.close()
    _database = None
    _showing = False
    _missing = False
    _cache.clear()


def loaded():
    """Whether there is a database to ask at all."""
    return _database is not None


def missing():
    """Whether the last load searched and found nothing to read.

    False after a load that was given a path, whether or not the file was
    there: naming a file that does not exist is a typo, and answering a typo
    by offering to fetch something else would be answering a different
    question. False too after a file was found and would not read, for the
    reason set out where `_missing` is declared.
    """
    return _missing


def showing():
    """Whether an external address is being marked with its country."""
    return _showing and _database is not None


def show(on):
    """Start or stop marking. What the g key calls."""
    global _showing
    _showing = bool(on) and _database is not None
    return _showing


def credit():
    """The attribution the database in hand asks for, or None.

    A pair of the words and the address they point at. DB-IP's free builds are
    Creative Commons Attribution 4.0, which asks whoever shows the data to say
    where it came from, and their own wording asks a web page for a link. So
    both readers are told: the startup line carries the words, and the browser
    is sent the pair and makes a link of it.

    Decided from what the file says it is rather than from how it arrived. A
    file somebody fetched by hand is under exactly the terms one this program
    fetched for them is under, and a credit that only appeared after a
    download would have the obligation the wrong way round.
    """
    if _database is None:
        return None
    if not (_database.database_type or "").lower().startswith(DBIP_TYPE):
        return None
    return DBIP_CREDIT, DBIP_HOME


def kind():
    """What the database in hand calls itself, or None.

    The name out of the file's own metadata, DBIP-Country-Lite or
    GeoLite2-Country or whatever a City build says. `credit` reads the same
    field to decide whose terms apply and answers with the terms; this answers
    with the name, which is what a line naming a file about to be replaced
    wants. A reader who put a GeoLite2 file where this program writes is owed
    the word for it before it goes.
    """
    if _database is None:
        return None
    return _database.database_type or None


def built():
    """The day the database in hand was built, as a date, or None.

    The same field `describe` puts in the startup line, on its own. What wants
    it separately is the line naming a file about to be replaced, which needs
    the date and must not have the rest of that sentence: `describe` ends an
    old file's line by saying which flag would fetch a newer one, and printing
    that to somebody who has just typed the flag would be answering a question
    they have already acted on.
    """
    if _database is None or not _database.build_epoch:
        return None
    return time.strftime("%Y-%m-%d", time.gmtime(_database.build_epoch))


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
            # And what to do about it, because a reader told their file is old
            # and left there has been given a complaint rather than a way out.
            # This is the line that names the flag: it is the one place the
            # program already knows the file is worth replacing.
            built += (", which is old enough that some of what it says has "
                      "since moved. --update-country-db fetches a current one")
    owed = credit()
    return "countries from %s%s%s" % (
        _database.path, built,
        ". %s, %s" % (owed[0], owed[1]) if owed else "")


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
