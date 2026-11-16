"""`--resume`: what a session keeps across a restart, and what it does not.

Most of this is round trips, built from real datagrams through a real decoder
rather than from hand-made records, because what is being pinned is that the
figures after a restart are the figures before it. JSON loses tuples, tuple
keys and the difference between a number and a string as an object key, and
any one of those lost quietly is a table that reads back fine and reports
something else. So each check goes through the text the store would hold and
compares what comes back, by the saved state and by the reports read off it.

The last part runs the collector for real, twice, and kills the first one
hard, which is the case a clean stop never reaches: a crash, an OOM kill, a
power cut, or `docker kill`. On Windows it is also the only stop there is.
"""
import json
import os
import random
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

from harness import SCRIPT, check, finish

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "tools"))

import send_flows  # noqa: E402  (a tool, found on the path set just above)
from lanname import Resolver  # noqa: E402
from netflume import Decoder  # noqa: E402

from nettail import cli, session, store  # noqa: E402
from nettail.detail import Ring  # noqa: E402
from nettail.keys import Controls  # noqa: E402
from nettail.sizescale import SizeScale  # noqa: E402
from nettail.tally import Tally  # noqa: E402
from nettail.traffic import Traffic  # noqa: E402

random.seed(7)
work = tempfile.mkdtemp(prefix="nettail-resume-")
ESC = re.compile(r"\033\[[0-9;]*m")


def through_json(value):
    """What the store would hand back for `value`."""
    return json.loads(json.dumps(value, separators=(",", ":")))


def text(value):
    """A state as one comparable string."""
    return json.dumps(value, sort_keys=True)


def datagrams(n, ipfix_every=0):
    """`n` datagrams of invented flows, a v5 one and now and then an IPFIX."""
    out = []
    for seq in range(n):
        flows = [send_flows.one_flow() for _ in range(5)]
        if ipfix_every and seq % ipfix_every == 0:
            out.append(("10.0.0.2", send_flows.ipfix_datagram(
                seq, flows, with_template=(seq == 0))))
        else:
            out.append(("10.0.0.1", send_flows.v5_datagram(seq * 5, flows)))
    return out


class Collector:
    """The objects `cli.main` builds and a session reads, built the same way."""

    def __init__(self, argv=(), flow_store=None):
        self.args = cli.build_parser().parse_args(
            ["--resolve", "off", *argv])
        self.decoder = Decoder()
        if self.args.templates:
            self.decoder.templates = cli.WatchedTemplates()
        self.tally = Tally()
        self.scale = SizeScale(dynamic=self.args.size_scale_dynamic)
        self.resolver = Resolver(mode="off")
        self.ring = Ring()
        self.shown = [0]
        self.serial = [0]
        # When each datagram "arrived". A clock of its own rather than the
        # wall's, so that two collectors fed the same datagrams stamp the same
        # first-seen and last-seen times and can be compared figure by figure.
        self.clock = 1_700_000_000.0

        def watch():
            if not isinstance(self.decoder.templates, cli.WatchedTemplates):
                self.decoder.templates = cli.WatchedTemplates()

        self.controls = Controls(self.args, self.scale, self.resolver, None,
                                 self.decoder.stats, self.tally,
                                 self.decoder.sequence, on_templates=watch)
        self.store = flow_store or store.DisabledStore()
        self.session = session.Session(
            self.store, self.args, self.decoder, self.tally, self.scale,
            self.resolver, self.controls, self.ring, self.shown, self.serial)

    def feed(self, grams):
        for exporter, data in grams:
            message = self.decoder.decode(data, exporter)
            self.decoder.take_events()
            if message is None:
                continue
            hdr = message.header
            self.clock += 1.0
            hdr["received"] = self.clock
            for rec in message.flows:
                self.scale.observe(rec.get("octets"))
                self.tally.add(rec, hdr)
                self.shown[0] += 1
                self.serial[0] += 1
                self.ring.keep(self.serial[0], (rec, hdr), ["tab"], {"tab"})

    def close(self):
        self.resolver.shutdown()


