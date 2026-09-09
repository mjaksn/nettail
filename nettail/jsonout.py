"""Where a run's JSON records go, and how a destination is spelled.

`--json` was a mode before it was a destination: the records went to stdout,
the table was not drawn, the keyboard was never started, and the browser
became the only human view there was. It takes a destination now. A run that
names a file gets its records there while the console and the browser go on
behaving exactly as they do with no flag at all, and the bare flag still means
stdout and still means all of that.

The split is the whole of this module, and `to_stdout` is the part worth
knowing. Every guard in the program that used to ask `args.json` was asking
whether stdout was carrying records rather than whether records were being
written, and the two were the same question only while stdout was the one
place they could go. A path is truthy, so a guard left as it was would take a
run writing to a file for a run writing to stdout and would stop drawing the
display this flag was extended to keep. The predicate lives here, below the
program rather than inside `cli.py`, because `keys.py` asks it too and the
imports run one way.
"""
import argparse
import configparser
import io
import json

# The destination the bare flag means, and the one a person writes when they
# want to say it out loud. A single hyphen is what every other program on a
# command line means by stdout, so there is nothing here to learn.
STDOUT = "-"


def dest_arg(text):
    """One `--json` destination, off the command line or out of a file.

    The words a settings file spells a switch with are refused rather than
    taken as a name to open. `json = true` in a file meant the flag itself
    while `--json` took no argument, and reading it now as a request for a
    file called `true` would be the quietest way there is to lose somebody's
    records: the run would start, the console would look right, the browser
    would look right, and the flows would be going into a file nobody would
    think to look in. What a switch in a file is read against is
    configparser's own table, so that table is what is refused here and the
    two cannot come apart.

    An empty destination is refused for the reason `--config ""` is: a value
    that arrived from an unset shell variable is a mistake to report rather
    than a request to guess at.
    """
    text = text.strip()
    if not text:
        raise argparse.ArgumentTypeError(
            "needs a destination: %r for stdout, or a path to append to"
            % (STDOUT,))
    if text.lower() in configparser.ConfigParser.BOOLEAN_STATES:
        raise argparse.ArgumentTypeError(
            "%r was how a settings file asked for the flag itself, and this "
            "option takes a destination now: %r for stdout, or a path to "
            "append to" % (text, STDOUT))
    return text


def to_stdout(args):
    """Whether stdout is the thing carrying the records.

    Asked of `args` rather than of a value, because the two callers outside
    `cli.py` are key handlers holding nothing else, and asked as a function
    rather than written out at each site so that there is one place the
    sentinel is compared against and no site can drift back into testing a
    path for truth.
    """
    return getattr(args, "json", None) == STDOUT


class Records:
    """The sink a run's JSON records go to, whichever kind it is.

    A file is opened and held. stdout is neither, and is looked up at each
    write instead: it is wrapped after the arguments are parsed, by the
    colour stripper and by the country one, and a handle taken here would be
    a fourth opinion about which of those wrappers the records should go
    through. Looking it up is also what keeps a bare `--json` byte for byte
    the run it always was.
    """

    def __init__(self, dest):
        self.dest = dest
        self.handle = None
        if dest != STDOUT:
            # Appended to rather than truncated. This is a collector, the
            # arrangement it is written for is one that runs under systemd,
            # and a restart that threw away the morning's records would be a
            # poor price for the tidiness of starting empty. Line buffered, so
            # that `tail -f` shows a flow when the flow arrives rather than
            # when four kilobytes of them have gone by, and with the newline
            # written rather than translated, so that one object per line is
            # what the file holds on Windows too.
            self.handle = io.open(dest, "a", encoding="utf-8", buffering=1,
                                  newline="\n")

    @property
    def is_stdout(self):
        return self.handle is None

    def write(self, record):
        """One record, on a line of its own."""
        line = json.dumps(record, default=str)
        if self.handle is None:
            # Flushed for the reason it always was: something is parsing this
            # and a block of records held back is a consumer told nothing for
            # as long as the link is quiet.
            print(line, flush=True)
        else:
            self.handle.write(line + "\n")

    def close(self):
        """Give the file back, if a file is what this was."""
        if self.handle is not None:
            self.handle.close()
            self.handle = None
