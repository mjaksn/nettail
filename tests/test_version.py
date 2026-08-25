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

finish("version")