def comparable(state):
    """A capture without the fields that say when it was taken.

    The serial is one of them: a session saved while running moves it on by
    `SERIAL_SKIP` when it is read back, which has a check of its own below.
    """
    state = dict(state)
    for moment in ("saved", "until", "elapsed", "serial"):
        state.pop(moment, None)
    return text(state)


# -- the tally reads back as it was ------------------------------------------

first = Collector()
first.feed(datagrams(60))
saved = through_json(first.tally.state())
again = Tally.from_state(saved)
check("a tally read back saves the same state it was read from",
      text(again.state()) == text(first.tally.state()))
check("and reports the same busiest pairs",
      again.top_pairs_by_bytes() == first.tally.top_pairs_by_bytes()
      and again.top_pairs_by_packets() == first.tally.top_pairs_by_packets())
check("the same longest flows, as tuples the summary unpacks",
      again.longest_flows() == first.tally.longest_flows()
      and all(isinstance(details, tuple)
              for _d, details in again.longest_flows()))
check("the same busiest moment and link floor",
      again.busiest_moment() == first.tally.busiest_moment()
      and again.min_link_speed() == first.tally.min_link_speed())
check("and the same tables for services, talkers and machines",
      again.top_services(9) == first.tally.top_services(9)
      and again.top_external(9) == first.tally.top_external(9)
      and again.top_internal(9) == first.tally.top_internal(9))
check("a counter keyed by a number keeps number keys",
      all(isinstance(key, int) for key in again.second_bits),
      repr(list(again.second_bits)[:3]))

# The same flows after the restart have to land where they would have landed
# had there been no restart, which is the claim all of this exists to make.
more = datagrams(20)
first.feed(more)
resumed_feed = Collector()
resumed_feed.clock = first.clock - len(more)
resumed_feed.tally.take(again)
resumed_feed.feed(more)
check("flows after a read-back count exactly as they would have without one",
      text(resumed_feed.tally.state()) == text(first.tally.state()))
resumed_feed.close()

# Every figure `reset` sets is one a restart would lose if it were missed, and
# a figure added there later and not here would be lost without anything
# failing. This is what makes it fail.
fresh = vars(Tally())
kept = set(Tally.COUNTERS) | set(Tally.SCALARS) | {
    "longest", "_events", "traffic", "top"}
check("the saved tally names every figure the tally holds",
      set(fresh) == kept,
      "unsaved %s, unknown %s" % (sorted(set(fresh) - kept),
                                  sorted(kept - set(fresh))))

# -- and so does a traffic table that has been pruned ----------------------

small = Traffic(cap=6)
for n in range(40):
    small.add("192.168.1.%d" % (n % 9), "8.8.%d.%d" % (n % 5, n % 7),
              100 * n, n, "TCP", "443/https", 1000.0 + n)
round_trip = Traffic.from_state(through_json(small.state()), cap=6)
check("a pruned traffic table reads back exactly, peers and drops included",
      text(round_trip.state()) == text(small.state()))
check("with its pairs keyed by tuple again",
      all(isinstance(key, tuple) for key in round_trip.pairs))

# -- the ring keeps the shared window and nothing else ---------------------

ring = Ring(bound=3)
for serial in range(1, 6):
    ring.keep(serial, ({"src_addr": "10.0.0.%d" % serial}, {"exporter": "e"}),
              ["a"], {"a"})
back = Ring(bound=3)
back.restore(through_json(ring.state()))
check("the ring reads back the last flows sent to anybody",
      [s for s in range(1, 6) if s in back] == [3, 4, 5],
      repr([s for s in range(1, 6) if s in back]))
check("as the records they were",
      back[5] == ({"src_addr": "10.0.0.5"}, {"exporter": "e"}))

# -- a whole session, through a store --------------------------------------

