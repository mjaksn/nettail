"""What the web interface refuses.

The checks here are the reason the feature can be shipped at all. What it
serves is a live map of who on this network talked to whom, and the control
route can turn on active probing, so every one of these is load-bearing rather
than defensive tidiness.

The `Host` rule gets the most attention because it is the one that stops DNS
rebinding, and because the obvious version of it is wrong in both of the cases
that actually arise.
"""
import argparse
import io
import json
import queue
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from harness import check, finish, plain

from nettail.cli import (
    ORIGIN_LINES,
    ORIGIN_WINDOW,
    OriginReports,
    web_origin_line,
    web_origin_note,
    wire_text,
)
from nettail.feed import Feed
from nettail.web import (
    ORIGIN_NOTICE_MAX,
    REQUEST_TIMEOUT,
    WIRE_KEPT,
    WebInterface,
    _Handler,
    content_policy,
    host_allowed,
    hosts_restricted,
    is_loopback,
    origin_allowed,
    origin_candidates,
    port_named,
    public_host,
    split_host,
    url_endpoint,
    web_host_arg,
    web_token_arg,
    web_url_arg,
)

# -- the Host rule, on its own -------------------------------------------

check("a loopback bind answers to its own address",
      host_allowed("127.0.0.1:2056", "127.0.0.1", 2056) is True)
check("and to localhost, which is what people type",
      host_allowed("localhost:2056", "127.0.0.1", 2056) is True)
check("case does not matter", host_allowed("LocalHost:2056", "127.0.0.1", 2056) is True)

# The whole of the rebinding attack is getting a name to resolve to this
# address. Refusing every name but localhost is what makes that pointless.
check("any other name is refused",
      host_allowed("evil.example.com:2056", "127.0.0.1", 2056) is False)
check("including one that merely contains the address",
      host_allowed("127.0.0.1.evil.example.com:2056", "127.0.0.1", 2056) is False)
check("a missing Host is refused", host_allowed("", "127.0.0.1", 2056) is False)
check("and a None one", host_allowed(None, "127.0.0.1", 2056) is False)
check("the wrong port is refused",
      host_allowed("127.0.0.1:9999", "127.0.0.1", 2056) is False)
check("a bare host against a non-default port is refused",
      host_allowed("127.0.0.1", "127.0.0.1", 2056) is False)

# Under a wildcard bind the bound address matches no Host that will ever
# arrive, which is why the comparison is against the connection's own local
# address instead. localhost is not accepted there: the connection did not
# come in on loopback.
check("a wildcard bind answers on the address the connection arrived on",
      host_allowed("192.0.2.10:2056", "192.0.2.10", 2056) is True)
check("and does not answer to localhost off loopback",
      host_allowed("localhost:2056", "192.0.2.10", 2056) is False)

# A name given with --web-host is accepted whichever address the connection
# arrived on. It cannot be rebound into: the attack needs a name the attacker
# controls, and one the operator typed is not that. Everything not on the list
# is refused exactly as before, and the port is still checked first.
NAMES = ("z2m", "collector.lan")
check("a name given with --web-host is accepted",
      host_allowed("z2m:2056", "192.0.2.10", 2056, NAMES) is True)
check("whichever address the connection arrived on",
      host_allowed("z2m:2056", "127.0.0.1", 2056, NAMES) is True)
check("in whatever case the browser writes it",
      host_allowed("Z2M:2056", "192.0.2.10", 2056, NAMES) is True)
check("and so is the second name",
      host_allowed("collector.lan:2056", "192.0.2.10", 2056, NAMES) is True)
check("a name not on the list is refused as before",
      host_allowed("other.lan:2056", "192.0.2.10", 2056, NAMES) is False)
check("a listed name on the wrong port is refused",
      host_allowed("z2m:9999", "192.0.2.10", 2056, NAMES) is False)
check("and a listed name does not let localhost in off loopback",
      host_allowed("localhost:2056", "192.0.2.10", 2056, NAMES) is False)
check("an IPv6 name meets a bracketed header",
      host_allowed("[fd00::1]:2056", "192.0.2.10", 2056, ("fd00::1",)) is True)

# Under a routable bind with no names, names are not compared at all: the LAN
# reaches the view directly and the token is its guard, which is the rule
# Jupyter, Syncthing and Ollama each settled on. Loopback is what rebinding
# is about, so it stays restricted, and names narrow a routable bind rather
# than widening it. The decision is made from the bound address, once.
check("loopback with no names is restricted",
      hosts_restricted("127.0.0.1", ()) is True)
check("loopback with names is restricted",
      hosts_restricted("127.0.0.1", ("z2m",)) is True)
check("a wildcard bind with no names is open",
      hosts_restricted("0.0.0.0", ()) is False)
check("so is a routable address with none",
      hosts_restricted("192.0.2.10", ()) is False)
check("and names narrow a wildcard bind",
      hosts_restricted("0.0.0.0", ("z2m",)) is True)

# Open, the port is still what it was and every name passes it.
check("open, any name is accepted",
      host_allowed("anything.example:2056", "192.0.2.10", 2056, (), False)
      is True)
check("open, the wrong port is still refused",
      host_allowed("anything.example:9999", "192.0.2.10", 2056, (), False)
      is False)
check("open, a bare host on a non-default port is still refused",
      host_allowed("anything.example", "192.0.2.10", 2056, (), False) is False)
check("open, a missing Host is still refused",
      host_allowed("", "192.0.2.10", 2056, (), False) is False)

# Open, the shape is all that is checked, so the shape check has to be a real
# one rather than a port comparison that a suffix which is not digits slips
# past. None of these is something a browser sends.
for header, why in (("foo:bar", "a suffix that is not a port"),
                    ("a:2056:x", "a second colon"),
                    ("fd00::1:2056", "an unbracketed IPv6 literal"),
                    ("[fd00::1]", "a bracketed literal with no port"),
                    ("[fd00::1]:9999", "a bracketed literal on the wrong port"),
                    ("[fd00::1", "an unclosed bracket"),
                    ("[]:2056", "empty brackets"),
                    (":2056", "no name at all")):
    check("open, %s is refused" % why,
          host_allowed(header, "192.0.2.10", 2056, (), False) is False, header)
check("open, a bracketed literal with the right port is accepted",
      host_allowed("[fd00::1]:2056", "192.0.2.10", 2056, (), False) is True)
check("and so is one bare on port 80",
      host_allowed("[fd00::1]", "192.0.2.10", 80, (), False) is True)

# The same shapes were slipping into the name comparison before, where they
# happened to fail, except the bracketed literal with no port, which matched
# a bound address of the same literal on any port at all.
check("restricted, a bracketed literal with no port is refused off port 80",
      host_allowed("[::1]", "::1", 2056) is False)
