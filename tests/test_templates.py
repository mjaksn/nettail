"""A template is spelled out the first time it arrives, and noted after that.

An exporter says what its records look like before it sends any, and every
field read afterwards is read through that. Printing it is this program's
work: netflume learns the template and raises nothing about it, so the store
this suite exercises is the seam. The shape once, a line for each refresh
after that, the shape again when the fields change under an ID already in
use, and none of it without --templates.

That flag is its own and not `--verbose`, which printed these until 0.13.1.
The two are checked apart at the end: a run with the field lines on says
nothing about a template, and a run spelling templates out puts no field line
under a flow.
"""
import io
import os
import socket
import struct
import sys

from harness import FakeTTY, check, finish, plain

import nettail as main

# --------------------------------------------------------------------------
# packet builders, the same shapes test_options_records uses
# --------------------------------------------------------------------------

def ipfix(sets, seq=0, domain=0):
    body = b"".join(sets)
    return struct.pack("!HHIII", 10, 16 + len(body), 1700000000, seq, domain) + body


def field_specs(fields):
    return b"".join(struct.pack("!HH", eid, length) for eid, length in fields)


def data_template(tid, fields):
    body = struct.pack("!HH", tid, len(fields)) + field_specs(fields)
    return struct.pack("!HH", 2, 4 + len(body)) + body


def options_template(tid, scope, opts):
    body = struct.pack("!HHH", tid, len(scope) + len(opts), len(scope))
    body += field_specs(scope) + field_specs(opts)
    return struct.pack("!HH", 3, 4 + len(body)) + body


def data_set(tid, payload):
    return struct.pack("!HH", tid, 4 + len(payload)) + payload


FLOW_FIELDS = [(8, 4), (12, 4), (7, 2), (11, 2), (4, 1), (1, 4), (2, 4)]
WIDER_FIELDS = FLOW_FIELDS + [(6, 1)]


def flow_payload(src=b"\xc0\xa8\x01\x0a", dst=b"\x08\x08\x08\x08", octets=1500):
    return (src + dst + struct.pack("!HH", 51000, 443) + bytes([6])
            + struct.pack("!II", octets, 12))


def v5(count=0, seq=0):
    """A v5 datagram with no records in it. v5 has no templates at all."""
    return struct.pack("!HHIIIIBBH", 5, count, 1000, 1700000000, 0, seq,
                       0, 0, 0)


# --------------------------------------------------------------------------
# the store: what it notices, and what it marks as old news
# --------------------------------------------------------------------------

store = main.cli.WatchedTemplates()
store.put("10.0.0.1", 0, 400, [("src_addr", "ipv4", 4)])
seen = store.take_templates()
check("a template arriving for the first time is noticed",
      [(entry[2], entry[5]) for entry in seen] == [(400, True)], repr(seen))
check("taking the templates clears them", store.take_templates() == [])

store.put("10.0.0.1", 0, 400, [("src_addr", "ipv4", 4)])
seen = store.take_templates()
check("the same template resent is reported as not new",
      [(entry[2], entry[5]) for entry in seen] == [(400, False)], repr(seen))

store.put("10.0.0.1", 0, 400, [("src_addr", "ipv4", 4), ("octets", "uint", 4)])
seen = store.take_templates()
check("a template changed under the same ID is new again",
      len(seen) == 1 and seen[0][5] is True and len(seen[0][3]) == 2,
      repr(seen))

store.put("10.0.0.2", 0, 400, [("src_addr", "ipv4", 4)])
seen = store.take_templates()
check("the same ID from another exporter is a template of its own",
      [(entry[0], entry[5]) for entry in seen] == [("10.0.0.2", True)],
      repr(seen))

store.put("10.0.0.1", 7, 400, [("src_addr", "ipv4", 4)])
seen = store.take_templates()
check("the same ID in another observation domain is a template of its own",
      [(entry[1], entry[5]) for entry in seen] == [(7, True)], repr(seen))

store.put("10.0.0.1", 0, 500, [("template_id", "uint", 4)], options=True)
seen = store.take_templates()
check("an options template is noticed and says that it is one",
      len(seen) == 1 and seen[0][4] is True, repr(seen))

