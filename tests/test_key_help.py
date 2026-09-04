"""The ? key lists the keyboard controls, and the list is kept honest.

Two things are worth checking here and the second is the one that lasts. The
first is that ? prints something useful. The second is that the table it prints
from and the dispatch that actually runs the keys agree, since the failure this
feature invites is a key added to one and forgotten in the other, and a test
that can only press the keys it already knows about will never notice.

The reminder line under the banner is a pointer at the listing rather than a
list of its own, so what is checked of it is that it points somewhere real.

There is a third thing now, which is that the keyboard and the command line
offer the same settings. A key that turns part of the display on wants a flag
beside it: a key is for changing your mind and a flag is for saying so at the
start, and since a settings file can say what the command line can and nothing
more, a setting with no flag is one a file cannot hold either. `--names` and
`--macs` were added because they were the two that had no flag, and the check
at the foot of this file is what stops the next one going the same way.
"""
import argparse
import io
import sys
import time
from collections import Counter

from harness import check, finish, plain
from lanname import Resolver
from netflume import SequenceWatch

import nettail as main
from nettail.keys import KEY_CHARS


def controls(out=None):
    """A Controls with ordinary objects behind it and nowhere to print."""
    args = argparse.Namespace(resolve="off", json=False, external_only=False,
                              fqdn=False, header_every=40, verbose=False,
                              hide_status=True, named_hosts=False,
                              show_macs=False)
    return main.Controls(args, main.SizeScale(), Resolver(mode="off", workers=1),
                         main.StickyHeader(), Counter(), main.Tally(),
                         SequenceWatch(), started=time.time(),
                         out=out if out is not None else io.StringIO())


# --- write_keys stands on its own -------------------------------------------
out = io.StringIO()
main.write_keys(out)
listing = plain(out.getvalue())
rows = [line for line in listing.splitlines() if line.strip()]
heading, rows = rows[0], rows[1:]

check("the listing has a heading", heading.strip() == "Keyboard controls", heading)
check("it prints one row a key, and no more",
      len(rows) == len(main.KEYS), f"{len(rows)} rows for {len(main.KEYS)} keys")
check("each row is its key and then its description, in table order",
      all(row.split(maxsplit=1) == [key, doc]
          for row, (key, doc) in zip(rows, main.KEYS)),
      repr(rows[:2]))
check("the keys are right aligned into a column of their own",
      len({len(row) - len(row.split(maxsplit=1)[1]) for row in rows}) == 1,
      repr([row[:10] for row in rows[:3]]))
check("it says what ? itself does", "this list" in listing)

# Told nowhere it goes to stderr, where the question was asked, rather than
# into the flows on stdout.
saved, sys.stderr = sys.stderr, io.StringIO()
try:
    main.write_keys()
    defaulted = plain(sys.stderr.getvalue())
finally:
    sys.stderr = saved
check("told nowhere it goes to stderr", "Keyboard controls" in defaulted,
      repr(defaulted[:60]))

# --- the ? key prints it ----------------------------------------------------
buffer = io.StringIO()
panel = controls(buffer)
message = panel.handle("?")
printed = plain(buffer.getvalue())
check("? prints the listing", "Keyboard controls" in printed, repr(printed[:80]))
check("and says nothing on top of it, the listing being its own answer",
      message is None, repr(message))
check("pressing it changes nothing else",
      not panel.quit and not panel.paused and panel.lines == 0)

# The listing is the same whoever asked for it.
direct = io.StringIO()
main.write_keys(direct)
check("? prints exactly what write_keys does",
      plain(direct.getvalue()) == printed, repr(printed[:80]))

# --- pressing it twice is pressing it twice ---------------------------------
panel.handle("?")
check("asking again asks again",
      plain(buffer.getvalue()).count("Keyboard controls") == 2)

# --- the three descriptions of the keys agree -------------------------------
#
# The table, the reminder line and the dispatch. A key in any one of them and
# missing from another is the defect this feature invites, and it is the sort
# that reaches a reader rather than a test unless something holds them
# together.
table = {KEY_CHARS.get(key, key) for key, _doc in main.KEYS}
dispatch = set(controls().actions())
check("every key that works is in the table",
      not (dispatch - table), repr(sorted(dispatch - table)))
check("and every key in the table works",
      not (table - dispatch), repr(sorted(table - dispatch)))

# --- the reminder line points somewhere real --------------------------------
#
# It named every key once and ran to two hundred characters, which wrapped on
# any ordinary terminal and scrolled away with the banner regardless. Now it
# says where the listing is, so what matters is that the key it names is the key
# that answers, and that it stays short enough to be one line.
check("the reminder line points at the help key",
      main.HELP_KEY in main.KEY_HELP, main.KEY_HELP)
check("and the key it points at is the one that answers",
      main.HELP_KEY in controls().actions(), repr(main.HELP_KEY))
check("and the listing is what that key prints",
      controls().actions()[main.HELP_KEY].__name__ == "_help")
check("it no longer names the keys one by one",
      not any(f"[{key}]" in main.KEY_HELP for key, _doc in main.KEYS),
      main.KEY_HELP)