check("and accepted on port 80", host_allowed("[::1]", "::1", 80) is True)
check("split_host hands back the literal without its brackets",
      split_host("[fd00::1]:2056", 2056) == "fd00::1")
check("and a name without its port", split_host("Z2M:2056", 2056) == "Z2M")

# -- the Origin rule, on its own -----------------------------------------

check("an origin naming this server is allowed",
      origin_allowed("http://127.0.0.1:2056", "127.0.0.1", 2056) is True)
check("and one naming it as localhost",
      origin_allowed("http://localhost:2056", "127.0.0.1", 2056) is True)
check("another origin is refused",
      origin_allowed("http://evil.example.com", "127.0.0.1", 2056) is False)
check("so is the right host on another port",
      origin_allowed("http://127.0.0.1:9999", "127.0.0.1", 2056) is False)
check("and https, which this server does not speak",
      origin_allowed("https://127.0.0.1:2056", "127.0.0.1", 2056) is False)
check("a missing origin is refused here and allowed by the caller",
      origin_allowed("", "127.0.0.1", 2056) is False)

# A browser leaves the port out of an Origin when it is the default for the
# scheme, so a collector on port 80 saw an Origin matching neither candidate
# and refused every key press it was sent.
check("on port 80 a browser omits the port, and is still allowed",
      origin_allowed("http://127.0.0.1", "127.0.0.1", 80) is True)
check("as localhost too", origin_allowed("http://localhost", "127.0.0.1", 80) is True)
check("the explicit port still works on 80",
      origin_allowed("http://127.0.0.1:80", "127.0.0.1", 80) is True)
check("and a portless origin is refused anywhere else",
      origin_allowed("http://127.0.0.1", "127.0.0.1", 2056) is False)

# The same raise-rather-than-return trap as the path token.
check("a non-ascii origin is refused rather than raised",
      origin_allowed("http://caf\xe9.example.com", "127.0.0.1", 2056) is False)

# The names are origins too, or a browser that reached the page by name could
# open it and press nothing.
check("an origin naming a --web-host name is allowed",
      origin_allowed("http://z2m:2056", "192.0.2.10", 2056, NAMES) is True)
check("and refused on another port",
      origin_allowed("http://z2m:9999", "192.0.2.10", 2056, NAMES) is False)
check("an unlisted name is still refused",
      origin_allowed("http://other.lan:2056", "192.0.2.10", 2056, NAMES)
      is False)
check("on port 80 the portless form of a name is allowed too",
      origin_allowed("http://z2m", "192.0.2.10", 80, NAMES) is True)
check("an IPv6 name is bracketed the way a browser writes it",
      origin_allowed("http://[fd00::1]:2056", "192.0.2.10", 2056, ("fd00::1",))
      is True)

# In the open case the origin has to be the name the request itself carried,
# so a page opened by any name can press keys and any other origin cannot.
ANY = "anything.example:2056"
check("open, an origin matching the Host is allowed",
      origin_allowed("http://anything.example:2056", "192.0.2.10", 2056, (),
                     ANY) is True)
check("and one naming another host is refused",
      origin_allowed("http://other.example:2056", "192.0.2.10", 2056, (),
                     ANY) is False)
check("the listed names do not apply in the open case",
      origin_allowed("http://z2m:2056", "192.0.2.10", 2056, NAMES, ANY)
      is False)
check("nor does the arrival address",
      origin_allowed("http://192.0.2.10:2056", "192.0.2.10", 2056, (), ANY)
      is False)
check("on port 80 a portless origin matches a portless Host",
      origin_allowed("http://anything.example", "192.0.2.10", 80, (),
                     "anything.example") is True)
check("and a Host that spells the port out",
      origin_allowed("http://anything.example", "192.0.2.10", 80, (),
                     "anything.example:80") is True)
check("a non-ascii Host is refused rather than raised",
      origin_allowed("http://caf\xe9.example:2056", "192.0.2.10", 2056, (),
                     "caf\xe9.example:2056") is False)

# -- the token given on the command line ---------------------------------
#
# Both bad shapes fail silently, which is why they are refused when the flag is
# read rather than left to be found out. A token with a slash is taken apart by
# the routing, so the URL the collector prints answers 404 for ever. A non-ascii
# one makes compare_digest raise on every request instead of returning False, so
# the server answers nothing at all. Either way the flag quietly does the
# opposite of what it exists for.

check("an ordinary token is accepted",
      web_token_arg("a-good-token") == "a-good-token")
check("and is stripped of stray space", web_token_arg("  tok  ") == "tok")
for bad, why in (("", "empty"), ("   ", "only space"),
                 ("has/a/slash", "a slash"), ("q?uery", "a query mark"),
                 ("caf\xe9", "non-ascii"), ("a b", "an inner space"),
                 ("with#hash", "a fragment mark")):
    try:
        web_token_arg(bad)
        refused = False
    except argparse.ArgumentTypeError:
        refused = True
    check("a token is refused for %s" % why, refused, repr(bad))

# -- the name given on the command line ------------------------------------
#
# Stored lowercased, without brackets and without a port, because that is the
# form the Host header is reduced to before the comparison. A port is the thing
# people will include, since the URL they are copying has one, and stored with
# it the name would match nothing for ever; so it is refused with the reason.

check("a name is accepted lowercased", web_host_arg(" Z2M ") == "z2m")
check("a bracketed IPv6 address loses its brackets",
      web_host_arg("[fd00::1]") == "fd00::1")
check("and a bare one is accepted as it is",
      web_host_arg("fd00::1") == "fd00::1")
for bad, why in (("", "empty"), ("  ", "only space"),
                 ("z2m:2056", "a port"), ("caf\xe9.lan", "non-ascii"),
                 ("z2m/view", "a slash"), ("a b", "an inner space"),
                 ("user@z2m", "a userinfo mark"), ("*", "a wildcard"),
                 ("*.lan", "a pattern")):
    try:
        web_host_arg(bad)
        refused = False
    except argparse.ArgumentTypeError:
        refused = True
    check("a name is refused for %s" % why, refused, repr(bad))

# The refusal of a wildcard has to say where any-name lives, or the next
# thing tried is a pattern that happens to pass.
try:
    web_host_arg("*")
    reason = ""
except argparse.ArgumentTypeError as exc:
    reason = str(exc)
check("and a wildcard is pointed at --web-bind", "--web-bind" in reason, reason)

# The flag is wired to the parser: it is in the help, and a bad value stops
# the program before it binds anything, with the reason on stderr.
helped = subprocess.run([sys.executable, "-m", "nettail", "--help"],
                        capture_output=True, text=True)
check("--web-host is in the help", "--web-host" in helped.stdout)
bad_flag = subprocess.run([sys.executable, "-m", "nettail", "--web-host",
                           "z2m:2056"], capture_output=True, text=True)
