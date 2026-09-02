"""ANSI colour codes, and the switches that turn them off.

There are two readers of what this program writes, a terminal and a browser,
and they do not have to want the same thing. The usual case is a service unit
whose stdout goes to a file, where escape codes are somebody else's problem to
strip, while the browser watching the same collector is a colour-capable
reader that should have them.

So colour is painted once, at full strength, and taken out again on the way to
whichever consumer is not having it. `PlainStream` is that boundary, and
`strip_colour` is what it does. Painting once rather than twice is what keeps
one flow's cells built once, which is the rule the display and the feed share.
"""
import re


class C:
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GREY = "\033[90m"

    @classmethod
    def disable(cls):
        for name in dir(cls):
            if name.isupper():
                setattr(cls, name, "")

    @classmethod
    def enabled(cls):
        """False once disable() has blanked the codes.

        This is the switch at the source, thrown only when nothing at all
        wants colour. When one consumer wants it and another does not, the
        codes are painted and taken out at the boundary instead, so ask
        `colour_on(stream)` rather than this if the question is about what a
        particular reader ends up seeing.
        """
        return bool(cls.RESET)


# Colour and nothing else. Every other escape this program writes is a cursor
# move, an erase, or a pair of scroll margins, and those are how the sticky
# header and the status bar work at all: taking them out would leave the
# display drawing over itself while looking, in a file, exactly as it should.
_SGR = re.compile("\033\\[[0-9;]*m")


def strip_colour(text):
    """`text` with the colour taken out and every other escape left alone.

    Whole sequences arrive here, because every escape in this program is
    written as part of one string handed to one `write`. Nothing buffers a
    half-written escape across two calls, so this does not have to either.
    """
    return _SGR.sub("", text)


class PlainStream:
    """A stream that takes what it is given without the colour.

    Wrapped around stdout or stderr for a run whose terminal is not having
    colour while the browser is. Everything downstream goes on painting as it
    always did and this takes it out again on the way past, which is why
    adding a second consumer did not mean threading a colour setting through
    every function that prints.

    It also answers `colour_on`, so the one place that changes its words
    rather than only its escapes, the superseded-name marker in the host list,
    can ask where it is writing to.
    """

    def __init__(self, stream):
        self.stream = stream

    def write(self, text):
        self.stream.write(strip_colour(text))
        # What the caller handed over, not what was written. A short count
        # would look to a caller like a stream that could not take it all.
        return len(text)

    def writelines(self, lines):
        # Its own rather than delegated, because a delegated one would reach
        # the stream underneath without passing what it carries through the
        # stripping above, and colour would arrive at a reader that refused
        # it. Nothing here calls it; the wrapper stands in for a real stream
        # and has to behave like one.
        for line in lines:
            self.write(line)

    def flush(self):
        self.stream.flush()

    def isatty(self):
        # Passed through, and it matters: whether the x key may clear the
        # screen, and whether the sticky header and the status bar may claim
        # the window, are settled by asking stdout this, and none of them has
        # anything to do with colour. The keyboard is not among them. It asks
        # stdin, which is never wrapped.
        return self.stream.isatty()

    def fileno(self):
        return self.stream.fileno()

    def __getattr__(self, name):
        """Everything else a stream has, from the one underneath.

        This stands where `sys.stdout` and `sys.stderr` stood, so anything
        reaching past the five methods above, for `encoding`, `buffer` or
        `reconfigure`, should find what it would have found there. Without this
        the UTF-8 reconfigure that runs before the wrapping is installed would
        quietly not run at all on a second pass through `main` in one process,
        and a Windows console page would take the display down on its first
        arrow.

        Only reached for names this class does not define, so `write` and the
        rest above still win. `stream` is refused explicitly: it is set in
        `__init__` and looking it up through here would be a loop.
        """
        if name == "stream":
            raise AttributeError(name)
        return getattr(self.stream, name)


def colour_on(stream):
    """Whether what is written to this stream keeps its colour."""
    return C.enabled() and not isinstance(stream, PlainStream)
