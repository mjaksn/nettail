"""Colour in the help, which argparse settles before this program can.

`--no-color`, `NO_COLOR` and a redirected stdout each turn colour off, and the
switch that reads them runs after the arguments have been parsed. Python 3.14
gave argparse a colour scheme of its own and chooses it during parsing, which
is upstream of that switch: on 3.14 the usage line came out painted whatever
the reader had asked for, `nettail --no-color --help` included, and a
redirected `--help` carried escapes into the file. The README quotes the usage
line as plain text, which is the other reason it has to stay that way.

The lever is FORCE_COLOR, set here and inherited by everything this suite
starts. It overrides the terminal check, so a captured subprocess is coloured
exactly as a terminal would be, and the failure reproduces on a build machine
rather than only in front of a person. That also makes this the suite that
notices if the environment a developer happens to be sitting in starts
colouring the help behind their back.

Before 3.14 argparse had no colours to force, so every check below passes on
its own there. The guard is what says whether the rest of the suite is
proving anything.
"""
import argparse
import os
import subprocess
import sys

from harness import ROOT, SCRIPT, check, finish, plain

# Set before anything is started rather than passed to each call: a child
# inherits the environment, and harness has already put PYTHONPATH there for
# the same reason.
os.environ["FORCE_COLOR"] = "1"


def run(*flags):
    """The program's own output for one argv, both streams, as it was written.

    Not stripped, because whether anything needed stripping is the question.
    """
    proc = subprocess.run([sys.executable, *SCRIPT, *flags],
                          capture_output=True, text=True, cwd=ROOT)
    return proc.stdout + proc.stderr


# --- the lever works, so a pass below means something -----------------------
# Asked of argparse directly rather than assumed from the version number. If a
# later release stops honouring FORCE_COLOR, or renames the keyword, the
# checks after this one would go on passing while testing nothing, and this is
# the line that says so.
if sys.version_info >= (3, 14):
    probe = argparse.ArgumentParser(prog="probe", color=True)
    helped = probe.format_help()
    check("FORCE_COLOR reaches argparse, so the checks below have teeth",
          plain(helped) != helped,
          "argparse produced no colour even when asked; the rest proves nothing")

# --- and the program's own help is plain whatever it says -------------------
usage = run("--help")
check("--help is free of escapes", plain(usage) == usage, repr(usage[:90]))
check("and still opens the way the README quotes it",
      usage.startswith("usage: nettail"), repr(usage[:60]))

# The one that named the bug: the flag that exists to turn colour off could
# not reach the help, because the help is printed while the flag is still
# being parsed.
denied = run("--no-color", "--help")
check("--no-color --help is free of escapes",
      plain(denied) == denied, repr(denied[:90]))

version = run("--version")
check("--version is free of escapes",
      plain(version) == version, repr(version[:90]))

# An unusable argv prints usage too, by the same formatter and down the same
# path, so it goes stale in the same way if only the help above is watched.
rejected = run("--nosuchflag")
check("the usage printed for a bad option is free of escapes",
      plain(rejected) == rejected, repr(rejected[:90]))
check("and it still says what was wrong",
      "--nosuchflag" in rejected, repr(rejected[-90:]))

finish("help colour")
