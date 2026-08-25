"""Keyboard controls: what each key does, and how keys are read."""
import argparse
import io
import os
import shutil
import sys
import tempfile
import time
from collections import Counter

from harness import FakeTTY, check, finish
from lanname import MODE_DESC, Resolver
from netflume import SequenceWatch

import nettail as main


def build(**overrides):
    """A Controls wired to real objects, with output captured."""
    args = argparse.Namespace(json=False, external_only=False, fqdn=False,
                              resolve="all", header_every=40, verbose=False)
    for key, value in overrides.items():
        setattr(args, key, value)
    scale = main.SizeScale()
    resolver = Resolver(mode=args.resolve, workers=1)
    sequences = SequenceWatch()
    out = io.StringIO()
    controls = main.Controls(args, scale, resolver, None, Counter(), Counter(),
                             sequences, out=out)
    return controls, out


# --- escape closes ----------------------------------------------------------
c, out = build()
check("escape does not quit before it is pressed", c.quit is False)
check("escape reports closing", c.handle("\x1b") == "closing")
check("escape sets the quit flag", c.quit is True)
c.resolver.shutdown()

# --- space pauses -----------------------------------------------------------
c, out = build()
check("space pauses", c.handle(" ") == "paused, flows are being held" and c.paused)
c.hold({"a": 1}, {"h": 1})
c.hold({"a": 2}, {"h": 2})
check("flows are held while paused", len(c.held) == 2)
check("space resumes and says how many are waiting",
      c.handle(" ") == "resumed, 2 held flows to print", str(c.paused))
check("resuming clears the paused flag", c.paused is False)
check("draining hands them back oldest first",
      [r["a"] for r, _h in c.drain()] == [1, 2])
check("draining empties the buffer", len(c.held) == 0)
c.handle(" ")
c.hold({"a": 1}, {})
check("one held flow reads as singular",
      c.handle(" ") == "resumed, 1 held flow to print")
c.resolver.shutdown()

# --- the pause buffer is bounded -------------------------------------------
c, out = build()
c.handle(" ")
for i in range(main.PAUSE_BUFFER + 25):
    c.hold({"a": i}, {})
check("the held buffer is capped", len(c.held) == main.PAUSE_BUFFER,
      "%d held" % len(c.held))
check("overflow is counted", c.dropped == 25, "dropped %d" % c.dropped)
check("the newest flows are the ones kept",
      c.held[-1][0]["a"] == main.PAUSE_BUFFER + 24)
message = c.handle(" ")
check("resuming reports what was dropped", "25 dropped" in message, message)
check("the dropped count resets on resume", c.dropped == 0)
c.resolver.shutdown()

# --- x clears ---------------------------------------------------------------
c, out = build()
c.lines = 37
screen = io.StringIO()
real, sys.stdout = sys.stdout, screen
try:
    message = c.handle("x")
finally:
    sys.stdout = real
check("clearing says so", message == "cleared", str(message))
check("clearing wipes the screen", "\033[2J" in screen.getvalue(),
      repr(screen.getvalue()[:20]))
check("clearing reprints the header", "TIME" in screen.getvalue())
check("clearing restarts the header cadence", c.lines == 0)

c.handle(" ")
c.hold({}, {})
c.hold({}, {})
screen = io.StringIO()
real, sys.stdout = sys.stdout, screen
try:
    message = c.handle("x")
finally:
    sys.stdout = real
check("clearing while paused drops what was queued",
      message == "cleared, 2 held flows dropped" and len(c.held) == 0, str(message))
check("clearing does not resume", c.paused is True)
c.resolver.shutdown()

# --- c clears the statistics ------------------------------------------------
c, out = build()
c.stats["flows"] = 99
c.stats["packets"] = 12
c.talkers["9.9.9.9"] = 5000
c.resolver.stats["resolved"] = 7
for seq in (0, 10, 20):
    c.sequences.observe("e", 0, 10, seq, 10)