path = os.path.join(work, "flows.sqlite3")
kept_store = store.FlowStore(path, retention_days=7)
before = Collector(["--templates", "--size-scale-dynamic"], kept_store)
before.feed(datagrams(40, ipfix_every=4))
before.controls.handle("p")          # a key pressed in a browser
before.controls.handle("n")
before.controls.handle("h")          # off to dns
check("the run being saved has learned a template",
      len(before.decoder.templates) == 1, str(len(before.decoder.templates)))
check("saving answers nothing when it worked", before.session.save() is None)
check("and a slow store's complaint would have said so",
      not before.session.complained)
captured = before.session.capture()
kept_store.close()
before.close()

reopened = store.FlowStore(path, retention_days=7)
after = Collector(["--templates", "--size-scale-dynamic"], reopened)
resumed, line = after.session.restore()
check("a saved session is resumed", resumed, repr(line))
check("and the startup line says how far it got",
      line and "resumed the session saved" in line
      and "%d flows" % before.tally.flows in line, repr(line))
read_back = json.loads(comparable(after.session.capture()))
wrote = json.loads(comparable(captured))
check("the whole session reads back as it was saved", read_back == wrote,
      "differs in %s" % sorted(key for key in set(read_back) | set(wrote)
                               if read_back.get(key) != wrote.get(key)))
check("the decoder's counters are the same object, holding the old counts",
      after.controls.stats is after.decoder.stats
      and after.decoder.stats["flows"] == before.decoder.stats["flows"])
check("the tally the keys hold is the one that was filled",
      after.controls.talkers is after.tally
      and after.tally.flows == before.tally.flows)
check("the run time goes on from where it stopped",
      abs((time.time() - after.controls.started) - captured["elapsed"]) < 5,
      "%.1f against %.1f" % (time.time() - after.controls.started,
                             captured["elapsed"]))
check("keys pressed before the restart are pressed after it",
      after.args.show_macs and after.args.named_hosts
      and after.resolver.mode == "dns")

# The template is what decides whether an IPFIX record can be read at all, and
# an exporter may not resend it for minutes. Without it the page would go
# quiet after every restart until it did.
data_only = send_flows.ipfix_datagram(99, [send_flows.one_flow()],
                                      with_template=False)
message = after.decoder.decode(data_only, "10.0.0.2")
check("an IPFIX record after a restart reads without waiting for a template",
      message is not None and len(message.flows) == 1,
      repr(after.decoder.stats["deferred"]))
check("and putting the template back raised nothing to report",
      after.decoder.take_events() == []
      and after.decoder.templates.take_templates(True) == [])
resend = send_flows.ipfix_datagram(100, [send_flows.one_flow()],
                                   with_template=True)
after.decoder.decode(resend, "10.0.0.2")
seen = after.decoder.templates.take_templates(True)
check("an exporter's next resend is a resend, not a template changed",
      len(seen) == 1 and seen[0][5] is False, repr(seen))

# -- a setting the command line has changed is the command line's --------

changed = Collector(["--templates", "--size-scale-dynamic", "--macs"],
                    store.FlowStore(path, retention_days=7))
changed.session.restore()
check("a setting typed differently this time is not overridden",
      changed.args.show_macs is True)
check("while one typed the same is put back",
      changed.args.named_hosts is True and changed.resolver.mode == "dns")
changed.store.close()
changed.close()

edited = Collector(["--templates"], store.FlowStore(path, retention_days=7))
edited.session.restore()
check("a scale the command line set up differently starts as asked",
      edited.scale.dynamic is False
      and edited.scale.top == edited.scale.fixed_top)
edited.store.close()
edited.close()

# -- the serial, after a clean stop and after a crash ----------------------

check("a session saved while running moves the serial past a crash's flows",
      after.serial[0] == captured["serial"] + session.SERIAL_SKIP,
      "%d from %d" % (after.serial[0], captured["serial"]))
after.session.save(clean=True)
reopened.close()
after.close()
clean = Collector(["--templates", "--size-scale-dynamic"],
                  store.FlowStore(path, retention_days=7))
clean.session.restore()
check("one saved on the way out carries on from where it was",
      clean.serial[0] == after.serial[0], "%d" % clean.serial[0])
