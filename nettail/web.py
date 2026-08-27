"""Serving the display to a browser, and taking keys back from one.

The half of the web interface that knows about HTTP. `feed.py` holds the events
and the queues; this turns one of those queues into a stream a browser can read
and turns a browser's key press into something the receive loop will act on.

Nothing here touches collector state. A request thread may read from a feed
queue and it may put a key on a queue, and that is the whole of its authority.
Everything that changes what the collector is doing happens on the receive
thread, which drains that key queue between datagrams, so the existing dispatch
in `Controls` stays the one place a key means anything.

Nothing here prints, either, and that rule is stricter than it sounds.
`sticky.py` and `statusbar.py` manage a scroll region on the terminal, and a
line written from another thread lands inside it and corrupts both. That is why
`log_message` below is silenced rather than merely quietened: the default
implementation of `BaseHTTPRequestHandler` writes a line to stderr per request,
which on a terminal running the status bar would be a mess appearing at random.

## What stops this being a hole in the machine

The data here is a live map of who on this network talked to whom, with the
names of local machines attached, and after the control route it can also turn
on active mDNS and NetBIOS probing. So the defaults are closed and the checks
are not optional.

- **Loopback unless asked otherwise.** `--web-bind` is a separate flag from
  `--web`, and anything that is not a loopback address is warned about in as
  many words, cleartext included.
- **A token in the path.** Not a cookie and not a query string. Nothing about
  the request carries ambient authority, so a page on some other origin has
  nothing to forge a request with, and the page loads no external resource so
  there is no referer header to leak the path through.
- **A `Host` check on every request.** This is what stops DNS rebinding, which
  is the attack that matters against anything listening on loopback. See
  `host_allowed` for why comparing against the bound address is not enough.
- **An `Origin` check on the control route**, refusing a request that names an
  origin other than this one.
- **Three routes and no fourth.** No directory listing, no file handler,
  nothing that turns part of a request into a path on disk. The page is read
  once at startup and lives in memory.
- **A cap on connected browsers.** Each stream holds a thread for as long as
  the tab is open, so the count is bounded rather than left to whoever is
  opening tabs. That cap covers the stream only, which is why there is also
  a timeout on reading a request: without one, a connection that promises a
  body and never sends it parks a thread the cap knows nothing about.
"""

import argparse
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import queue
import re
import secrets
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Echoes the collector's own 2055, which is the whole argument for it: somebody
# who remembers the port flows arrive on can guess the port they are served on.
DEFAULT_WEB_PORT = 2056

# How many browsers may watch at once. Each one holds a thread for as long as
# its tab is open, so this is a thread count as much as an audience. Four is
# generous for a thing meant to be watched by the person who started it, and
# the point of the cap is that a probe cannot turn a tab into a thread farm.
MAX_CLIENTS = 4

# Seconds between comment frames on an idle stream. Without them a stream that
# has nothing to say is indistinguishable from one whose far end has gone away,
# and neither the browser nor anything between the two would notice for hours.
HEARTBEAT = 15.0

# How long a watcher waits before looking around. Short, because this is
# also how quickly a closed tab gives its place back, and a cap that stays
# full of connections nobody is on the other end of is worse than no cap.
POLL = 1.0

# Key presses allowed to queue up before the receive loop gets round to them.
# Reached only by a browser sending faster than the loop turns, which is a
# script rather than a person, so the limit exists to bound memory rather than
# to serve anybody.
KEY_QUEUE_MAX = 64

# How long a request may spend reading a body. A control message is a few dozen
# bytes; anything slower than this is not a browser being slow.
BODY_MAX = 4096

# How long a connection may take to say what it wants before it is dropped.
#
# Bounding the body by size is not enough: a client that promises four kilobytes
# and then sends nothing parks the thread reading it, and since that read now
# happens before the token is checked, so that a refusal can be delivered rather
# than reset, it parks it without any credential at all. ThreadingHTTPServer
# starts a thread per connection and caps nothing, and MAX_CLIENTS bounds only
# the stream, so a handful of those connections would be a thread farm.
#
# Generous, because on loopback a real request is finished in a millisecond and
# this only ever has to be longer than a network hiccup. It covers the request
# line and headers too, which http.server reads with no limit of its own.
REQUEST_TIMEOUT = 10.0

