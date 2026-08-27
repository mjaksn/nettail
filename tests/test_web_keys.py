"""Keys arriving from a browser, through the real receive loop.

Follows `test_keys_end_to_end`: a scripted socket and a scripted keyboard, with
`main()` driven for real. What differs is where the keys come from. They are
put on the queue a browser's POST would put them on, and the check is that they
reach the same dispatch and move the same state.

Also pins the two `--json` interactions, which are the ones nobody would notice
going wrong: the clear key must not put escape codes into a machine-readable
stream, and pause must hold the browser view without holding stdout.
"""
import io
import socket
import struct
import sys
import time

from harness import FakeTTY, check, finish, plain

import nettail as main
from nettail.keys import (
    KEY_CHARS,
    KEYS,
    WEB_EXCLUDED,
    WEB_UNLISTED,
    web_buttons,
    web_keys,
)

V5_HDR = struct.Struct("!HHIIIIBBH")
V5_REC = struct.Struct("!4s4s4sHHIIIIHHBBBBHHBBH")


def v5_packet(seq, count=2):
    pkt = V5_HDR.pack(5, count, 100000, int(time.time()), 0, seq, 0, 0, 0)
    for i in range(count):
        pkt += V5_REC.pack(
            bytes([192, 168, 1, 10 + i]), bytes([8, 8, 8, 8]), bytes([192, 168, 1, 1]),
            1, 2, 12, 1500, 90000, 100000, 51000 + i, 443, 0,
            0x18, 6, 0, 0, 0, 24, 24, 0)
    return pkt


# -- what a browser is allowed to press ---------------------------------

listed = {key for key, _doc in KEYS}
pressable = {key for key, _doc in web_keys()}
buttoned = {key for key, _doc in web_buttons()}

# Being pressable and being worth a button are two questions, and two tables.
check("every excluded key is a real key", set(WEB_EXCLUDED) <= listed)
check("and so is every unlisted one", set(WEB_UNLISTED) <= listed)
check("the browser may press everything but the excluded",
      pressable == listed - set(WEB_EXCLUDED))
check("the quit key is not among them", "esc" not in pressable)
check("but the help key is", "?" in pressable)

check("the buttons are a subset of what may be pressed", buttoned <= pressable)
# A button reading "this list" beside the list would be absurd, so the help key
# has none. It stays pressable, because somebody who knows the program will
# reach for it out of habit and it costs nothing to answer.
check("and leave out the help key", "?" not in buttoned)
check("keeping everything else", buttoned == pressable - set(WEB_UNLISTED))
check("the terminal listing still shows every key, browser or not",
      len(KEYS) == len(pressable) + len(WEB_EXCLUDED))