check("a --web-host with a port stops the program at parse",
      bad_flag.returncode == 2, "exit %d" % bad_flag.returncode)
check("and says which flag the port belongs to",
      "--web-port" in bad_flag.stderr, bad_flag.stderr[-200:])

# -- the address a proxy is reached at ------------------------------------
#
# --web-url is stored as an origin, written the way a browser writes one,
# because the Origin check compares it byte for byte: lowercased, the default
# port dropped, an IPv6 literal bracketed, and no trailing slash.

for given, stored in (("https://Flows.Example.com", "https://flows.example.com"),
                      ("https://flows.example.com/", "https://flows.example.com"),
                      ("HTTPS://flows.example.com:443", "https://flows.example.com"),
                      ("http://flows.example.com:80/", "http://flows.example.com"),
                      ("https://flows.example.com:8443",
                       "https://flows.example.com:8443"),
                      ("http://flows.lan:443", "http://flows.lan:443"),
                      ("https://[FD00::1]:8443", "https://[fd00::1]:8443"),
                      (" https://flows.example.com ", "https://flows.example.com")):
    check("a web url %r is stored as %r" % (given, stored),
          web_url_arg(given) == stored, repr(web_url_arg(given)))
for bad, why in (("", "empty"), ("flows.example.com", "no scheme"),
                 ("ftp://flows.example.com", "another scheme"),
                 ("https://flows.example.com/nettail", "a path"),
                 ("https://flows.example.com/?a=1", "a query"),
                 ("https://flows.example.com/#top", "a fragment"),
                 ("https://flows.example.com?", "an empty query"),
                 ("https://user@flows.example.com", "a user name"),
                 ("https://u:p@flows.example.com", "a password"),
                 ("https://flows.example.com:zz", "a port that is not a number"),
                 ("https://flows.example.com:0", "port zero"),
                 ("https://flows.example.com:70000", "a port out of range"),
                 ("https://", "no host"), ("https://*.example.com", "a pattern"),
                 ("https://caf\xe9.example.com", "non-ascii")):
    try:
        web_url_arg(bad)
        refused = False
    except argparse.ArgumentTypeError:
        refused = True
    check("a web url is refused for %s" % why, refused, "%a" % bad)

# An address literal is stored the way a browser serialises it, since the
# Origin check compares byte for byte; a numeric host in any other spelling a
# browser would read as an address, and a mapped IPv4 one, are refused rather
# than guessed at; and so are characters a browser would not keep in a host.
check("an uncompressed IPv6 literal is stored compressed",
      web_url_arg("https://[2001:DB8:0:0::1]") == "https://[2001:db8::1]")
check("and a padded one too",
      web_url_arg("https://[fd00:0:0:0:0:0:0:5]:8443") == "https://[fd00::5]:8443")
check("a plain IPv4 literal is kept",
      web_url_arg("https://192.0.2.10") == "https://192.0.2.10")
check("a name ending in letters is not taken for an address",
      web_url_arg("https://flows.example") == "https://flows.example")
for bad, why in (("https://127.000.0.1", "a zero-padded IPv4 address"),
                 ("https://0x7f.0.0.1", "a hex IPv4 address"),
                 ("https://1234", "a bare number"),
                 ("https://[::ffff:192.0.2.10]", "a mapped IPv4 address"),
                 ("https://flows.example.com" + chr(92) + "x", "a backslash"),
                 ("https://a|b.example.com", "a pipe"),
                 ("https://a<b.example.com", "an angle bracket"),
                 ("https://flows.example.com" + chr(127), "a DEL")):
    try:
        web_url_arg(bad)
        refused = False
    except argparse.ArgumentTypeError:
        refused = True
    check("a web url is refused for %s" % why, refused, "%a" % bad)

# A wildcard is refused in words about a URL rather than in --web-host's,
# which talk about --web-bind and would send the reader to the wrong flag.
try:
    web_url_arg("https://*.example.com")
    pattern_reason = ""
except argparse.ArgumentTypeError as exc:
    pattern_reason = str(exc)
check("a wildcard web url is refused as a pattern",
      "not a pattern" in pattern_reason, pattern_reason)
check("in words about a web url", pattern_reason.startswith("a web url"),
      pattern_reason)
check("and not in --web-host's, which point at --web-bind",
      "--web-bind" not in pattern_reason, pattern_reason)

# The browser leaves the port out of Host when it is the proxy's default, and
# a proxy passes the name on bare (nginx's $host) or as it found it. Both are
# taken for the name --web-url gave; nothing else new is.
PUBLIC = url_endpoint("https://flows.example.com")
check("the endpoint of a portless https url is port 443",
      PUBLIC == ("flows.example.com", 443), repr(PUBLIC))
check("a bare Host naming the proxy's name is taken",
      public_host("flows.example.com", PUBLIC) is True)
check("and in any case", public_host("Flows.Example.COM", PUBLIC) is True)
check("and with the default port written out",
      public_host("flows.example.com:443", PUBLIC) is True)
check("but not with some other port",
      public_host("flows.example.com:2056", PUBLIC) is False)
check("nor under another name", public_host("other.example.com", PUBLIC) is False)
check("nor a name that merely starts with it",
      public_host("flows.example.com.evil.example", PUBLIC) is False)
check("nor with no --web-url at all",
      public_host("flows.example.com", None) is False)
check("nor with no Host at all", public_host(None, PUBLIC) is False)
ODD = url_endpoint("https://flows.example.com:8443")
check("a written port is the one meant", ODD == ("flows.example.com", 8443))
check("and a Host carrying it is taken",
      public_host("flows.example.com:8443", ODD) is True)
check("as is the bare name a proxy reduced it to",
      public_host("flows.example.com", ODD) is True)
V6 = url_endpoint("https://[fd00::1]:8443")
check("an IPv6 literal is taken bracketed",
      public_host("[fd00::1]:8443", V6) is True)
check("and is not taken unbracketed", public_host("fd00::1", V6) is False)

# The Origin is the browser's, and the browser opened the proxy's address, so
# the origin --web-url gave is the one https origin taken, and only exactly.
ORIGIN = "https://flows.example.com"
check("the proxy's origin is taken",
      origin_allowed(ORIGIN, "127.0.0.1", 2056, (), None, ORIGIN) is True)
check("and in the open case too",
      origin_allowed(ORIGIN, "192.0.2.10", 2056, (), ANY, ORIGIN) is True)
check("but not over plain http",
      origin_allowed("http://flows.example.com", "127.0.0.1", 2056, (), None,
                     ORIGIN) is False)
check("nor with a port written in",
      origin_allowed("https://flows.example.com:2056", "127.0.0.1", 2056, (),
                     None, ORIGIN) is False)
check("nor from another site",
      origin_allowed("https://evil.example.com", "127.0.0.1", 2056, (), None,
                     ORIGIN) is False)
