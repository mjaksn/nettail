"""The k key: pinning the column header and letting it go, mid-run.

The header and the status bar want the same thing, and there is only one of
it. DECSTBM is one pair of margins rather than two settings, so exactly one of
the two writes the region and the other asks, and until this key existed which
one it was could only change when a window was resized. It can change on a
keypress now, in both directions, and that is what this suite is about.

What makes it worth pinning rather than trusting is that every way of getting
it wrong looks fine for a moment. Reset the margins while the bar is up and
the flows scroll over the bar, which the bar repaints twice a second, so it
reads as flicker rather than as a bug. Write the region without the bar's two
rows and the flows use a window two rows short of the one they have. Neither
raises anything and neither is visible in a redirected run, which is the only
kind a suite can watch.

The escape codes are read back off a fake stream here rather than looked at,
which is the same bargain `test_sticky_header` and `test_status_bar` strike:
what a terminal does with them is a manual check and always was.
"""
import io
import shutil

from harness import check, finish

import nettail
from nettail.keys import STICKY_KEY, Controls
from nettail.statusbar import STATUS_ROWS
from nettail.sticky import HEADER_ROWS, StickyHeader, scroll_region

# The same two fakes `test_sticky_header` installs, and for the same reasons:
# the console mode call has no console to work on here, and the window has to
# be a size this file can do arithmetic against rather than whatever the
# runner's terminal happens to be.
nettail.sticky.enable_windows_vt = lambda: True
shutil.get_terminal_size = lambda fallback=(80, 24): shutil.os.terminal_size(
    (100, 24))


class FakeTerminal(io.StringIO):
    """A stream that claims to be a terminal, so the drawing paths run."""

    def isatty(self):
        return True


class Args:
    def __init__(self, **kw):
        self.sticky_header = kw.get("sticky_header", False)
        self.hide_status = kw.get("hide_status", False)
        self.json = kw.get("json", None)


class FakeBar:
    """Only what the key asks of a bar: whether it is up and what it holds."""

    def __init__(self, active=False):
        self.active = active
        self.claimed = 0

    @property
    def reserved(self):
        return STATUS_ROWS if self.active else 0

    def claim(self):
        self.claimed += 1


def pinned(rows=24, cols=100, bottom=0):
    """A header already up, with the region it would have written."""
    stream = FakeTerminal()
    header = StickyHeader(stream)
    header.bottom_reserved = bottom
    header.rows, header.cols = rows, cols
    header.active = True
    stream.seek(0)
    stream.truncate()
    return header, stream


# --- unpinning hands the margins over rather than resetting them ------------
header, stream = pinned(bottom=STATUS_ROWS)
header.unpin(handing_over=True)
written = stream.getvalue()
check("unpinning to a bar writes no region of its own",
      "\033[r" not in written and ";1H" not in written.replace("\033[1;1H", ""),
      repr(written))
check("and clears the header off the top row",
      "\033[1;1H\033[2K" in written, repr(written))
check("and the header knows it is down", not header.active)
check("and holds nothing at the foot any more", header.bottom_reserved == 0)

# With nothing to hand to, the flows should get the whole window back, which
# is the one case where this writes margins at all.
header, stream = pinned()
header.unpin()
written = stream.getvalue()
check("unpinning with no bar resets the margins", "\033[r" in written,
      repr(written))
check("and parks the cursor at the foot rather than at the top",
      "\033[24;1H" in written, repr(written))
# The foot is where the next flow is going and the row there is already
# blank, so a newline would scroll the window on with nothing to put on the
# row it frees, leaving that blank row as a gap in the history. `stop` ends
# on one because a run ending wants the summary below the flows.
check("and scrolls nothing, so no blank row is left among the flows",
      "\n" not in written, repr(written))

# --- pinning again mid-run draws the header without clearing the screen -----
stream = FakeTerminal()
header = StickyHeader(stream)
header.rows, header.cols = 24, 100
ok = header.resume()
written = stream.getvalue()
check("resume reports that the header is up", ok)
check("and clears nothing, so the flows on screen stay where they are",
      "\033[2J" not in written, repr(written))
check("and writes the region for a window with no bar in it",
      scroll_region(24, HEADER_ROWS, 0) in written, repr(written))
check("and puts the header on row 1",
      "\033[1;1H" in written and "PROTO" in written, repr(written)[:200])

# The same again with the bar up, which is the case the arithmetic is for.
stream = FakeTerminal()
header = StickyHeader(stream)
header.rows, header.cols = 24, 100
header.resume(STATUS_ROWS)
written = stream.getvalue()
check("resuming over a bar reserves the bar's rows too",
      scroll_region(24, HEADER_ROWS, STATUS_ROWS) in written, repr(written))