clean.store.close()
clean.close()

# Picked up and then killed before saving anything of its own, twice over.
# Each run numbered flows the session it picked up knows nothing about, so
# the next one has to find a crash's leftovers rather than a clean stop, and
# has to number past what the killed run handed out rather than past what it
# was handed: two kills in a row must not reuse one range.
killed = Collector(["--templates", "--size-scale-dynamic"],
                   store.FlowStore(path, retention_days=7))
killed.session.restore()
killed.store._db.close()          # no save, no commit: the process is gone
killed.close()
next_one = Collector(["--templates", "--size-scale-dynamic"],
                     store.FlowStore(path, retention_days=7))
next_one.session.restore()
check("a run killed before its first save is not taken for a clean stop",
      next_one.serial[0] == killed.serial[0] + session.SERIAL_SKIP,
      "%d after %d" % (next_one.serial[0], killed.serial[0]))
next_one.store._db.close()        # and killed again
next_one.close()
third = Collector(["--templates", "--size-scale-dynamic"],
                  store.FlowStore(path, retention_days=7))
third.session.restore()
check("and a second kill in a row does not hand out the same numbers again",
      third.serial[0] == next_one.serial[0] + session.SERIAL_SKIP,
      "%d after %d" % (third.serial[0], next_one.serial[0]))
third.store.close()
third.close()

# What an exporter said about itself is trusted only from a session young
# enough that the exporter is likely to be saying it still. Older than that,
# a reboot or an upgrade may have reused a template ID for another layout.
aged = Collector(["--templates", "--size-scale-dynamic"],
                 store.FlowStore(path, retention_days=7))
saved_at = aged.store.load_session()[0]
ok, said = aged.session.restore(now=saved_at + session.TEMPLATE_AGE + 1)
check("a session older than the template age still resumes", ok, repr(said))
check("without its templates, which are learned again",
      len(aged.decoder.templates) == 0 and "learned again" in said,
      "%d held, %r" % (len(aged.decoder.templates), said))
check("while the figures still come back",
      aged.tally.flows == before.tally.flows)
aged.store.close()
aged.close()

# A clock behind the one the session was saved by is what a machine with no
# battery backed clock has after a power cut and before it synchronises. That
# says nothing about how long the collector was down, so it is not taken as a
# young session.
behind = Collector(["--templates", "--size-scale-dynamic"],
                   store.FlowStore(path, retention_days=7))
saved_at = behind.store.load_session()[0]
ok, said = behind.session.restore(now=saved_at - 3600)
check("a clock behind the saved one still resumes", ok, repr(said))
check("without trusting templates of an age it cannot tell",
      len(behind.decoder.templates) == 0 and "learned again" in said,
      "%d held, %r" % (len(behind.decoder.templates), said))
behind.store.close()
behind.close()

# -- a session another run has written past is not picked up ----------------
#
# A build that never lets a session go, such as the release before this one
# after a rollback, can run on the same store for days and leave the session
# where it was. Picked up after rolling forward, its figures would come back
# without that run's flows in them. Flows that arrived well after the session
# expected to be saved again are what tells.


def flow_at(when):
    return {"_received": when, "_timestamp": when, "_exporter": "10.0.0.9",
            "_version": 5, "src_addr": "192.168.1.10", "dst_addr": "8.8.8.8"}