# How much room the endpoint columns get when the cells are bound for a
# browser. The terminal's forty is what fits beside everything else on an
# eighty column window; a page has no such constraint, so a long name reaches
# it whole rather than trimmed to an ellipsis somebody cannot expand.
WEB_ENDPOINT_WIDTH = 96

PAGE_FILE = "web.html"

# The inline script and style, pulled out of the page so their hashes can go in
# the content security policy. Non-greedy, and anchored on the closing tag of
# whichever of the two it matched, so the two blocks cannot run together.
_INLINE = re.compile(r"<(script|style)\b[^>]*>(.*?)</\1>", re.S | re.I)


def load_page():
    """The page itself, read once from the package.

    Read at startup rather than per request, so that no part of a request ever
    turns into a path on disk. A missing file is worth saying something about
    rather than serving an error to a browser: it means the wheel was built
    without the data file listed, which is a packaging mistake and looks like
    nothing else.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), PAGE_FILE)
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def content_policy(page):
    """A policy naming the hashes of exactly what this page contains.

    The page never changes once it is loaded, so its inline script and style
    can be hashed once here and the header is a constant for the life of the
    process. That buys the same protection a nonce would without templating a
    response per request, and it means the policy cannot drift from the page:
    edit the script and the hash follows it.
    """
    digests = {"script": [], "style": []}
    for tag, body in _INLINE.findall(page):
        digest = hashlib.sha256(body.encode("utf-8")).digest()
        digests[tag.lower()].append(
            "'sha256-%s'" % base64.b64encode(digest).decode("ascii"))
    return "; ".join([
        "default-src 'none'",
        "script-src " + (" ".join(digests["script"]) or "'none'"),
        "style-src " + (" ".join(digests["style"]) or "'none'"),
        # The stream and the control route, and nothing else anywhere.
        "connect-src 'self'",
        "img-src 'none'",
        "font-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ])


# Spaces at the end of a cell, counting any escape sequences after them as
# still being the end. See `unpad`.
_TRAILING_PAD = re.compile(r" +(?=(?:\033\[[0-9;]*m)*$)")


def unpad(cell):
    """A painted cell with the padding a terminal needed taken back off.

    `row_cells` pads every cell out to its column, because padding is how a
    terminal makes a column. A browser makes one with a table, so here the
    padding is dead weight twice over: a hundred characters of it per flow on
    the wire, and a column that ends up as wide as the padding rather than as
    wide as what is in it.

    Taking it off is not `rstrip`, and that is the whole reason this exists. A
    coloured cell ends in its reset escape with the padding in front of that,
    so anything anchored at the end of the string finds no whitespace there and
    removes nothing. Which is why the browser's DESTINATION column used to be
    ninety-six characters wide while SOURCE, the one cell deliberately left
    uncoloured, was the width of its contents.
    """
    return _TRAILING_PAD.sub("", cell)


def host_allowed(header, local_addr, port):
    """Whether a `Host` header may talk to a server bound at this address.

    Comparing against the address the socket was bound to is the obvious rule
    and it is wrong twice over. People type `localhost` while the code bound
    `127.0.0.1`, and a literal comparison turns them away. Bind to `0.0.0.0`
    and the bound address matches no `Host` header that will ever arrive, so
    every request fails.

    What works is to compare against the address this particular connection
    arrived on, which `getsockname` gives even under a wildcard bind, and to
    accept `localhost` alongside it when that address is a loopback one. Every
    other name is refused, and that is the part doing the work: rebinding an
    address into a name is the whole of the DNS rebinding attack, so a server
    that answers to no name except `localhost` cannot be rebound into.
    """
    if not header:
        return False
    name = header.strip()
    # Split a port off the end, but only when what follows the colon really is
    # one. A bare `Host: localhost` arrives whenever the port is the default
    # for the scheme, which is what somebody who bound --web-port 80 will see.
    if ":" in name:
        head, _, tail = name.rpartition(":")
        if tail.isdigit():
            if tail != str(port):
                return False
            name = head
    elif port not in (80,):
        # A Host with no port at all, against a server that is not on the port
        # a browser would have omitted. Not something a browser sends.
        return False
    name = name.strip("[]").lower()
    if name == str(local_addr).lower():
        return True
    if name in ("localhost", "localhost.localdomain"):
        try:
            return ipaddress.ip_address(local_addr).is_loopback
        except ValueError:
            return False
    return False


def origin_allowed(origin, local_addr, port):
    """Whether an `Origin` header names this server.

    Beside `host_allowed` and testable the same way, because the two are the
    same kind of rule and the same kind of thing goes wrong with them.

    The portless candidates are not decoration. A browser leaves the port out
    of an Origin when it is the default for the scheme, so without them a
    collector on port 80 would refuse every key press it was ever sent. And the
    ascii check is the same trap as the one in the path: `compare_digest`
    refuses a non-ascii `str` by raising rather than by returning False, and
    every header here arrives decoded as latin-1.
    """
    if not origin or not origin.isascii():
        return False
    candidates = ["http://%s:%d" % (local_addr, port),
                  "http://localhost:%d" % port]
    if port == 80:
        candidates += ["http://%s" % local_addr, "http://localhost"]
    for candidate in candidates:
        if hmac.compare_digest(origin, candidate):
            return True
    return False


def is_loopback(addr):
    """Whether an address is one only this machine can reach."""
    try:
        return ipaddress.ip_address(addr).is_loopback
    except ValueError:
        return False


def web_token_arg(text):
    """A token given on the command line, checked for the two ways it fails.

    Both failures are silent, which is why this exists rather than a note in
    the help. A token containing a slash is taken apart by the routing, so the
    URL the collector prints answers 404 for ever. A token with a byte over 127
    makes `compare_digest` raise on every request instead of returning False,
    and the server answers nothing at all. Either way the flag quietly does the
    opposite of what it is for, which is keeping one bookmark working.
    """
    token = text.strip()
    if not token:
        raise argparse.ArgumentTypeError("a web token cannot be empty")
    if not token.isascii():
        raise argparse.ArgumentTypeError(
            "a web token must be ascii; it is compared byte for byte and a "
            "wider character stops the comparison working at all")
    bad = [ch for ch in "/?#% 	" if ch in token]
    if bad:
        raise argparse.ArgumentTypeError(
            "a web token cannot contain %s; it is one path segment of a URL"
            % ", ".join(repr(ch) for ch in bad))
    return token


def new_token():
    """A fresh path token. Urlsafe, because it goes in a URL."""
    return secrets.token_urlsafe(24)


def _frame(event, payload):
    """One server-sent event, ready to go on the wire."""
    return "event: %s\ndata: %s\n\n" % (event, json.dumps(payload, default=str))


class _Handler(BaseHTTPRequestHandler):
    """Three routes and nothing else."""

    # Named so that a response says as little about what is running as it can.
    server_version = "nettail"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    # socketserver puts this on the socket in setup(), so it governs reading
    # the request line, the headers and the body. The stream lifts it again
    # once it starts writing, for the reason given there.
    timeout = REQUEST_TIMEOUT

    def log_message(self, fmt, *args):
        """Say nothing.

        The default writes a line to stderr per request. On a terminal running
        the sticky header or the status bar that line lands inside a scroll
        region those two are managing, and corrupts what is drawn there. There
        is no quieter setting that is safe, because the problem is the writing
        rather than the volume, so this says nothing at all. What a browser did
        is the browser's business anyway.
        """

    def log_error(self, fmt, *args):
        """Also nothing, for the same reason."""

    # -- the checks ---------------------------------------------------------

    @property
    def site(self):
        return self.server.site

    def _local_addr(self):
        """The address this connection arrived on, wildcard bind included.

        The fallback is `bound_addr` and not `bind`, which since binding and
        serving became two steps is the name of a method. Reading it gave back
        a bound method, which matches no Host header ever sent, so the branch
        that exists to fall back gracefully refused the request instead.
        """
        try:
            return self.connection.getsockname()[0]
        except OSError:
            return self.site.bound_addr

    def _refuse(self, code, why):
        # The connection goes with the refusal. Several of the callers below
        # answer a POST before its body has been read, and on a kept-alive
        # connection those unread bytes would be parsed as the next request
        # line, so the browser's following key press would be answered with
        # nonsense. Closing is cheaper than draining a body already ignored.
        self.close_connection = True
        body = (why + "\n").encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        # Said out loud as well as done. Closing without announcing it is legal
        # and leaves the client to discover it, which for a browser reusing the
        # connection means one request failing for no visible reason.
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _route(self):
        """The part of the path after the token, or None when it does not match.

        The token is compared with `compare_digest` rather than `==` so that a
        wrong guess takes the same time as any other wrong guess. It is a long
        random string and the difference is not the weak point here, but the
        habit costs nothing and the alternative is explaining why it was fine.
        """
        parts = self.path.split("?", 1)[0].split("/")
        # "", "t", token, rest...
        if len(parts) < 3 or parts[1] != "t":
            return None
        # `compare_digest` refuses a str with a byte over 127 in it by raising
        # rather than by returning False, and http.server hands this method the
        # request line decoded as latin-1, so any byte at all can arrive here.
        # A token is urlsafe base64 and can never contain one, so this is only
        # ever true of a request that was never going to match. Checked rather
        # than caught, because the alternative is an unauthenticated request
        # producing a traceback, and a traceback goes to stderr, which is the
        # one place a thread other than the receive loop must never write.
        if not parts[2].isascii():
            return None
        if not hmac.compare_digest(parts[2], self.site.token):
            return None
        return "/".join(parts[3:])

    def _checked(self):
        """Common checks. Returns the route, or None having already answered."""
        if not host_allowed(self.headers.get("Host"), self._local_addr(),
                            self.site.port):
            # Deliberately the same answer a bad token gets. A response that
            # distinguished them would tell somebody probing which of the two
            # they had got right.
            self._refuse(404, "not found")
            return None
        route = self._route()
        if route is None:
            self._refuse(404, "not found")
            return None
        return route

    # -- GET ----------------------------------------------------------------

    def do_GET(self):
        route = self._checked()
        if route is None:
            return
        if route == "":
            self._page()
        elif route == "events":
            self._stream()
        else:
            self._refuse(404, "not found")

    def _page(self):
        body = self.site.page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", self.site.policy)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _stream(self):
        site = self.site
        client = site.bus.subscribe(limit=MAX_CLIENTS)
        if client is None:
            # Either the cap is reached or the collector is going away. Both are
            # temporary from the browser's point of view, so both say so.
            self._refuse(503, "too many watchers, or the collector is closing")
            return

        # Everything from here is inside the finally that gives the place back,
        # the header write included. wfile is unbuffered, so end_headers goes
        # straight at the socket and raises if the peer has already gone. With
        # the subscription taken outside this, that raise would leak a client
        # the feed goes on publishing to and which nothing ever drains, and
        # four of those leave the cap full of connections that do not exist.
        site.watching.add(threading.current_thread())
        try:
            # The request timeout was there to stop a connection stalling
            # before it said what it wanted. This one has said, and what it
            # asked for is to be sent things for as long as its tab is open,
            # which on a quiet network means minutes of saying nothing. Leaving
            # the timeout on would make a slow reader look like a stalled
            # request and cut the stream. Falling behind is already answered by
            # the bounded queue, and going away by the peer check below.
            self.connection.settimeout(None)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            # No content length, so the body runs until the connection closes,
            # which is what a stream is.
            self.close_connection = True
            self._pump(client)
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            # The tab was closed, or the far end went away. Neither is news.
            pass
        finally:
            site.watching.discard(threading.current_thread())
            site.bus.unsubscribe(client)

    def _pump(self, client):
        """Move events from one client's queue onto its socket until it ends."""
        bus = self.site.bus
        self._send(_frame("hello", bus.hello()))
        idle_since = time.time()
        while True:
            events, dropped = bus.drain(client)
            if dropped:
                # Said out loud rather than papered over. A stream with a hole
                # in it that looks continuous is worse than one that admits it.
                self._send(_frame("dropped", {"count": dropped}))
            for kind, data in events:
                self._send(_frame(kind, data))
            if events or dropped:
                idle_since = time.time()
                continue
            # Emptied before the closed flag is looked at, so that the exit
            # summary published a moment before shutdown is still sent.
            if client.closed:
                return
            client.ready.wait(POLL)
            client.ready.clear()
            # A browser that has gone away says so by closing its end, and the
            # only way this thread finds out is by looking. Waiting for the
            # next heartbeat write to fail would work, but it would also hold
            # this watcher's place against the cap for as long as the heartbeat
            # interval, so somebody who closed four tabs and opened one would
            # be turned away by four connections that no longer exist.
            if self._peer_gone():
                return
            if time.time() - idle_since >= HEARTBEAT:
                # A comment frame. The browser ignores it; everything between
                # here and the browser learns the connection is alive.
                self._send(": ping\n\n")
                idle_since = time.time()

    def _peer_gone(self):
        """Whether the browser has closed its end.

        A client of a stream sends nothing, so anything readable on the
        socket is either the end of it or a request this handler is never
        going to answer. Peeked rather than read, so that nothing is taken
        off a socket this thread does not otherwise consume.
        """
        try:
            readable, _w, _x = select.select([self.connection], [], [], 0)
            if not readable:
                return False
            return self.connection.recv(1, socket.MSG_PEEK) == b""
        except (OSError, ValueError):
            return True

    def _send(self, text):
        self.wfile.write(text.encode("utf-8"))
        self.wfile.flush()

    # -- POST ---------------------------------------------------------------

    def do_POST(self):
        # The body comes off the socket before anything is decided, including
        # whether the token was any good.
        #
        # This is not politeness. Answering and closing while the client is
        # still writing hands it a reset instead of the answer, and the answer
        # is the useful part: a browser posting to a read-only collector saw a
        # broken connection rather than the 403 that explains itself, and one
        # posting with a stale token, which is what a bookmark holds after a
        # restart, saw the same instead of a 404. The cost is a bounded read
        # from a client that has already been allowed to send headers.
        try:
            # A Content-Length that is not a number is exactly as malformed as
            # a body that is not json, and int() raising is a traceback rather
            # than an answer, so it is caught rather than left to escape.
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._refuse(400, "expected a small json body")
            return
        if length <= 0 or length > BODY_MAX:
            # Not read. A length this program will not accept is not a length
            # it should spend time swallowing either, and one it cannot trust
            # is not a length it can use to get back in step, which is why
            # every refusal takes the connection with it.
            self._refuse(400, "expected a small json body")
            return
        raw = self.rfile.read(length)

        route = self._checked()
        if route is None:
            return
        if route != "key":
            self._refuse(404, "not found")
            return

        if self.site.readonly:
            self._refuse(403, "this collector is serving the display only")
            return
        # A cross-origin form or fetch names where it came from. The token in
        # the path already means an attacker has nothing to aim with, but a
        # request that says it came from somewhere else is refused on its own
        # account rather than on the strength of the token alone.
        origin = self.headers.get("Origin")
        if origin and not self._origin_ok(origin):
            self._refuse(403, "bad origin")
            return

        try:
            payload = json.loads(raw.decode("utf-8"))
            key = payload["key"]
            value = payload.get("value")
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            self._refuse(400, "expected {\"key\": \"...\"}")
            return

        if not isinstance(key, str) or key not in self.site.allowed:
            # Only keys the collector already answers, so this route is a
            # keyboard rather than a way to reach anything else.
            self._refuse(400, "not a key this collector takes")
            return
        if value is not None and (not isinstance(value, str)
                                  or len(value) > 64):
            self._refuse(400, "a key's value must be a short string")
            return

        try:
            self.site.keys.put_nowait((key, value))
        except queue.Full:
            self._refuse(503, "the collector is not keeping up with the keys")
            return
        body = b"{\"queued\":true}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _origin_ok(self, origin):
        return origin_allowed(origin, self._local_addr(), self.site.port)


