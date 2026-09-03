"""The stream itself: the greeting, the events, the cap, and the close.

Binds a real server on a port the operating system picks, and reads frames off
it with `urllib`. Every read carries a timeout and every server is stopped in a
`finally`, because a suite that hangs takes a CI job with it and this one runs
on nine of them.
"""
import ipaddress
import json
import queue
import re
import socket
import struct
import time
import urllib.error
import urllib.request

from harness import check, finish

from nettail.feed import Feed
from nettail.web import ASK_QUEUE_MAX, MAX_CLIENTS, WebInterface, unpad

TIMEOUT = 6.0


def open_stream(site, host=None):
    host = host or "127.0.0.1:%d" % site.port
    url = "http://%s/t/%s/events" % (host, site.token)
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"Host": host}), timeout=TIMEOUT)


def read_frames(stream, want, limit=200):
    """Pull frames off a stream until `want` of them have names, or it stops."""
    frames = []
    name = None
    for _ in range(limit):
        raw = stream.readline()
        if not raw:
            break
        line = raw.decode("utf-8", "replace")
        if line.startswith("event: "):
            name = line[7:].strip()
        elif line.startswith("data: ") and name:
            frames.append((name, json.loads(line[6:])))
            name = None
            if len(frames) >= want:
                break
    return frames


# -- the padding a terminal needs, taken off before the browser sees it --
#
# `row_cells` pads every cell out to its column, because that is how a terminal
# makes a column. A table does not need it, and leaving it on made the browser's
# DESTINATION column ninety-six characters wide.
#
# The page used to trim it and could not: a coloured cell ends in its reset
# escape with the padding in front of that, so a trim anchored at the end of the
# string found no whitespace and removed nothing. SOURCE, the one cell
# deliberately left uncoloured, was the only endpoint that came out right.

check("padding comes off an uncoloured cell",
      unpad("192.168.1.10:51000" + " " * 78) == "192.168.1.10:51000")
check("and off a coloured one, which is the case that was broken",
      unpad("\033[36m8.8.8.8:443\033[0m".replace("\033[0m", " " * 79 + "\033[0m"))
      == "\033[36m8.8.8.8:443\033[0m")
check("through however many escapes trail it",
      unpad("\033[36mx" + " " * 9 + "\033[0m\033[2m\033[0m")
      == "\033[36mx\033[0m\033[2m\033[0m")
check("a cell with no padding is untouched",
      unpad("\033[2m...AP...\033[0m") == "\033[2m...AP...\033[0m")

# Leading spaces are how a right-aligned column aligns, so they stay.
check("leading spaces on a right aligned cell are kept",
      unpad("     12") == "     12")
check("even under colour",
      unpad("\033[38;5;71m    1.5K\033[0m") == "\033[38;5;71m    1.5K\033[0m")
check("and a cell of nothing but padding empties",
      unpad("      ") == "")

bus = Feed()
asks = queue.Queue(maxsize=ASK_QUEUE_MAX)
site = WebInterface(bus, queue.Queue(maxsize=8), {"e"}, bind="127.0.0.1",
                    port=0, asks=asks)
url = site.start()


