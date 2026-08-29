"""Colour, once there are two readers of it who need not agree.

The terminal and the browser are separate consumers, and the arrangement the
web interface is most useful in is exactly the one where they differ: a
service unit, or a detached container, whose stdout is a file or a pipe and
whose reader is somebody with a browser open. Colour is painted once at the
source and taken out again on the way to whichever of the two is not having
it.

Three things here fail silently when they break, which is why each is pinned.
Only colour may be stripped, because the sticky header and the status bar
write their margins and their cursor moves to the same stream. A run with no
web interface must behave exactly as it did before any of this. And the host
list marks a superseded name with a star when there is no colour to dim it
with, which is the one place a reader without colour is shown different words
rather than the same words undressed.
"""
import argparse
import io
import json
import subprocess
import sys
import time
import urllib.request

from harness import check, finish

import nettail as main
from nettail.cli import colour_choice, for_web, tee, write_hosts
from nettail.colour import C, PlainStream, colour_on, strip_colour
from nettail.feed import Feed

# -- only the colour comes out ---------------------------------------------
#
# A general "strip ANSI" would take the margins and the cursor moves with it,
# and the display would draw over itself while looking right in a file. These
# are the sequences sticky.py and statusbar.py write to the same stream the
# rows go to.

check("a colour code is taken out", strip_colour("\033[36mx\033[0m") == "x")
check("and a 256-colour one, which the size ramp writes directly",
      strip_colour("\033[38;5;71m9\033[0m") == "9")
check("and a bold and a dim", strip_colour("\033[1ma\033[2mb\033[0m") == "ab")
for escape, what in (("\033[1;20r", "the scroll region"),
                     ("\033[5;1H", "a cursor move"),
                     ("\033[2K", "an erase to end of line"),
                     ("\033[2J", "a screen clear"),
                     ("\033[r", "the margins being given back")):
    check("%s survives" % what, strip_colour(escape + "x") == escape + "x",
          repr(escape))
check("text with no escapes at all is untouched",
      strip_colour("192.168.1.10:51000") == "192.168.1.10:51000")

# -- the stream that does the taking ---------------------------------------

buffer = io.StringIO()
plain_stream = PlainStream(buffer)
written = plain_stream.write("\033[36mhi\033[0m")
check("a plain stream writes without the colour", buffer.getvalue() == "hi")
check("and reports what it was handed, not what it wrote",
      written == len("\033[36mhi\033[0m"), written)

# The keyboard and the x key both ask stdout whether it is a terminal, and
# neither question has anything to do with colour, so the answer passes
# through rather than being invented here.


class _Tty:
    def __init__(self):
        self.text = ""

    def write(self, text):
        self.text += text
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return True


check("isatty passes through", PlainStream(_Tty()).isatty() is True)
check("a stream that is not a terminal says so",
      PlainStream(io.StringIO()).isatty() is False)

check("colour_on is False for a stream taking the colour out",
      colour_on(plain_stream) is False)
check("and True for an ordinary one while the codes are painted",
      colour_on(io.StringIO()) is True)


# Standing in for sys.stdout means standing in for all of it. The UTF-8
# reconfigure runs before any of this is installed, but a second pass through
# main() in one process would find the wrapper, and finding no reconfigure
# there would take the Windows code page guard out silently.

class _Console(_Tty):
    encoding = "utf-8"

    def __init__(self):
        _Tty.__init__(self)
        self.reconfigured = False

    def reconfigure(self, **kwargs):
        self.reconfigured = True

    def writelines(self, lines):
        for line in lines:
            self.write(line)


console = _Console()
wrapped = PlainStream(console)
check("an attribute of the stream underneath is reachable",
      wrapped.encoding == "utf-8")
check("and so is reconfigure, which the encoding guard asks for",
      hasattr(wrapped, "reconfigure"))
wrapped.reconfigure(encoding="utf-8", errors="replace")
check("and calling it reaches the real stream", console.reconfigured is True)
wrapped.writelines(["\033[36ma\033[0m", "b"])
check("writelines takes the colour out rather than slipping past it",
      console.text == "ab", repr(console.text))
try:
    unknown = PlainStream(console).nothing_like_this
    reached = unknown is not None
except AttributeError:
    reached = False
check("an attribute neither has is still an error", reached is False)


# -- which reader gets what ------------------------------------------------