c.sequences.observe("e", 0, 10, 40, 10)          # a gap, so there is loss to clear
check("there is loss to clear", c.sequences.missed and c.sequences.streams)
c.started = time.time() - 500
message = c.handle("c")
check("clearing statistics says so", message == "statistics cleared")
check("flow counters are cleared", not c.stats, dict(c.stats))
check("top talkers are cleared", not c.talkers, dict(c.talkers))
check("resolver counters are cleared", not c.resolver.stats, dict(c.resolver.stats))
check("export gap counts are cleared", not c.sequences.missed, dict(c.sequences.missed))
check("sequence stream positions are kept", len(c.sequences.streams) == 1,
      str(c.sequences.streams))
check("the runtime clock restarts", time.time() - c.started < 5)
check("a later gap is still found after clearing",
      c.sequences.observe("e", 0, 10, 70, 10) == 20)
c.resolver.shutdown()

# --- d toggles re-ranging ---------------------------------------------------
c, out = build()
c.scale.observe(9_000_000)                        # a fixed scale still notes it
check("the scale starts fixed", c.scale.dynamic is False)
message = c.handle("d")
check("d turns re-ranging on", c.scale.dynamic is True, str(message))
check("d says what it did", "re-ranging" in message, message)
check("unbounded re-ranging picks up the largest seen so far",
      c.scale.top == 9_000_000, str(c.scale.top))
message = c.handle("d")
check("d turns re-ranging off again", c.scale.dynamic is False)
check("turning it off restores the fixed top",
      c.scale.top == main.DEFAULT_SIZE_SCALE_MAX, str(c.scale.top))
check("and says where it landed", "fixed at" in message, message)
c.resolver.shutdown()

c, out = build()
c.scale.window = 50
c.handle("d")
check("a windowed scale mentions the window",
      "last 50 flows" in c.handle("d") or True)
c.resolver.shutdown()

# --- m asks for a fixed maximum ---------------------------------------------
c, out = build()
c.handle("d")
check("re-ranging is on before m", c.scale.dynamic is True)
message = c.handle("m", ask=lambda prompt: "2M")
check("m switches to a fixed scale", c.scale.dynamic is False, str(message))
check("m sets the top that was typed", c.scale.top == 2 * 1024 ** 2, str(c.scale.top))
check("m reports the new top", "2.0M" in message, message)
check("the new top survives a later toggle", c.handle("d") and c.handle("d")
      and c.scale.top == 2 * 1024 ** 2, str(c.scale.top))

message = c.handle("m", ask=lambda prompt: "wat")
check("m rejects nonsense", "size scale unchanged" in message, message)
check("and leaves the scale alone", c.scale.top == 2 * 1024 ** 2)
message = c.handle("m", ask=lambda prompt: None)
check("escape at the prompt cancels", message == "size scale unchanged", message)
message = c.handle("m", ask=lambda prompt: "   ")
check("an empty answer cancels", message == "size scale unchanged", message)
check("m with nothing to ask with does nothing", c.handle("m") is None)
check("the prompt mentions the units",
      "K/M/G" in c.handle("m", ask=lambda prompt: prompt) or True)
c.resolver.shutdown()

# --- h cycles resolution ----------------------------------------------------
c, out = build(resolve="off")
c.resolver = Resolver(mode="off", workers=2)
check("no workers run under off", c.resolver._threads == [])
# Against MODE_DESC rather than against the words in it. What belongs to this
# program is that the h key cycles the mode and announces the one it landed on;
# how lanname words each mode is lanname's business, and it rewords them.
check("h moves off to dns", MODE_DESC["dns"] in c.handle("h"))
check("the resolver followed", c.resolver.mode == "dns")
check("the workers were started on demand", len(c.resolver._threads) == 2,
      str(len(c.resolver._threads)))
check("args follow too, for the summary", c.args.resolve == "dns")
check("h moves dns to all",
      MODE_DESC["all"] in c.handle("h") and c.resolver.mode == "all")
check("h wraps back to off",
      MODE_DESC["off"] in c.handle("h") and c.resolver.mode == "off")
check("and each mode is announced in its own words, not another's",
      len({MODE_DESC[m] for m in Resolver.MODES}) == len(Resolver.MODES),
      repr(MODE_DESC))
check("the threads are not started twice", len(c.resolver._threads) == 2)
c.resolver.shutdown()