check("and an https origin is still refused with no --web-url",
      origin_allowed(ORIGIN, "127.0.0.1", 2056, ("flows.example.com",))
      is False)

# The note explaining a refusal lists origin_candidates, so every one of them
# has to be an origin the check takes, or the note would advise something
# that is then refused as well.
for args in (("127.0.0.1", 2056, (), None, None),
             ("192.0.2.10", 2056, NAMES, None, ORIGIN),
             ("192.0.2.10", 80, NAMES, None, None),
             ("192.0.2.10", 2056, (), ANY, ORIGIN),
             ("192.0.2.10", 80, (), "anything.example:80", None)):
    listed = origin_candidates(*args)
    check("every origin listed for %r is taken" % (args,),
          listed and all(origin_allowed(o, *args) for o in listed), repr(listed))
check("a non-ascii Host lists no origin of its own",
      origin_candidates("192.0.2.10", 2056, (), "café:2056", None) == [])

# What the note does with text off the wire. A header is whatever whoever
# reached the port sent, so nothing in it may reach the terminal as a control
# sequence, and its length is bounded.
check("printable ascii is shown as sent",
      wire_text("http://ha-sup:2056") == "http://ha-sup:2056")
check("an escape is not", "" not in wire_text("http://a]0;xb"))
check("nor anything past ascii", wire_text("http://café") == "http://caf?")
check("a long header is cut short", len(wire_text("a" * 1000)) < 130)
check("a missing Host is said to be missing", wire_text(None) == "(none)")

note = web_origin_note("http://ha-sup:2056", "nettail.example.com:2056",
                       ["http://nettail.example.com:2056"], "key")
check("the note names the origin sent and the one taken",
      "http://ha-sup:2056" in note
      and "It takes http://nettail.example.com:2056." in note, note)
check("and, in the open case, the Host they had to agree with",
      "which was nettail.example.com:2056" in note, note)
check("and points at --web-url when none was given", "--web-url" in note)
check("and the route the page asked on", "to /key was refused" in note, note)
check("and says how later refusals are reported",
      "Later refusals are reported in one line each, up to %d every %g "
      "seconds" % (ORIGIN_LINES, ORIGIN_WINDOW) in note, note)
behind_note = plain(web_origin_note(
    "https://flows.example.com", None, ["http://flows.example.com"], "key",
    public="http://flows.example.com"))
check("behind --web-url the note says the flag may not be the address opened",
      "--web-url (http://flows.example.com) is not the address the browser "
      "opened" in behind_note, behind_note)

# A header may be most of 64 KB. Only the start of it is printed, and only the
# start is cleaned, so a long one costs what a short one does.
long_origin = "http://" + "a" * 65000
check("a long header is cut before it is printed",
      wire_text(long_origin) == long_origin[:120] + "...")
check("and one exactly at the limit is not marked as cut",
      wire_text("b" * 120) == "b" * 120)

# How refusals reach stderr. A watcher given a --web-readonly URL holds the
# token, and every request they send with a foreign Origin is refused, so the
# lines printed are limited over time and the rest counted. Driven with a
# clock that only moves when told to.
class _Clock:
    now = 100.0

    def __call__(self):
        return self.now


def _refusal(i):
    return ("http://flood%d" % i, None, ["http://localhost:2056"], "key")


clock = _Clock()
sink = io.StringIO()
reports = OriginReports(clock=clock, out=sink)
reports.report([_refusal(i) for i in range(40)], 7)
said = plain(sink.getvalue())
check("the first refusal of a flood is explained in full",
      said.count("was refused with 403") == 1, said[:300])
check("and a few after it get a line each",
      said.count("refused origin ") == ORIGIN_LINES,
      str(said.count("refused origin ")))
check("and nothing is said yet about the rest", "more refused" not in said)
clock.now += ORIGIN_WINDOW
reports.report([], 0)
said = plain(sink.getvalue())
held = 40 - 1 - ORIGIN_LINES + 7
check("once the stretch ends the rest are counted in one line",
      "and %d more refused for their origin" % held in said, said[-200:])
reports.report([_refusal(99)], 0)
said = plain(sink.getvalue())
check("and the next stretch shows lines again",
      "refused origin http://flood99 on /key (403)" in said)
check("without explaining a second time",
      said.count("was refused with 403") == 1)
reports.report([_refusal(i) for i in range(ORIGIN_LINES + 3)], 0)
reports.flush()
said = plain(sink.getvalue())
check("flush reports what the stretch held back",
      said.rstrip().endswith("and 4 more refused for their origin, counted "
                             "rather than shown one by one"), said[-200:])
quiet = io.StringIO()
OriginReports(clock=clock, out=quiet).flush()
check("and says nothing when nothing was held back", quiet.getvalue() == "")
lines = OriginReports(clock=clock, out=io.StringIO())
for _second in range(60):
    clock.now += 1.0
    lines.report([_refusal(i) for i in range(500)], 0)
check("a minute of flooding prints a bounded number of lines",
      lines.out.getvalue().count("\n") <= 1 + (60 / ORIGIN_WINDOW + 1)
      * (ORIGIN_LINES + 1), str(lines.out.getvalue().count("\n")))
check("a later refusal is one line naming the origin and the route",
      plain(web_origin_line("http://ha-sup:2056", "detail"))
      == "refused origin http://ha-sup:2056 on /detail (403)")

# The queue between the request threads and the receive loop is bounded, and
# what does not fit is counted rather than kept or waited for.
spill = WebInterface(Feed(), queue.Queue(), set(), bind="127.0.0.1", port=2056)
for i in range(ORIGIN_NOTICE_MAX + 5):
    spill.note_origin(("http://o%d" % i, None, [], "key"))
taken, unkept = spill.take_origin_notices()
check("refusals up to the bound are kept, oldest first",
      len(taken) == ORIGIN_NOTICE_MAX and taken[0][0] == "http://o0",
      str(len(taken)))
check("and the rest are counted", unkept == 5, str(unkept))
check("and taking them empties both",
      spill.take_origin_notices() == ([], 0))
restricted_note = web_origin_note("https://evil.example.com", None,
                                  ["https://flows.example.com"], "detail",
                                  public="https://flows.example.com")
check("a restricted view's note names no Host",
      "Host, which was" not in restricted_note, restricted_note)
check("and one already behind --web-url does not suggest it again",
      "pass --web-url" not in restricted_note, restricted_note)
hostile = web_origin_note("http://x[2J", "y[H:2056", ["http://z"],
                          "key") + web_origin_line("http://w[2J", "key")
# The note's own colour is SGR and is taken out first; what is left must hold
# no escape and no bell, since every one of those came from the request.
bare = plain(hostile)
check("nothing from the request reaches the terminal as a control",
      "" not in bare and "" not in bare, "%a" % bare)

