"""--version, and the three places the number has to agree.

The flag is a one-liner. What is worth a suite is everything it has to agree
with: the package attribute it prints, the number in pyproject.toml, and the
heading the changelog opens with. release.yml refuses to publish when the tag
disagrees with the first two, which is a good place to find out and a late
one, so the same comparison is made here where it costs a second.
"""
import os
import re
import subprocess
import sys

from harness import ROOT, SCRIPT, check, finish

import nettail as main

PYPROJECT = os.path.join(ROOT, "pyproject.toml")
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")


def read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


# --- the flag prints the package's own number -------------------------------
proc = subprocess.run([sys.executable, *SCRIPT, "--version"],
                      capture_output=True, text=True, cwd=ROOT)
printed = (proc.stdout + proc.stderr).strip()
check("--version exits cleanly", proc.returncode == 0, str(proc.returncode))
check("and names the program", printed.startswith("nettail "), repr(printed))
check("and prints the package version",
      printed == f"nettail {main.__version__}", repr(printed))

# The import that makes that work is a real ordering dependency: cli.py takes
# __version__ from the package, which only has one because __init__.py assigns
# it above its own submodule imports. Moving that line below them would raise
# on import, so this is really a check that the package still imports at all
# through the same door the console script uses.
check("the package exposes a version at all",
      isinstance(main.__version__, str) and main.__version__,
      repr(getattr(main, "__version__", None)))

# --- and it agrees with what is packaged and published ----------------------
declared = re.search(r'(?m)^version = "([^"]+)"', read(PYPROJECT))
check("pyproject declares a version", declared is not None)
check("pyproject and the package agree",
      declared and declared.group(1) == main.__version__,
      f"pyproject {declared and declared.group(1)}, package {main.__version__}")

heading = re.search(r"(?m)^## \[([^\]]+)\]", read(CHANGELOG))
check("the changelog opens with a released version", heading is not None)
check("and it is this one",
      heading and heading.group(1) == main.__version__,
      f"changelog {heading and heading.group(1)}, package {main.__version__}")

# --- it is offered where a reader would look --------------------------------
usage = subprocess.run([sys.executable, *SCRIPT, "--help"],
                       capture_output=True, text=True, cwd=ROOT).stdout
check("--version is in the help", "--version" in usage, repr(usage[:120]))
check("the help names the program rather than the interpreter",
      usage.startswith("usage: nettail"), repr(usage[:60]))

# --- the dependency ranges, in the three places they are written ------------
#
# pyproject.toml is the one that ships and the one pip enforces. The CI suite
# step installs the same two packages by writing the ranges out again, and
# requirements.lock pins an exact version for the image. Nothing resolves one
# from another, so they are copies, and they have drifted: the CI step went on
# installing netflume <0.2 after the package had moved to <0.5, which left the
# suite green against a decoder the wheel would refuse to sit beside. Held
# here because a copy that disagrees fails nothing on its own.
CI = os.path.join(ROOT, ".github", "workflows", "ci.yml")
LOCK = os.path.join(ROOT, "requirements.lock")

# The runtime list alone. `build-system` declares setuptools a line or two
# away and it is not one of these: it is what turns this tree into a wheel,
# never something the collector imports.
runtime = re.search(r"^dependencies = \[(.*?)^\]", read(PYPROJECT),
                    re.M | re.S)
check("pyproject has a runtime dependency list", runtime is not None)
SPEC = r'"([a-z]+)((?:[<>=]=?[0-9.]+,?)+)"'
ranges = dict(re.findall(SPEC, runtime.group(1) if runtime else ""))
check("pyproject names both dependencies", sorted(ranges) == ["lanname",
                                                              "netflume"],
      str(sorted(ranges)))

ci_ranges = dict(re.findall(SPEC, read(CI)))
for name, spec in sorted(ranges.items()):
    check("the CI step installs the %s range pyproject declares" % name,
          ci_ranges.get(name) == spec,
          "pyproject %r, ci.yml %r" % (spec, ci_ranges.get(name)))

# And the exact pin in the lock file has to be a version those ranges allow,
# or the image is built from something the wheel would refuse.
locked = dict(re.findall(r'^([a-z]+)==([0-9.]+)', read(LOCK), re.M))
for name, spec in sorted(ranges.items()):
    floor = re.search(r'>=([0-9.]+)', spec)
    cap = re.search(r'<([0-9.]+)', spec)
    pin = locked.get(name)
    def parts(text):
        return tuple(int(piece) for piece in text.split("."))
    ok = (pin is not None and floor is not None and cap is not None
          and parts(floor.group(1)) <= parts(pin) < parts(cap.group(1)))
    check("the lock pins a %s the range allows" % name, ok,
          "locked %r against %r" % (pin, spec))

finish("version")
