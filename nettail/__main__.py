"""Run the collector with ``python -m nettail``.

The installed ``nettail`` command calls ``cli.main`` directly, so this is for
a checkout, where there is no console script and nothing at the repository
root to run. The test suite starts subprocesses this way for the same reason.
"""

from .cli import main

if __name__ == "__main__":
    main()