# Naming a proxy narrows a routable bind as --web-host does, so a view behind
# one answers to its own address and that name rather than to anything.
proxied = WebInterface(Feed(), queue.Queue(), set(), bind="0.0.0.0", port=2056,
                       public="https://Flows.Example.com/")
check("a routable bind behind a named proxy is restricted",
      proxied.restricted is True)
check("the proxy's name is among the view's names",
      "flows.example.com" in proxied.hosts, repr(proxied.hosts))
check("an origin given directly is reduced as the flag reduces it",
      proxied.public == "https://flows.example.com", repr(proxied.public))
spaced = WebInterface(Feed(), queue.Queue(), set(), bind="127.0.0.1",
                      port=2056, public="https://flows.example.com ")
check("and its endpoint is taken from the reduced form",
      spaced.public_endpoint == ("flows.example.com", 443),
      repr(spaced.public_endpoint))

# Naming a proxy makes an IPv6 bind compare names, and the origin it then
# takes for its own address has to be bracketed, as a browser writes it, or a
# page opened directly by that address can press no key at all.
v6 = WebInterface(Feed(), queue.Queue(), set(), bind="fd00::5", port=2056,
                  public="https://flows.example.com")
check("an IPv6 bind behind --web-url still takes its own address's origin",
      origin_allowed("http://[fd00::5]:2056", "fd00::5", 2056, v6.hosts, None,
                     v6.public) is True)
check("and never the unbracketed spelling no browser sends",
      "http://fd00::5:2056" not in origin_candidates(
          "fd00::5", 2056, v6.hosts, None, v6.public))
check("and the printed url is the proxy's, with no port of this server's",
      proxied.url == "https://flows.example.com/t/%s/" % proxied.token,
      proxied.url)

helped_url = "--web-url" in helped.stdout
check("--web-url is in the help", helped_url)
bad_url = subprocess.run([sys.executable, "-m", "nettail", "--web-url",
                          "https://flows.example.com/nettail"],
                         capture_output=True, text=True)
check("a --web-url with a path stops the program at parse",
      bad_url.returncode == 2, "exit %d" % bad_url.returncode)

# -- the address a request is judged against ------------------------------
#
# `_local_addr` reads the connection's own address so that a wildcard bind is
# still checked against something a Host header can match. Its fallback, for
# when the socket has already gone, used to name `site.bind`, which since
# binding and serving became two steps is a method. Reading it gave back a
# bound method, which matches no Host header ever sent, so the branch that
# exists to fall back gracefully refused the request instead.

_fallback = WebInterface(Feed(), queue.Queue(), set(), bind="127.0.0.1",
                         port=2056)


class _Gone:
    """A handler whose socket has been torn down under it."""

    site = _fallback

    class connection:
        @staticmethod
        def getsockname():
            raise OSError("the socket has gone")


_addr = _Handler._local_addr(_Gone())
check("the fallback address is an address", isinstance(_addr, str),
      repr(_addr))
check("and is the one that was bound", _addr == _fallback.bound_addr)
check("so a Host header can still match it",
      host_allowed("127.0.0.1:2056", _addr, 2056) is True)

check("loopback is recognised", is_loopback("127.0.0.1") is True)
check("and so is the rest of the range", is_loopback("127.53.1.9") is True)
check("a routable address is not", is_loopback("192.0.2.10") is False)
check("and neither is nonsense", is_loopback("not-an-address") is False)

# -- the content security policy -----------------------------------------

policy = content_policy("<style>body{}</style><script>void 0;</script>")
check("the policy forbids everything by default",
      "default-src 'none'" in policy)
check("and names a hash for the inline script", "script-src 'sha256-" in policy)
check("and one for the inline style", "style-src 'sha256-" in policy)
check("the stream and the control route are the only connections allowed",
      "connect-src 'self'" in policy)
check("nothing may frame the page", "frame-ancestors 'none'" in policy)
check("a page with no script gets no script permission",
      "script-src 'none'" in content_policy("<style>body{}</style>"))

# The hash has to follow the page, which is the whole point of computing it
# rather than writing one down.
check("editing the script changes the hash",
      content_policy("<script>a</script>") != content_policy("<script>b</script>"))

# -- the port a Host header names -----------------------------------------
#
# Read back only to say something useful about a refusal, never to decide one.
# What matters is that it agrees with split_host about what counts as a port,
# so that the two cannot come to disagree and have the diagnostic blame a port
# the check was perfectly happy with.

for header, expected in (("127.0.0.1:12056", 12056),
                         ("example.lan:8080", 8080),
                         ("[::1]:9999", 9999),
                         ("[::1]", None),
                         ("localhost", None),
                         ("127.0.0.1", None),
                         ("host:notaport", None),
                         ("host:²", None),
                         ("a:1:2", None),
                         ("[bad", None),
                         ("", None),
                         (None, None)):
    check("port_named(%r) is %r" % (header, expected),
          port_named(header) == expected, repr(port_named(header)))

# The superscript two above is the reason the conversion is allowed to fail
# rather than trusted: str.isdigit() is true of it and int() will not take it.
check("a port of superscript digits does not raise", port_named("h:²") is None)

# Agreement with the check itself. Where port_named reads a port that is not
# ours, split_host has to have refused the header, and where it reads ours,
# split_host has to have kept it.
for header in ("127.0.0.1:2056", "127.0.0.1:2057", "localhost:2056",
               "[::1]:2056", "[::1]:9", "host:80"):
    named = port_named(header)
    kept = split_host(header, 2056) is not None
    check("split_host and port_named agree about %r" % header,
          kept == (named == 2056), "named %r, kept %s" % (named, kept))


# -- against a real server ------------------------------------------------

bus = Feed()
keys = queue.Queue(maxsize=8)
site = WebInterface(bus, keys, {"e", "s"}, bind="127.0.0.1", port=0)
site.start()
host = "127.0.0.1:%d" % site.port
good = "http://%s/t/%s/" % (host, site.token)


def fetch(url, host_header=None, method="GET", body=None, origin=None):
    """Returns the status code, or the code of the error it raised."""
    headers = {"Host": host_header if host_header is not None else host}
    if origin is not None:
        headers["Origin"] = origin
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, method=method,
                                     headers=headers)
    try:
        return urllib.request.urlopen(request, timeout=5).status
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError:
        return 0


def raw(request_bytes):
    """Send a request byte for byte and read back its status line.

    `urllib` encodes a request line as ascii and refuses anything else, which
    is exactly the restriction an attacker does not have. Some of what has to
    be refused here cannot be expressed through a client that well behaved, so
    those go out on a bare socket.

    An empty string back means the connection closed without answering, which
    is what a handler that raised used to do.
    """
    conn = socket.create_connection(("127.0.0.1", site.port), timeout=5)
    try:
        conn.sendall(request_bytes)
        return conn.recv(128).decode("latin-1").split("\r\n")[0]
    except OSError:
        return ""
    finally:
        conn.close()


