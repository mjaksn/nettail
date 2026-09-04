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

`FilterStream` underneath it is that boundary in the general: everything a
real stream is asked for, with one method to override. Colour was the only
thing the two readers disagreed about until a country flag became the second,
and `country.CodeStream` stands on the same plumbing rather than on a second
copy of it.
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


def strip_payload(value):
    """The same, for a structure of finished strings rather than one block.

    `strip_colour` is the boundary for prose, which arrives as a block of text
    somebody printed. The details report arrives as nested lists of strings
    painted where they were built, with a serial, a flag and a count among
    them, so taking the colour out of it means walking it. What is not a
    string, a list, a tuple or a dict comes back untouched, which is what
    leaves a serial a serial and a flag a flag.

    A tuple comes back as a list, which is what the payload becomes on its way
    through JSON in any case.
    """
    if isinstance(value, str):
        return strip_colour(value)
    if isinstance(value, dict):
        return dict((key, strip_payload(item)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return [strip_payload(item) for item in value]
    return value


class FilterStream:
    """A stream that rewrites what passes through it on its way to another.

    There are two things a reader may be shown differently from the browser
    watching the same collector, colour and a country flag, and neither is a
    difference in what the text says. So the text is written once at full
    strength and rewritten on the way to whichever reader is not having it,
    and this is the plumbing both of those boundaries stand on: everything a
    real stream is asked for, with one method to override.

    Two of these can be wrapped around one terminal, in either order, which is
    why `colour` below asks through the wrapping rather than answering for
    itself.
    """

    def __init__(self, stream):
        self.stream = stream

    def transform(self, text):
        """What this boundary takes out. The base takes out nothing."""
        return text

    @property
    def colour(self):
        """Whether colour written here still reaches the reader.

        Read through whatever is wrapped, so that a stream spelling flags out
        inside one taking colour out, or the same pair the other way round,
        gives the one answer either way. A real stream has no such attribute
        and is taken to be keeping the colour, which is what `colour_on`
        assumes for anything it is handed.
        """
        return getattr(self.stream, "colour", True)

    def write(self, text):
        self.stream.write(self.transform(text))
        # What the caller handed over, not what was written. A short count
        # would look to a caller like a stream that could not take it all.
        return len(text)

    def writelines(self, lines):
        # Its own rather than delegated, because a delegated one would reach
        # the stream underneath without passing what it carries through the
        # rewriting above, and colour would arrive at a reader that refused
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
        reaching past the methods above, for `encoding`, `buffer` or
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


class PlainStream(FilterStream):
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

    # Answered here rather than through whatever is wrapped: this is the
    # boundary colour stops at, whatever else is in front of or behind it.
    colour = False

    def transform(self, text):
        return strip_colour(text)


def behind(stream, kind):
    """Whether `stream` already has a wrapper of this kind somewhere in it.

    `isinstance` answers for the outermost wrapper alone, and there can be
    two: a terminal having neither colour nor flags is behind both. `main`
    asks this before wrapping either, so that a second pass through it in one
    process, which is a thing the suite does, does not put a second copy of
    one around a stream that already has it.
    """
    while isinstance(stream, FilterStream):
        if isinstance(stream, kind):
            return True
        stream = stream.stream
    return False


def colour_on(stream):
    """Whether what is written to this stream keeps its colour.

    Asked of the stream rather than by looking for a particular wrapper
    around it, because there can be more than one: a terminal spelling flags
    out as letters is behind a wrapper of its own, and a colour question that
    only recognised the outermost would answer yes for a stream that takes
    the colour straight back out.
    """
    return C.enabled() and getattr(stream, "colour", True)
