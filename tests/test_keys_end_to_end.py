"""Scripted keys through the real receive loop: pause, resume, clear, quit."""
import io
import socket
import struct
import sys
import time

from harness import FakeTTY, check, finish

import nettail as main

V5_HDR = struct.Struct("!HHIIIIBBH")
V5_REC = struct.Struct("!4s4s4sHHIIIIHHBBBBHHBBH")


def v5_packet(seq, count=3):
    pkt = V5_HDR.pack(5, count, 100000, int(time.time()), 0, seq, 0, 0, 0)
    for i in range(count):
        pkt += V5_REC.pack(
            bytes([192, 168, 1, 10 + i]), bytes([8, 8, 8, 8]), bytes([192, 168, 1, 1]),
            1, 2, 12, 1500, 90000, 100000, 51000 + i, 443, 0,
            0x18, 6, 0, 0, 0, 24, 24, 0)
    return pkt


def run(script, packets, argv=(), answer=None):
    """Drive main() with a scripted keyboard and a scripted socket.

    `script` is a list of keys; None means nobody typed on that poll.
    """
    keys = list(script)
    queue = list(packets)

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
            if FakeSocket.calls > 500:            # the escape never came
                raise KeyboardInterrupt
            if queue:
                return queue.pop(0), ("10.0.0.1", 2055)
            raise socket.timeout

    def fake_start(self):
        self.enabled = True
        return True

    def fake_poll(self):
        # Honour `enabled` exactly as the real one does, so a mode that never
        # starts the keyboard really does see no keys.
        if not self.enabled:
            return None
        return keys.pop(0) if keys else None

    def fake_read_line(self, prompt, out=None):
        return answer

    def fake_stop(self):
        self.enabled = False

    socket.socket = FakeSocket
    main.Keyboard.start = fake_start
    main.Keyboard.poll = fake_poll
    main.Keyboard.stop = fake_stop
    main.Keyboard.read_line = fake_read_line

    out, err = FakeTTY(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    sys.argv = ["nettail", "--resolve", "off", "--no-color"] + list(argv)
    try:
        main.main()
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    return out.getvalue(), err.getvalue()


def rows(text):
    return [ln for ln in text.splitlines() if "8.8.8.8" in ln]


# --- escape closes the program ----------------------------------------------
out, err = run([None, "\x1b", None], [v5_packet(0)])
check("flows printed before the escape", len(rows(out)) == 3, str(len(rows(out))))
check("escape says it is closing", "closing" in err)
check("the summary still prints", "Summary" in err and "flows decoded      3" in err,
      repr([ln for ln in err.splitlines() if "flows decoded" in ln]))
check("the key hint is shown at startup", main.KEY_HELP in err,
      repr([ln for ln in err.splitlines() if "keys" in ln]))
check("and it points at ? rather than listing them",
      main.HELP_KEY in main.KEY_HELP and "[esc] quit" not in err, main.KEY_HELP)

# --- space holds flows back, and resuming prints them ------------------------
out, err = run([None, " ", None, None, None, " ", None, "\x1b", None],
               [v5_packet(0), v5_packet(3), v5_packet(6)])
check("everything arrives in the end", len(rows(out)) == 9, str(len(rows(out))))
check("pausing is announced", "paused, flows are being held" in err)
check("resuming says how many were waiting", "resumed, 6 held flows to print" in err,
      repr([ln for ln in err.splitlines() if "resumed" in ln]))
check("the held flows were decoded, not dropped", "flows decoded      9" in err)

# --- x while paused throws the queue away ------------------------------------
out, err = run([None, " ", None, None, None, "x", None, " ", None, "\x1b", None],
               [v5_packet(0), v5_packet(3), v5_packet(6)])
check("only the flows before the pause were printed", len(rows(out)) == 3,
      str(len(rows(out))))
check("clearing reports what it dropped", "6 held flows dropped" in err,
      repr([ln for ln in err.splitlines() if "dropped" in ln]))
check("the dropped flows were still counted", "flows decoded      9" in err)
check("the screen was cleared", "\033[2J" in out)

# --- e narrows the display mid-run ------------------------------------------
internal = V5_HDR.pack(5, 1, 100000, int(time.time()), 0, 0, 0, 0, 0) + V5_REC.pack(
    bytes([192, 168, 1, 5]), bytes([192, 168, 1, 6]), bytes([192, 168, 1, 1]),
    1, 2, 12, 1500, 90000, 100000, 51000, 443, 0, 0x18, 6, 0, 0, 0, 24, 24, 0)
out, err = run([None, "e", None, None, "\x1b", None], [internal, internal])
check("e is announced", "only flows with a public endpoint" in err)
check("the flow before e was shown and the one after was hidden",
      out.count("192.168.1.6") == 1, "%d of 2 shown" % out.count("192.168.1.6"))

# --- c resets the counters mid-run ------------------------------------------
out, err = run([None, "c", None, None, "\x1b", None],
               [v5_packet(0), v5_packet(3)])
check("clearing statistics is announced", "statistics cleared" in err)
check("the summary counts only what came after",
      "flows decoded      3" in err,
      repr([ln for ln in err.splitlines() if "flows decoded" in ln]))

# --- d and m change the colour scale mid-run --------------------------------
out, err = run([None, "d", None, "\x1b", None], [v5_packet(0)])
check("d is announced", "re-ranging" in err,
      repr([ln for ln in err.splitlines() if "scale" in ln]))

# --- m asks, through the loop ------------------------------------------------
out, err = run([None, "m", None, "\x1b", None], [v5_packet(0)], answer="2M")
check("m is answered and announced", "size scale fixed at 2.0M" in err,
      repr([ln for ln in err.splitlines() if "scale" in ln]))

out, err = run([None, "m", None, "\x1b", None], [v5_packet(0)], answer=None)
check("cancelling the prompt leaves the scale alone",
      "size scale unchanged" in err,
      repr([ln for ln in err.splitlines() if "scale" in ln]))

# --- ? lists the keys without disturbing the run -----------------------------
out, err = run([None, "?", None, "\x1b", None], [v5_packet(0), v5_packet(3)])
check("? prints the listing through the loop", "Keyboard controls" in err,
      repr(err[-200:]))
check("it names a key and what that key does",
      "print the traffic summary now, without stopping" in err,
      repr([ln for ln in err.splitlines() if "summary" in ln]))
check("the listing goes to stderr, leaving the flows alone",
      "Keyboard controls" not in out)
check("flows keep arriving either side of it", len(rows(out)) == 6,
      str(len(rows(out))))
check("and the run ends normally afterwards", "closing" in err and "Summary" in err)

# --- keys stay out of the way of --json --------------------------------------
out, err = run([None, "\x1b", None, " ", None], [v5_packet(0)], argv=["--json"])
check("json mode still emits one object per flow",
      len([ln for ln in out.splitlines() if ln.strip()]) == 3,
      str(len(out.splitlines())))
check("no key hint in json mode", main.KEY_HELP not in err)
check("keys are inert in json mode, escape and all",
      "closing" not in err and "paused" not in err, repr(err[-160:]))

finish("end-to-end keyboard")