def choice(colour="auto", no_color=False, web=False, web_colour="on",
           isatty=True, env=None):
    args = argparse.Namespace(colour=colour, no_color=no_color, web=web,
                              web_colour=web_colour)
    return colour_choice(args, isatty, env)


# Without --web nothing about this run has changed, and that is the claim
# worth pinning hardest: the browser's answer is False, so the pair resolves
# the way the single switch used to and the codes are blanked at the source.
check("a terminal gets colour", choice(isatty=True) == (True, False))
check("a redirected stream does not", choice(isatty=False) == (False, False))
check("always overrides a redirected stream",
      choice(colour="always", isatty=False) == (True, False))
check("never overrides a terminal",
      choice(colour="never", isatty=True) == (False, False))
check("--no-color is still never",
      choice(no_color=True, isatty=True) == (False, False))
check("NO_COLOR turns a terminal's colour off",
      choice(isatty=True, env="1") == (False, False))
check("and loses to an explicit always",
      choice(colour="always", isatty=True, env="1") == (True, False))

# With --web the browser answers for itself. This is the case the whole
# change is about: a detached container has no terminal, and the browser view
# is the only thing that image is for.
check("a browser gets colour where a redirected stdout does not",
      choice(web=True, isatty=False) == (False, True))
check("on a terminal both have it", choice(web=True, isatty=True) == (True, True))
check("--colour never leaves the browser alone",
      choice(colour="never", web=True, isatty=True) == (False, True))
check("NO_COLOR is a convention about a terminal, so it does the same",
      choice(web=True, isatty=True, env="1") == (False, True))
check("--web-colour off turns the browser's off",
      choice(web=True, web_colour="off", isatty=False) == (False, False))
check("and leaves the terminal's alone",
      choice(web=True, web_colour="off", isatty=True) == (True, False))
check("--web-colour without --web decides nothing",
      choice(web=False, web_colour="on", isatty=False) == (False, False))


# -- the host list, where a reader without colour is shown other words ------

class _Names:
    """Enough of a resolver for the host list."""

    @staticmethod
    def local_hosts():
        return [("192.168.1.20", ["nas", "nas-old"])]


coloured = io.StringIO()
write_hosts(_Names(), out=coloured)
check("with colour a superseded name is dimmed",
      "\033[2mnas-old\033[0m" in coloured.getvalue(), coloured.getvalue())
check("and nothing explains a star, because there is none",
      "*" not in coloured.getvalue())

marked = io.StringIO()
write_hosts(_Names(), out=PlainStream(marked))
check("without colour it is starred instead", "nas-old*" in marked.getvalue(),
      marked.getvalue())
check("and the footer says what the star means",
      "* marks a name that has been superseded" in marked.getvalue())


# -- the tee, which has two consumers to satisfy ---------------------------

def prose(bus, out):
    # per_reader, as the real call site passes for this one block: taking the
    # colour out of a finished rendering cannot put the star back.
    tee(bus, "hosts", lambda stream: write_hosts(_Names(), out=stream),
        out=out, per_reader=True)


def published(bus, watcher):
    events, _dropped = bus.drain(watcher)
    return "".join(payload.get("text", "") for name, payload in events
                   if name == "prose")


# Agreeing is the ordinary case, and there the browser is shown the very
# characters the terminal was: one rendering, shared.
bus = Feed()
watcher = bus.subscribe()
terminal = io.StringIO()
prose(bus, terminal)
check("when both take colour they are shown the same characters",
      published(bus, watcher) == terminal.getvalue())

# Disagreeing is the case this was built for. The terminal is having no
# colour, the browser is, and the star has to go to the one without it.
bus = Feed()
watcher = bus.subscribe()
inner = io.StringIO()
prose(bus, PlainStream(inner))
web_copy = published(bus, watcher)
check("a terminal without colour is starred", "nas-old*" in inner.getvalue(),
      inner.getvalue())
check("and has no escape codes in it", "\033[" not in inner.getvalue())
check("while the browser is dimmed", "\033[2mnas-old\033[0m" in web_copy)
check("and is not starred, having colour to say it with", "*" not in web_copy)


# Rendering twice is what the star costs, and it is charged only to the block
# that needs it. Everything else is rendered once and taken apart on the way
# out, because a second pass reads live state again: the summary would ask
# the resolver for every top talker a second time and time the run afresh.