def post(route, payload, origin=None):
    """POST a json body to one of the control routes, and give back the code.

    Rather less than `test_web_security`'s poster, which has to forge a Host
    and a token as well. What is being checked here is what the route makes of
    a body it was given, so everything else about the request is right.
    """
    headers = {"Host": "127.0.0.1:%d" % site.port,
               "Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    request = urllib.request.Request(
        "http://127.0.0.1:%d/t/%s/%s" % (site.port, site.token, route),
        data=json.dumps(payload).encode("utf-8"), headers=headers,
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
            return answer.status
    except urllib.error.HTTPError as exc:
        return exc.code

check("starting reports a url", url.startswith("http://127.0.0.1:"))
check("with the token in the path", "/t/%s/" % site.token in url)
check("and the port it actually bound", ":%d/" % site.port in url)

try:
    # -- the page ---------------------------------------------------------

    host = "127.0.0.1:%d" % site.port
    page = urllib.request.urlopen(
        urllib.request.Request("http://%s/t/%s/" % (host, site.token),
                               headers={"Host": host}), timeout=TIMEOUT)
    body = page.read().decode("utf-8")
    check("the page is html", page.headers["Content-Type"].startswith("text/html"))
    check("and carries its policy", bool(page.headers["Content-Security-Policy"]))
    check("which forbids everything by default",
          "default-src 'none'" in page.headers["Content-Security-Policy"])
    check("the page is not cached", page.headers["Cache-Control"] == "no-store")
    check("no referrer leaves it", page.headers["Referrer-Policy"] == "no-referrer")
    outside = body.replace("http://www.w3.org", "")
    check("the page reaches for nothing outside itself",
          "//fonts." not in outside and "http://" not in outside)
    # Everything off the wire is a string somebody on this network chose: a
    # hostname is whatever answered the reverse lookup, the mDNS query or the
    # NetBIOS probe. So the page has to put those characters on screen as
    # characters. Assigning any of it as markup is the classic hole in a web
    # front end bolted onto a network tool, and this is the check that says it
    # has not been opened. The word itself appears in a comment there, which is
    # why what is looked for is an assignment rather than a mention.
    for hole in (r"innerHTML\s*=", r"outerHTML\s*=", r"insertAdjacentHTML",
                 r"document\.write", r"\.srcdoc", r"eval\("):
        check("the page never builds markup: %s" % hole,
              re.search(hole, body) is None)

    # Nor does it build a selector out of anything it was given. A keystroke is
    # whatever the reader pressed, and a quote or a backslash pasted into an
    # attribute selector makes it invalid, so querySelector throws and takes
    # the handler with it. A literal selector is fine; a concatenated one is
    # the same class of mistake as building markup, so it is checked here.
    check("nor a selector out of what it was given",
          re.search(r"querySelector(All)?\s*\([^)]*\+", body) is None)

    # -- the flags font ---------------------------------------------------
    # The one thing this interface serves besides the page, and the exception
    # to its being a single file. It is asked for through BASE like the stream
    # and the key route, because the page answers at two addresses, with and
    # without the trailing slash, and a relative url in CSS would resolve
    # against whichever one the reader arrived by.
    check("the page asks for the font through BASE, not relatively",
          re.search(r"BASE\s*\+\s*['\"]/flags\.woff2", body) is not None,
          "a relative url breaks on one of the two addresses the page "
          "answers at")
    check("and registers it for the flag letters and nothing else",
          "U+1F1E6-1F1FF" in body)
    # And asks for it only when the collector says it is marking countries. An
    # eager load is what this replaced: FontFace.load() fetches when it is
    # called, so a page that built the face at startup sent 78 KB to every
    # browser watching a run that will never draw a flag.
    check("and asks for it only when there are flags to draw",
          re.search(r"payload\.countries\s*\)\s*\{\s*wantFlags\(\)", body)
          is not None, "the fetch has to hang off the status")
    check("and asks in one place, so there is one place to be wrong",
          body.count("new FontFace(") == 1, str(body.count("new FontFace(")))
    check("the policy lets it fetch that font, and only from here",
          "font-src 'self'" in page.headers["Content-Security-Policy"])

    font = urllib.request.urlopen(
        urllib.request.Request("http://%s/t/%s/flags.woff2" % (host, site.token),
                               headers={"Host": host}), timeout=TIMEOUT)
    body_font = font.read()
    check("the font is served", font.status == 200)
    check("as a woff2", font.headers["Content-Type"] == "font/woff2")
    check("and is one", body_font[:4] == b"wOF2", repr(body_font[:4]))
    check("and is the whole of it, rather than a truncated read",
          len(body_font) == len(site.font) > 20000, str(len(body_font)))
    # Everything else here is no-store, which is right for a live view. This
    # is part of the release rather than the run: it cannot change while the
    # process lives, and a new one comes with a new version and a new token.
    check("it is cached, unlike everything else here",
          "immutable" in (font.headers["Cache-Control"] or ""),
          repr(font.headers["Cache-Control"]))
    check("and is not sniffed for a type of its own",
          font.headers["X-Content-Type-Options"] == "nosniff")

    # Behind the token like every other route. A font is only a font, but an
    # unguarded one answers a scanner that never had the token and says what
    # this is.
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                "http://%s/t/%s/flags.woff2" % (host, "not-the-token"),
                headers={"Host": host}), timeout=TIMEOUT)
        check("the font is behind the token", False, "it was served")
    except urllib.error.HTTPError as exc:
        check("the font is behind the token", exc.code == 404, str(exc.code))

    # A response that is not a 200 fails an EventSource permanently: the state
    # goes to CLOSED rather than CONNECTING, so the retry branch never sees it.
    # Handling only CONNECTING left the reader on "connecting" for ever with
    # the server's explanation going nowhere, which is exactly what the fifth
    # tab gets when MAX_CLIENTS turns it away with a 503 that says so.
    check("the page notices a connection that was refused outright",
          "EventSource.CLOSED" in body)
    check("and moves off connecting when it happens",
          'setState("gone", "refused")' in body)

    # A tab in the background gives up its stream, because a browser that is
    # not running the page goes on buffering what arrives until the tab is
    # killed for memory. Three separate things ask for it and none of them
    # covers the others: the visibility flag misses a window that is merely
    # starved, the clock watcher cannot run inside a freeze, and the freeze
    # event does not fire for a tab that is only being scheduled seldom. None
    # of this executes here, so these are greps, blunt in the same way the
    # markup checks above are blunt: a quiet deletion of any one of them would
    # otherwise fail nothing and be found again the hard way.
    for trigger, why in (
            (r'addEventListener\(\s*"visibilitychange"', "the tab is hidden"),
            (r'addEventListener\(\s*"freeze"', "the browser freezes it"),
            (r'setInterval\(', "its own clock runs late")):
        check("the page gives the stream up when %s" % why,
              re.search(trigger, body) is not None)
    check("and the grace before it does is not an accident",
          "HIDE_GRACE" in body)

    # The clock watcher is the one cause that can park a tab which is plainly
    # in front of somebody, after a laptop wake or a long stall, so the
    # indicator has to have something to say that is not "in the background".
    check("a tab paused because it was not being run says so",
          "paused, this tab was not being run" in body)
    check("and one paused in the background says that instead",
          "paused in the background" in body)

    # A refused connection and one abandoned after five failures both leave a
    # dead EventSource behind. Left assigned, it reads as a live stream to
    # anything that checks, and the clock watcher would park it, take the
    # notice off the indicator and reconnect into whatever refused it. Both
    # branches say to reload, so the page has to mean it. A name is a blunt
    # thing to grep for, but nothing here runs the page, and the alternative
    # is a rule that fails nowhere when it is dropped.
    check("the page records having stopped trying", "gaveUp" in body)

    # What arrives on the stream waits for an animation frame and goes on in
    # one append. `toTail` reads `scrollHeight`, which lays the table out, and
    # a table lays out whole, so a row appended per event meant a pass over the
    # whole history; a reconnect, which arrives as a backlog inside a single
    # task, was thousands of those passes and looked from the outside like a
    # frozen tab. Nothing here runs the page, so this is greps again: that the
    # page waits for a frame, and that there is one place a row can reach the
    # table. The second is the one that rots quietly, because an append put
    # back anywhere else works perfectly well until the link is busy.
    check("the page applies what arrives on a frame",
          "requestAnimationFrame" in body)
    start = body.find("function paint(")
    end = body.find("\n  function ", start + 1)
    check("and paint is where a row goes on", start != -1 and end != -1)
    inside, outside = body[start:end], body[:start] + body[end:]
    check("which is the only place one does",
          "rows.appendChild(" in inside and "rows.appendChild(" not in outside)
    # A clear has to take the queue behind it with it. Rows already waiting
    # were on their way to a table the reader has just emptied, and letting
    # them land afterwards would answer the key with the rows it was pressed
    # to be rid of.
    check("a clear empties what is queued behind it", "pendingClear" in body)

    # -- the flow details dialog ------------------------------------------
    #
    # Nothing here runs the page, so these are greps again, and each is for a
    # rule that fails nothing at all when it is quietly dropped.
    check("a row opens a dialog rather than a panel", "showModal(" in body)
    check("through one listener on the table rather than one per row",
          re.search(r'rows\.addEventListener\(\s*"click"', body) is not None)
    # Typing x into the dialog would otherwise clear the table underneath the
    # very row the dialog was opened from.
    check("and no keystroke is forwarded while it is open",
          re.search(r"if \(detail\.open\)\s*\{\s*return;", body) is not None)
    # A dialog left open over a parked stream would sit on "asking the
    # collector" until the tab came back, since the answer comes back there.
    check("parking the stream closes it", "detail.close()" in body)
    # The interval starts when the dialog opens and has to stop when it
    # closes, or two open-and-close cycles leave two timers asking.
    check("the dialog asks again on a clock", "setInterval(askAgain" in body)
    check("and the clock is stopped", "clearInterval(" in body)
    check("on whichever way it was closed, through one close listener",
          re.search(r'detail\.addEventListener\(\s*"close"', body) is not None)
    check("the cadence comes from the collector rather than the page",
          "detail_refresh" in body)

    # The down arrow works the Follow box, and is the one key the page answers
    # by itself: following the tail is the tab's business and the collector has
    # no name for it, so it is in neither KEYS nor the greeting and nothing on
    # the control route would notice it going. Greps again, and three of them,
    # because each half of this is quiet when it breaks. Answered below the
    # readonly guard it would stop working on the display-only collector whose
    # reader most wants it; without preventDefault the browser scrolls, the
    # scroll handler reads the position back into the box, and at the tail that
    # puts the tick straight back on.
    opens = body.find('document.addEventListener("keydown"')
    ends = body.find("// ---- the stream", opens)
    handler = body[opens:ends]
    arrow = handler.find('event.key === "ArrowDown"')
    check("the down arrow is answered by the page", opens != -1 and arrow != -1)
    # Scoped to the branch and not to the rest of the handler, which has a
    # preventDefault of its own for the keys that do go to the collector: read
    # to the end and the check the arrow's own guard is here for would pass
    # with that guard deleted, which is the whole failure it is written for.
    branch = handler[arrow:handler.find("if (readonly)")]
    check("and by working the Follow box rather than by sending a key",
          "followBox.click()" in branch)
    check("and it takes the browser's own scrolling out of the way",
          "event.preventDefault()" in branch)
    check("it is answered before the guard that keeps keys off a "
          "display-only collector",
          -1 < arrow < handler.find("if (readonly)"))
    check("and before the one that hands a keystroke to a focused box",
          arrow < handler.find('tagName === "INPUT"'))

    # -- the greeting -----------------------------------------------------

    bus.set_hello({
        "nettail": "0.1.2",
        "banner": "Listening for NetFlow/IPFIX",
        "columns": [{"name": "TIME", "align": "<"},
                    {"name": "BYTES", "align": ">"}],
        "keys": [{"key": "e", "doc": "show only public flows"}],
        "readonly": False,
    })
    bus.status({"shown": {"flows": "0"}})

    stream = open_stream(site)
    try:
        frames = read_frames(stream, 1)
        check("a stream opens with a greeting", frames[0][0] == "hello")
        hello = frames[0][1]
        check("naming the version", hello["nettail"] == "0.1.2")
        check("carrying the banner", "Listening" in hello["banner"])
        check("the columns the terminal uses", hello["columns"][0]["name"] == "TIME")
        check("their alignments too", hello["columns"][1]["align"] == ">")
        check("and the keys a browser may press", hello["keys"][0]["key"] == "e")
        check("with the status as it stands",
              hello["status"]["shown"]["flows"] == "0")

        # -- events ------------------------------------------------------

        bus.flow({"cells": ["10:00:00", "1.5K"], "record": {"proto": 6}})
        bus.prose("notice", "\033[33msomething\033[0m")
        bus.clear()
        frames = read_frames(stream, 3)
        kinds = [name for name, _payload in frames]
        check("a flow reaches the stream", "flow" in kinds)
        check("so does prose", "prose" in kinds)
        check("and a clear", "clear" in kinds)

        by_name = dict(frames)
        check("the flow carries its cells",
              by_name["flow"]["cells"] == ["10:00:00", "1.5K"])
        check("and the record behind them",
              by_name["flow"]["record"]["proto"] == 6)
        check("prose keeps its kind", by_name["prose"]["kind"] == "notice")
        check("and its escape codes, so the browser sees what the terminal saw",
              "\033[33m" in by_name["prose"]["text"])

        # -- asking about a flow -----------------------------------------
        #
        # The route a click goes out on. It changes nothing, so it is allowed
        # under --web-readonly, which `test_web_security` pins beside the
        # refusal the key route gets there. Everything in the body is text off
        # the wire that ends up in a report published to every browser
        # watching, so all of it is checked at this boundary rather than where
        # the report is built.

        check("a well formed question is accepted",
              post("detail",
                   {"ask": 1, "n": 2,
                    "ends": ["192.168.1.10", "8.8.8.8"]}) == 200)
        check("and reaches the queue the receive loop drains",
              asks.get_nowait() == (1, 2, ("192.168.1.10", "8.8.8.8")))
        check("a question about a flow that is gone still names its ends",
              post("detail", {"ask": 2, "n": None,
                              "ends": ["192.168.1.10", None]}) == 200)
        check("with the missing half kept as nothing",
              asks.get_nowait() == (2, None, ("192.168.1.10", None)))
        # An address is parsed and written back out, so that a browser that
        # spells one differently from the decoder does not ask about a key
        # that cannot be in the tally.
        check("an IPv6 address is normalised on the way through",
              post("detail", {"ask": 3, "n": None,
                              "ends": ["2001:0DB8:0000:0000:0000:0000:0000:0001",
                                       None]}) == 200
              and asks.get_nowait()[2][0] == "2001:db8::1")
        # And the spelling netflume decodes into survives untouched, which is
        # the case that actually happens: what the page sends back is what the
        # collector sent it in the first place.
        decoded = str(ipaddress.IPv6Address(
            bytes.fromhex("00000000000000000000ffffc0a8010a")))
        check("and the decoder's own spelling comes back as itself",
              post("detail", {"ask": 4, "n": None,
                              "ends": [decoded, None]}) == 200
              and asks.get_nowait()[2][0] == decoded, decoded)

        for name, payload in (
            ("an ask that is not a number", {"ask": "one"}),
            ("an ask that is a bool", {"ask": True}),
            ("a negative ask", {"ask": -1}),
            ("an enormous ask", {"ask": 2 ** 80}),
            ("no ask at all", {"n": 1}),
            ("a serial that is not a number", {"ask": 1, "n": "two"}),
            ("a serial that is a bool", {"ask": 1, "n": False}),
            ("ends that are not a pair", {"ask": 1, "ends": ["10.0.0.1"]}),
            ("ends that are not a list", {"ask": 1, "ends": "10.0.0.1"}),
            ("an end that is not an address",
             {"ask": 1, "ends": ["not-an-address", None]}),
            ("an end that is a hostname",
             {"ask": 1, "ends": ["evil.example.com", None]}),
            ("an end that is far too long",
             {"ask": 1, "ends": ["1" * 200, None]}),
            ("a field this route does not know",
             {"ask": 1, "n": 1, "ends": [None, None], "extra": 1}),
            ("a body that is not an object", [1, 2, 3]),
        ):
            got = post("detail", payload)
            check("the detail route refuses %s" % name, got == 400,
                  "got %r" % (got,))
        check("nothing refused reached the queue", asks.empty(),
              repr(list(asks.queue)))

        check("a foreign origin is refused here too",
              post("detail", {"ask": 1, "n": 1, "ends": [None, None]},
                   origin="http://evil.example.com") == 403)
        check("and a matching one is not",
              post("detail", {"ask": 1, "n": 1, "ends": [None, None]},
                   origin="http://127.0.0.1:%d" % site.port) == 200)
        asks.get_nowait()

        # A queue the receive loop is not draining is refused rather than
        # waited on, which is the one thing a request thread may never do.
        while not asks.full():
            asks.put_nowait((0, None, (None, None)))
        check("a full queue is refused rather than waited on",
              post("detail", {"ask": 1, "n": 1, "ends": [None, None]}) == 503)
        while not asks.empty():
            asks.get_nowait()

        # And an unknown route under a good token is still a 404, so the
        # branch that added a second one did not open the door to a third.
        check("no third control route appeared",
              post("anything", {"ask": 1}) == 404)

        # The answer goes back on the stream rather than in the response,
        # which is what lets the collector build it on the thread that owns
        # the figures.
        bus.detail({"ask": 41, "held": False, "sections": [],
                    "ends": [], "pair": None})
        frames = read_frames(stream, 1)
        check("the answer arrives on the stream",
              bool(frames) and frames[0][0] == "detail", repr(frames))
        check("with the ask's id on it", frames[0][1]["ask"] == 41)

        # -- falling behind ----------------------------------------------
        #
        # A browser that cannot keep up is told how much it missed rather than
        # shown a stream with a hole in it that looks continuous.
        behind = Feed(backlog=2)
        watcher = behind.subscribe()
        for n in range(6):
            behind.flow({"n": n})
        _events, dropped = behind.drain(watcher)
        check("the bus counts what a slow client missed", dropped == 4)

        # -- the cap ------------------------------------------------------

        # One stream is already open above, so the cap is reached partway
        # through this and the refusal is what ends the loop.
        extra = []
        refused = 0
        try:
            for _ in range(MAX_CLIENTS + 2):
                try:
                    extra.append(open_stream(site))
                except urllib.error.HTTPError as exc:
                    refused = exc.code
                    break
            check("past the cap a watcher is turned away", refused == 503,
                  "got %r" % (refused,))
            check("and the cap is where the module says it is",
                  len(extra) + 1 == MAX_CLIENTS,
                  "%d open, cap %d" % (len(extra) + 1, MAX_CLIENTS))
        finally:
            for handle in extra:
                try:
                    handle.close()
                except OSError:
                    pass
    finally:
        stream.close()

    # Closing a stream from this end is noticed on the other when the writer
    # next tries to use the socket, which is not instant. Wait for the places
    # to come free rather than race the next connection against them.
    deadline = time.time() + TIMEOUT
    while bus.clients and time.time() < deadline:
        time.sleep(0.05)
    check("closing the watchers frees their places", bus.clients == 0,
          "%d still attached" % bus.clients)

    # -- a connection aborted mid-handshake must not leak its place --------
    #
    # The header write goes straight at the socket, so a peer that has already
    # gone makes end_headers raise. With the subscription taken outside the
    # try that gives it back, that raise leaked a client the feed went on
    # publishing to and nothing ever drained: four of them and the interface
    # was closed for the rest of the run, having also printed four tracebacks
    # to the terminal on the way.
    request = ("GET /t/%s/events HTTP/1.1\r\nHost: %s\r\n\r\n"
               % (site.token, host)).encode("ascii")
    for _ in range(MAX_CLIENTS + 2):
        aborted = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Linger zero makes close() an immediate reset rather than a polite
        # shutdown, which is what a browser killed mid-load looks like.
        aborted.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                           struct.pack("ii", 1, 0))
        try:
            aborted.connect(("127.0.0.1", site.port))
            aborted.sendall(request)
        finally:
            aborted.close()

    deadline = time.time() + TIMEOUT
    while bus.clients and time.time() < deadline:
        time.sleep(0.05)
    check("an aborted connection leaves no subscription behind",
          bus.clients == 0, "%d leaked" % bus.clients)
    check("and the interface still serves afterwards",
          open_stream(site) is not None)
    deadline = time.time() + TIMEOUT
    while bus.clients and time.time() < deadline:
        time.sleep(0.05)

    # -- shutting down ----------------------------------------------------
    #
    # The claim being pinned is that a browser open at the moment the collector
    # exits still receives the summary. Daemon threads alone would not give
    # that: server_close joins nothing, so the interpreter could exit with a
    # writer halfway through the frame it was just handed.
    last = open_stream(site)
    try:
        read_frames(last, 1)                      # the greeting
        bus.prose("summary", "Traffic summary")
        site.stop(timeout=3.0)
        frames = read_frames(last, 5)
        texts = [payload.get("text", "") for name, payload in frames
                 if name == "prose"]
        check("the summary published before the close still arrives",
              any("Traffic summary" in text for text in texts),
              repr(frames))
    finally:
        last.close()

    check("and the stream is finished afterwards", bus.subscribe() is None)
finally:
    site.stop(timeout=1.0)

finish("web server")
