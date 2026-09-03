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
import os
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


def run(web_presses, packets, argv=(), rounds=400, settle=0.0, gap=None,
        keyboard=None, presses=(), window=None, port_notices=()):
    """Drive main() with keys arriving as if from a browser.

    `web_presses` is a list of (after_n_polls, key, value). The queue is filled
    from the fake socket's poll counter so a press can be timed against the
    flows, which is what makes pause and resume observable.

    `settle` waits that long once the packets have run out. The status clock
    only strikes every REPAINT_INTERVAL, and these runs are over in a few
    milliseconds, so without it the only status a test sees is the one
    published before the first datagram arrived.

    `keyboard` is True or False to say whether the run has a terminal
    keyboard, which is what decides whether the banner offers the q key, and
    None to let it work that out for itself as it normally would.

    `presses` are keys arriving from the terminal rather than from a browser,
    which is the only way to reach a key that a browser is not allowed to
    press. They are handed over one per pass round the loop, in order.

    `port_notices` are ports a request thread would have noted, timed against
    the poll counter the same way `web_presses` are. They stand in for a
    request whose Host named another port, which cannot be made to arrive
    here: the fake interface answers nothing.

    `window` is a (columns, lines) pair to run with, set through the two
    environment variables `shutil.get_terminal_size` reads before it asks the
    operating system anything. A suite has no terminal, so anything that
    measures one is otherwise answered with zeros, and a key whose whole job
    is to decide whether something fits would always decide that it does
    not.

    Stubbed rather than arranged for real, in both directions. Arranging for
    one means a stdin that claims to be a terminal, which is enough on Windows
    and not on a platform where starting the keyboard also wants termios on a
    real file descriptor. Arranging for none means being sure nothing has left
    a terminal on stdin, which is not this suite's to be sure of: run.py gives
    each suite its own process and a terminal can be inherited into it, so a
    check that read the ambient answer passed alone and failed in the runner.

    `gap` is a (drop_after_n_polls, take_back_after_n_polls) pair, timed the
    same way, and stands in for a tab that goes to the background: the client
    is given up, flows go by with nobody watching, and a second one arrives in
    its place. The greeting is recorded at both ends so a check can compare
    them. Each end waits a repaint interval first, so that there is a status
    from before the gap and a status after it rather than neither.
    """
    waited = []
    queued = list(web_presses)
    noticed = list(port_notices)
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
            for note in list(noticed):
                if note[0] <= FakeSocket.calls:
                    noticed.remove(note)
                    seen["site"].port_notice = note[1]
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
            self.port_notice = None
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

    typed = list(presses)

    class FakeKeyboard(main.cli.Keyboard):
        def start(self):
            self.enabled = bool(keyboard)
            return self.enabled

        def stop(self):
            self.enabled = False

        def poll(self):
            return typed.pop(0) if typed else None

    FakeSocket.calls = 0
    real_socket, real_web = socket.socket, main.cli.WebInterface
    real_keyboard = main.cli.Keyboard
    real_argv, real_out, real_err = sys.argv, sys.stdout, sys.stderr
    socket.socket = FakeSocket
    main.cli.WebInterface = FakeInterface
    if keyboard is not None:
        main.cli.Keyboard = FakeKeyboard
    out, err = io.StringIO(), FakeTTY()
    sys.argv = ["nettail", "--web", "--resolve", "off"] + list(argv)
    sys.stdout, sys.stderr = out, err
    real_window = {name: os.environ.get(name) for name in ("COLUMNS", "LINES")}
    if window is not None:
        os.environ["COLUMNS"], os.environ["LINES"] = (str(n) for n in window)
    try:
        main.cli.main()
    finally:
        for name, was in real_window.items():
            if was is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = was
        socket.socket = real_socket
        main.cli.WebInterface = real_web
        main.cli.Keyboard = real_keyboard
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

# Without --json either, because stdout here is a pipe rather than a terminal,
# which is what a collector run as a service has and what the browser reaches.
# Clearing a screen that is not there only puts the escapes in the capture. The
# terminal case, where the key does clear, is test_keys and test_keys_end_to_end.
result = run([(2, "x", None)], [v5_packet(0)])
check("nor into a redirected stdout without --json",
      "\033[2J" not in result["out"] and "\033[H" not in result["out"],
      repr(result["out"][:80]))

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

