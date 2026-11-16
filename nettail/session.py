"""Picking a session up where the last run of the collector left it.

What `--resume` is. A collector in a container is restarted for reasons that
have nothing to do with the network it is watching: an image pulled, a host
rebooted, a daemon upgraded. The flows already survive that, in the flow
store. This keeps the rest: the statistics behind the status bar, the summary
and the details dialog, the templates an exporter taught it, what the keys
have been set to, and enough of the browser's numbering that a tab which
reconnects finds its rows still mean what they meant.

It lives in the flow store, beside the flows, so that the two are committed
together and a crash loses both from the same moment rather than one from
each. A run with no store has nowhere to keep it, which is why `--resume` asks
for one.

Four things about it are worth knowing before changing it.

**What is not kept, and why.** The stream positions a sequence watch holds
are left to be learned again, so that the exports an exporter sent while the
collector was down are not reported as lost on the network, which is what a
gap means everywhere else in this program. The name cache and the list of
local hosts the l key prints are lanname's own, and fill again as flows
arrive. Pause is an act and not a setting,
and the flows it holds are in memory and go with the process. The per-second
rates are measured over the last few seconds and have nothing to carry.

**A setting the command line has changed is not overridden by a key pressed
before the restart.** Each setting is saved with the value the run started
with beside the value it ended with, and the second is put back only when this
run started with the same first. So a key pressed in a browser survives a
`docker restart`, and an edit to the compose file that changes the same
setting wins, which is what somebody who edited the file meant.

**Loading is all or nothing, and never fatal.** Everything is built into
fresh objects first and handed over only once the whole of it has been read,
so a file from somewhere else, or one cut short, costs a line saying so and a
new session rather than a run that starts with half of its figures.

**The elapsed time is kept, not the start time.** A start time carried across
would count the hours the collector was down as hours it was watching, and
every average the summary and the bar work out over the session would be
diluted by them.
"""

import json
import time
from collections import Counter, OrderedDict

from netflume import TemplateStore

from . import country
from .tally import Tally

# What the saved text is. A different number is a file this build cannot read,
# and is met with a new session rather than with a guess.
FORMAT = 1

# How often the session is saved between the save made on the way out, in
# seconds. A clean stop saves at the end, so this is only what a crash, a hard
# kill or a power cut loses.
SAVE_EVERY = 60.0

# How many times its own length the next save waits, at least. A save runs on
# the receive loop, which is where datagrams wait, and its cost is the size of
# the tables: milliseconds on a home network, and about two seconds measured
# with 50,000 endpoints and pairs, which is their cap, and the full set of rate
# events, which came to 35 MB of text. Each address held a single service
# there, so one with hundreds would cost more again. A fixed minute would then
# hold the socket unread for one second in thirty, and a receive buffer the
# kernel has capped well below the 4 MB asked for does not ride that out. So
# a save that took long puts the next one further off, which keeps the share
# of the loop spent saving near one percent whatever the network is.
SAVE_SPACING = 100

# How old a saved session may be and still have its templates and sampling
# rates put back, in seconds. Both are what an exporter said about itself, and
# an exporter that was rebooted or upgraded while the collector was stopped may
# say something else now, under the same template IDs: records cut up by a
# stale layout would go into the store and the figures quietly wrong until the
# next resend. A restart, a pull or a reboot of this host is minutes, and keeps
# them. A collector stopped for longer learns them again, which is what it
# would have done without `--resume`; half an hour is the template timeout
# Cisco's NetFlow v9 exporters default to, so by then a well behaved exporter
# has resent its templates anyway.
TEMPLATE_AGE = 30 * 60

# How far the flow serial is moved on after a session that was not saved on the
# way out. The flows published between the last save and the crash were given
# numbers the saved serial does not know about, and a tab still showing them
# would otherwise have them answered by different flows that were given the same
# numbers again. The ends a dialog's ask carries are checked against the flow it
# finds, so that would be caught, but a number that can only mean one flow is
# better than a check that catches a number meaning two. This is far more
# flows than the busiest link this program has met publishes in a save
# interval, and a serial is only ever compared for equality.
SERIAL_SKIP = 10 ** 7

