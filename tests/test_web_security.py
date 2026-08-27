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
import json
import queue
import socket
import threading
import time
import urllib.error
import urllib.request

from harness import check, finish

from nettail.feed import Feed
from nettail.web import (
    REQUEST_TIMEOUT,
    WebInterface,
    _Handler,
    content_policy,
    host_allowed,
    is_loopback,
    origin_allowed,
    web_token_arg,
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
                         port=0, readonly=True)
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
    finally:
        quiet.stop(timeout=1.0)

    # -- the token --------------------------------------------------------

    other = WebInterface(Feed(), queue.Queue(), set(), bind="127.0.0.1", port=0)
    check("two collectors do not share a token", other.token != site.token)
    check("a token is long enough to be worth having", len(site.token) >= 24)
    pinned = WebInterface(Feed(), queue.Queue(), set(), bind="127.0.0.1",
                          port=0, token="a-pinned-token")
    check("a pinned token is used as given", pinned.token == "a-pinned-token")
    check("and appears in the url", "/t/a-pinned-token/" in pinned.url)
finally:
    site.stop(timeout=1.0)

finish("web security")