for clean_stop in (True, False):
    target = os.path.join(work, "past-%s.sqlite3" % clean_stop)
    writer = Collector([], store.FlowStore(target, retention_days=7))
    writer.feed(datagrams(4))
    moment = time.time()
    writer.session.save(clean=clean_stop, now=moment)
    writer.store.close()
    writer.close()
    # Within the minute or so a run killed while saving can leave: the same
    # session, still the last word.
    db = store.FlowStore(target, retention_days=7)
    db.write(flow_at(moment + session.STALE_AFTER / 2))
    db.close()
    near = Collector([], store.FlowStore(target, retention_days=7))
    ok, said = near.session.restore()
    check("a session with flows only just after its save is picked up (%s)"
          % ("clean" if clean_stop else "running"), ok, repr(said))
    near.store.close()
    near.close()

    target = os.path.join(work, "past-%s-later.sqlite3" % clean_stop)
    writer = Collector([], store.FlowStore(target, retention_days=7))
    writer.feed(datagrams(4))
    writer.session.save(clean=clean_stop, now=moment)
    expected = json.loads(writer.store.load_session()[1])["until"]
    writer.store.close()
    writer.close()
    db = store.FlowStore(target, retention_days=7)
    db.write(flow_at(expected + session.STALE_AFTER + 3600))
    db.close()
    later = Collector([], store.FlowStore(target, retention_days=7))
    later.feed(datagrams(1))
    figures = later.tally.flows
    ok, said = later.session.restore()
    check("one another run has stored flows past is not (%s)"
          % ("clean" if clean_stop else "running"),
          not ok and said and "new one" in said, repr(said))
    check("and leaves what this run has counted alone",
          later.tally.flows == figures)
    later.store.close()
    later.close()

# -- what cannot be read starts a new session, and says so ----------------


def poisoned(value):
    target = os.path.join(work, "bad-%d.sqlite3" % random.randrange(10 ** 9))
    db = store.FlowStore(target, retention_days=7)
    db.save_session(value)
    db.close()
    return target


whole = through_json(captured)
for why, value in (
        ("text that is not JSON", "{not json"),
        ("a format from another build", json.dumps({"format": 999})),
        ("a session cut short", json.dumps(dict(whole, tally={"counters": {}}))),
        # Valid JSON holding something that is not a figure, in each of the
        # places that used to be read after the check that catches them, or
        # that would have been taken and failed later on every flow.
        ("a serial of infinity", json.dumps(dict(whole, serial=float("inf")))),
        ("a session with no time saved",
         json.dumps({k: v for k, v in whole.items() if k != "saved"})),
        ("a size scale whose top is text",
         json.dumps(dict(whole, scale=dict(whole["scale"], top="big")))),
        ("a tally whose count is text",
         json.dumps(dict(whole, tally=dict(
             whole["tally"], scalars=dict(whole["tally"]["scalars"],
                                          flows="many")))))):
    where = poisoned(value)
    victim = Collector([], store.FlowStore(where, retention_days=7))
    victim.feed(datagrams(3))
    figures = text(victim.tally.state())
    ok, said = victim.session.restore()
    check("%s starts a new session" % why, not ok, repr(said))
    check("and says so, rather than nothing", bool(said) and "new one" in said,
          repr(said))
    check("leaving what was already counted as it was",
          text(victim.tally.state()) == figures)
    victim.store.close()
    victim.close()

nothing = Collector([], store.FlowStore(os.path.join(work, "empty.sqlite3"),
                                        retention_days=7))
check("a store with no session saved in it is a first run, said nowhere",
      nothing.session.restore() == (False, None))
nothing.store.close()
nothing.close()

# -- the table is an addition, not a new schema ---------------------------
#
# An older build refuses any schema version it was not written for, so a
# version bump here would stop the release before this one starting on a
# volume this one had touched. Rolling an image back is exactly what somebody
# does when a new one misbehaves.
old_path = os.path.join(work, "old.sqlite3")
old = sqlite3.connect(old_path)
old.executescript("""
    CREATE TABLE flows (ingest_id INTEGER PRIMARY KEY, received REAL NOT NULL,
        flow_time REAL NOT NULL, exporter TEXT NOT NULL,
        version INTEGER NOT NULL, src_addr TEXT, dst_addr TEXT,
        src_port INTEGER, dst_port INTEGER, proto INTEGER, octets INTEGER,
        packets INTEGER, record_json TEXT NOT NULL);
    PRAGMA user_version = 1;
""")
old.close()
upgraded = store.FlowStore(old_path, retention_days=7)
check("a store from before the session table opens and gains one",
      upgraded.load_session() is None)