def run(web_presses, packets, argv=(), rounds=400, settle=0.0, gap=None):
    """Drive main() with keys arriving as if from a browser.

    `web_presses` is a list of (after_n_polls, key, value). The queue is filled
    from the fake socket's poll counter so a press can be timed against the
    flows, which is what makes pause and resume observable.

    `settle` waits that long once the packets have run out. The status clock
    only strikes every REPAINT_INTERVAL, and these runs are over in a few
    milliseconds, so without it the only status a test sees is the one
    published before the first datagram arrived.

    `gap` is a (drop_after_n_polls, take_back_after_n_polls) pair, timed the
    same way, and stands in for a tab that goes to the background: the client
    is given up, flows go by with nobody watching, and a second one arrives in
    its place. The greeting is recorded at both ends so a check can compare
    them. Each end waits a repaint interval first, so that there is a status
    from before the gap and a status after it rather than neither.
    """
    waited = []
    queued = list(web_presses)
    waiting = list(packets)
    seen = {}

    class FakeSocket:
        calls = 0

        def __init__(self, *a, **kw):
            pass

        def setsockopt(self, *a):
            pass

        def bind(self, *a):
            pass

        def settimeout(self, value):
            seen["timeout"] = value

        def close(self):
            pass

        def recvfrom(self, _n):
            FakeSocket.calls += 1
            for press in list(queued):
                if press[0] <= FakeSocket.calls:
                    queued.remove(press)
                    seen["site"].keys.put_nowait((press[1], press[2]))
            if gap is not None:
                bus = seen["site"].bus
                if FakeSocket.calls == gap[0] - 1:
                    # The status clock is read at the top of the loop, so the
                    # wait goes on the poll before the one that gives the
                    # client up. Waited for here, it would be the greeting
                    # that got recorded first and the status that followed it.
                    time.sleep(main.REPAINT_INTERVAL + 0.1)
                elif FakeSocket.calls == gap[0]:
                    seen["hello_before_gap"] = bus.hello()
                    bus.unsubscribe(seen["client"])
                    seen["client"] = None
                elif FakeSocket.calls == gap[1]:
                    seen["client"] = bus.subscribe()
                    seen["hello_after_gap"] = bus.hello()
                    time.sleep(main.REPAINT_INTERVAL + 0.1)
            if FakeSocket.calls > rounds:
                raise KeyboardInterrupt
            if waiting:
                return waiting.pop(0), ("10.0.0.1", 2055)
            if settle and not waited:
                waited.append(True)
                time.sleep(settle)
            raise socket.timeout

    class FakeInterface:
        """Stands in for the server: the queue, and nothing that binds a port."""

        def __init__(self, bus, keys, allowed, **kw):
            self.bus = bus
            self.keys = keys
            self.allowed = allowed
            self.readonly = kw.get("readonly", False)
            self.port = 2056
            self.token = "test-token"
            self.url = "http://127.0.0.1:2056/t/test-token/"
            self.asked_for = kw.get("bind", "127.0.0.1")
            self.bound_addr = self.asked_for
            self.stopped = False
            self.serving = False
            seen["site"] = self

        # Bound and serving are two steps for a reason: the greeting has to be
        # set between them, so a browser can never arrive before there is one.
        def bind(self):
            # A real bind reads the address back off the socket, which turns a
            # name into the address it resolved to. The warning about exposing
            # this network's traffic is decided from that, so a fake that kept
            # the name would test the wrong thing.
            try:
                self.bound_addr = socket.gethostbyname(self.asked_for)
            except OSError:
                self.bound_addr = self.asked_for
            return self.url

        def serve(self):
            self.serving = True
            seen["greeting_when_serving"] = self.bus.hello()
            # A watcher, so that the tees publish. Everything they hand it is
            # drained at the end of the run and read back as `prose`.
            seen["client"] = self.bus.subscribe()
            return self.url

        def start(self):
            self.bind()
            return self.serve()

        def stop(self, timeout=2.0):
            self.stopped = True

    FakeSocket.calls = 0
    real_socket, real_web = socket.socket, main.cli.WebInterface
    real_argv, real_out, real_err = sys.argv, sys.stdout, sys.stderr
    socket.socket = FakeSocket
    main.cli.WebInterface = FakeInterface
    out, err = io.StringIO(), FakeTTY()
    sys.argv = ["nettail", "--web", "--resolve", "off"] + list(argv)
    sys.stdout, sys.stderr = out, err
    try:
        main.cli.main()
    finally:
        socket.socket = real_socket
        main.cli.WebInterface = real_web
        sys.argv, sys.stdout, sys.stderr = real_argv, real_out, real_err
    seen["out"] = out.getvalue()
    seen["err"] = err.getvalue()
    # What the browser was sent, in order. The prose kinds are what most of
    # these checks are about; the flows are here so a check can count them.
    events = []
    if seen.get("client") is not None:
        events = seen["site"].bus.drain(seen["client"])[0]
    seen["events"] = events
    seen["prose"] = [(payload["kind"], payload["text"])
                     for kind, payload in events if kind == "prose"]
    return seen


# -- a key from a browser reaches the dispatch --------------------------

result = run([(2, "e", None)], [v5_packet(0)])
check("the socket waits the short time when the web interface is up",
      result["timeout"] == 0.25)