check("and the header says what it is holding for it",
      header.bottom_reserved == STATUS_ROWS)
check("and scrolls from the foot of the region the bar left, not the window",
      "\033[22;1H\n" in written, repr(written))

# A window with no room for a header does not get one, and says nothing it
# would have to take back.
stream = FakeTerminal()
header = StickyHeader(stream)
refused = header.resume(bottom_reserved=23)
check("a window with no room refuses to pin", not refused)
check("and stays down", not header.active)
check("and writes nothing at all", stream.getvalue() == "", repr(stream.getvalue()))


# --- the key, against the two things it has to keep in step -----------------
def controls(args, sticky=None, bar=None):
    return Controls(args, scale=None, resolver=None, sticky=sticky,
                    stats={}, talkers=None, sequences=None, bar=bar,
                    out=io.StringIO())


# Pinned, with a bar up: standing down has to give the bar its margins.
header, _stream = pinned(bottom=STATUS_ROWS)
bar = FakeBar(active=True)
args = Args(sticky_header=True)
said = controls(args, header, bar).handle(STICKY_KEY)
check("the key unpins a header that is up", not header.active, said)
check("and the setting follows it", args.sticky_header is False)
check("and the bar is asked to take the margins back", bar.claimed == 1,
      str(bar.claimed))
check("and the reader is told which way it went",
      said and "unpinned" in said, repr(said))

# Down, with a bar up: pinning again has to reserve the bar's rows.
stream = FakeTerminal()
header = StickyHeader(stream)
header.rows, header.cols = 24, 100
bar = FakeBar(active=True)
args = Args(sticky_header=False)
said = controls(args, header, bar).handle(STICKY_KEY)
check("the key pins a header that is down", header.active, said)
check("and the setting follows it", args.sticky_header is True)
check("and it took the bar's rows off the region",
      header.bottom_reserved == STATUS_ROWS)
check("and the bar is not asked to write anything, since it is not the "
      "writer now", bar.claimed == 0, str(bar.claimed))

# No bar at all is the ordinary case and must not reach for one.
stream = FakeTerminal()
header = StickyHeader(stream)
header.rows, header.cols = 24, 100
args = Args(sticky_header=False)
said = controls(args, header, None).handle(STICKY_KEY)
check("the key works with no bar in the run", header.active, said)
check("and reserves nothing at the foot", header.bottom_reserved == 0)

# --- the setting moves even where nothing can be drawn ----------------------
# The b key's bargain, and this key strikes the same one: a run with the
# records on stdout has no display to pin a header to, but the reader has
# still said what they want and a settings file can still hold it. The failure
# this stops is the quiet one where a key appears to do nothing anywhere.
args = Args(sticky_header=False, json="-")
said = controls(args, pinned()[0], None).handle(STICKY_KEY)
check("with the records on stdout the setting still moves",
      args.sticky_header is True, repr(said))
check("and the reply says so without claiming anything was drawn",
      said == "column header pinned", repr(said))

args = Args(sticky_header=False)
said = controls(args, None, None).handle(STICKY_KEY)
check("and a run with no header object at all is not a traceback",
      args.sticky_header is True, repr(said))

# Redirected into a file or a pipe is the other half of that, and it is the
# one a real run found: the stream is not a terminal, so there is no window
# to hold a region in, and the key used to take the pinning path anyway and
# answer "no room for the column header in a window this size" without moving
# anything. The size was never the problem and the setting has to move.
stream = io.StringIO()                 # a file or a pipe, which is no tty
header = StickyHeader(stream)
args = Args(sticky_header=False)
said = controls(args, header, None).handle(STICKY_KEY)
check("redirected, the setting moves rather than blaming the window size",
      args.sticky_header is True, repr(said))
check("and it is not told there is no room, which was never true",
      said == "column header pinned", repr(said))
check("and nothing was drawn for a header nobody can see",
      not header.active and stream.getvalue() == "",
      repr(stream.getvalue()))

# A console that will not take the escapes is the third way to have nowhere
# to draw, and the one that is a terminal all the same. This is a Windows
# console without VT processing, where isatty answers yes and every escape
# would arrive as characters, so the header may not be drawn and the reader
# may not be told the window is too small: it never was.
nettail.sticky.enable_windows_vt = lambda: False
stream = FakeTerminal()
header = StickyHeader(stream)
args = Args(sticky_header=False)
said = controls(args, header, None).handle(STICKY_KEY)
check("a console that cannot take the escapes still moves the setting",
      args.sticky_header is True, repr(said))
check("and is not told there is no room either", said == "column header pinned",
      repr(said))
check("and has nothing drawn at it",
      not header.active and stream.getvalue() == "",
      repr(stream.getvalue()))
nettail.sticky.enable_windows_vt = lambda: True

finish("sticky header toggle")