def counted(text="a line\n"):
    calls = []

    def render(out):
        calls.append(1)
        print(text, end="", file=out)

    return calls, render


for label, out, want in (
        ("both take colour", io.StringIO(), 1),
        ("they disagree", PlainStream(io.StringIO()), 1)):
    bus = Feed()
    bus.subscribe()
    calls, render = counted()
    tee(bus, "summary", render, out=out)
    check("an ordinary block is rendered once when %s" % label,
          len(calls) == want, len(calls))

bus = Feed()
watcher = bus.subscribe()
calls, render = counted("\033[2mdim\033[0m\n")
tee(bus, "summary", render, out=PlainStream(io.StringIO()))
check("and the browser still gets its colour from that one rendering",
      "\033[2m" in published(bus, watcher))

bus = Feed()
bus.subscribe()
calls, render = counted()
tee(bus, "hosts", render, out=io.StringIO(), per_reader=True)
check("a per-reader block agreeing is still rendered once", len(calls) == 1,
      len(calls))
bus = Feed()
bus.subscribe()
calls, render = counted()
tee(bus, "hosts", render, out=PlainStream(io.StringIO()), per_reader=True)
check("and twice only when the two readers differ", len(calls) == 2, len(calls))

# The list itself is taken once by the caller and handed to both renderings,
# so a name the resolver finds in between cannot appear in one and not the
# other.
changing = _Names()
first = write_hosts.__doc__ is not None
snapshot = [("192.168.1.20", ["nas", "nas-old"])]
one = io.StringIO()
write_hosts(changing, out=one, hosts=snapshot)
check("the host list renders from the list it is given",
      "nas-old" in one.getvalue() and first)
empty = io.StringIO()
write_hosts(changing, out=empty, hosts=[])
check("and an empty one says so rather than going back to the resolver",
      "none yet" in empty.getvalue(), empty.getvalue())

# -- what reaches the feed when the browser has refused colour -------------

check("prose keeps its colour by default", for_web("\033[36mx\033[0m")
      == "\033[36mx\033[0m")
main.cli._WEB_COLOUR = False
try:
    check("and loses it when the browser is not having any",
          for_web("\033[36mx\033[0m") == "x")
finally:
    main.cli._WEB_COLOUR = True


# -- end to end, which is where the bug was --------------------------------
#
# stdout piped, no terminal anywhere, exactly as a detached container runs.
# What is checked is the banner the browser is greeted with, because that is
# the text the reporter saw arrive white.

def greeting(port, token, extra=()):
    proc = subprocess.Popen(
        [sys.executable, "-m", "nettail", "--web", "--web-port", str(port),
         "--port", str(port + 1), "--web-token", token, "--resolve", "off"]
        + list(extra),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                host = "127.0.0.1:%d" % port
                stream = urllib.request.urlopen(urllib.request.Request(
                    "http://%s/t/%s/events" % (host, token),
                    headers={"Host": host}), timeout=5)
            except OSError:
                time.sleep(0.3)
                continue
            try:
                name = None
                for _ in range(40):
                    line = stream.readline().decode("utf-8", "replace")
                    if line.startswith("event: "):
                        name = line[7:].strip()
                    elif line.startswith("data: ") and name == "hello":
                        return json.loads(line[6:])
            finally:
                stream.close()
            break
        return {}
    finally:
        proc.terminate()
        proc.communicate(timeout=15)


hello = greeting(2251, "colour-e2e")
check("a piped run still greets a browser in colour",
      "\033[" in hello.get("banner", ""), repr(hello.get("banner", ""))[:120])
check("and the columns arrive as ever", bool(hello.get("columns")))

hello = greeting(2253, "colour-e2e", ["--web-colour", "off"])
check("unless the browser was told not to have any",
      "\033[" not in hello.get("banner", "x"), repr(hello.get("banner", ""))[:120])
check("and the banner is still there, just plain",
      "Listening" in hello.get("banner", ""))

# The status payload is figures spelled out by values.py rather than anything
# painted, and it has to stay that way: it is the one publish that never goes
# near the stripper, so a colour code appearing in it would reach a browser
# whatever --web-colour said.
check("nothing in the status payload was ever painted",
      "\033[" not in json.dumps(hello.get("status", {})))

# The source switch is untouched by any of this: it is still what a run
# wanting no colour anywhere throws, and still what the size ramp asks.
check("the codes are painted while anything wants them", C.enabled() is True)

finish("colour")
