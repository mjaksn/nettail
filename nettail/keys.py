"""Keyboard control while the collector is running.

Reading keys and acting on them are deliberately separate. `Keyboard` is the
half that needs a real terminal and differs per platform; `Controls` holds the
state the keys act on and can be driven directly, which is what makes the
behaviour testable somewhere without a tty.
"""

import argparse
import os
import sys
import time
from collections import deque

from lanname import MODE_DESC, Resolver

from . import country
from .colour import C
from .display import HEADER_LINE
from .sizescale import size_scale_arg
from .values import human_bytes

PAUSE_BUFFER = 2000      # flows held while paused before the oldest are dropped

# The key that lists the others. Named once, because three things have to agree
# about it: the table below, the dispatch that answers it, and the reminder
# line that sends the reader to it.
HELP_KEY = "?"

# The key that draws the web interface URL as a QR code. Named once for the
# same reason and for one more thing that has to agree: it is kept back from
# the browser, so it appears in the table below, in the dispatch, in
# WEB_EXCLUDED, and in the line under the banner that points a reader at it.
QR_KEY = "q"

# Every key and what it does, in the order the listing shows them.
#
# The only place a key is written down. The dispatch in Controls.actions is
# checked against this by the suite, so a key that works and is listed nowhere,
# or is listed and does nothing, fails a test rather than reaching a reader.
# Neither is the sort of thing anyone notices until they go looking for a key
# that is not there.
KEYS = (
    ("space", "pause and resume printing, holding flows meanwhile"),
    ("x", "clear the screen, and the held flows with it while paused"),
    ("s", "print the traffic summary now, without stopping"),
    ("l", "list the local addresses seen, and their names"),
    ("c", "clear the statistics and restart the runtime clock"),
    ("b", "hide the status bar at the foot of the window, or bring it back"),
    ("d", "re-range the size colour scale as flows arrive, or pin it"),
    ("m", "ask for a new fixed top for the size colour scale"),
    ("h", "cycle host name resolution: off, dns, all"),
    ("n", "show a host by its name in place of its address"),
    ("p", "show hardware addresses on a line under each flow"),
    ("f", "show full domain names instead of the first label"),
    ("e", "show only flows with a public endpoint, or show all"),
    ("g", "mark external addresses with the country they are in, or stop"),
    (QR_KEY, "print a QR code for the web interface URL, and the URL under it"),
    (HELP_KEY, "this list"),
    ("esc", "close the program, printing the exit summary"),
)

# The keys a browser may not press at all, spelled as the table above spells
# them.
#
# The escape key closes the program. Ending a process is a different kind of act
# from turning a column on, and it would end it for everybody: the terminal this
# was started from, and every other browser watching. Mirroring a keyboard is
# not a good enough reason for that to arrive as a side effect, so it does not
# cross. If it is ever wanted it should be a control that says it stops the
# collector, asked for on purpose, rather than one more key in a grid.
#
# The QR key is here for a duller reason: there is nothing for it to do. Its
# answer is drawn out of half block characters for a terminal and written to
# the terminal alone, and what it encodes is the address of the page the
# browser is already looking at. Allowing it would give a browser a key that
# appears to work and visibly does nothing, which is worse than not having it,
# and publishing the symbol instead would put forty columns of block
# characters through a table that has no reason to expect them.
WEB_EXCLUDED = ("esc", QR_KEY)

# The keys a browser may press but is given no button for.
#
# Being pressable and being worth a button are two different questions, which is
# why they are two tables. The help key answers "what are the keys", and a
# browser already has that answer permanently, as a drawer of labelled buttons.
# A button that said "this list" beside the list would be absurd. The key itself
# is another matter: somebody who knows the program will press it out of habit,
# and it costs nothing to answer.
WEB_UNLISTED = (HELP_KEY,)


def web_keys():
    """The keys a browser may press, in the order the listing shows them.

    Derived from `KEYS` rather than written out again, so a key added there
    reaches the browser without anybody remembering to add it twice, and the
    only way to keep one back is to name it in `WEB_EXCLUDED` and say why.
    """
    return tuple((key, doc) for key, doc in KEYS if key not in WEB_EXCLUDED)