# Which settings the keys move and a session keeps, by the name each is
# compared under. The scale is one entry of three parts because the m and d
# keys move two of them and a window decides what the observed state means.
SETTINGS = ("hide_status", "sticky_header", "named_hosts", "show_macs",
            "verbose", "templates", "external_only", "resolve", "fqdn",
            "country", "scale")


def settings(args, scale, resolver):
    """What the keys have set, as plain values that survive JSON unchanged."""
    return {
        "hide_status": bool(getattr(args, "hide_status", False)),
        "sticky_header": bool(getattr(args, "sticky_header", False)),
        "named_hosts": bool(getattr(args, "named_hosts", False)),
        "show_macs": bool(getattr(args, "show_macs", False)),
        "verbose": bool(getattr(args, "verbose", False)),
        "templates": bool(getattr(args, "templates", False)),
        "external_only": bool(getattr(args, "external_only", False)),
        "resolve": resolver.mode,
        "fqdn": bool(resolver.fqdn),
        "country": bool(country.showing()),
        "scale": [bool(scale.dynamic), scale.fixed_top, scale.window],
    }


def _pairs(mapping):
    """A mapping as a list of pairs, tuple keys as lists, order kept."""
    return [[list(key) if isinstance(key, tuple) else key, value]
            for key, value in mapping.items()]


def _keyed(pairs):
    """`_pairs` back again, list keys as the tuples they were."""
    return [(tuple(key) if isinstance(key, list) else key, value)
            for key, value in pairs]


