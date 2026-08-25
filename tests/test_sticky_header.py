"""Checks for the --sticky-header feature. Run from the repo root."""
import io
import shutil

from harness import FakeTTY, check, finish

import nettail
import nettail as main


class FakePipe(io.StringIO):
    def isatty(self):
        return False


def fake_size(cols, lines):
    return lambda fallback=(80, 24): shutil.os.terminal_size((cols, lines))


# --- happy path: pins the header, sets the region, parks the cursor ---------
nettail.sticky.enable_windows_vt = lambda: True
shutil.get_terminal_size = fake_size(120, 30)

out = FakeTTY()
s = main.StickyHeader(out)
check("start() returns True on a tty", s.start() is True)
painted = out.getvalue()
check("clears the screen", "\033[2J" in painted, repr(painted[:40]))
check("scroll region is rows 2..30", "\033[2;30r" in painted, repr(painted))
check("header drawn on row 1", "\033[1;1H" in painted and "TIME" in painted)
check("cursor parked inside the region",
      painted.endswith("\033[2;1H"), repr(painted[-20:]))
check("header truncated to width", "\n" not in painted)

# --- resize: only re-measures every RESIZE_POLL_LINES lines -----------------
out.seek(0)
out.truncate()
for _ in range(main.RESIZE_POLL_LINES - 1):
    s.check_resize()
check("no repaint before the poll interval", out.getvalue() == "")
shutil.get_terminal_size = fake_size(100, 40)
s.check_resize()
check("repaints with the new region on resize", "\033[2;40r" in out.getvalue(),
      repr(out.getvalue()))

# --- resize to a window too short: gives up cleanly -------------------------
out.seek(0)
out.truncate()
shutil.get_terminal_size = fake_size(100, 3)
for _ in range(main.RESIZE_POLL_LINES):
    s.check_resize()
check("deactivates when the window gets too short", s.active is False)
check("resets the region on the way out",
      "\033[r" in out.getvalue(), repr(out.getvalue()))

# --- stop() is idempotent ---------------------------------------------------
out.seek(0)
out.truncate()
s.stop()
check("stop() on an inactive header writes nothing", out.getvalue() == "")

# --- header wider than the window is truncated to one row -------------------
shutil.get_terminal_size = fake_size(40, 24)
out2 = FakeTTY()
s2 = main.StickyHeader(out2)
s2.start()
body = out2.getvalue().split("\033[1;1H")[1].split("\033[2;1H")[0]
check("long header cut to the window width", len(body.replace(main.C.BOLD, "")
                                                 .replace(main.C.RESET, "")) == 40,
      repr(body))

# --- not a tty: falls back instead of emitting escapes ----------------------
shutil.get_terminal_size = fake_size(120, 30)
pipe = FakePipe()
s3 = main.StickyHeader(pipe)
check("start() returns False when stdout is a pipe", s3.start() is False)
check("nothing written to a pipe", pipe.getvalue() == "")

# --- tiny terminal at startup ----------------------------------------------
shutil.get_terminal_size = fake_size(120, 4)
out4 = FakeTTY()
s4 = main.StickyHeader(out4)
check("start() returns False in a 4-row window", s4.start() is False)
check("nothing written when it cannot start", out4.getvalue() == "")

finish("sticky-header")