try:
    check("the page is served to a correct request", fetch(good) == 200)

    # -- the port a refused Host named ------------------------------------
    #
    # The refusal stays exactly as it was and the browser is still told
    # nothing, because telling it would tell somebody probing which half of
    # the check they had got right. What is new is that the reader at the
    # terminal is left something to go on, out of band, and this is the half
    # of that which happens on a request thread: note the port and nothing
    # else. Printing it is the receive loop's job, and test_web_keys pins it.

    site.port_notice = None
    check("a request on the right port notes nothing",
          fetch(good) == 200 and site.port_notice is None)

    other = site.port + 1
    check("one naming another port is still refused",
          fetch(good, host_header="127.0.0.1:%d" % other) == 404)
    check("and the port it named is noted for the receive loop",
          site.port_notice == other, repr(site.port_notice))

    # A wrong token on the right port is the case this must not blame the
    # port for, since the port was right and saying otherwise sends the
    # reader after the wrong thing entirely.
    site.port_notice = None
    wrong_token = "http://%s/t/%s/" % (host, "x" * len(site.token))
    check("a wrong token on the right port notes no port",
          fetch(wrong_token) == 404 and site.port_notice is None,
          repr(site.port_notice))

    # A Host with no port at all cannot have named the wrong one. It is still
    # refused, because a browser omits the port only when it is 80.
    site.port_notice = None
    check("a Host with no port is refused",
          fetch(good, host_header="127.0.0.1") == 404)
    check("and notes nothing, having named no port",
          site.port_notice is None, repr(site.port_notice))

    site.port_notice = None
    check("a Host whose port is not a number notes nothing",
          fetch(good, host_header="127.0.0.1:zzz") == 404
          and site.port_notice is None)
    site.port_notice = None

    # A wrong token and a wrong Host get the same answer on purpose. Telling
    # them apart would tell somebody probing which half they had right.
    bad_token = "http://%s/t/%s/" % (host, "x" * len(site.token))
    check("a wrong token is refused", fetch(bad_token) == 404)
    check("a missing token is refused", fetch("http://%s/" % host) == 404)
    check("a token of the wrong length is refused",
          fetch("http://%s/t/short/" % host) == 404)

    check("a forged Host is refused",
          fetch(good, host_header="evil.example.com") == 404)
    check("and one naming another port",
          fetch(good, host_header="127.0.0.1:1") == 404)
    check("localhost is accepted",
          fetch(good, host_header="localhost:%d" % site.port) == 200)

    # There is no route from a request to the filesystem, so this is a 404 for
    # the ordinary reason rather than because a traversal was detected.
    for probe in ("../../etc/passwd", "..%2f..%2fetc%2fpasswd", "web.html",
                  "../web.py", "events/../../"):
        check("no route reaches the filesystem: %s" % probe,
              fetch(good + probe) == 404)

    check("an unknown route under a good token is refused",
          fetch(good + "anything") == 404)

    # A byte over 127 in the path used to raise straight out of the handler:
    # compare_digest refuses a non-ascii str by raising rather than by
    # returning False, and http.server hands the handler a request line decoded
    # as latin-1, so any byte at all can reach it. The traceback went to
    # stderr, which is the one place a thread other than the receive loop must
    # never write, because the status bar and the sticky header are managing a
    # scroll region on it. No token was needed to do it, so a probe in a loop
    # could wreck the display for the rest of a run.
    #
    # What is checked is that an answer comes back at all. Some of these get a
    # 404 from the token check and some a 400 from http.server's own parsing of
    # the request line, and either is a fine way to refuse one; what must not
    # happen is the connection dying silently, which is what a handler that
    # raised did.
    for probe in (b"\xff", b"caf\xe9", b"\xc3\xa0\xc3\xa9", b"%zz", b"\x00"):
        line = raw(b"GET /t/" + probe + b"/ HTTP/1.1\r\nHost: "
                   + host.encode("ascii") + b"\r\n\r\n")
        check("a non-ascii token is answered rather than raised: %r" % probe,
              line.startswith("HTTP/1.") and (" 4" in line or " 5" in line),
              "got %r" % (line,))
        check("and it is a refusal rather than the page: %r" % probe,
              " 200 " not in line, "got %r" % (line,))
    check("the server still answers afterwards", fetch(good) == 200)

    # -- the control route ------------------------------------------------

    def press(payload, origin=None, url=None, host_header=None):
        body = json.dumps(payload).encode("utf-8")
        return fetch(url or (good + "key"), method="POST", body=body,
                     origin=origin, host_header=host_header)

    check("a key the collector answers is accepted", press({"key": "e"}) == 200)
    check("with a matching origin", press({"key": "e"}, origin="http://" + host) == 200)
    check("a foreign origin is refused",
          press({"key": "e"}, origin="http://evil.example.com") == 403)
    check("a key the collector does not answer is refused",
          press({"key": "\x1b"}) == 400)
    check("and so is one that is not a key at all", press({"key": "zzz"}) == 400)
    check("a key that is not a string is refused", press({"key": 7}) == 400)
    check("a body with no key is refused", press({"nope": 1}) == 400)
    check("an over-long value is refused",
          press({"key": "m", "value": "9" * 200}) == 400)
    check("the control route needs the token too",
          press({"key": "e"}, url="http://%s/t/nope/key" % host) == 404)
    check("and the right Host",
          press({"key": "e"}, host_header="evil.example.com") == 404)

    # The same trap as the path, on the header compared the same way.
    check("a non-ascii origin is refused rather than raised",
          press({"key": "e"}, origin="http://caf\xe9.example.com") == 403)

    # A Content-Length that is not a number is exactly as malformed as a body
    # that is not json, and used to be a traceback rather than an answer.
    line = raw(("POST /t/%s/key HTTP/1.1\r\nHost: %s\r\n"
                "Content-Length: abc\r\n\r\n" % (site.token, host)).encode())
    check("a malformed Content-Length is a bad request", "400" in line,
          repr(line))
    check("and the server still answers after one", fetch(good) == 200)

    # A refusal that happens before the body has been read has to take the
    # connection with it. The page's fetch() reuses connections, so leaving the
    # unread body on a kept-alive socket would have it parsed as the next
    # request line, and the browser's following key press would be answered
    # with nonsense produced from its own previous one.
    body = json.dumps({"key": "e"}).encode("utf-8")
    request = (b"POST /t/" + site.token.encode() + b"/key HTTP/1.1\r\n"
               b"Host: " + host.encode() + b"\r\n"
               b"Origin: http://evil.example.com\r\n"
               b"Content-Type: application/json\r\n"
               b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n"
               + body)
    conn = socket.create_connection(("127.0.0.1", site.port), timeout=5)
    try:
        conn.sendall(request)
        answer = conn.recv(4096).decode("latin-1")
        check("a refused post says so once", answer.count("HTTP/1.") == 1,
              repr(answer[:120]))
        check("and refuses it", " 403 " in answer.split("\r\n")[0])
        check("and closes rather than leaving the body to be misread",
              "Connection: close" in answer, repr(answer[:200]))
    finally:
        conn.close()
    check("the server is unbothered by it", fetch(good) == 200)

    # A body that is promised and never sent used to park the thread reading
    # it. Since that read happens before the token is checked, so that a
    # refusal can be delivered rather than reset, it parked one without any
    # credential at all. ThreadingHTTPServer caps no threads and MAX_CLIENTS
    # bounds only the stream, so a handful of these was a thread farm.
    before = threading.active_count()
    stalled = []
    try:
        for _ in range(6):
            conn = socket.create_connection(("127.0.0.1", site.port), timeout=5)
            conn.sendall(("POST /t/nonsense/key HTTP/1.1\r\nHost: %s\r\n"
                          "Content-Length: 4096\r\n\r\n" % host).encode())
            stalled.append(conn)
        check("a stalled request does take a thread",
              threading.active_count() > before,
              "%d threads" % (threading.active_count() - before))
        deadline = time.time() + REQUEST_TIMEOUT + 10
        while threading.active_count() > before and time.time() < deadline:
            time.sleep(0.2)
        check("but it is dropped rather than kept for ever",
              threading.active_count() <= before,
              "%d still up after %.0fs"
              % (threading.active_count() - before, REQUEST_TIMEOUT + 10))
    finally:
        for conn in stalled:
            conn.close()
    check("and the server still serves afterwards", fetch(good) == 200)

    # The stream is the one connection that legitimately says nothing for a
    # long time, so the request timeout has to be lifted once it starts. A
    # watcher cut off after ten seconds would be worse than no timeout.
    watcher = urllib.request.urlopen(
        urllib.request.Request("http://%s/t/%s/events" % (host, site.token),
                               headers={"Host": host}), timeout=30)
    try:
        check("a watcher outlives the request timeout",
              watcher.readline() != b"")
        time.sleep(REQUEST_TIMEOUT + 3)
        check("and is still there well after it would have fired",
              bus.clients >= 1, "%d attached" % bus.clients)
    finally:
        watcher.close()

    queued = []
    while not keys.empty():
        queued.append(keys.get_nowait())
    # Every refusal has to actually reach the client. The body is read off the
    # socket before anything is decided, because answering and closing while
    # the client is still writing hands it a reset instead of the answer: a
    # browser posting to a read-only collector, or with the stale token a
    # bookmark holds after a restart, would see a broken connection rather than
    # the refusal that explains itself.
    for name, kwargs, want in (
        ("a stale token", {"url": "http://%s/t/stale-token/key" % host}, 404),
        ("a forged Host", {"host_header": "evil.example.com"}, 404),
        ("a foreign origin", {"origin": "http://evil.example.com"}, 403),
        ("a key it does not take", {}, 400),
    ):
        payload = {"key": "zzz"} if want == 400 else {"key": "e"}
        got = press(payload, **kwargs)
        check("a refusal is delivered rather than reset: %s" % name,
              got == want, "got %r, wanted %r" % (got, want))

    check("only the accepted presses were queued",
          queued == [("e", None), ("e", None)], repr(queued))

    # -- read only --------------------------------------------------------

    quiet_bus = Feed()
    quiet = WebInterface(quiet_bus, queue.Queue(), set(), bind="127.0.0.1",
                         port=0, readonly=True, lookups=queue.Queue())
    quiet.start()
    quiet_host = "127.0.0.1:%d" % quiet.port
    quiet_url = "http://%s/t/%s/" % (quiet_host, quiet.token)
    try:
        check("a read-only collector still serves the page",
              fetch(quiet_url, host_header=quiet_host) == 200)
        body = json.dumps({"key": "e"}).encode("utf-8")
        check("but refuses the control route",
              fetch(quiet_url + "key", host_header=quiet_host, method="POST",
                    body=body) == 403)
        # And still answers questions about a flow, which is the whole reason
        # the readonly refusal moved under the key branch: asking what a flow
        # was changes nothing, and a view served for watching is still a view
        # somebody may want to read properly.
        asking = json.dumps({"ask": 1, "n": 1,
                             "ends": [None, None]}).encode("utf-8")
        check("while still answering questions about a flow",
              fetch(quiet_url + "detail", host_header=quiet_host,
                    method="POST", body=asking) == 200)
        # And still takes a tab's filter, for the same reason: it changes what
        # one browser is sent and nothing the collector is doing. A subscriber
        # is made on the feed directly, since what is checked is the route's
        # decision rather than the stream.
        watching = quiet_bus.subscribe()
        narrowing = json.dumps({"client": watching.id,
                                "term": "53"}).encode("utf-8")
        check("and still taking a tab's filter",
              fetch(quiet_url + "filter", host_header=quiet_host,
                    method="POST", body=narrowing) == 200
              and watching.filter == "53")
        # And a scroll up, which is the same kind of thing: older rows for
        # one tab, and nothing the collector is doing.
        older = json.dumps({"client": watching.id, "before": 5,
                            "ask": 1}).encode("utf-8")
        check("and still answering a scroll up",
              fetch(quiet_url + "history", host_header=quiet_host,
                    method="POST", body=older) == 200
              and quiet.lookups.get_nowait()
              == ("before", watching, 5, "53", 1))
        quiet_bus.unsubscribe(watching)
    finally:
        quiet.stop(timeout=1.0)

    # -- open to any name -------------------------------------------------
    #
    # A routable bind with no names. The suites bind loopback only, so the
    # decision is overridden on the server rather than bound for real; the
    # derivation itself is pinned above. Set after start(), because bind()
    # is where it is derived.
    open_keys = queue.Queue(maxsize=8)
    opened = WebInterface(Feed(), open_keys, {"e"}, bind="127.0.0.1", port=0)
    opened.start()
    opened.restricted = False
    any_host = "anything.example:%d" % opened.port
    opened_url = "http://127.0.0.1:%d/t/%s/" % (opened.port, opened.token)
    try:
        check("open, the page is served under any name",
              fetch(opened_url, host_header=any_host) == 200)
        check("but not under the wrong port",
              fetch(opened_url, host_header="anything.example:1") == 404)
        check("nor without a token", fetch(opened_url.replace(
            "/t/%s/" % opened.token, "/"), host_header=any_host) == 404)
        body = json.dumps({"key": "e"}).encode("utf-8")
        check("a key press from a page opened by that name is accepted",
              fetch(opened_url + "key", host_header=any_host, method="POST",
                    body=body, origin="http://" + any_host) == 200)
        check("and one from an origin that is not the Host is refused",
              fetch(opened_url + "key", host_header=any_host, method="POST",
                    body=body, origin="http://other.example:%d" % opened.port)
              == 403)
        check("so exactly one key was queued", open_keys.qsize() == 1)
    finally:
        opened.stop(timeout=1.0)

    # -- reached by name --------------------------------------------------
    #
    # The case --web-host exists for: a browser on another machine asks for
    # this one by name, and the connection's own address is not what the Host
    # header says. Only loopback is reachable from a suite, so the arrival
    # address is 127.0.0.1 throughout and the names stand in for the
    # difference.
    named_keys = queue.Queue(maxsize=8)
    named = WebInterface(Feed(), named_keys, {"e"}, bind="127.0.0.1", port=0,
                         hosts=("Z2M", "collector.lan"))
    named.start()
    named_host = "z2m:%d" % named.port
    named_url = "http://127.0.0.1:%d/t/%s/" % (named.port, named.token)
    try:
        check("a loopback bind with names stays restricted",
              named.restricted is True)
        check("the printed url carries the first name given",
              named.url.startswith("http://z2m:%d/" % named.port), named.url)
        check("the page is served under that name",
              fetch(named_url, host_header=named_host) == 200)
        check("and under the second",
              fetch(named_url, host_header="collector.lan:%d" % named.port)
              == 200)
        check("and still under its address",
              fetch(named_url, host_header="127.0.0.1:%d" % named.port) == 200)
        check("a name it was not given is refused",
              fetch(named_url, host_header="other.lan:%d" % named.port) == 404)
        body = json.dumps({"key": "e"}).encode("utf-8")
        check("a key press from a page opened by name is accepted",
              fetch(named_url + "key", host_header=named_host, method="POST",
                    body=body, origin="http://" + named_host) == 200)
        check("and one from an unlisted origin is not",
              fetch(named_url + "key", host_header=named_host, method="POST",
                    body=body, origin="http://other.lan:%d" % named.port)
              == 403)
        check("so exactly one key was queued", named_keys.qsize() == 1)
    finally:
        named.stop(timeout=1.0)

    # -- behind a proxy ---------------------------------------------------
    #
    # What a reverse proxy terminating TLS hands this server: the browser's
    # own Host, which names the proxy and usually no port, and the browser's
    # Origin, which says https. Each was refused before --web-url, the first
    # as a 404 and the second as a 403 on every key press.
    proxy_keys = queue.Queue(maxsize=8)
    behind = WebInterface(Feed(), proxy_keys, {"e"}, bind="127.0.0.1", port=0,
                          public="https://flows.example.com")
    behind.start()
    behind_url = "http://127.0.0.1:%d/t/%s/" % (behind.port, behind.token)
    try:
        check("the page is served under the proxy's bare name",
              fetch(behind_url, host_header="flows.example.com") == 200)
        check("and with its default port written in",
              fetch(behind_url, host_header="flows.example.com:443") == 200)
        check("and still to this machine directly",
              fetch(behind_url, host_header="127.0.0.1:%d" % behind.port)
              == 200)
        check("but not under some other bare name",
              fetch(behind_url, host_header="other.example.com") == 404)
        check("nor without the token",
              fetch("http://127.0.0.1:%d/t/%s/" % (behind.port, "x" * 32),
                    host_header="flows.example.com") == 404)
        body = json.dumps({"key": "e"}).encode("utf-8")
        check("a key press from the proxy's https origin is accepted",
              fetch(behind_url + "key", host_header="flows.example.com",
                    method="POST", body=body,
                    origin="https://flows.example.com") == 200)
        check("and one from another https origin is not",
              fetch(behind_url + "key", host_header="flows.example.com",
                    method="POST", body=body,
                    origin="https://evil.example.com") == 403)
        check("so exactly one key was queued behind the proxy",
              proxy_keys.qsize() == 1)

        # The refusal leaves the receive loop what it needs to explain it,
        # and an accepted request leaves nothing. Loopback is restricted, so
        # there is no Host to have been judged against.
        taken, unkept = behind.take_origin_notices()
        refused = taken[0] if len(taken) == 1 else None
        check("a refused origin is noted for the receive loop, once",
              refused is not None and refused[0] == "https://evil.example.com"
              and refused[1] is None and unkept == 0, repr((taken, unkept)))
        check("with the origins that would have been taken, the proxy's first",
              refused is not None
              and refused[2][0] == "https://flows.example.com", repr(refused))
        check("and the route it was refused on",
              refused is not None and refused[3] == "key", repr(refused))
        fetch(behind_url + "key", host_header="flows.example.com",
              method="POST", body=body, origin="https://flows.example.com")
        check("and an accepted one notes nothing",
              behind.take_origin_notices() == ([], 0))
        fetch(behind_url + "key", host_header="flows.example.com",
              method="POST", body=body, origin="https://" + "x" * 5000)
        taken, _unkept = behind.take_origin_notices()
        check("a long origin is kept only to its start",
              len(taken) == 1 and len(taken[0][0]) == WIRE_KEPT,
              repr([len(r[0]) for r in taken]))
        for _ in range(3):
            fetch(behind_url + "detail", host_header="flows.example.com",
                  method="POST", body=b"{}", origin="https://evil.example.com")
        taken, unkept = behind.take_origin_notices()
        check("every refusal is noted, not only the latest",
              [r[3] for r in taken] == ["detail"] * 3 and unkept == 0,
              repr((taken, unkept)))
    finally:
        behind.stop(timeout=1.0)

    # -- the token --------------------------------------------------------

    other = WebInterface(Feed(), queue.Queue(), set(), bind="127.0.0.1", port=0)
    check("two collectors do not share a token", other.token != site.token)
    check("a token is long enough to be worth having", len(site.token) >= 24)
    pinned = WebInterface(Feed(), queue.Queue(), set(), bind="127.0.0.1",
                          port=0, token="a-pinned-token")
    check("a pinned token is used as given", pinned.token == "a-pinned-token")
    check("and appears in the url", "/t/a-pinned-token/" in pinned.url)

    # A caller that is not the flag gets the flag's normalisation of the two
    # things that would otherwise match nothing: case, and the brackets a
    # literal is written with in a URL, which the Host reduction takes off.
    literal = WebInterface(Feed(), queue.Queue(), set(), bind="127.0.0.1",
                           port=2056, hosts=(" [FD00::1] ", "fd00::1"))
    check("a bracketed literal given directly is reduced",
          literal.hosts == ("fd00::1",), repr(literal.hosts))
    check("and bracketed once in the url", "http://[fd00::1]:2056/" in literal.url,
          literal.url)
finally:
    site.stop(timeout=1.0)

finish("web security")