class Session:
    """The saving and the loading, with everything they read and write.

    Built after the objects it describes and before any key has been pressed,
    which is what lets it note the settings this run started with: those are
    what a later restart compares its own against.

    `shown` and `serial` are the one element lists `cli.main` keeps the flows
    shown and the flow serial in, handed over so that this can read and set
    them without either being a module global.
    """

    def __init__(self, flow_store, args, decoder, tally, scale, resolver,
                 controls, ring, shown, serial, every=SAVE_EVERY):
        self.store = flow_store
        self.args = args
        self.decoder = decoder
        self.tally = tally
        self.scale = scale
        self.resolver = resolver
        self.controls = controls
        self.ring = ring
        self.shown = shown
        self.serial = serial
        self.every = every
        self.started_with = settings(args, scale, resolver)
        self._next = time.time() + every
        # How long the last save took, in seconds, which sets how long the
        # next one waits; see `SAVE_SPACING`.
        self.cost = 0.0
        # Whether a save has failed already, so that a store gone bad says so
        # once rather than once a minute for the rest of the run.
        self.complained = False

    # -- saving -------------------------------------------------------------

    def capture(self, now=None):
        """Everything a restart would want, as one JSON-ready dictionary.

        Whether it was saved on the way out is not in here. The store keeps
        that beside it, so it can be taken back the moment a session is
        picked up without the whole text being written again.
        """
        now = time.time() if now is None else now
        decoder = self.decoder
        sequence = decoder.sequence
        sampling = decoder.sampling
        templates = decoder.templates
        scale = self.scale
        return {
            "format": FORMAT,
            "saved": now,
            "elapsed": now - self.controls.started,
            "shown": self.shown[0],
            "serial": self.serial[0],
            "settings": {"start": self.started_with,
                         "now": settings(self.args, scale, self.resolver)},
            "scale": {"top": scale.top, "largest": scale.largest,
                      "seen": scale.seen,
                      "recent": [list(entry) for entry in scale._recent]},
            "stats": _pairs(decoder.stats),
            "sequence": None if sequence is None else {
                "missed": _pairs(sequence.missed),
                "units": _pairs(sequence.units),
                "resyncs": sequence.resyncs,
                "backwards": sequence.backwards,
                "evicted": sequence.evicted,
            },
            "sampling": None if sampling is None else {
                "rates": _pairs(sampling.rates),
                "evicted": sampling.evicted,
            },
            "templates": {
                "entries": [[exporter, domain, tid,
                             [list(field) for field in fields], options]
                            for (exporter, domain, tid), (fields, options)
                            in templates.templates.items()],
                "learned": templates.learned,
                "evicted": templates.evicted,
                "dropped": templates.dropped,
            },
            "resolver": _pairs(self.resolver.stats),
            "tally": self.tally.state(),
            "ring": self.ring.state(),
        }

    def due(self, now=None):
        """Whether the interval between saves has gone by."""
        now = time.time() if now is None else now
        return now >= self._next

    def save(self, clean=False, now=None):
        """Write the session into the store's open transaction.

        Answers None, or the reason it could not, the first time it could not.
        The store's next commit is what puts it on disk, and closing the store
        commits, which is what the save made on the way out relies on.
        """
        now = time.time() if now is None else now
        began = time.perf_counter()
        try:
            text = json.dumps(self.capture(now=now), separators=(",", ":"))
            self.store.save_session(text, clean=clean, now=now)
        except Exception as exc:  # reported, and never fatal
            self._next = now + self.every
            if self.complained:
                return None
            self.complained = True
            return "the session could not be saved for --resume: %s" % (exc,)
        self.cost = time.perf_counter() - began
        self._next = now + max(self.every, SAVE_SPACING * self.cost)
        return None

    # -- loading ------------------------------------------------------------

    def restore(self, now=None):
        """Load the saved session, if there is one. Answers (resumed, line).

        `line` is what to tell the reader either way, or None when there was
        nothing saved, which is every first run and says nothing worth a line.
        """
        now = time.time() if now is None else now
        try:
            saved = self.store.load_session()
        except Exception as exc:  # a new session, said so
            return False, ("the saved session could not be read, so this is a "
                           "new one: %s" % (exc,))
        if saved is None:
            return False, None
        saved_at, text, clean = saved
        try:
            state = json.loads(text)
            if not isinstance(state, dict) or state.get("format") != FORMAT:
                return False, ("the saved session was written by a build this "
                               "one cannot read, so this is a new one")
            built = self._build(state)
        except (ValueError, TypeError, KeyError, AttributeError,
                IndexError) as exc:
            return False, ("the saved session could not be read, so this is a "
                           "new one: %s" % (exc,))
        fresh = now - saved_at <= TEMPLATE_AGE
        self._apply(built, clean, fresh, now)
        # From here this run numbers flows the saved session does not know
        # about, so the session on disk is a crash's leftovers until this run
        # saves its own. Said at once, since a kill may come before that.
        try:
            self.store.mark_unclean()
        except Exception:  # the save on the way out says it again anyway
            pass
        return True, self._describe(state, clean, fresh, now)

    def _build(self, state):
        """Everything in `state` as the objects it describes, or a raise."""
        built = {
            "tally": Tally.from_state(state["tally"], top=self.tally.top),
            "stats": Counter(dict(_keyed(state["stats"]))),
            "resolver": Counter(dict(_keyed(state["resolver"]))),
            "templates": [
                (exporter, domain, tid,
                 [tuple(field) for field in fields], bool(options))
                for exporter, domain, tid, fields, options
                in state["templates"]["entries"]],
            "template_counts": tuple(
                int(state["templates"][name])
                for name in ("learned", "evicted", "dropped")),
            "ring": [(int(serial), dict(rec), dict(hdr))
                     for serial, rec, hdr in state["ring"]],
            "elapsed": float(state["elapsed"]),
            "shown": int(state["shown"]),
            "serial": int(state["serial"]),
            "start": dict(state["settings"]["start"]),
            "now": dict(state["settings"]["now"]),
        }
        sequence = state.get("sequence")
        built["sequence"] = None if sequence is None else (
            Counter(dict(_keyed(sequence["missed"]))),
            dict(_keyed(sequence["units"])),
            int(sequence["resyncs"]), int(sequence["backwards"]),
            int(sequence["evicted"]))
        sampling = state.get("sampling")
        built["sampling"] = None if sampling is None else (
            OrderedDict(_keyed(sampling["rates"])), int(sampling["evicted"]))
        scale = state["scale"]
        built["scale"] = (scale["top"], scale["largest"], int(scale["seen"]),
                          [tuple(entry) for entry in scale["recent"]])
        # The settings are checked here rather than where they are applied,
        # because applying is meant to be the half that cannot fail: a raise
        # partway through it would leave a run with some of the old session
        # and some of the new one.
        for name, value in built["now"].items():
            if name == "scale":
                dynamic, fixed_top, _window = value
                if not isinstance(fixed_top, (int, float)):
                    raise ValueError("the size scale's top is %r" % (fixed_top,))
            elif name == "resolve":
                if not isinstance(value, str):
                    raise ValueError("the resolve mode is %r" % (value,))
            elif not isinstance(value, bool):
                raise ValueError("%s is %r" % (name, value))
        return built

    def _apply(self, built, clean, fresh, now):
        """Hand over what `_build` made. Nothing here reads the saved text.

        `fresh` is whether the session is young enough for what exporters
        said about themselves to be trusted still; see `TEMPLATE_AGE`. The
        counts of templates learned go back either way, since those are
        figures about the session rather than claims about an exporter.
        """
        self._settings(built["start"], built["now"], built["scale"])

        # After the settings, because the t key's setting may have installed
        # the store that spells templates out, and installing one replaces
        # whatever the decoder held. Put through the base class so that a
        # store which records every put for the t key does not report the
        # whole lot as just arrived, and the events the puts raise are taken
        # off the store before the decoder can pass them on as news.
        store = self.decoder.templates
        for exporter, domain, tid, fields, options in (
                built["templates"] if fresh else ()):
            TemplateStore.put(store, exporter, domain, tid, fields,
                              options=options)
        store.take_events()
        store.learned, store.evicted, store.dropped = built["template_counts"]

        self.tally.take(built["tally"])
        self.decoder.stats.clear()
        self.decoder.stats.update(built["stats"])
        self.resolver.stats.clear()
        self.resolver.stats.update(built["resolver"])
        sequence = self.decoder.sequence
        if sequence is not None and built["sequence"] is not None:
            missed, units, resyncs, backwards, evicted = built["sequence"]
            sequence.missed.clear()
            sequence.missed.update(missed)
            sequence.units.clear()
            sequence.units.update(units)
            sequence.resyncs = resyncs
            sequence.backwards = backwards
            sequence.evicted = evicted
        sampling = self.decoder.sampling
        if sampling is not None and built["sampling"] is not None:
            rates, evicted = built["sampling"]
            sampling.rates.clear()
            if fresh:
                sampling.rates.update(rates)
            sampling.evicted = evicted
        self.ring.restore(built["ring"])
        self.controls.started = now - built["elapsed"]
        self.shown[0] = built["shown"]
        self.serial[0] = built["serial"] + (0 if clean else SERIAL_SKIP)

    def _settings(self, start, ended, scale_state):
        """Put back each setting this run started the way the last one did."""
        args, resolver, scale = self.args, self.resolver, self.scale
        for name in SETTINGS:
            if name not in ended or start.get(name) != self.started_with[name]:
                continue
            value = ended[name]
            if name == "resolve":
                if value in resolver.MODES:
                    resolver.set_mode(value)
                    args.resolve = value
            elif name == "fqdn":
                if bool(value) != bool(resolver.fqdn):
                    resolver.set_fqdn(bool(value))
                args.fqdn = bool(value)
            elif name == "country":
                if country.loaded():
                    country.show(bool(value))
            elif name == "scale":
                dynamic, fixed_top, _window = value
                top, largest, seen, recent = scale_state
                scale.fixed_top = fixed_top
                scale.dynamic = bool(dynamic)
                scale.top = top
                scale.largest = largest
                scale.seen = seen
                scale._recent.clear()
                scale._recent.extend(recent)
            elif name == "templates":
                args.templates = bool(value)
                if value and self.controls.on_templates is not None:
                    self.controls.on_templates()
            else:
                setattr(args, name, bool(value))

    @staticmethod
    def _describe(state, clean, fresh, now):
        ago = max(0, now - float(state["saved"]))
        flows = state["tally"]["scalars"]["flows"]
        how = ("" if clean
               else ", from the last save before it stopped without saving")
        stale = ("" if fresh else "; exporters' templates and sampling rates "
                 "are older than that and will be learned again")
        return ("resumed the session saved %s ago, %d flow%s counted so far%s%s"
                % (_span(ago), flows, "" if flows == 1 else "s", how, stale))


def _span(seconds):
    """A short length of time for the startup line."""
    seconds = int(seconds)
    if seconds < 60:
        return "%d second%s" % (seconds, "" if seconds == 1 else "s")
    if seconds < 3600:
        minutes = seconds // 60
        return "%d minute%s" % (minutes, "" if minutes == 1 else "s")
    if seconds < 86400:
        hours = seconds // 3600
        return "%d hour%s" % (hours, "" if hours == 1 else "s")
    days = seconds // 86400
    return "%d day%s" % (days, "" if days == 1 else "s")