def web_buttons():
    """The keys a browser puts a button on: the pressable ones, less the quiet.

    A subset of `web_keys`, never a separate list, so that a key cannot end up
    with a button it is not allowed to press.
    """
    return tuple((key, doc) for key, doc in web_keys()
                 if key not in WEB_UNLISTED)


# The one line printed under the startup banner, which is a pointer and not a
# list. Naming all sixteen ran to two hundred characters and wrapped on any
# ordinary terminal, so a banner already three lines long arrived four or five;
# and it scrolled away with the banner regardless, leaving the reader who
# wanted it an hour later no better off for its having been thorough. The
# listing answers that reader properly and on demand, so this only has to say
# where it is.
#
# It has to say one thing beyond that, though, which is that there are keys at
# all. A reader who does not already know the program answers the keyboard has
# no reason to press anything, ? included, so a line that only named the key
# would be an answer to a question nobody had thought to ask. Hence a sentence
# rather than an instruction.
#
# The key is taken from the constant rather than typed again here, so the line
# cannot come to point at a key that is not the one that answers.
KEY_HELP = (f"keys: the collector takes single keypresses; "
            f"press {HELP_KEY} to list them")

# The two keys with no printable character of their own, as the table spells
# them against what the terminal actually sends. The table is written for a
# reader, who has a space bar and an escape key rather than a \x20 and a \x1b.
KEY_CHARS = {"space": " ", "esc": "\x1b"}

# Wide enough for "space", which is the longest of them. The keys sit in a
# column of their own in the listing, right aligned against the descriptions,
# so the eye runs down the keys rather than hunting along the sentences.
KEY_WIDTH = max(len(key) for key, _doc in KEYS)


def write_keys(out=None, keys=None):
    """List every key and what it does.

    `keys` narrows the listing to a particular set, which is what the browser's
    copy asks for: it cannot press the escape key or the QR key, so a listing
    offering either there would advertise something the control route refuses.

    What the ? key prints, and what the reminder line under the banner points
    at rather than tries to be. A line has room to name the keys or to explain
    them and not both; this is the half with the explanations, and it costs a
    keypress instead of costing every run four lines of banner.

    Written to stderr like the other listings, so that a run with stdout
    redirected into a file gets its answer on the terminal where the question
    was asked rather than into the middle of the flows.
    """
    out = out if out is not None else sys.stderr
    print(f"\n{C.BOLD}{C.BLUE}Keyboard controls{C.RESET}", file=out)
    for key, doc in (KEYS if keys is None else keys):
        print(f"  {C.CYAN}{key:>{KEY_WIDTH}}{C.RESET}  {C.GREY}{doc}{C.RESET}",
              file=out)


class Keyboard:
    """Single keypresses from the terminal, without blocking the receive loop."""

    def __init__(self, stream=None):
        self.stream = stream if stream is not None else sys.stdin
        self.enabled = False
        self._fd = None
        self._saved = None

    def start(self):
        """Put the terminal in cbreak mode. False when there is no terminal."""
        try:
            if not self.stream.isatty():
                return False
            if os.name == "nt":
                import msvcrt  # noqa: F401  present on every Windows
                self.enabled = True
                return True
            import termios
            import tty

            self._fd = self.stream.fileno()
            self._saved = termios.tcgetattr(self._fd)
            # cbreak rather than raw: Ctrl-C has to keep working.
            tty.setcbreak(self._fd)
            self.enabled = True
        except Exception:
            # A stdin that cannot be put into cbreak is a reason to go without
            # keys, never a reason to fail to start.
            self.enabled = False
        return self.enabled

    def stop(self):
        """Give the terminal back. Safe to call when never started."""
        if self._saved is not None:
            try:
                import termios

                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
            except Exception:
                pass
            self._saved = None
        self.enabled = False

    # -- reading ------------------------------------------------------------

    def _pending(self):
        """True when another byte is already waiting, which is how an escape
        sequence is told apart from somebody pressing escape."""
        if os.name == "nt":
            import msvcrt

            return msvcrt.kbhit()
        import select

        return bool(select.select([self.stream], [], [], 0)[0])

    def _raw_char(self):
        if os.name == "nt":
            import msvcrt

            return msvcrt.getwch()
        return self.stream.read(1)

    def poll(self):
        """One keypress, or None if nobody has typed. Never blocks."""
        if not self.enabled or not self._pending():
            return None

        ch = self._raw_char()
        if ch in ("\x00", "\xe0"):
            # Windows sends two bytes for function and arrow keys.
            self._raw_char()
            return None
        if ch == "\x1b" and self._pending():
            # On POSIX the arrow and function keys are escape-prefixed
            # sequences, so a lone \x1b is only really escape when nothing
            # follows it. Quitting on an arrow key would be a nasty surprise.
            while self._pending():
                self._raw_char()
            return None
        return ch

    def read_line(self, prompt, out=None):
        """Ask for a line of text, echoing it. None if the user pressed escape.

        This blocks, so the receive loop stops for as long as the answer takes.
        The socket buffer absorbs the wait, and if the wait is long enough that
        it does not, the export gap is reported like any other.
        """
        out = out if out is not None else sys.stderr
        out.write(prompt)
        out.flush()
        typed = ""
        while True:
            ch = self._raw_char()
            if ch in ("\r", "\n"):          # which one arrives is platform lore
                out.write("\n")
                out.flush()
                return typed
            if ch == "\x1b":
                out.write("  cancelled\n")
                out.flush()
                return None
            if ch in ("\x7f", "\x08"):
                if typed:
                    typed = typed[:-1]
                    out.write("\b \b")
                    out.flush()
                continue
            if ch.isprintable():
                typed += ch
                out.write(ch)
                out.flush()


