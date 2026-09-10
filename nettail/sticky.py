"""Pinning the column header to the top row of the window.

Uses the VT100 scroll region (DECSTBM). The header is drawn once on row 1 and
scrolling is confined to rows 2..bottom, so ordinary print() calls scroll
underneath a header that never moves. The cost is scrollback: most terminals
discard lines that scroll out of a margin region instead of keeping them.
"""

import os
import shutil
import sys

from .colour import C
from .display import HEADER_LINE

HEADER_ROWS = 1          # screen rows reserved at the top
MIN_STICKY_ROWS = 6      # below this the pinned header eats the whole window
RESIZE_POLL_LINES = 16   # how often to re-measure the window, in flow lines


def scroll_region(rows, top_reserved=0, bottom_reserved=0):
    """The DECSTBM string for a window with rows held back at either end.

    DECSTBM is one pair of margins, not two settings, so the header at the top
    and the status bar at the bottom cannot each claim their own. Both ask here
    instead, saying only how many rows they need, and whoever writes the region
    writes it once with the total.
    """
    return f"\033[{top_reserved + 1};{rows - bottom_reserved}r"


def enable_windows_vt():
    """Turn on ANSI escape processing for the Windows console. True if usable."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)          # STD_OUTPUT_HANDLE
        if handle in (0, -1):
            return False
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False                             # not a real console
        enabled = 0x0004                             # VIRTUAL_TERMINAL_PROCESSING
        if mode.value & enabled:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | enabled))
    except Exception:
        return False


class StickyHeader:
    """Pins HEADER_LINE to the top of the window while flows scroll below it."""

    def __init__(self, stream=None):
        self.stream = stream if stream is not None else sys.stdout
        self.active = False
        self.rows = 0
        self.cols = 0
        self._since_poll = 0
        # Rows the status bar has claimed at the foot of the window. Left at
        # zero the header behaves as it always has; set before start() it is
        # folded into the one scroll region both features share.
        self.bottom_reserved = 0

    def drawable(self):
        """Whether this terminal takes a pinned header at all.

        Deliberately not a question about room. `start` and `resume` ask that
        for themselves, and keeping the two apart is what lets a refusal from
        either of them mean the one thing it says. They were one question
        until the k key had to tell them apart, and it had to because a
        console that will not take the escapes is not a window with no room
        in it, and answering the reader as though it were blames the window
        for something the window never did.
        """
        return self.stream.isatty() and enable_windows_vt()

    def start(self):
        """Try to claim the top row. Returns True if the header is now pinned."""
        if not self.drawable():
            return False
        size = shutil.get_terminal_size(fallback=(0, 0))
        if size.lines - self.bottom_reserved < MIN_STICKY_ROWS or size.columns < 1:
            return False
        self.rows, self.cols = size.lines, size.columns
        self.active = True
        self._paint()
        return True

    def _paint(self):
        """Clear the screen, draw the header, and set the scroll region."""
        # A header that wrapped would push itself out of its own row.
        head = HEADER_LINE[:self.cols]
        region = scroll_region(self.rows, HEADER_ROWS, self.bottom_reserved)
        self.stream.write(
            "\033[2J"                                 # clear the screen
            f"{region}"                               # scroll rows 2..bottom
            f"\033[1;1H{C.BOLD}{head}{C.RESET}"        # header on row 1
            f"\033[{HEADER_ROWS + 1};1H"               # cursor into the region
        )
        self.stream.flush()

    def repaint(self):
        """Draw the header again, after something else cleared the screen."""
        if self.active:
            self._paint()

    def reflow(self):
        """Write the region again for whatever is reserved now, drawing nothing.

        What the status bar calls when it hands its rows back part way through
        a run. The header is still sitting on row 1 untouched, so there is no
        need to clear the screen and every reason not to: the flows below it
        should stay exactly where they are. DECSTBM homes the cursor, so it is
        put back at the foot of the region afterwards.
        """
        if not self.active:
            return
        self.stream.write(
            scroll_region(self.rows, HEADER_ROWS, self.bottom_reserved)
            + f"\033[{self.rows - self.bottom_reserved};1H")
        self.stream.flush()

    def resume(self, bottom_reserved=0):
        """Pin the header again part way through a run. True if it is up.

        Not `start()`, for the reason `StatusBar.resume` is not `start()`
        either: that one clears the screen, which is right when the collector
        is starting and quite wrong when it has been running for an hour. The
        header needs a row nothing else is using, so one row is scrolled up,
        and the flows that were on screen stay where they are a line higher.

        What the foot has reserved is passed in rather than read off anything
        here, because the bar is what knows whether it is up, and when it is
        the region written here covers both reservations. That is the single
        writer arrangement as it always was: the header owns the margins
        whenever it is active, and the bar asks.
        """
        if self.active or not self.drawable():
            return False
        size = shutil.get_terminal_size(fallback=(0, 0))
        if size.lines - bottom_reserved < MIN_STICKY_ROWS or size.columns < 1:
            return False
        self.rows, self.cols = size.lines, size.columns
        self.bottom_reserved = bottom_reserved
        self.active = True
        head = HEADER_LINE[:self.cols]
        self.stream.write(
            # To the foot of whatever region is in force now, so that the
            # newline scrolls that region rather than overwriting a row in the
            # middle of it. Then the region covering both reservations, the
            # header on the row which has just come free, and back to the foot
            # for the flows to go on arriving at.
            f"\033[{self.rows - bottom_reserved};1H\n"
            + scroll_region(self.rows, HEADER_ROWS, self.bottom_reserved)
            + f"\033[1;1H{C.BOLD}{head}{C.RESET}"
            + f"\033[{self.rows - self.bottom_reserved};1H")
        self.stream.flush()
        return True

    def unpin(self, handing_over=False):
        """Give the top row back part way through a run, leaving the flows.

        `stop()` is the end of a run: it resets the margins outright and parks
        the cursor below them, which is what the summary and the shell prompt
        after it want, and is wrong while flows are still arriving.

        `handing_over` says whether anything else is going to write the
        margins. The status bar does when it is up, and then nothing is
        written here at all, because one pair of margins has one writer and
        this is the moment which of the two it is changes. With nothing else
        left the flows should have the whole window, so the region is reset
        here, as `StatusBar.stop` resets it when the header is not there to
        take it over.

        The newline both of those `stop` methods end on is the one thing not
        borrowed, and leaving it out is what makes the promise above hold.
        They are ending a run, and want the summary and the shell prompt
        after it below the flows rather than on top of them. Here the flows
        are still arriving, into the row at the foot that is already blank
        and waiting for the next one, so a newline sent there scrolls the
        window on with nothing to put on the row it frees. That blank row
        stays where it is put, and the reader is left looking at a gap
        between the last flow before the keypress and the first one after it.
        """
        if not self.active:
            return
        self.active = False
        self.bottom_reserved = 0
        self.stream.write("\033[1;1H\033[2K")          # the header off row 1
        if not handing_over:
            self.stream.write(f"\033[r\033[{self.rows};1H")
        self.stream.flush()

    def check_resize(self):
        """Re-measure the window every so often and redraw if it changed."""
        if not self.active:
            return
        self._since_poll += 1
        if self._since_poll < RESIZE_POLL_LINES:
            return
        self._since_poll = 0
        size = shutil.get_terminal_size(fallback=(self.cols, self.rows))
        if size.lines == self.rows and size.columns == self.cols:
            return
        if size.lines - self.bottom_reserved < MIN_STICKY_ROWS or size.columns < 1:
            self.stop()
            return
        self.rows, self.cols = size.lines, size.columns
        self._paint()

    def stop(self):
        """Release the scroll region. Safe to call when never started."""
        if not self.active:
            return
        self.active = False
        # Reset the margins and park the cursor below them, or the summary and
        # the shell prompt would land inside a region that no longer applies.
        self.stream.write(f"\033[r\033[{self.rows};1H\n")
        self.stream.flush()
