"""The events a browser watches, and the bounded queues they wait in.

This is the half of the web interface that knows nothing about HTTP. It holds
the event vocabulary, one bounded queue per connected client, and the counter
that says how much a slow client missed. `web.py` sits on top of it and turns
what is here into a stream; the receive loop in `cli.py` publishes into it and
never learns whether anyone was listening.

Keeping the two apart is what makes this testable. A suite can subscribe, drive
a whole session through `Feed`, and read the events back without binding a
socket, which is the same bargain `Controls` strikes against the keyboard.

Two rules hold the whole design up.

**Publishing must be free when nobody is watching.** The display path builds no
per-flow dictionary today, and building one on the chance that a browser might
be attached would put real work in the hot path of a busy link. So callers ask
`feed.active` first and build only then. `Feed` is written so that an inactive
one answers that question with an attribute lookup.

**A slow client must never reach back into the receive loop.** Each subscriber
gets a `deque` with a maximum length, and a publish that finds one full drops
the oldest and counts it. That is the same bargain `PAUSE_BUFFER` strikes with
held flows, and for the same reason: dropping the oldest keeps the newest,
which is what somebody watching a live stream is actually looking at. The count
goes to the client so that a gap is something it can say out loud rather than a
silence it presents as continuity.
"""

import threading
from collections import deque

# How many events one client may fall behind by. A flow event is a few hundred
# bytes, so this is a couple of megabytes per client in the worst case, and a
# client that far behind has lost the plot anyway. Small enough that a paused
# tab cannot grow without bound, large enough to ride out a garbage collection
# or a window that was dragged between monitors.
CLIENT_BACKLOG = 4000

# Every event kind, and what it carries. Written down here because three things
# have to agree about it: what `cli.py` publishes, what `web.py` names in the
# stream, and what the page dispatches on.
EVENTS = (
    ("hello", "the collector's settings, the key table, the columns, a status"),
    ("flow", "one flow record, the same shape --json prints"),
    ("status", "the status bar snapshot, on a clock"),
    ("prose", "a block of text the terminal also printed, ANSI intact"),
    ("clear", "the x key: throw away what is on screen"),
    ("dropped", "how many events this client missed while it was behind"),
)

# The prose kinds, in the order a session tends to produce them. The page
# styles a summary differently from a one-line reply, so the kind travels with
# the text rather than being guessed at from its shape.
PROSE_KINDS = ("banner", "notice", "reply", "summary", "hosts", "keys")


class Client:
    """One connected browser: a bounded backlog and what it has missed.

    Owned by the feed, read by the writer thread serving that browser. The lock
    is the feed's, not one per client, because a publish touches every client
    and taking one lock beats taking N.
    """

    def __init__(self, backlog=CLIENT_BACKLOG):
        self.queue = deque(maxlen=backlog)
        self.dropped = 0
        # Set when the writer should stop: on shutdown, or when the client cap
        # turns a browser away after it has already been given a queue.
        self.closed = False
        # Raised by the feed when something is put on the queue, so a writer
        # waits rather than polls. A threading.Event rather than a Condition
        # because a writer only ever waits for "something happened", and the
        # queue itself is the state it then reads.
        self.ready = threading.Event()


