"""The scroll region the status bar claims, and what it draws inside it."""
import io
import shutil

from harness import FakeTTY, check, finish, plain

import nettail
import nettail as main
from nettail.statusbar import STATUS_ROWS, StatusBar


class FakePipe(io.StringIO):
    def isatty(self):
        return False


def fake_size(cols, lines):
    return lambda fallback=(80, 24): shutil.os.terminal_size((cols, lines))


SNAP = {
    "elapsed": 61.0, "packets": 10, "flows": 20, "bytes_rx": 4096,
    "pkt_rate": 1.0, "flow_rate": 2.0, "bit_rate": 800.0,
    "external_bytes": 0, "inbound": 0, "outbound": 0, "counted_bytes": 0,
    "peak": 0, "top_talker": None, "resolve": "off", "fqdn": False,
    "names_found": 0, "names_missed": 0, "names_dropped": 0,
    "scale_top": 102400, "scale_dynamic": False, "scale_window": 0,
    "external_only": False, "paused": False, "held": 0,
    "versions": ["v5"], "templates": 0,
    "deferred": 0, "gaps": 0, "parse_errors": 0, "sampling": 0,
    "lead_proto": None, "lead_service": None,
}


def snap_of():
    return SNAP


nettail.statusbar.enable_windows_vt = lambda: True
shutil.get_terminal_size = fake_size(120, 30)

# --- happy path: claims the foot of the window, leaves the top alone --------
out = FakeTTY()
bar = StatusBar(out)
check("usable() on a tty", bar.usable() is True)
check("start() returns True on a tty", bar.start() is True)
claimed = out.getvalue()
check("scrolls the screen clear rather than erasing it",
      "\033[2J" not in claimed and claimed.startswith("\n" * 30),
      repr(claimed[:40]))
check("scroll region is rows 1..28", "\033[1;28r" in claimed, repr(claimed))
check("the top margin is left where it was", "\033[2;" not in claimed,
      repr(claimed))
check("cursor parked at the foot of the region",
      claimed.endswith("\033[28;1H"), repr(claimed[-20:]))

# --- painting: the last two rows, and the cursor put back afterwards --------
out.seek(0)
out.truncate()
check("update() paints the first time it is asked",
      bar.update(snap_of, now=1000.0) is True)
painted = out.getvalue()
check("cursor saved before the bar is drawn", painted.startswith("\0337"),
      repr(painted[:10]))
check("cursor restored after", painted.endswith("\0338"), repr(painted[-10:]))
check("first row is row 29", "\033[29;1H" in painted, repr(painted))
check("second row is row 30", "\033[30;1H" in painted, repr(painted))
check("each row is wiped before it is written", painted.count("\033[2K") == 2,
      repr(painted))
check("the figures are on it", "flows 20" in plain(painted), repr(plain(painted)))

rows = [plain(part) for part in painted.split("\033[2K")[1:]]
widths = [len(row.replace("\0338", "").replace("\033[30;1H", "")) for row in rows]
check("rows stop one column short of the window", max(widths) <= 119, str(widths))

# --- the repaint interval keeps a busy network from thrashing it ------------
out.seek(0)
out.truncate()
check("too soon to draw again", bar.update(snap_of, now=1000.2) is False)
check("and nothing was written", out.getvalue() == "")
check("far enough on to draw", bar.update(snap_of, now=1000.6) is True)
out.seek(0)
out.truncate()
check("force draws whatever the clock says",
      bar.update(snap_of, now=1000.61, force=True) is True)
check("forcing wrote a bar", "\033[29;1H" in out.getvalue())

# --- repaint() puts it back after something else cleared the screen ---------
out.seek(0)
out.truncate()
bar.repaint()
check("repaint redraws the rows it last drew", "\033[29;1H" in out.getvalue()
      and "flows 20" in plain(out.getvalue()), repr(out.getvalue()[:60]))

# --- resize: new geometry, new region ---------------------------------------
out.seek(0)
out.truncate()
shutil.get_terminal_size = fake_size(100, 40)
bar.update(snap_of, now=1002.0)
after = out.getvalue()
check("resize re-claims with the new region", "\033[1;38r" in after, repr(after))
check("and draws on the new bottom rows", "\033[39;1H" in after, repr(after))
check("a resize does not scroll the screen away", "\n" not in after, repr(after))

# --- stop(): rows wiped, margins handed back --------------------------------
out.seek(0)
out.truncate()
bar.stop()
released = out.getvalue()
check("both rows wiped on the way out", released.count("\033[2K") == 2,
      repr(released))
check("scroll region released", "\033[r" in released, repr(released))
check("bar is no longer active", bar.active is False)