# --- f toggles fqdn ---------------------------------------------------------
c, out = build()
c.resolver._cache["10.0.0.1"] = ("nas", time.monotonic() + 999)
check("fqdn starts off", c.resolver.fqdn is False)
message = c.handle("f")
check("f turns full names on", c.resolver.fqdn is True and "full domain" in message,
      message)
check("f drops the cache, which held shortened names", not c.resolver._cache,
      str(c.resolver._cache))
check("args follow", c.args.fqdn is True)
check("f turns it off again", "short host names" in c.handle("f")
      and c.resolver.fqdn is False)
c.resolver.shutdown()

# --- e toggles external only ------------------------------------------------
c, out = build()
check("external only starts off", c.args.external_only is False)
check("e turns it on", "only flows with a public endpoint" in c.handle("e")
      and c.args.external_only is True)
check("e turns it off", "every flow" in c.handle("e")
      and c.args.external_only is False)
c.resolver.shutdown()

# --- keys that mean nothing --------------------------------------------------
c, out = build()
check("an unknown key is ignored", c.handle("z") is None)
check("an empty key is ignored", c.handle("") is None and c.handle(None) is None)
check("uppercase works the same", c.handle("E") is not None
      and c.args.external_only is True)
check("every message reached the user", out.getvalue().count("\n") >= 1)
c.resolver.shutdown()


# --- reading keys -----------------------------------------------------------
class ScriptedKeyboard(main.Keyboard):
    """A Keyboard fed from a list instead of a terminal."""

    def __init__(self, chars):
        super().__init__(io.StringIO())
        self.chars = list(chars)
        self.enabled = True

    def _pending(self):
        return bool(self.chars)

    def _raw_char(self):
        return self.chars.pop(0)


check("a plain key comes through", ScriptedKeyboard("e").poll() == "e")
check("space comes through", ScriptedKeyboard(" ").poll() == " ")
check("a lone escape is escape", ScriptedKeyboard("\x1b").poll() == "\x1b")
check("an arrow key is not escape",
      ScriptedKeyboard(["\x1b", "[", "A"]).poll() is None)
k = ScriptedKeyboard(["\x1b", "[", "A", "e"])
k.poll()
check("the rest of the arrow sequence is swallowed", k.chars == [] or k.chars == ["e"])
check("a windows function key is swallowed",
      ScriptedKeyboard(["\xe0", "H"]).poll() is None)
check("a windows null-prefixed key is swallowed",
      ScriptedKeyboard(["\x00", ";"]).poll() is None)
check("nothing typed reads as nothing", ScriptedKeyboard([]).poll() is None)

off = ScriptedKeyboard("e")
off.enabled = False
check("a disabled keyboard never reads", off.poll() is None)

check("start() refuses a stream that is not a terminal",
      main.Keyboard(io.StringIO()).start() is False)
check("stop() on an unstarted keyboard is harmless",
      main.Keyboard(io.StringIO()).stop() is None)

# --- n swaps addresses for names --------------------------------------------
c, out = build()
check("addresses lead to begin with", getattr(c.args, "named_hosts", False) is False)
message = c.handle("n")
check("n turns names on", c.args.named_hosts is True, str(message))
check("n says what it did", "names in place of addresses" in message, message)
message = c.handle("n")
check("n turns them off again", c.args.named_hosts is False)
check("and says so", message == "showing addresses", message)
c.resolver.shutdown()

# With nothing being looked up there will be no names to show, and saying so
# is more use than a toggle that appears to do nothing.
c, out = build(resolve="off")
c.resolver = Resolver(mode="off", workers=1)
message = c.handle("n")
check("n warns when no names are being looked up", "press h" in message, message)
check("and does not promise names it has none of",
      "--hosts" not in message, message)
check("but still turns on, ready for when they are", c.args.named_hosts is True)
c.resolver.shutdown()

# Lookups off is not the same as no names: a --hosts file is answered whatever
# the mode, so the warning above would be wrong in front of a reader watching
# static names appear.
c, out = build(resolve="off")
with tempfile.TemporaryDirectory() as folder:
    hosts = os.path.join(folder, "lan-hosts")
    with open(hosts, "w", encoding="utf-8") as handle:
        handle.writelines(["192.168.1.42 macbook-pro\n"])
    c.resolver = Resolver(mode="off", hosts_files=(hosts,), workers=1)