check("a browser key is answered",
      "showing only flows with a public endpoint" in plain(result["err"]))
check("and the server is stopped on the way out", result["site"].stopped is True)

# The m key is the only one that asks a question. At a terminal it blocks on a
# typed line; from a browser the value arrives with the key, which is what the
# bound default argument in the drain is for.
result = run([(2, "m", "5M")], [v5_packet(0)])
check("a key that takes a value uses the one that came with it",
      "size scale fixed at 5.0M" in plain(result["err"]), result["err"][-200:])

result = run([(2, "m", "not-a-size")], [v5_packet(0)])
check("and a bad value is refused without stopping anything",
      "size scale unchanged" in plain(result["err"]))

# -- pause holds the browser view, and stdout goes on ------------------

result = run([(2, " ", None)], [v5_packet(0), v5_packet(2)], argv=["--json"])
lines = [line for line in result["out"].splitlines() if line.strip()]
check("under --json every flow still reaches stdout while paused",
      len(lines) == 4, "%d lines" % len(lines))
check("and stdout is still parseable json",
      all(line.startswith("{") and line.endswith("}") for line in lines))

# -- the clear key does not corrupt a machine readable stream ----------

result = run([(2, "x", None)], [v5_packet(0)], argv=["--json"])
check("the clear key writes no escape codes to stdout under --json",
      "\033[2J" not in result["out"] and "\033[H" not in result["out"],
      repr(result["out"][:80]))
check("and stdout is nothing but flows",
      all(line.startswith("{") for line in result["out"].splitlines()
          if line.strip()))

# Without --json it is a terminal, and clearing one is what the key is for.
result = run([(2, "x", None)], [v5_packet(0)])
check("without --json the screen is still cleared", "\033[2J" in result["out"])

# -- the greeting is in place before anything can ask for it -------------
#
# A browser is handed the greeting once and builds its table head and its
# buttons from it. One that arrived while the greeting was still empty would
# render every flow into no columns for the rest of the session, and nothing
# would ever correct it, so the order here is load-bearing rather than tidy.

result = run([], [v5_packet(0)])
greeting = result["greeting_when_serving"]
check("the server does not answer until the greeting exists",
      greeting.get("columns") is not None)
check("which carries the columns", greeting["columns"][0]["name"] == "TIME")
check("and the keys", any(k["key"] == "s" for k in greeting["keys"]))
check("and the banner, so a late arrival still gets one",
      "Listening for NetFlow" in plain(greeting["banner"]))

# -- the status bar does not draw itself into a json stream --------------
#
# The x key was given this guard; the b key was not, and it draws on stdout.

result = run([(2, "b", None)], [v5_packet(0)], argv=["--json"])
check("the b key writes no scroll region to stdout under --json",
      "\033[" not in result["out"], repr(result["out"][:80]))
check("and stdout is still nothing but flows",
      all(line.startswith("{") for line in result["out"].splitlines()
          if line.strip()))

# -- the ? listing offers each view only the keys it can press -----------

result = run([(2, "?", None)], [v5_packet(0)])
check("the terminal listing still names every key",
      "esc" in plain(result["err"]))

listings = [text for kind, text in result["prose"] if kind == "keys"]
check("the browser is sent a listing of its own", len(listings) == 1,
      "%d sent" % len(listings))
if listings:
    shown = plain(listings[0])
    check("which leaves out the key it cannot press", "esc" not in shown)
    check("and keeps the ones it can, the help key included",
          all(key in shown for key, _doc in web_keys()))

check("the control route takes the help key",
      KEY_CHARS.get("?", "?") in result["site"].allowed)
check("and the quit key it does not",
      KEY_CHARS.get("esc") not in result["site"].allowed)

# The page decides what to answer from this, not from what has a button.
greeting = result["greeting_when_serving"]
check("the greeting names every pressable key as a character",
      set(greeting["pressable"])
      == {KEY_CHARS.get(k, k) for k, _d in web_keys()},
      repr(greeting["pressable"]))
