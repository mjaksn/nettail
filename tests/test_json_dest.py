"""`--json` as a destination: the file, the console beside it, and the words
a destination may be spelled with.

The flag was a mode. It put the records on stdout, and everything a console
does was turned off around them because stdout was where the console drew.
Given a path it is not a mode at all: the records go to the file and the
table, the keys, the status bar and the browser carry on exactly as they do
without the flag. That sentence is the feature, and the first check here is
the only place anything says it.

What makes it quiet when broken is that a path is a true value. Every guard
that used to ask `args.json` would go on doing something sensible-looking with
a file named on the command line while suppressing the display nobody asked it
to suppress, and no exception would be raised anywhere. So the checks below
ask for the table and the records together rather than for either alone.
"""
import io
import os
import socket
import struct
import sys
import tempfile
import time

from harness import FakeTTY, check, finish, plain

import nettail as main
from nettail import config, jsonout
from nettail.cli import build_parser

V5_HDR = struct.Struct("!HHIIIIBBH")
V5_REC = struct.Struct("!4s4s4sHHIIIIHHBBBBHHBBH")


def v5_packet(seq=0, count=1):
    pkt = V5_HDR.pack(5, count, 100000, int(time.time()), 0, seq, 0, 0, 0)
    for i in range(count):
        pkt += V5_REC.pack(
            bytes([192, 168, 1, 10 + i]), bytes([8, 8, 8, 8]),
            bytes([192, 168, 1, 1]),
            1, 2, 12, 1500, 90000, 100000, 51000 + i, 443, 0,
            0x18, 6, 0, 0, 0, 24, 24, 0)
    return pkt