class _Server(ThreadingHTTPServer):
    # A watcher's thread lives as long as its tab, so nothing may wait on one
    # at exit. The ordered shutdown below is the mechanism; this is the
    # backstop for a thread that has somehow got past it.
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        """Say nothing about a request that raised.

        The default prints a traceback to stderr, and this is the one thing in
        the program that must not: `sticky.py` and `statusbar.py` are managing
        a scroll region on that terminal, and several pages of traceback landing
        inside it corrupts the display until the next repaint, if there is one.
        Silencing `log_message` alone was not enough, because it only covers
        what the handler chooses to say, not what escapes it.

        A specific bug is better fixed than swallowed, and the two that were
        reachable from an unauthenticated request are fixed above rather than
        left to this. What this is for is the next one.
        """


class WebInterface:
    """The server, the token, and what a request is allowed to do.

    Held by `cli.main` for the life of a run. Everything a handler needs is
    reachable from here, which is how a handler manages to need nothing else.
    """

    def __init__(self, bus, keys, allowed, bind="127.0.0.1",
                 port=DEFAULT_WEB_PORT, token=None, readonly=False):
        self.bus = bus
        self.keys = keys
        self.allowed = frozenset(allowed)
        self.bind_addr = bind
        self.port = port
        # What was actually bound, once it has been. Starts as what was asked
        # for so that anything reading it before bind() gets something sensible
        # rather than None.
        self.bound_addr = bind
        self.token = token or new_token()
        self.readonly = readonly
        self.page = load_page()
        self.policy = content_policy(self.page)
        self.watching = set()
        self._httpd = None
        self._thread = None

    @property
    def url(self):
        # A wildcard bind is every address, which is no use in a URL, so the
        # one printed names the loopback address: it is the one that certainly
        # works and the one somebody on this machine should be using anyway.
        shown = self.bound_addr
        if shown in ("0.0.0.0", "", "::"):
            shown = "127.0.0.1"
        return "http://%s:%d/t/%s/" % (shown, self.port, self.token)

    def bind(self):
        """Claim the port without answering anything yet.

        Split from serving because the caller needs the URL before it can build
        the greeting a browser is met with, and the greeting has to be in place
        before the first browser can arrive. Binding settles the URL; serving
        is what lets somebody ask for it. Between the two the caller has as
        long as it likes.

        Raises OSError when the port is taken.
        """
        self._httpd = _Server((self.bind_addr, self.port), _Handler)
        self._httpd.site = self
        # Read back what was actually bound rather than what was asked for. The
        # port may have been chosen by the operating system, which is what the
        # suite does, and the address may have been given as a name, which is
        # what somebody writing `--web-bind localhost` does. Both matter later:
        # the port is checked against every Host header, and the address is
        # what decides whether this is a loopback bind worth saying nothing
        # about or a routable one worth warning about.
        self.bound_addr, self.port = self._httpd.server_address[:2]
        return self.url

    def serve(self):
        """Start answering. Returns the URL, as `bind` did."""
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name="nettail-web", daemon=True)
        self._thread.start()
        return self.url

    def start(self):
        """Bind and serve in one go, for a caller with no greeting to set."""
        self.bind()
        return self.serve()

    def stop(self, timeout=2.0):
        """Let every watcher finish, then close.

        Called after the exit summary has been published, so that the last
        thing a browser is handed is the report rather than a connection that
        died in the middle of one. `server_close` joins nothing by itself and
        the threads are daemons, so without this the interpreter is free to
        exit while a writer is halfway through a frame.
        """
        self.bus.close()
        deadline = time.time() + timeout
        for thread in list(self.watching):
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            thread.join(remaining)
        if self._httpd is not None:
            # shutdown() only means anything once serve_forever is running, so
            # a server that was bound and never served is closed and no more.
            if self._thread is not None:
                self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(1.0)
