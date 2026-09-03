"""Run the collector with ``python -m nettail``.

The installed ``nettail`` command calls ``cli.main`` directly, so this is for
a checkout, where there is no console script and nothing at the repository
root to run. The test suite starts subprocesses this way for the same reason.
"""

from .cli import main

if __name__ == "__main__":
    # The status goes out rather than being dropped, so that the errands which
    # answer with one, `--update-country-db` among them, say the same thing
    # here as they do through the console script, which setuptools wraps in
    # exactly this. A collector run answers None, and `SystemExit(None)` is
    # zero, so nothing that ran before this changes.
    raise SystemExit(main())