upgraded.close()
version = sqlite3.connect(old_path).execute("PRAGMA user_version").fetchone()[0]
check("without its schema version moving", version == store.SCHEMA_VERSION == 1,
      str(version))

# -- a save that took long puts the next one further off --------------------
#
# A save runs where datagrams wait, and at the tables' caps it is seconds, so
# the interval is a floor and the cost of the last save can stretch it.


class SlowStore(store.DisabledStore):
    def save_session(self, text, clean=False, now=None):
        time.sleep(0.05)


slow = Collector([], SlowStore())
slow.session.every = 1.0
moment = time.time()
slow.session.save(now=moment)
check("a save that took long waits that much longer before the next",
      slow.session._next - moment >= session.SAVE_SPACING * 0.05,
      "%.2fs" % (slow.session._next - moment))
quick = Collector([], store.DisabledStore())
quick.session.every = 1.0
quick.session.save(now=moment)
check("while a quick one keeps to the interval it was given",
      abs(quick.session._next - moment - 1.0) < 0.5,
      "%.2fs" % (quick.session._next - moment))
slow.close()
quick.close()


# Nothing has changed since the last save, so there is nothing to write: on a
# quiet network a save is otherwise tens of megabytes rewritten and committed
# for nothing. The save on the way out is never skipped, since what it adds
# is that the stop was clean.
class CountingStore(store.DisabledStore):
    def __init__(self):
        self.writes = []

    def save_session(self, text, clean=False, now=None):
        self.writes.append(clean)


counting = CountingStore()
idle = Collector([], counting)
idle.feed(datagrams(2))
idle.session.save()
idle.session.save()
check("a save with nothing new since the last is skipped",
      counting.writes == [False], repr(counting.writes))
idle.feed(datagrams(1))
idle.session.save()
check("while one after more flows is written", counting.writes == [False] * 2,
      repr(counting.writes))
idle.controls.handle("v")
idle.session.save()
check("and so is one after a key", len(counting.writes) == 3,
      repr(counting.writes))
idle.session.save(clean=True)
check("the save on the way out is written whatever has changed",
      counting.writes[-1] is True, repr(counting.writes))
idle.close()


# The resolver's counts are kept by lanname's lookup threads, so a save can
# meet one being added to. Trying again is all that takes.
class Restless(dict):
    def __init__(self, fails):
        super().__init__(resolved=1)
        self.fails = fails

    def items(self):
        if self.fails:
            self.fails -= 1
            raise RuntimeError("dictionary changed size during iteration")
        return super().items()


check("a counter another thread is adding to is read on a second try",
      session._settled(Restless(2)) == {"resolved": 1})
try:
    session._settled(Restless(99))
    check("and one that never settles fails the save rather than hanging",
          False, "it was read")
except RuntimeError:
    check("and one that never settles fails the save rather than hanging", True)


# A failed save is said once, and said again after a save has worked in
# between, so a store that goes bad later in the run is not kept quiet.
class Flaky(store.DisabledStore):
    def __init__(self):
        self.failing = True

    def save_session(self, text, clean=False, now=None):
        if self.failing:
            raise OSError("disk full")


flaky = Flaky()
shaky = Collector([], flaky)
first_said = shaky.session.save(clean=True)
again_said = shaky.session.save(clean=True)
flaky.failing = False
shaky.session.save(clean=True)
flaky.failing = True
later_said = shaky.session.save(clean=True)
check("a save that fails says so", first_said and "disk full" in first_said,
      repr(first_said))
check("once, not at every save after it", again_said is None, repr(again_said))
check("and again when it fails after working",
      later_said and "disk full" in later_said, repr(later_said))
shaky.close()