class Feed:
    """Where the collector publishes, and where browsers read from.

    An instance with no subscribers is inert: `active` is False, every publish
    returns immediately, and the caller skips building whatever it was going to
    publish. That is the state every run without `--web` stays in for its whole
    life, and it is why this costs nothing when it is not wanted.
    """

    def __init__(self, backlog=CLIENT_BACKLOG):
        self.backlog = backlog
        self._lock = threading.Lock()
        self._clients = []
        # Mirrors len(self._clients) so that the hot path can ask whether to
        # bother without taking the lock. Written under the lock, read without
        # it: a stale read costs one event published into nothing, or one not
        # published to a browser that connected microseconds ago, and neither
        # is worth serialising the receive loop for.
        self.active = False
        # What a browser needs to render a complete page when it arrives an
        # hour into a session. Held rather than rebuilt, because the collector
        # is the only thing that can produce it and it is not on the HTTP
        # thread when the request comes in.
        self._hello = None
        self._last_status = None
        # Set by close(). A browser that connects during teardown is
        # turned away rather than handed a queue nothing will ever fill.
        self._closed_down = False

    # -- subscribing --------------------------------------------------------

    def subscribe(self, limit=None):
        """Hand back a Client, or None when it cannot have one.

        None means either that the collector is shutting down or that the
        limit is already reached. The limit is checked in here, under the
        same lock that appends, because checking it outside and subscribing
        after leaves room for two browsers arriving together to both find
        space and both take it.
        """
        with self._lock:
            if self._closed_down:
                return None
            if limit is not None and len(self._clients) >= limit:
                return None
            client = Client(self.backlog)
            self._clients.append(client)
            self.active = True
            return client

    def unsubscribe(self, client):
        """Drop a client. Safe to call twice, which a writer's finally does."""
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)
            self.active = bool(self._clients)
        client.closed = True
        client.ready.set()

    @property
    def clients(self):
        """How many browsers are attached. For the status line and the cap."""
        with self._lock:
            return len(self._clients)

    # -- publishing ---------------------------------------------------------

    def publish(self, kind, data=None):
        """Put one event in front of every client. Never blocks, never raises.

        Called from the receive loop, so the contract is that nothing a client
        does can slow this down or stop it. A full queue loses its oldest entry
        and gains one on the drop counter, which the writer turns into a
        `dropped` event the page can show.
        """
        if not self.active:
            return
        event = (kind, data)
        with self._lock:
            for client in self._clients:
                if len(client.queue) == client.queue.maxlen:
                    client.dropped += 1
                client.queue.append(event)
                client.ready.set()

    def drain(self, client):
        """Everything one client is waiting for, and how much it missed.

        Taken in one pass under the lock so that a writer never holds it
        while it writes to a socket, which is the one thing that could let
        a slow client reach back into the receive loop.
        """
        with self._lock:
            events = list(client.queue)
            client.queue.clear()
            dropped, client.dropped = client.dropped, 0
        return events, dropped

    def flow(self, record):
        """One flow, already shaped the way --json prints it."""
        self.publish("flow", record)

    def prose(self, kind, text):
        """A block the terminal also printed, escape codes and all."""
        if text:
            self.publish("prose", {"kind": kind, "text": text})

    def status(self, snap):
        """The status snapshot, kept for late arrivals as well as sent."""
        self._last_status = snap
        self.publish("status", snap)

    def clear(self):
        self.publish("clear", None)

    # -- what a late arrival needs -----------------------------------------

    def set_hello(self, hello):
        """The fixed half of what a new client is told: settings, keys, columns."""
        self._hello = hello

    def hello(self):
        """The greeting for a client that has just connected.

        The status is spliced in here rather than stored inside the greeting,
        so that a browser opened twenty minutes in gets figures from twenty
        minutes in instead of the ones the collector started with.

        It is the last status published, though, and publishing stops while
        nobody is watching. A client arriving into a quiet stretch is therefore
        told what was true when the last watcher left, and a fresh status
        reaches it within a repaint interval. That matters to one caller: a tab
        that gave up its stream in the background counts what it missed by
        subtracting, and has to subtract from a status frame rather than from
        this, which would be the same figure it noted on the way out.
        """
        greeting = dict(self._hello or {})
        greeting["status"] = self._last_status
        return greeting

    # -- shutting down ------------------------------------------------------

    def close(self):
        """Tell every writer to finish, and refuse new subscribers.

        Called on the way out, after the exit summary has been published, so
        that the last thing a browser receives is the report rather than a
        connection that died mid-sentence. Waking the writers is this side's
        job; joining them is `web.py`'s.
        """
        with self._lock:
            self._closed_down = True
            clients = list(self._clients)
        for client in clients:
            client.closed = True
            client.ready.set()