def run(argv, packets):
    """Drive main() with a scripted socket, as the other end to end suites do."""
    queue = list(packets)

    class FakeSocket:
        def __init__(self, *a, **kw):
            pass

        def setsockopt(self, *a):
            pass

        def bind(self, *a):
            pass

        def settimeout(self, *a):
            pass

        def close(self):
            pass

        def recvfrom(self, _n):
            if not queue:
                raise KeyboardInterrupt
            return queue.pop(0), ("10.0.0.1", 2055)

    real_socket = socket.socket
    socket.socket = FakeSocket
    out, err = FakeTTY(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    sys.argv = ["nettail", "--resolve", "off"] + list(argv)
    try:
        main.main()
    finally:
        sys.stdout, sys.stderr = real_out, real_err
        socket.socket = real_socket
    return out.getvalue(), err.getvalue()


def lines_of(path):
    with io.open(path, encoding="utf-8") as handle:
        return [line for line in handle.read().splitlines() if line.strip()]


work = tempfile.mkdtemp(prefix="nettail-json-")

# --- the whole point: a file, and a table on the screen as well --------------
target = os.path.join(work, "flows.jsonl")
out, err = run(["--json", target], [v5_packet()])
records = lines_of(target)
check("a named destination gets the record", len(records) == 1, str(records))
check("and the record is the one the flow made",
      records and '"dst_port": 443' in records[0], str(records[:1]))
check("while stdout still draws the table head", "PROTO" in plain(out),
      repr(plain(out)[:120]))
check("and the flow itself", "8.8.8.8" in plain(out), repr(plain(out)[:200]))
check("nothing of the record reaches stdout", "dst_port" not in out,
      repr(plain(out)[:200]))
check("and the file is named on stderr where it can be read",
      target in plain(err), repr([ln for ln in err.splitlines() if "JSON" in ln]))

# --- the bare flag is the run it always was ---------------------------------
out, err = run(["--json"], [v5_packet()])
check("the bare flag still writes its records to stdout",
      '"dst_port": 443' in out, repr(out[:120]))
check("and draws no table around them", "PROTO" not in plain(out),
      repr(plain(out)[:120]))
out_dash, _err = run(["--json", "-"], [v5_packet()])
check("and a lone hyphen means the same thing",
      ("PROTO" not in plain(out_dash)) and '"dst_port": 443' in out_dash,
      repr(out_dash[:120]))

# --- a second run adds to the file rather than emptying it ------------------
run(["--json", target], [v5_packet(seq=1)])
check("a second run appends rather than truncating",
      len(lines_of(target)) == 2, str(len(lines_of(target))))

# --- pause holds the screen and not the file --------------------------------
# The space key is for a reader who wants to look at something, and a sink
# something else is parsing is not a thing that can be held. That was already
# true of stdout under the bare flag, and it is the same bargain here: the
# console stops, the records do not. The keyboard being live at all is half of
# what is being checked, since a run writing to a file has a terminal and the
# bare flag never started one.
held = os.path.join(work, "held.jsonl")


def run_keys(script, packets, argv):
    """As `run` above, with a keyboard scripted the way the key suites do."""
    keys, queue = list(script), list(packets)

    class FakeSocket:
        calls = 0

        def __init__(self, *a, **kw):
            pass

        def setsockopt(self, *a):
            pass

        def bind(self, *a):
            pass

        def settimeout(self, *a):
            pass

        def close(self):
            pass

        def recvfrom(self, _n):
            FakeSocket.calls += 1
            if FakeSocket.calls > 500:
                raise KeyboardInterrupt
            if queue:
                return queue.pop(0), ("10.0.0.1", 2055)
            raise socket.timeout

    real_socket = socket.socket
    saved = (main.Keyboard.start, main.Keyboard.poll, main.Keyboard.stop)
    socket.socket = FakeSocket
    main.Keyboard.start = lambda self: setattr(self, "enabled", True) or True
    main.Keyboard.poll = lambda self: (keys.pop(0) if keys and self.enabled
                                       else None)
    main.Keyboard.stop = lambda self: setattr(self, "enabled", False)
    out, err = FakeTTY(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    sys.argv = ["nettail", "--resolve", "off", "--no-color"] + list(argv)
    try:
        main.main()
    finally:
        sys.stdout, sys.stderr = real_out, real_err
        socket.socket = real_socket
        main.Keyboard.start, main.Keyboard.poll, main.Keyboard.stop = saved
    return out.getvalue(), err.getvalue()


# Space, then two flows, then the escape that ends the run while still paused.
out, err = run_keys([" ", None, None, ""],
                    [v5_packet(seq=1), v5_packet(seq=2)],
                    ["--json", held])
check("the keyboard is live on a run writing to a file",
      "paused" in plain(out) + plain(err),
      repr(plain(out)[-160:] + plain(err)[-160:]))
check("the records keep arriving while the screen is held",
      len(lines_of(held)) == 2, str(lines_of(held)))
check("and the held flows are not drawn", "8.8.8.8" not in plain(out),
      repr(plain(out)[-200:]))

# --- the words a settings file used to spell the flag with ------------------
for word in ("true", "yes", "on", "off", "0"):
    try:
        jsonout.dest_arg(word)
        ok, said = False, "accepted"
    except Exception as exc:  # noqa: BLE001  the message is what is checked
        ok, said = True, str(exc)
    check("%r is refused rather than opened as a file" % word, ok, said)
    check("and the refusal names both spellings that work",
          "'-'" in said and "path" in said, said)

check("a hyphen is a destination", jsonout.dest_arg("-") == jsonout.STDOUT)
check("and so is a path", jsonout.dest_arg(" flows.jsonl ") == "flows.jsonl")
try:
    jsonout.dest_arg("")
    empty_ok, empty_said = False, "accepted"
except Exception as exc:  # noqa: BLE001
    empty_ok, empty_said = True, str(exc)
check("an empty destination is refused, as an empty --config is",
      empty_ok, empty_said)

# The same refusal reaches a reader through a settings file, in the words the
# file's own machinery puts around it, because the parser's `type` is what
# `config` converts with and there is no second opinion about what a value
# means.
parser = build_parser()
settings, complaints = config.parse(parser, "json = true\n", source="a file")
check("a file saying the old thing is complained about, not obeyed",
      "json" not in settings, str(settings))
check("and the complaint names the file, the key and the way out",
      len(complaints) == 1 and "a file" in complaints[0]
      and "'-'" in complaints[0], str(complaints))
settings, complaints = config.parse(parser, "json = /tmp/f.jsonl\n",
                                    source="a file")
check("while a file naming a path is simply read",
      settings.get("json") == "/tmp/f.jsonl" and not complaints,
      str((settings, complaints)))

# --- and the predicate every guard in the program is asking -----------------
check("to_stdout is true of the bare flag",
      jsonout.to_stdout(build_parser().parse_args(["--json"])))
check("and false of a named file, which is the one that used to read true",
      not jsonout.to_stdout(build_parser().parse_args(["--json", "f.jsonl"])))
check("and false when the flag was never given",
      not jsonout.to_stdout(build_parser().parse_args([])))

finish("json destination")