out.seek(0)
out.truncate()
bar.stop()
check("stop() on an inactive bar writes nothing", out.getvalue() == "")

# --- with the pinned header: one region, written once ------------------------
shutil.get_terminal_size = fake_size(120, 30)
nettail.sticky.enable_windows_vt = lambda: True
both = FakeTTY()
sticky = main.StickyHeader(both)
sticky.bottom_reserved = STATUS_ROWS
check("the header starts with rows reserved below it", sticky.start() is True)
head = both.getvalue()
check("one region covers both reservations", "\033[2;28r" in head, repr(head))

both.seek(0)
both.truncate()
paired = StatusBar(both, sticky=sticky)
check("the bar starts alongside the header", paired.start() is True)
check("the bar does not write a second region", "r" not in both.getvalue()
      .replace("\0337", "").replace("\0338", "") or "\033[1;28r"
      not in both.getvalue(), repr(both.getvalue()))
check("nor disturbs the screen the header just painted",
      "\033[2J" not in both.getvalue() and "\n" not in both.getvalue(),
      repr(both.getvalue()))

both.seek(0)
both.truncate()
paired.update(snap_of, now=2000.0)
check("the bar still draws on the bottom rows", "\033[29;1H" in both.getvalue())

# --- a resize with both up is laid out once, by the bar ---------------------
both.seek(0)
both.truncate()
shutil.get_terminal_size = fake_size(100, 40)
paired.update(snap_of, now=2001.0)
resized = both.getvalue()
check("the header is repainted with the combined region",
      "\033[2;38r" in resized, repr(resized))
check("the header knows the new size", sticky.rows == 40 and sticky.cols == 100)
check("the bar writes no region of its own", "\033[1;38r" not in resized,
      repr(resized))

# --- the bar giving up hands its rows back to the header --------------------
both.seek(0)
both.truncate()
paired.stop()
check("the header is told the rows are free again", sticky.bottom_reserved == 0)
check("the bar does not release a region it never claimed",
      "\033[r" not in both.getvalue(), repr(both.getvalue()))
check("but it does wipe its own rows", both.getvalue().count("\033[2K") == 2,
      repr(both.getvalue()))
check("and the header is given the whole window back",
      "\033[2;40r" in both.getvalue(), repr(both.getvalue()))
check("without clearing the flows off the screen to prove it",
      "\033[2J" not in both.getvalue(), repr(both.getvalue()))

# --- shrinking too far mid-run ----------------------------------------------
shutil.get_terminal_size = fake_size(120, 30)
small = FakeTTY()
shrinking = StatusBar(small)
shrinking.start()
small.seek(0)
small.truncate()
shutil.get_terminal_size = fake_size(120, 4)
shrinking.update(snap_of, now=3000.0)
check("gives up when the window gets too short", shrinking.active is False)
check("and resets the region on the way out", "\033[r" in small.getvalue(),
      repr(small.getvalue()))
check("wiping rows the shrunken window actually has",
      "\033[3;1H" in small.getvalue() and "\033[29;1H" not in small.getvalue(),
      repr(small.getvalue()))

# --- shrinking past what the header needs, with both of them up -------------
shutil.get_terminal_size = fake_size(120, 30)
pair = FakeTTY()
crowded = main.StickyHeader(pair)
crowded.bottom_reserved = STATUS_ROWS
crowded.start()
squeezed = StatusBar(pair, sticky=crowded)
squeezed.start()
pair.seek(0)
pair.truncate()
shutil.get_terminal_size = fake_size(120, 7)
squeezed.update(snap_of, now=4000.0)
tight = pair.getvalue()
check("the header stands down when the window cannot hold both",
      crowded.active is False)
check("the bar is the one that carries on", squeezed.active is True)
check("and takes the region over itself", "\033[1;5r" in tight, repr(tight))
check("no region the header would have refused is ever written",
      "\033[2;5r" not in tight, repr(tight))

# --- refusing to start ------------------------------------------------------
shutil.get_terminal_size = fake_size(120, 30)
pipe = FakePipe()
piped = StatusBar(pipe)
check("usable() is False on a pipe", piped.usable() is False)
check("start() returns False on a pipe", piped.start() is False)
check("nothing written to a pipe", pipe.getvalue() == "")

shutil.get_terminal_size = fake_size(120, 4)
short = FakeTTY()
cramped = StatusBar(short)
check("start() returns False in a 4-row window", cramped.start() is False)
check("nothing written when it cannot start", short.getvalue() == "")
check("update() on a bar that never started does nothing",
      cramped.update(snap_of, now=1.0) is False and short.getvalue() == "")

finish("status bar")
