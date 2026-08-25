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

    def start(self):
        """Try to claim the top row. Returns True if the header is now pinned."""
        if not self.stream.isatty() or not enable_windows_vt():
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
