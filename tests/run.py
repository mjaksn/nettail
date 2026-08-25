#!/usr/bin/env python3
"""Run every test suite, each in its own process.

    python tests/run.py            all of them
    python tests/run.py tally      only the suites whose name contains "tally"
    python tests/run.py -v         show every check, not just the failures

Separate processes are not fussiness: several suites replace `socket.socket`,
`shutil.get_terminal_size` or the keyboard for their duration, and sharing an
interpreter would let one suite's fakes decide another suite's result.
"""

import glob
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def suites(patterns):
    found = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
    if not patterns:
        return found
    return [path for path in found
            if any(word in os.path.basename(path) for word in patterns)]


def main():
    argv = sys.argv[1:]
    verbose = "-v" in argv or "--verbose" in argv
    patterns = [word for word in argv if not word.startswith("-")]

    paths = suites(patterns)
    if not paths:
        print("no suites match", " ".join(patterns))
        return 1

    started = time.time()
    passed = failed = checks = 0
    broken = []

    for path in paths:
        name = os.path.basename(path)[:-3]
        result = subprocess.run([sys.executable, path], capture_output=True,
                                text=True, cwd=HERE)
        lines = result.stdout.splitlines()
        ran = sum(1 for line in lines if line.startswith(("PASS ", "FAIL ")))
        checks += ran

        if result.returncode == 0:
            passed += 1
            print(f"  ok      {name:32} {ran:4} checks")
            if verbose:
                print("\n".join("            " + line for line in lines))
        else:
            failed += 1
            broken.append(name)
            print(f"  FAILED  {name:32} {ran:4} checks")
            shown = [line for line in lines if line.startswith("FAIL ")]
            if not shown:
                # Died rather than failed a check: the traceback is the news.
                shown = (result.stderr.strip().splitlines() or ["no output"])[-6:]
            for line in shown:
                print("            " + line)

    elapsed = time.time() - started
    print()
    print(f"{passed} suite{'' if passed == 1 else 's'} passed, {failed} failed, "
          f"{checks} checks in {elapsed:.0f}s")
    if broken:
        print("failed: " + ", ".join(broken))
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