# -- every key setting is one a session keeps -------------------------------
#
# `SETTINGS` is written out by hand beside `KEYS` and `Controls.toggles`, and a
# key added with its flag and left out of it would pass every other suite while
# its setting quietly did not survive a restart. So each toggle a key shows is
# named here against the setting a session keeps for it, and a toggle with no
# entry fails until somebody says which setting it is.
KEPT_BY = {
    "b": "hide_status", "k": "sticky_header", "d": "scale",
    "e": "external_only", "f": "fqdn", "g": "country", "h": "resolve",
    "n": "named_hosts", "p": "show_macs", "t": "templates", "v": "verbose",
}
# Pause is an act rather than a setting, and its held flows are in memory.
NOT_KEPT = {" "}
probe = Collector()
toggles = set(probe.controls.toggles())
check("every key that shows as on is either kept or deliberately not",
      toggles == set(KEPT_BY) | NOT_KEPT,
      "unaccounted %s, stale %s" % (sorted(toggles - set(KEPT_BY) - NOT_KEPT),
                                    sorted(set(KEPT_BY) - toggles)))
check("and every one kept is a setting the session saves",
      set(KEPT_BY.values()) == set(session.SETTINGS)
      == set(session.settings(probe.args, probe.scale, probe.resolver)),
      "%s against %s" % (sorted(set(KEPT_BY.values())),
                         sorted(session.SETTINGS)))
probe.close()

# -- the options ------------------------------------------------------------

parser = cli.build_parser()
check("--resume is off unless asked for",
      parser.parse_args([]).resume is False)
check("and saves every minute unless told otherwise",
      parser.parse_args([]).resume_every == session.SAVE_EVERY == 60.0)
try:
    parser.parse_args(["--resume-every", "0"])
    check("a save interval of nothing is refused", False, "it was taken")
except SystemExit:
    check("a save interval of nothing is refused", True)


# -- the collector, run for real, killed, and run again ---------------------


def start(target, last="recording shown flows"):
    proc = subprocess.Popen(
        [sys.executable, "-u", *SCRIPT, "--bind", "127.0.0.1", "--port", "0",
         "--resolve", "off", "--json", os.devnull, "--flow-store", target,
         "--resume", "--resume-every", "0.2", "--flow-commit-every", "0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    banner = []
    while True:
        line = proc.stderr.readline()
        if not line:
            break
        banner.append(line)
        # The blank line is the end of the banner, so a line that was going
        # to be in it and is not cannot leave this waiting for ever.
        if last in line or not line.strip():
            break
    found = re.search(r"Listening for NetFlow/IPFIX on 127\.0\.0\.1:(\d+)",
                      ESC.sub("", "".join(banner)))
    return proc, (int(found.group(1)) if found else 0), "".join(banner)


def saved_flows(target):
    db = sqlite3.connect(target)
    try:
        row = db.execute(
            "SELECT value FROM session WHERE name = 'state'").fetchone()
    finally:
        db.close()
    return None if row is None else json.loads(row[0])["tally"]["scalars"][
        "flows"]


def send(port, n):
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for seq in range(n):
            sender.sendto(send_flows.v5_datagram(seq * 5, [
                send_flows.one_flow() for _ in range(5)]), ("127.0.0.1", port))
    finally:
        sender.close()


def until(target, want, limit=15.0):
    deadline = time.time() + limit
    got = None
    while time.time() < deadline:
        try:
            got = saved_flows(target)
        except sqlite3.Error:
            got = None
        if got == want:
            return got
        time.sleep(0.1)
    return got


live = os.path.join(work, "live.sqlite3")
proc, port, banner = start(live)
check("the collector starts on a port of its own", port > 0, banner)
try:
    send(port, 4)
    counted = until(live, 20)
finally:
    proc.kill()
    proc.communicate(timeout=15)
check("a running collector saves its session without being stopped",
      counted == 20, repr(counted))

proc, port, banner = start(live, last="resumed the session")
try:
    check("the next run says it picked the session up",
          "resumed the session saved" in ESC.sub("", banner)
          and "20 flows" in banner, banner)
    send(port, 2)
    counted = until(live, 30)
finally:
    proc.kill()
    proc.communicate(timeout=15)
check("and counts on from where the killed one stopped", counted == 30,
      repr(counted))

finish("resume")