check("a template still reads back out of the store",
      store.get("10.0.0.1", 0, 500) == ([("template_id", "uint", 4)], True),
      repr(store.get("10.0.0.1", 0, 500)))


# --------------------------------------------------------------------------
# the block: what a reader is shown
# --------------------------------------------------------------------------

def rendered(seen):
    buffer = io.StringIO()
    main.cli.report_templates(seen, out=buffer)
    return plain(buffer.getvalue())


text = rendered([("10.0.0.1", 0, 400,
                  [("src_addr", "ipv4", 4), ("dst_addr", "ipv4", 4),
                   ("src_port", "uint", 2), ("octets", "uint", 4)],
                  False, True)])
check("the block names the exporter, the domain and the template",
      "10.0.0.1 (domain 0) sent template 400" in text, repr(text))
check("the block counts the fields", "4 fields" in text, repr(text))
check("the block says how long a record is", "14 bytes a record" in text,
      repr(text))
check("a field is named, typed and measured", "src_addr:ipv4/4" in text,
      repr(text))
check("every field is spelled out",
      all(spec in text for spec in ("dst_addr:ipv4/4", "src_port:uint/2",
                                    "octets:uint/4")), repr(text))

one = rendered([("10.0.0.1", 0, 400, [("octets", "uint", 4)], False, True)])
check("one field is not called fields", "1 field," in one, repr(one))

opts = rendered([("10.0.0.1", 0, 500,
                  [("template_id", "uint", 4),
                   ("sampling_interval", "uint", 4)], True, True)])
check("an options template is labelled as one",
      "sent options template 500" in opts, repr(opts))

var = rendered([("10.0.0.1", 0, 401,
                 [("octets", "uint", 4), ("ie96", "auto", 0xFFFF)],
                 False, True)])
check("a variable length field is not given a width",
      "ie96:auto/var" in var, repr(var))
# 4 for the fixed field, plus the one byte of length prefix the variable
# one costs even when it carries nothing. netflume refuses a record
# shorter than that, so a floor of 4 would be a floor no record can sit on.
check("a template holding one gives its record size as a floor",
      "at least 5 bytes a record" in var, repr(var))

unknown = rendered([("10.0.0.1", 0, 402, [("ie999", "auto", 8)],
                     False, True)])
check("an element with no name still says which it was",
      "ie999:auto/8" in unknown, repr(unknown))

# The width has to be pinned rather than assumed. `report_templates` wraps to
# the window stderr is going to, and stderr here is whatever the suite was
# started from: under `tests/run.py` that is a pipe, which measures zero and
# falls back to SUMMARY_WIDTH, but run directly at a terminal wider than 120
# it is the terminal. `qr.window` reads COLUMNS and LINES before it measures
# anything, which is the lever for exactly this.
os.environ["COLUMNS"], os.environ["LINES"] = "100", "40"
try:
    wide = rendered([("10.0.0.1", 0, 403, [("octets", "uint", 4)] * 40,
                      False, True)])
finally:
    del os.environ["COLUMNS"], os.environ["LINES"]
check("a long field list is wrapped rather than run off the window",
      len(wide.splitlines()) > 2
      and max(len(ln) for ln in wide.splitlines()) <= 100,
      repr([len(ln) for ln in wide.splitlines()]))

# --- and what a refresh is shown as ---------------------------------------

again = rendered([("10.0.0.1", 0, 400,
                   [("src_addr", "ipv4", 4), ("octets", "uint", 4)],
                   False, False)])
check("a refresh says the template arrived again",
      "10.0.0.1 (domain 0) resent template 400, unchanged" in again,
      repr(again))
check("a refresh is one line and no more",
      len([ln for ln in again.splitlines() if ln.strip()]) == 1, repr(again))
check("a refresh does not list the fields",
      "src_addr:ipv4/4" not in again, repr(again))

again_opts = rendered([("10.0.0.1", 0, 500, [("template_id", "uint", 4)],
                        True, False)])
check("an options template says so when it is refreshed too",
      "resent options template 500, unchanged" in again_opts,
      repr(again_opts))

