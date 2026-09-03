"""The small amount of scaffolding the test suites share.

Each suite is a plain script: it runs top to bottom, prints a line per check,
and exits non-zero if any of them failed. That suits what these tests do, since
most build state up over a sequence of steps and assert along the way, and it
keeps the project free of test dependencies for the same reason the collector
itself is standard library only.

Run one suite with `python tests/test_tally.py`, or all of them with
`python tests/run.py`. Each runs in its own process, which matters: several of
them replace things like `socket.socket` or `shutil.get_terminal_size` for the
duration, and sharing an interpreter would let one suite's fakes leak into the
next.
"""

import atexit
import io
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# How a subprocess starts the collector. The package is the entry point now:
# there is no script at the repository root to point at, and `-m` is what the
# installed `nettail` command runs too.
SCRIPT = ["-m", "nettail"]

# So a suite can be run from anywhere, not only from the repository root.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# And so can a subprocess. `-m nettail` has to import the package, which an
# uninstalled checkout only manages if the repository root is on the path, and
# run.py gives each suite the tests directory as its working directory. Set
# here rather than at each call site: every suite imports this module, and a
# child process inherits the environment.
os.environ["PYTHONPATH"] = os.pathsep.join(
    [ROOT] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p])

# A home directory of this suite's own, and nobody else's.
#
# The collector looks for a settings file, and after the working directory it
# looks under the home directory. So a developer who follows the README once
# and runs `nettail --save-config` has a file in ~/.nettail that every suite
# starting a collector would then read, in process and in a subprocess alike:
# `external-only = true` alone changes what several of them assert, and the
# failure would look like a bug in the code rather than like a file somebody
# saved last week. The repository's .gitignore keeps one out of the checkout
# and can do nothing about the home directory, so the home directory is
# replaced here instead.
#
# Every variable an expanduser or a config search consults is pointed at one
# empty directory, and a child process inherits it. The machine-wide places,
# /etc/nettail and %PROGRAMDATA%, are left alone deliberately: a suite that
# ignored those would not be testing the search this program really does, and
# a machine with a file in /etc has said something about every program on it.
_HOME = tempfile.mkdtemp(prefix="nettail-tests-home-")
atexit.register(shutil.rmtree, _HOME, True)
for _variable in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME", "APPDATA",
                  "LOCALAPPDATA"):
    os.environ[_variable] = _HOME

_ESCAPES = re.compile(r"\033\[[0-9;]*m")
_failures = []


def check(name, cond, detail=""):
    """Record one assertion. The detail is shown only when it fails."""
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if not cond else ""))
    if not cond:
        _failures.append(name)


def plain(text):
    """Text with its colour stripped, for matching on.

    Worth using on anything the report prints: an assertion that matches a
    coloured line literally will break the next time a figure is tinted, which
    says nothing about whether the figure is right.
    """
    return _ESCAPES.sub("", text)


def finish(what):
    """End a suite: say how it went, and exit non-zero if anything failed."""
    print()
    if _failures:
        print("FAILED: " + ", ".join(_failures))
        sys.exit(1)
    print("all %s checks passed" % what)


class FakeTTY(io.StringIO):
    """A stream that claims to be a terminal.

    Colour, the sticky header and the keyboard all ask whether they are talking
    to one, so a test that wants any of them has to answer yes.
    """

    def isatty(self):
        return True