check("including the help key", "?" in greeting["pressable"])
check("while the buttons leave it out",
      "?" not in [entry["key"] for entry in greeting["keys"]])

# -- --web-bind localhost is a loopback bind ----------------------------
#
# The warning is about exposing this network's traffic to whoever can reach the
# address. Written as a name rather than as digits it is the same bind, and
# saying otherwise would teach a reader to ignore the warning.

result = run([], [v5_packet(0)], argv=["--web-bind", "localhost"])
check("binding by name to loopback draws no exposure warning",
      "not to loopback" not in plain(result["err"]), plain(result["err"])[-200:])
check("and binding to a wildcard still does",
      "not to loopback" in plain(
          run([], [v5_packet(0)], argv=["--web-bind", "0.0.0.0"])["err"]))

# -- the count a hidden tab comes back to --------------------------------
#
# A tab that goes to the background gives up its stream, because a browser that
# is not running goes on buffering what arrives until it is killed for memory.
# It therefore cannot count what it missed: nothing reached it. It asks the
# collector instead, noting this figure when it disconnects and subtracting on
# the way back, so the number it shows is the collector's own.


def statuses(result):
    return [payload for kind, payload in result["events"] if kind == "status"]


result = run([], [v5_packet(0), v5_packet(2)], settle=0.6)
counts = [s["flows_shown"] for s in statuses(result) if "flows_shown" in s]
check("the status carries a count of flows shown", counts != [])
check("and it reaches the total that arrived", counts and max(counts) == 4,
      repr(counts))
check("the greeting carries it too, for a tab that reconnects",
      "flows_shown" in (result["greeting_when_serving"].get("status") or {"": 0})
      or result["greeting_when_serving"].get("status") is None)

# The greeting cannot answer this on its own, and a page that believed it would
# report nothing at all. Publishing stops when the last watcher leaves, so the
# status spliced into a greeting is the one from before the gap: the very
# figure the page noted on its way out. It has to wait for a status frame.

result = run([], [v5_packet(n) for n in (0, 2, 4, 6)], gap=(2, 4), settle=0.6)
before = (result.get("hello_before_gap") or {}).get("status") or {}
after = (result.get("hello_after_gap") or {}).get("status") or {}
check("a figure exists before the gap", before.get("flows_shown", 0) > 0,
      repr(before.get("flows_shown")))
check("and the greeting after the gap repeats it rather than counting it",
      after.get("flows_shown") == before.get("flows_shown"),
      "%r then %r" % (before.get("flows_shown"), after.get("flows_shown")))
counts = [s["flows_shown"] for s in statuses(result) if "flows_shown" in s]
check("while a status frame does carry the figure the gap moved",
      counts and max(counts) > before.get("flows_shown", 0),
      "%r against %r" % (counts, before.get("flows_shown")))

# The figure has to be flows *shown*, not flows decoded. Under --external-only
# the two differ by a lot, and a count of everything decoded would tell a
# reader they had missed far more than they could ever have seen.
local = V5_HDR.pack(5, 1, 100000, int(time.time()), 0, 0, 0, 0, 0) + V5_REC.pack(
    bytes([192, 168, 1, 10]), bytes([192, 168, 1, 20]), bytes([192, 168, 1, 1]),
    1, 2, 12, 1500, 90000, 100000, 51000, 443, 0, 0x18, 6, 0, 0, 0, 24, 24, 0)
result = run([], [local], argv=["--external-only"], settle=0.6)
counts = [s["flows_shown"] for s in statuses(result) if "flows_shown" in s]
decoded = [s["snap"]["flows"] for s in statuses(result)]
check("a flow the filter hides is decoded", max(decoded) == 1)
check("but is not counted as one a browser would have seen",
      max(counts) == 0, "%r shown against %r decoded" % (counts, decoded))

# -- read only ----------------------------------------------------------

result = run([], [v5_packet(0)], argv=["--web-readonly"])
check("a read-only run offers the browser no keys", result["site"].allowed == set())
check("and says so in the banner", "watching only" in plain(result["err"]))

finish("web keys")