check("but it does say there are keys at all, which is the part a reader "
      "who does not know cannot guess",
      "keypress" in main.KEY_HELP, main.KEY_HELP)
# Eighty columns because that is the narrow terminal anyone still has, and one
# line because wrapping is what the old line was replaced for.
check("and fits an ordinary terminal on one line",
      len(main.KEY_HELP) <= 80, f"{len(main.KEY_HELP)} characters")
check("the banner filters still find it by its prefix",
      main.KEY_HELP.startswith("keys:"), main.KEY_HELP)

# --- ? is advertised where a reader would look ------------------------------
check("? is in the table", main.HELP_KEY in dict(main.KEYS))
check("and the dispatch answers it", main.HELP_KEY in controls().actions())

# --- the keys that are not characters ---------------------------------------
check("the table spells space and esc for a reader",
      {"space", "esc"} <= set(dict(main.KEYS)))
check("and the dispatch takes what a terminal sends",
      {" ", "\x1b"} <= set(controls().actions()))
check("KEY_CHARS is what maps one to the other",
      KEY_CHARS == {"space": " ", "esc": "\x1b"}, repr(KEY_CHARS))

# --- an unknown key is still nothing ----------------------------------------
quiet = io.StringIO()
panel = controls(quiet)
check("a key nobody claimed does nothing", panel.handle("z") is None)
check("and prints nothing", quiet.getvalue() == "")
check("an empty key is not a key", panel.handle("") is None)


# --- every key that is a setting has a flag beside it -----------------------

# The keys that do something rather than turn something on. These have nothing
# to be set at startup: pausing, clearing and printing are acts, and asking for
# them before there is anything on screen means nothing. Everything else in
# KEYS changes what the display is doing and belongs on the command line too.
#
# Written down here and not in the program, which is the point of it: this is
# the second opinion, and a table the program handed over would agree with
# itself and say nothing. A key added to KEYS is either an act, and belongs in
# this list, or a setting, and wants a flag; either way somebody has to say
# which, and until they do this fails rather than letting a setting arrive with
# no way to ask for it. `Controls.toggles` is checked against these two at the
# foot of the file for the same reason and in the same direction.
ACTIONS = {
    "space",    # pause and resume
    "x",        # clear the screen
    "s",        # print the summary now
    "l",        # print the host list
    "c",        # clear the statistics
    "m",        # ask for a new fixed top, which --size-scale-max is
    "q",        # print the QR code
    "?",        # print this listing
    "esc",      # close the program
}

# What each remaining key sets, against the flag that sets the same thing at
# startup. The dest is the attribute both of them move, which is what makes
# them the same setting rather than two that happen to agree.
SETTINGS = {
    "b": "hide_status",
    "d": "size_scale_dynamic",
    # The one whose state is not kept on `args`. `country` holds it on the
    # module, because `display`, `cli` and `statusbar` all ask and share no
    # arguments between them, which is the arrangement `services` uses and is
    # reasoned about where the state is declared. It is still one setting with
    # one flag, which is what this table is for.
    "g": "country",
    "h": "resolve",
    "n": "named_hosts",
    "p": "show_macs",
    "f": "fqdn",
    "e": "external_only",
    "v": "verbose",
    "t": "templates",
}

# The acts that are still plainly on or off while a run is going. Pause is the
# only one: it sets nothing a file could hold, which is why it is an act, but a
# reader can see perfectly well that it is on and a button that would not say
# so would be the odd one out. Written here so that `toggles` gaining a key
# that is neither a setting nor this fails rather than passing quietly.
STATEFUL_ACTS = {"space"}

keys = [key for key, _doc in main.KEYS]
check("every key is either an act or a setting",
      sorted(ACTIONS | set(SETTINGS)) == sorted(keys),
      "unaccounted for: %s" % sorted(set(keys) - ACTIONS - set(SETTINGS)))

flags = {action.dest: action.option_strings[0]
         for action in main.build_parser()._actions if action.option_strings}
for key, dest in sorted(SETTINGS.items()):
    check("the %s key has a flag beside it, and it is %s"
          % (key, flags.get(dest, "missing")), dest in flags,
          "no command line option sets %r, so a settings file cannot either"
          % dest)

# --- the collector answers which keys are showing as on ---------------------

# `Controls.toggles` is what a browser draws an active key from, and before it
# existed the page kept a list of four written in JavaScript. b, n, p, g and h
# were absent from it and so never lit, however they were pressed, and nothing
# failed: a key missing from that table was invisible rather than wrong. Held
# here in both directions, so a setting added to KEYS with no state to report
# fails, and so does a state reported for a key that is neither a setting nor
# one of the acts above.
#
# The space key is keyed as `actions` keys it and as the page keys its buttons,
# which is the character rather than the word.
CHAR = {"space": " "}
expected = {CHAR.get(key, key)
            for key in set(SETTINGS) | STATEFUL_ACTS}
reported = set(controls().toggles())
check("every setting reports whether it is on",
      expected <= reported,
      "no state reported for: %s" % sorted(expected - reported))
check("and nothing reports a state that is not a setting or a stateful act",
      reported <= expected,
      "unaccounted for: %s" % sorted(reported - expected))

finish("key help")
