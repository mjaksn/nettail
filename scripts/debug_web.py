"""Start nettail with a fixed debug configuration and open the web view.

Run by the VS Code and PyCharm launch configs in .vscode and .idea, which
start this in place of nettail itself. The web interface only starts
answering once the collector has bound its socket, and a browser opened on a
fixed delay would race that, so this polls the URL and opens a browser only
once something answers.

The argument list lives here and nowhere else, so the two IDE configs do not
carry two copies of it to drift apart: each just points at this file, and a
variant is a name in VARIANTS rather than a second copy of ARGS.
"""

import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

# Started by path, not with -m, so Python puts this file's own directory on
# sys.path rather than the repository root, and an ambient install of
# nettail would be picked up instead of the checkout. tests/harness.py fixes
# the same thing the same way.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nettail import cli  # noqa: E402  ROOT has to be on sys.path first
from nettail.web import DEFAULT_WEB_PORT  # noqa: E402

TOKEN = "DebugToken"
URL = f"http://127.0.0.1:{DEFAULT_WEB_PORT}/t/{TOKEN}/"
POLL_INTERVAL = 0.25
POLL_TIMEOUT = 30

ARGS = [
    "--port", "9000",
    "--country",
    "--web",
    "--web-bind", "127.0.0.1",
    "--web-port", str(DEFAULT_WEB_PORT),
    "--web-token", TOKEN,
    "--resolve", "all",
    "--resolve-public",
    "--fqdn",
    "--names",
    "--flow-store",
]

# A launch config can ask for one of these by name, as its one argument, to
# add a few flags on top of ARGS rather than carrying a second copy of the
# whole list. "templates" is for the p, t and v keys, which need IPFIX
# traffic to show anything at all: see "Traffic for the manual checks" in
# AGENTS.md. nettail decodes whatever arrives rather than choosing an
# exporter, so the other half of that is pointing
# tests/tools/send_flows.py --port 9000 --exporter ipfix at this listener.
VARIANTS = {
    "templates": ["--macs", "--templates", "--verbose"],
}


def _open_when_ready():
    deadline = time.monotonic() + POLL_TIMEOUT
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(URL, timeout=1)
        except urllib.error.HTTPError:
            break
        except OSError:
            time.sleep(POLL_INTERVAL)
            continue
        else:
            break
    else:
        print(f"debug_web: gave up waiting for {URL}", file=sys.stderr)
        return
    webbrowser.open(URL)


def main():
    variant = sys.argv[1] if len(sys.argv) > 1 else None
    args = list(ARGS)
    if variant is not None:
        try:
            args += VARIANTS[variant]
        except KeyError:
            raise SystemExit(
                f"debug_web: unknown variant {variant!r}, expected one of "
                f"{sorted(VARIANTS)}") from None
    sys.argv = ["nettail", *args]
    threading.Thread(target=_open_when_ready, daemon=True).start()
    return cli.main()


if __name__ == "__main__":
    raise SystemExit(main())
