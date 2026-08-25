"""Option records must stay off the screen, and their sampling rate must reach
the reader.

Splitting options from flows, reading a rate out of the several forms exporters
advertise it in, and remembering it per domain are all netflume's work and are
tested there. What this suite holds on to is the part that is this program's:
an option record must never be drawn as a flow or emitted by --json, it must be
counted apart from flows, and the fact that the counts are a sample has to be
said twice, once while running and once in the summary.
"""
import io
import socket
import struct
import sys

from harness import FakeTTY, check, finish, plain

import nettail as main

# --------------------------------------------------------------------------
# packet builders
# --------------------------------------------------------------------------

def ipfix(sets, seq=0, domain=0):
    body = b"".join(sets)
    return struct.pack("!HHIII", 10, 16 + len(body), 1700000000, seq, domain) + body


def field_specs(fields):
    return b"".join(struct.pack("!HH", eid, length) for eid, length in fields)


def ipfix_options_template(tid, scope, opts):
    body = struct.pack("!HHH", tid, len(scope) + len(opts), len(scope))
    body += field_specs(scope) + field_specs(opts)
    return struct.pack("!HH", 3, 4 + len(body)) + body


def data_template(tid, fields):
    body = struct.pack("!HH", tid, len(fields)) + field_specs(fields)
    return struct.pack("!HH", 2, 4 + len(body)) + body


def data_set(tid, payload):
    return struct.pack("!HH", tid, 4 + len(payload)) + payload


FLOW_FIELDS = [(8, 4), (12, 4), (7, 2), (11, 2), (4, 1), (1, 4), (2, 4)]


def flow_payload(src=b"\xc0\xa8\x01\x0a", dst=b"\x08\x08\x08\x08", octets=1500):
    return (src + dst + struct.pack("!HH", 51000, 443) + bytes([6])
            + struct.pack("!II", octets, 12))


# --------------------------------------------------------------------------
# end to end: nothing on screen, everything in the summary
# --------------------------------------------------------------------------


def run(argv, msg):
    class FakeSocket:
        def __init__(self, *a, **kw):
            self.left = 1

        def setsockopt(self, *a):
            pass

        def bind(self, *a):
            pass

        def settimeout(self, *a):
            pass

        def close(self):
            pass

        def recvfrom(self, _n):
            if self.left <= 0:
                raise KeyboardInterrupt
            self.left -= 1
            return msg, ("10.0.0.1", 2055)

    socket.socket = FakeSocket
    out, err = FakeTTY(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    sys.argv = ["nettail", "--resolve", "off"] + argv
    try:
        main.main()
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    return out.getvalue(), err.getvalue()


sampled = ipfix([ipfix_options_template(300, [(145, 4)], [(34, 4)]),
                 data_set(300, struct.pack("!II", 999, 1000)),
                 data_template(400, FLOW_FIELDS),
                 data_set(400, flow_payload())])

out, err = run([], sampled)
flow_rows = [ln for ln in out.splitlines() if "8.8.8.8" in ln]
junk_rows = [ln for ln in out.splitlines()
             if "?" in ln and "8.8.8.8" not in ln and "TIME" not in ln]
check("the real flow is displayed", len(flow_rows) == 1, str(out.splitlines()))
check("no junk row is displayed", junk_rows == [], str(junk_rows))
check("the flow count excludes option records",
      "flows decoded      1" in plain(err),
      repr([ln for ln in err.splitlines() if "flows decoded" in ln]))
check("option records are counted separately",
      "option records     1" in plain(err),
      repr([ln for ln in err.splitlines() if "option records" in ln]))
check("the sampling warning reaches the user", "1-in-1000 sampling" in err)
check("the warning says how far the counts are out",
      "roughly 1000x higher" in err,
      repr([ln for ln in err.splitlines() if "1-in-1000" in ln]))
check("the summary has a sampling section",
      "Sampling" in err and "1 in 1000" in err,
      repr([ln for ln in err.splitlines() if "1 in 1000" in ln]))

out, err = run(["--json"], sampled)
lines = [ln for ln in out.splitlines() if ln.strip()]
check("--json emits exactly one object, the flow", len(lines) == 1, str(lines))
check("--json does not emit the option record",
      "sampling_interval" not in out, str(lines))

finish("options and sampling")