mixed = rendered([("10.0.0.1", 0, 400, [("octets", "uint", 4)], False, False),
                  ("10.0.0.1", 0, 401, [("packets", "uint", 4)], False, True)])
check("a set holding one of each is reported in the order it arrived",
      mixed.index("resent template 400") < mixed.index("sent template 401"),
      repr(mixed))


# --------------------------------------------------------------------------
# end to end: through a run, on stderr, and only under --templates
# --------------------------------------------------------------------------

def run(argv, msgs, script=None):
    """Drive main() with a scripted socket, and a scripted keyboard for a key.

    A key is polled at the top of the loop and the datagram read at the foot
    of it, so a key in `script` has taken effect before the datagram beside it
    in `msgs` is decoded. That ordering is what lets the t key be pressed on a
    run that started without the flag and still be shown the template in the
    very next datagram.

    The loop drains the keyboard until it answers None, so a key is written
    here as the key and then a None, and a datagram nobody types over is a
    None on its own. Without that a script reads as one press per datagram
    and is silently two, which is a test that passes for the wrong reason.
    """
    keys = list(script or ())

    class FakeSocket:
        def __init__(self, *a, **kw):
            self.left = list(msgs)

        def setsockopt(self, *a):
            pass

        def bind(self, *a):
            pass

        def settimeout(self, *a):
            pass

        def close(self):
            pass

        def recvfrom(self, _n):
            if not self.left:
                # Keys left to type are keys the loop has to come round for,
                # so a quiet moment rather than the end of the run.
                if keys:
                    raise socket.timeout
                raise KeyboardInterrupt
            return self.left.pop(0), ("10.0.0.1", 2055)

    socket.socket = FakeSocket
    # Put back afterwards rather than left in place. These are attributes of
    # the class, so a run that patched them and walked away would hand the
    # next run a keyboard that says it started and answers every poll with
    # None, which is not the keyboard that run asked for and is not the one
    # the checks below were written against.
    real_keyboard = {name: getattr(main.Keyboard, name)
                     for name in ("start", "stop", "poll")}
    if script is not None:
        main.Keyboard.start = lambda self: setattr(self, "enabled", True) or True
        main.Keyboard.stop = lambda self: setattr(self, "enabled", False)
        main.Keyboard.poll = lambda self: keys.pop(0) if keys else None
    out, err = FakeTTY(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    sys.argv = ["nettail", "--resolve", "off"] + argv
    try:
        main.main()
    finally:
        sys.stdout, sys.stderr = real_out, real_err
        for name, method in real_keyboard.items():
            setattr(main.Keyboard, name, method)
    return plain(out.getvalue()), plain(err.getvalue())


flows = ipfix([data_template(400, FLOW_FIELDS), data_set(400, flow_payload())])
resent = ipfix([data_template(400, FLOW_FIELDS),
                data_set(400, flow_payload())], seq=1)
changed = ipfix([data_template(400, WIDER_FIELDS)], seq=2)

out, err = run(["--templates"], [flows])
check("a run under --templates spells the template out",
      "sent template 400" in err, repr(err[:400]))
check("the block lists the fields", "src_addr:ipv4/4" in err, repr(err[:400]))
check("the block goes to stderr and not into the flows",
      "sent template 400" not in out, repr(out))
check("the flow itself is still shown", "8.8.8.8" in out, repr(out))

out, err = run(["--templates"], [flows, resent])
check("a template resent is not spelled out twice",
      err.count("sent template 400:") == 1,
      repr([ln for ln in err.splitlines() if "template 400" in ln]))
check("a template resent is noted in a line of its own",
      err.count("resent template 400, unchanged") == 1,
      repr([ln for ln in err.splitlines() if "template 400" in ln]))
check("that line carries no field list",
      err.count("src_addr:ipv4/4") == 1,
      repr([ln for ln in err.splitlines() if "src_addr" in ln]))

out, err = run(["--templates"], [flows, resent, resent, resent])
check("every refresh is noted, not only the first",
      err.count("resent template 400, unchanged") == 3,
      repr([ln for ln in err.splitlines() if "template 400" in ln]))

out, err = run(["--templates"], [flows, changed])
check("a template that changed is spelled out again",
      err.count("sent template 400:") == 2,
      repr([ln for ln in err.splitlines() if "template 400" in ln]))
check("and is not reported as a refresh",
      "resent template 400" not in err,
      repr([ln for ln in err.splitlines() if "template 400" in ln]))
check("the second block carries the field that was added",
      err.count("tcp_flags:uint/1") == 1,
      repr([ln for ln in err.splitlines() if "tcp_flags" in ln]))

out, err = run([], [flows, resent])
check("nothing is spelled out without --templates",
      "sent template" not in err, repr(err[:400]))
check("and nothing is noted either", "resent template" not in err,
      repr(err[:400]))

out, err = run(["--templates"], [v5()])
check("v5 carries no templates and prints none",
      "sent template" not in err and "resent template" not in err,
      repr(err[:400]))

# The split, in both directions. Verbosity is the field lines under a flow
# and templates are the shapes those fields are read through, and the volumes
# are nothing alike: one line per flow for ever against a burst at startup.
# Riding on one flag meant a reader who wanted the second took the first.
#
# The datagram carries a field the row has no column for, so that the line
# --verbose writes under a flow has something to say. Every field used above
# is one the row itself carries, and those are left out of that line rather
# than repeated under it.
extra = ipfix([data_template(401, FLOW_FIELDS + [(10, 4)]),
               data_set(401, flow_payload() + struct.pack("!I", 7))])

out, err = run(["--verbose"], [extra])
check("--verbose alone spells out no template",
      "sent template" not in err and "resent template" not in err,
      repr(err[:400]))
check("and still writes the field line under the flow",
      "in_if=7" in out, repr(out[:400]))

out, err = run(["--templates"], [extra])
check("--templates alone writes no field line under a flow",
      "in_if=7" not in out, repr(out[:400]))
check("and still spells the template out",
      "sent template 401" in err, repr(err[:400]))

# The t key on a run that started without the flag, which is the path the
# store is installed on rather than swapped in before the first byte. It fails
# quietly when it is wrong: templates go on being learned by the store the
# decoder came with, which remembers none of them, so the key reports nothing
# for the rest of the run and nothing anywhere says why.
# The first datagram after the key is spelled out in full rather than called a
# resend, and that is right: the store installed then has seen nothing, so the
# template really is news to the reader, who was shown nothing about it when it
# first arrived.
out, err = run([], [flows, resent, resent], script=[None, "t", None])
check("the t key installs the store mid-run",
      err.count("sent template 400:") == 1, repr(err[:700]))
check("and every resend after it is noted",
      err.count("resent template 400, unchanged") == 1, repr(err[:700]))

# The key turned off again, which is where the store used to grow without any
# bound: it stayed installed, nothing drained it, and every resend for however
# long the key was off was counted out to whoever turned it back on. What is
# held now is the shape and not the drumbeat. Reading the run below: the key
# goes on, off with the wider layout arriving while it is off, a resend of that
# passes unwatched, and the key goes on again.
out, err = run([], [flows, resent, changed, changed, changed],
               script=[None, "t", None, "t", None, None, "t", None])
check("a template changed while the key was off is spelled out on return",
      err.count("sent template 400:") == 2,
      repr([ln for ln in err.splitlines() if "template 400" in ln]))
check("and spelled out as it now stands",
      err.count("tcp_flags:uint/1") == 1,
      repr([ln for ln in err.splitlines() if "tcp_flags" in ln]))
check("a resend nobody was watching is not counted out afterwards",
      err.count("resent template 400, unchanged") == 1,
      repr([ln for ln in err.splitlines() if "template 400" in ln]))

sampled = ipfix([options_template(300, [(145, 4)], [(34, 4)]),
                 data_set(300, struct.pack("!II", 999, 1000))])
out, err = run(["--templates"], [sampled])
check("an options template reaches the reader labelled",
      "sent options template 300" in err, repr(err[:600]))
check("its scope and option fields are both listed",
      "template_id:uint/4" in err and "sampling_interval:uint/4" in err,
      repr(err[:600]))

finish("template reporting")