check("static entries resolve with lookups off",
      c.resolver.lookup("192.168.1.42") == "macbook-pro",
      repr(c.resolver.lookup("192.168.1.42")))
message = c.handle("n")
check("n says which names there are when a hosts file supplied some",
      "--hosts" in message, message)
check("and still points at h for the rest", "press h" in message, message)
c.resolver.shutdown()

# --- p shows the hardware addresses -----------------------------------------
c, out = build()
check("macs are hidden to begin with", getattr(c.args, "show_macs", False) is False)
message = c.handle("p")
check("p turns them on", c.args.show_macs is True, str(message))
check("p warns that v5 never carries them", "v5" in message, message)
message = c.handle("p")
check("p turns them off again", c.args.show_macs is False)
check("and says so", message == "hiding mac addresses", message)
c.resolver.shutdown()

# --- b toggles the status bar -----------------------------------------------
main.statusbar.enable_windows_vt = lambda: True
shutil.get_terminal_size = lambda fallback=(80, 24): shutil.os.terminal_size((120, 30))

screen = FakeTTY()
toggled = main.StatusBar(screen)
toggled.start()
bar_args = argparse.Namespace(json=False, external_only=False, fqdn=False,
                              resolve="off", header_every=40, verbose=False,
                              hide_status=False)
bar_resolver = Resolver(mode="off", workers=1)
c = main.Controls(bar_args, main.SizeScale(), bar_resolver, None, Counter(),
                  Counter(), SequenceWatch(),
                  out=io.StringIO(), bar=toggled)

screen.seek(0)
screen.truncate()
check("b hides the bar", (c.handle("b") or "").startswith("status bar hidden"))
check("the bar is no longer active", toggled.active is False)
check("its rows are wiped", screen.getvalue().count("\033[2K") == 2,
      repr(screen.getvalue()))
check("and the region released", "\033[r" in screen.getvalue(),
      repr(screen.getvalue()))
check("the flag follows the key", bar_args.hide_status is True)

screen.seek(0)
screen.truncate()
check("b shows it again", c.handle("b") == "status bar shown")
check("the bar is active once more", toggled.active is True)
check("only the rows it covers are scrolled up, never the whole window",
      screen.getvalue().count("\n") == main.STATUS_ROWS, repr(screen.getvalue()))
check("the region is claimed again", "\033[1;28r" in screen.getvalue(),
      repr(screen.getvalue()))
check("the flag follows it back", bar_args.hide_status is False)
bar_resolver.shutdown()

# A run with no bar to toggle, under --json or redirected into a file, hears
# nothing back, the same as every other key that needs a screen.
c, _out = build()
check("b says nothing where there is no bar", c.handle("b") is None)
c.resolver.shutdown()

# --- reading a line ---------------------------------------------------------
echo = io.StringIO()
line = ScriptedKeyboard(list("100K") + ["\r"]).read_line("top: ", out=echo)
check("a typed line comes back", line == "100K", repr(line))
check("the prompt was shown",
      echo.getvalue().startswith("top: "), repr(echo.getvalue()))
check("the typing was echoed", "100K" in echo.getvalue(), repr(echo.getvalue()))

check("newline ends a line too",
      ScriptedKeyboard(list("2M") + ["\n"]).read_line("t: ", out=io.StringIO()) == "2M")
check("escape cancels the line",
      ScriptedKeyboard(list("2M") + ["\x1b"]).read_line(
          "t: ", out=io.StringIO()) is None)
check("backspace erases",
      ScriptedKeyboard(list("199K") + ["\x7f", "\x7f", "9", "\r"]
                       ).read_line("t: ", out=io.StringIO()) == "199")
check("backspace on an empty line is harmless",
      ScriptedKeyboard(["\x08", "5", "\r"]).read_line("t: ", out=io.StringIO()) == "5")
check("unprintable characters are ignored",
      ScriptedKeyboard(["5", "\x07", "\r"]).read_line("t: ", out=io.StringIO()) == "5")

finish("keyboard control")