# -- the one line the two readers are not shown alike --------------------
#
# The QR key is kept back from a browser, so the line pointing at it is kept
# back too: offering it would advertise something the control route then
# refuses, which is the objection the ? listing already answers for the escape
# key. The banner is therefore rendered twice, and this is the difference. It
# is worth pinning in both directions, because the failure that costs anything
# is the quiet one, where both copies come out the same and nobody notices
# which.

result = run([], [v5_packet(0)], keyboard=True)
terminal = plain(result["err"])
greeting = plain(result["greeting_when_serving"]["banner"])
check("the terminal is told about the q key", "press q for a QR code" in terminal,
      repr(terminal[:400]))
check("the browser is not", "press q for a QR code" not in greeting,
      repr(greeting[:400]))
check("and is still given the rest of the banner",
      "Listening for NetFlow" in greeting and "Web interface" in greeting)

# Without a keyboard there is nothing to press, so neither reader is told.
result = run([], [v5_packet(0)], keyboard=False)
check("a run with no keyboard tells nobody about the key",
      "press q for a QR code" not in plain(result["err"])
      and "press q for a QR code"
      not in plain(result["greeting_when_serving"]["banner"]))

# -- a Host naming another port is reported, once ------------------------
#
# The refusal happens on a request thread and says nothing to the browser,
# deliberately, since telling it apart from a wrong token would tell somebody
# probing which half they had right. The reader at the terminal is owed more,
# and this is where they get it: on the receive thread, because a line written
# from a request thread lands inside the scroll region and takes the pinned
# header and the status bar with it.
#
# Once a run, and that is the part worth pinning. It is a fact about how the
# collector was started rather than about the request, so a second telling
# says nothing, and one per request would hand anyone who can reach the port a
# way to scribble over the display for as long as they liked.

result = run([], [v5_packet(0)] * 4, port_notices=[(2, 9000)])
err = plain(result["err"])
check("a port mismatch is reported on stderr", "asked for port 9000" in err,
      repr(err[-300:]))
check("naming the port actually being served", "listening on 2056" in err)
check("and the flag that settles it", "--web-port 9000" in err)
check("nothing about it reaches stdout, where the flows are",
      "9000" not in result["out"])
check("nor the browser, which was told 404 and nothing else",
      not any("9000" in text for _kind, text in result["prose"]))

result = run([], [v5_packet(0)] * 8, port_notices=[(2, 9000), (4, 9000),
                                                   (6, 9001)])
err = plain(result["err"])
check("three refusals are still reported once",
      err.count("asked for port") == 1, str(err.count("asked for port")))
check("and it is the first of them that is reported",
      "asked for port 9000" in err and "9001" not in err)

result = run([], [v5_packet(0)] * 4)
check("a run nothing was refused in says nothing",
      "asked for port" not in plain(result["err"]))


# -- and pressing it draws the symbol, on the terminal only --------------
#
# The only route to this key is a terminal, since a browser may not press it,
# so it is the only key here that has to be pressed the other way to be
# reached at all. What that covers is the wiring between the dispatch and the
# encoder, which nothing else touches: the URL it is handed, the window it
# measures, and the stream it writes to.

result = run([], [v5_packet(0)], keyboard=True, presses=["q"],
             window=(80, 40), argv=["--hide-status"])
terminal = plain(result["err"])
check("pressing q draws a symbol on the terminal", "█" in terminal,
      repr(terminal[-200:]))
check("with the URL under it, which is the URL the interface printed",
      "http://127.0.0.1:2056/t/test-token/" in terminal)
check("and none of it goes to stdout, where the flows are",
      "█" not in result["out"])
check("nor to the browser, which may not press the key",
      not any("█" in text for _kind, text in result["prose"]))

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

# Whether the browser has any reason to fetch the flags font. False here,
# because this run asked for no countries, and the page fetches the font on
# the strength of this and nothing else. An eager fetch is what that replaced,
# and it would send 78 KB to every browser watching a run like this one.
marking = [s["countries"] for s in statuses(result) if "countries" in s]
check("the status says whether countries are being marked",
      marking != [], str(len(statuses(result))))
check("and says no for a run with no database to mark from",
      marking and not any(marking), str(marking))
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
