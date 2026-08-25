"""The colour scale behind the BYTES column.

The column is tinted along a cool-to-hot ramp so the size of a flow reads at a
glance. Byte counts span several orders of magnitude, so position on the ramp
is logarithmic: every step up the ramp is a constant multiple of bytes rather
than a constant number of them.
"""

import argparse
import math
from collections import deque

from .colour import C

# xterm-256 indices: blue through teal and green into sand, orange and red.
#
# Chosen from the muted end of the cube, none of them at full saturation. The
# rest of the report is drawn in the terminal's own sixteen colours, which take
# their tone from whatever theme the reader has chosen, and a ramp of primaries
# beside them looks like something that wandered in from another program. These
# sit at a similar weight: enough separation to rank a column at a glance,
# without any one figure shouting.
SIZE_RAMP = (60, 61, 67, 73, 72, 71, 107, 143, 179, 173, 167, 131)

SIZE_SCALE_FLOOR = 64            # cold end of the ramp, under one bare ACK
DEFAULT_SIZE_SCALE_MAX = 100 * 1024   # "100.0K" as the BYTES column spells it
MIN_DYNAMIC_SCALE_MAX = 4096     # stops an all-tiny capture from running hot


class SizeScale:
    """Maps a flow's byte count onto a colour from SIZE_RAMP.

    A fixed scale never moves. A dynamic one ranges against the largest flow
    observed, either since start or, when `window` is set, among the last
    `window` flows, so the colours track what the traffic is doing now instead
    of being pinned by one big transfer an hour ago.
    """

    def __init__(self, top=DEFAULT_SIZE_SCALE_MAX, dynamic=False, window=0):
        self.dynamic = dynamic
        self.window = window
        # Remembered so that turning re-ranging off returns to where the
        # scale was, rather than to the default.
        self.fixed_top = top
        # A dynamic scale starts small and grows; a fixed one never moves.
        self.top = MIN_DYNAMIC_SCALE_MAX if dynamic else top
        self.largest = 0
        self.seen = 0
        # Sliding window maximum, used only when `window` is set: (position,
        # octets) held in decreasing order of octets, so the front is always
        # the largest flow still in scope. A run-long scale ranges against
        # `largest` instead and leaves this empty.
        self._recent = deque()

    def observe(self, octets):
        """Fold one decoded flow into a dynamic scale. A no-op on a fixed one."""
        octets = octets or 0
        self.largest = max(self.largest, octets)
        if not self.dynamic:
            return

        if not self.window:
            # A run-long scale only ever grows, so the running maximum says
            # everything. Ranging it through the deque instead would retain one
            # entry per flow on a stream of ever-smaller flows, since nothing
            # would evict them.
            self.top = max(self.largest, MIN_DYNAMIC_SCALE_MAX)
            return

        self.seen += 1
        # Anything smaller than this flow and older than it can never be the
        # window maximum again, so it can go.
        while self._recent and self._recent[-1][1] <= octets:
            self._recent.pop()
        self._recent.append((self.seen, octets))

        oldest = self.seen - self.window
        while self._recent[0][0] <= oldest:
            self._recent.popleft()

        self.top = max(self._recent[0][1], MIN_DYNAMIC_SCALE_MAX)

    def fraction(self, octets):
        """Position on the ramp: 0.0 at the floor, 1.0 at the top of the scale."""
        if not octets or octets <= SIZE_SCALE_FLOOR:
            return 0.0
        span = math.log10(self.top) - math.log10(SIZE_SCALE_FLOOR)
        if span <= 0:
            return 1.0
        pos = (math.log10(octets) - math.log10(SIZE_SCALE_FLOOR)) / span
        return min(1.0, max(0.0, pos))

    def set_dynamic(self, dynamic):
        """Turn re-ranging on or off, keeping any configured window."""
        if dynamic == self.dynamic:
            return
        self.dynamic = dynamic
        self._recent.clear()
        self.seen = 0
        if not dynamic:
            self.top = self.fixed_top
        elif self.window:
            # A windowed scale knows nothing until flows arrive to fill it.
            self.top = MIN_DYNAMIC_SCALE_MAX
        else:
            self.top = max(self.largest, MIN_DYNAMIC_SCALE_MAX)

    def set_top(self, top):
        """Pin the scale to a fixed top, leaving re-ranging behind."""
        self.fixed_top = top
        self.dynamic = False
        self.top = top
        self._recent.clear()
        self.seen = 0

    def paint(self, text, octets):
        """Wrap text in this flow's colour. Returns it untouched when colour is off."""
        if octets is None or not C.enabled():
            return text
        index = SIZE_RAMP[round(self.fraction(octets) * (len(SIZE_RAMP) - 1))]
        return f"\033[38;5;{index}m{text}{C.RESET}"


class SpanScale:
    """Colours byte counts against the range in front of you.

    The flow display needs a scale that means the same thing from one line to
    the next, so it uses a fixed or slowly moving top. A report is different:
    it is read all at once, and the useful question is which of these figures
    is large compared to the others. So the ramp is stretched over exactly the
    values being printed, smallest to largest, and a figure's colour says where
    it falls among its neighbours rather than against any absolute idea of big.

    Nothing is always the cold end. A zero sits at the bottom of the ramp even
    where the other figures are larger, and a report whose every figure is zero
    is painted entirely cold, which is what a run that carried nothing should
    look like. Equal figures share the middle of the ramp only when there is
    something there to be equal about.
    """

    def __init__(self, values=()):
        usable = sorted(value for value in values if value and value > 0)
        self.low = usable[0] if usable else 0
        self.high = usable[-1] if usable else 0

    def fraction(self, octets):
        if not octets or octets <= 0 or self.high <= 0:
            return 0.0
        if self.high <= self.low:
            # Every figure is the same size, so none stands out. Sitting
            # mid-ramp says that better than painting them all hot or all cold.
            return 0.5
        span = math.log10(self.high) - math.log10(self.low)
        if span <= 0:
            return 0.5
        pos = (math.log10(max(octets, self.low)) - math.log10(self.low)) / span
        return min(1.0, max(0.0, pos))

    def paint(self, text, octets):
        """Wrap text in this figure's colour. Untouched when colour is off."""
        if octets is None or not C.enabled():
            return text
        index = SIZE_RAMP[round(self.fraction(octets) * (len(SIZE_RAMP) - 1))]
        return f"\033[38;5;{index}m{text}{C.RESET}"


def size_scale_arg(text):
    """Parse --size-scale-max: a byte count, optionally suffixed K, M, G or T."""
    raw = text.strip().lower()
    factor = 1
    if raw and raw[-1] in "kmgt":
        factor = {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4}[raw[-1]]
        raw = raw[:-1]
    try:
        value = float(raw) * factor
    except (ValueError, OverflowError):
        # from None: argparse shows what is raised here and nothing else, and
        # the float() failure underneath adds nothing a reader of a bad
        # --size-scale-max needs to see.
        raise argparse.ArgumentTypeError(f"not a byte count: {text!r}") from None
    # "nan" and "inf" survive float() but blow up in int() below.
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError(f"not a byte count: {text!r}")
    if value <= SIZE_SCALE_FLOOR:
        raise argparse.ArgumentTypeError(
            f"must be more than {SIZE_SCALE_FLOOR} bytes, the bottom of the scale")
    return int(value)


def size_window_arg(text):
    """Parse --size-scale-window: how many recent flows the scale ranges over."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a flow count: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1 flow")
    return value