class Controls:
    """What each key does, and the state it does it to.

    handle() takes a key and returns the line shown to the user, or None when
    the key means nothing here. Everything it touches is passed in, so a test
    can hand it ordinary objects and read the result.
    """

    def __init__(self, args, scale, resolver, sticky, stats, talkers,
                 sequences, started=None, out=None, summary=None, hosts=None,
                 bar=None, on_clear=None):
        self.args = args
        self.scale = scale
        self.resolver = resolver
        self.sticky = sticky
        self.bar = bar
        self.stats = stats
        self.talkers = talkers
        self.sequences = sequences
        self.started = started if started is not None else time.time()
        self.out = out if out is not None else sys.stderr
        # What the s and l keys print. Set by whoever has the counters and the
        # resolver to report on.
        self.summary = summary
        self.hosts = hosts
        # Called when the x key clears the screen, so that a view which is not
        # this terminal can clear itself too. None when there is no such view,
        # which is every run without the web interface.
        self.on_clear = on_clear
        # What the ? key prints. Unlike the summary and the host list this
        # needs nothing from the collector, so the default below answers it
        # here; the hook exists so that whoever has a second view to write the
        # listing to can arrange for it to go to both.
        self.listing = None
        # What the q key prints. Set only by a run that has a web interface to
        # point at, so that being None is the whole of how this knows there is
        # no URL to encode.
        self.qr = None

        self.quit = False
        self.paused = False
        self.lines = 0
        self.held = deque(maxlen=PAUSE_BUFFER)
        self.dropped = 0

    # -- the flows held while paused ---------------------------------------

    def hold(self, rec, hdr):
        """Keep a flow back until the display is resumed."""
        if len(self.held) == self.held.maxlen:
            self.dropped += 1
        self.held.append((rec, hdr))

    def drain(self):
        """Hand back what was held, oldest first, and forget it."""
        waiting = list(self.held)
        self.held.clear()
        return waiting

    # -- dispatch -----------------------------------------------------------

    def actions(self, ask=None):
        """What each key does, keyed by what the terminal sends.

        Handed back rather than built inside handle() so that the set of keys
        can be read without pressing any of them, which is what lets the suite
        hold this and KEYS to each other. A key that works and is listed
        nowhere is as much a defect as one that is listed and does nothing,
        and neither shows up in a test that can only press the keys it already
        knows about.
        """
        return {
            "\x1b": self._quit,
            " ": self._pause,
            "x": self._clear_screen,
            "s": self._summary,
            "l": self._hosts,
            "c": self._clear_stats,
            "b": self._status_bar,
            "n": self._named_hosts,
            "p": self._show_macs,
            "d": self._dynamic,
            "m": lambda: self._fixed_max(ask),
            "h": self._resolve_mode,
            "f": self._fqdn,
            "e": self._external,
            "g": self._country,
            QR_KEY: self._qr,
            HELP_KEY: self._help,
        }

    def handle(self, key, ask=None):
        """Act on one key. `ask` is what to call when a key needs an answer."""
        if not key:
            return None
        action = self.actions(ask).get(key.lower())
        if action is None:
            return None
        message = action()
        if message:
            print(f"{C.CYAN}{message}{C.RESET}", file=self.out)
        return message

    # -- the keys -----------------------------------------------------------

    def _quit(self):
        self.quit = True
        return "closing"

    def _pause(self):
        self.paused = not self.paused
        if self.paused:
            return "paused, flows are being held"
        waiting, dropped = len(self.held), self.dropped
        self.dropped = 0
        note = f"resumed, {waiting} held flow{'' if waiting == 1 else 's'} to print"
        if dropped:
            note += f", {dropped} dropped while the buffer was full"
        return note

    def _clear_screen(self):
        discarded = len(self.held)
        self.held.clear()
        self.dropped = 0
        self.lines = 0
        json_mode = bool(getattr(self.args, "json", False))
        # The screen being cleared is a thing that happens to a terminal, so it
        # happens only where there is one to clear. There are two ways for
        # there not to be, and both of them arrive from a browser.
        #
        # Under --json, stdout is a stream something else is parsing. That used
        # to be unreachable, because --json turns the keyboard off, but the web
        # interface can press this key with --json running, and one keypress
        # would put two escape sequences into the middle of somebody's data.
        #
        # Redirected without --json, stdout is a file or a pipe, and the escape
        # would land in it along with the header reprinted after it. A terminal
        # keyboard could always do that, needing a tty on stdin alone, but a
        # collector run as a service has no terminal at either end and is the
        # arrangement --web is most worth having. So the question is asked of
        # the stream rather than of where the keypress came from.
        screen = not json_mode and sys.stdout.isatty()
        if screen:
            print("\033[2J\033[H", end="", flush=True)
        if self.on_clear is not None:
            self.on_clear()
        if self.sticky is not None and self.sticky.active:
            self.sticky.repaint()
        elif screen:
            print(C.BOLD + HEADER_LINE + C.RESET)
        # The clear took the bar with it. It has nothing new to say yet, so it
        # goes back up saying what it said a moment ago.
        if self.bar is not None and self.bar.active:
            self.bar.repaint()
        if discarded:
            plural = "" if discarded == 1 else "s"
            return f"cleared, {discarded} held flow{plural} dropped"
        return "cleared"

    def _summary(self):
        """Print the report the program prints on the way out, now instead.

        The report is its own confirmation, so nothing is said on top of it.
        """
        if self.summary is None:
            return None
        self.summary()
        return None

    def _hosts(self):
        """List the local addresses seen this session, and their names.

        Like the summary, the list is its own confirmation and says nothing
        on top of itself.
        """
        if self.hosts is None:
            return None
        self.hosts()
        return None

    def _qr(self):
        """Print a QR code for the web interface URL, with the URL under it.

        Like the summary and the host list, the block is its own confirmation
        and says nothing on top of itself. A run without a web interface has
        no URL to encode and says so, because a key that answered a press with
        silence would be indistinguishable from one that had gone wrong.
        """
        if self.qr is None:
            return "the web interface is not running"
        self.qr()
        return None

    def _help(self):
        """List every key and what it does.

        Like the summary and the host list, the listing is its own
        confirmation and says nothing on top of itself. Unlike those two it
        needs nothing from the collector to print, so it is answered here
        rather than handed out to whoever holds the counters, unless somebody
        has set a hook because the listing has to reach more than one place.
        """
        if self.listing is not None:
            self.listing()
        else:
            write_keys(self.out)
        return None

    def _status_bar(self):
        """Take the status bar off the foot of the window, or put it back.

        Nothing happens where there was never a bar to toggle, which is
        the case under --json and when output is redirected into a file. It is
        the same silence those runs get from every other key needing a screen.

        Under --json that used to be true by accident rather than by rule: the
        keyboard is off there, so this could not be reached. A browser can
        reach it, and the bar draws itself on stdout, so one press would put a
        scroll region and two rows of status bar into the middle of somebody's
        data and then repaint them every half second. The bar belongs to a
        terminal, so like the x key it does nothing where there is not one.
        """
        if self.bar is None or getattr(self.args, "json", False):
            return None
        if self.bar.active:
            self.bar.stop()
            self.args.hide_status = True
            return "status bar hidden, the flows have the whole window"
        if not self.bar.resume():
            return "no room for the status bar in a window this size"
        self.args.hide_status = False
        return "status bar shown"

    def _named_hosts(self):
        """Show a host by its name in place of its address, where one is known.

        Only affects rows printed from here on. What is already on screen was
        laid out the other way and is not revisited, which is the same bargain
        the h and f keys strike.
        """
        wanted = not getattr(self.args, "named_hosts", False)
        self.args.named_hosts = wanted
        if not wanted:
            return "showing addresses"
        if self.resolver.mode == "off":
            # Lookups being off does not mean no names: a --hosts file is
            # answered from whatever the mode, so saying nothing is known
            # would be wrong in front of a reader watching names appear.
            if self.resolver.static:
                return ("showing names in place of addresses, though with "
                        "lookups off only the --hosts entries have one: "
                        "press h to look the rest up")
            return ("showing names in place of addresses, though none are "
                    "being looked up: press h to start")
        return "showing names in place of addresses where one is known"

    def _show_macs(self):
        """Put the hardware addresses on a line under the addresses."""
        wanted = not getattr(self.args, "show_macs", False)
        self.args.show_macs = wanted
        if not wanted:
            return "hiding mac addresses"
        return ("showing mac addresses, on the exporters that send them "
                "(v5 never does)")

    def _clear_stats(self):
        self.stats.clear()
        self.talkers.clear()
        self.resolver.stats.clear()
        # The counters go, the stream positions stay. Resetting those would
        # make every exporter look restarted and start the learning again.
        self.sequences.missed.clear()
        self.sequences.units.clear()
        self.sequences.backwards = 0
        self.sequences.resyncs = 0
        self.started = time.time()
        return "statistics cleared"

    def _dynamic(self):
        wanted = not self.scale.dynamic
        self.scale.set_dynamic(wanted)
        if not wanted:
            return f"size scale fixed at {human_bytes(self.scale.top)}"
        if self.scale.window:
            return f"size scale re-ranging over the last {self.scale.window} flows"
        return "size scale re-ranging on the largest flow seen"

    def _fixed_max(self, ask):
        if ask is None:
            return None
        typed = ask("new top of the size scale, K/M/G/T accepted: ")
        if typed is None or not typed.strip():
            return "size scale unchanged"
        try:
            value = size_scale_arg(typed)
        except argparse.ArgumentTypeError as exc:
            return f"{exc}, size scale unchanged"
        self.scale.set_top(value)
        return f"size scale fixed at {human_bytes(value)}"

    def _resolve_mode(self):
        modes = Resolver.MODES
        following = modes[(modes.index(self.resolver.mode) + 1) % len(modes)]
        self.resolver.set_mode(following)
        self.args.resolve = following
        return f"host names: {MODE_DESC[following]}"

    def _fqdn(self):
        wanted = not self.resolver.fqdn
        self.resolver.set_fqdn(wanted)
        self.args.fqdn = wanted
        return ("showing full domain names, cached names dropped" if wanted
                else "showing short host names, cached names dropped")

    def _external(self):
        self.args.external_only = not self.args.external_only
        return ("showing only flows with a public endpoint"
                if self.args.external_only else "showing every flow")

    def _country(self):
        """Mark external addresses with the country they are in, or stop.

        The one key whose answer depends on something outside the program.
        Without a database there is nothing to turn on, and a key that
        silently did nothing would be read as a display that knows no
        countries rather than as a run that was never given a file to read
        them out of, so it says which.

        Whether it is on lives in `country` rather than on `args`, where the
        other display switches live. Three modules with no arguments in common
        ask the question, and the reasoning is written where the state is.
        """
        if not country.loaded():
            return ("no country database is loaded: start with --country, or "
                    "--country-db pointing at a MaxMind format file")
        if country.show(not country.showing()):
            return "marking external addresses with their country"
        return "no longer marking countries"
