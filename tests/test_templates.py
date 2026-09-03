"""A template is spelled out the first time it arrives, and noted after that.

An exporter says what its records look like before it sends any, and every
field read afterwards is read through that. Printing it is this program's
work: netflume learns the template and raises nothing about it, so the store
this suite exercises is the seam. The shape once, a line for each refresh
after that, the shape again when the fields change under an ID already in
use, and none of it without --verbose.
"""
import io
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
check("a template holding one gives its record size as a floor",
      "at least 4 bytes a record" in var, repr(var))

unknown = rendered([("10.0.0.1", 0, 402, [("ie999", "auto", 8)],
                     False, True)])
check("an element with no name still says which it was",
      "ie999:auto/8" in unknown, repr(unknown))

wide = rendered([("10.0.0.1", 0, 403, [("octets", "uint", 4)] * 40,
                  False, True)])
check("a long field list is wrapped rather than run off the window",
      len(wide.splitlines()) > 2
      and max(len(ln) for ln in wide.splitlines()) <= main.cli.SUMMARY_WIDTH,
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
# end to end: through a run, on stderr, and only under --verbose
# --------------------------------------------------------------------------

def run(argv, msgs):
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
                raise KeyboardInterrupt
            return self.left.pop(0), ("10.0.0.1", 2055)

    socket.socket = FakeSocket
    out, err = FakeTTY(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    sys.argv = ["nettail", "--resolve", "off"] + argv
    try:
        main.main()
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    return plain(out.getvalue()), plain(err.getvalue())


flows = ipfix([data_template(400, FLOW_FIELDS), data_set(400, flow_payload())])
resent = ipfix([data_template(400, FLOW_FIELDS),
                data_set(400, flow_payload())], seq=1)
changed = ipfix([data_template(400, WIDER_FIELDS)], seq=2)

out, err = run(["--verbose"], [flows])
check("a run under --verbose spells the template out",
      "sent template 400" in err, repr(err[:400]))
check("the block lists the fields", "src_addr:ipv4/4" in err, repr(err[:400]))
check("the block goes to stderr and not into the flows",
      "sent template 400" not in out, repr(out))
check("the flow itself is still shown", "8.8.8.8" in out, repr(out))

out, err = run(["--verbose"], [flows, resent])
check("a template resent is not spelled out twice",
      err.count("sent template 400:") == 1,
      repr([ln for ln in err.splitlines() if "template 400" in ln]))
check("a template resent is noted in a line of its own",
      err.count("resent template 400, unchanged") == 1,
      repr([ln for ln in err.splitlines() if "template 400" in ln]))
check("that line carries no field list",
      err.count("src_addr:ipv4/4") == 1,
      repr([ln for ln in err.splitlines() if "src_addr" in ln]))

out, err = run(["--verbose"], [flows, resent, resent, resent])
check("every refresh is noted, not only the first",
      err.count("resent template 400, unchanged") == 3,
      repr([ln for ln in err.splitlines() if "template 400" in ln]))

out, err = run(["--verbose"], [flows, changed])
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
check("nothing is spelled out without --verbose",
      "sent template" not in err, repr(err[:400]))
check("and nothing is noted either", "resent template" not in err,
      repr(err[:400]))

out, err = run(["--verbose"], [v5()])
check("v5 carries no templates and prints none",
      "sent template" not in err and "resent template" not in err,
      repr(err[:400]))

sampled = ipfix([options_template(300, [(145, 4)], [(34, 4)]),
                 data_set(300, struct.pack("!II", 999, 1000))])
out, err = run(["--verbose"], [sampled])
check("an options template reaches the reader labelled",
      "sent options template 300" in err, repr(err[:600]))
check("its scope and option fields are both listed",
      "template_id:uint/4" in err and "sampling_interval:uint/4" in err,
      repr(err[:600]))

finish("template reporting")
