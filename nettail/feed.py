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

import secrets
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
    ("flow", "one flow's cells to draw, and the record --json prints"),
    ("status", "the status bar snapshot, on a clock"),
    ("prose", "a block of text the terminal also printed, ANSI intact"),
    ("clear", "the x key: throw away what is on screen"),
    ("detail", "the answer to one browser's ask about a flow, by id"),
    ("filter", "this client's filter changed, and every flow after this obeys it"),
    ("dropped", "how many events this client missed while it was behind"),
)

# The prose kinds, in the order a session tends to produce them. The page
# styles a summary differently from a one-line reply, so the kind travels with
# the text rather than being guessed at from its shape. A template listing is
# its own kind rather than a notice, because a notice is something gone wrong
# and a template is only the shape of what is arriving.
PROSE_KINDS = ("banner", "notice", "template", "reply", "summary", "hosts",
               "keys")


class Client:
    """One connected browser: a bounded backlog and what it has missed.

    Owned by the feed, read by the writer thread serving that browser. The lock
    is the feed's, not one per client, because a publish touches every client
    and taking one lock beats taking N.
    """

    def __init__(self, backlog=CLIENT_BACKLOG, term=None):
        self.queue = deque(maxlen=backlog)
        self.dropped = 0
        # What the page names this subscription by when it changes its filter.
        # Random rather than counted, so that one tab cannot set another's
        # filter by guessing the number next to its own. Every tab already
        # holds the token, so this is about accidents rather than attackers.
        self.id = secrets.token_urlsafe(16)
        # The term as the reader typed it, for saying back to them, and the
        # same term case folded, which is what flows are compared against.
        # Both empty and None respectively when this client sees every flow.
        self.term = ""
        self.filter = None
        self.set_term(term)
        # Set when the writer should stop: on shutdown, or when the client cap
        # turns a browser away after it has already been given a queue.
        self.closed = False
        # Raised by the feed when something is put on the queue, so a writer
        # waits rather than polls. A threading.Event rather than a Condition
        # because a writer only ever waits for "something happened", and the
        # queue itself is the state it then reads.
        self.ready = threading.Event()

    def set_term(self, term):
        """Take a filter term, or clear the filter with an empty one."""
        self.term = (term or "").strip()
        self.filter = self.term.casefold() or None

    def wants(self, folded):
        """Whether a flow with these case folded terms passes this filter.

        None for `folded` means the terms were never worked out, which happens
        when the filter was set between the publisher asking whether anybody
        filters and handing the flow over. The flow goes through: a row too
        many under a filter just applied is a smaller wrong than a row lost
        from a view that asked for everything.
        """
        return self.filter is None or folded is None or self.filter in folded


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
        # Whether any client has a filter, which is whether a publisher has to
        # work out what a flow can be matched on at all. Written and read the
        # way `active` is, and for the same reason.
        self.filtering = False
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

    def subscribe(self, limit=None, term=None):
        """Hand back a Client, or None when it cannot have one.

        None means either that the collector is shutting down or that the
        limit is already reached. The limit is checked in here, under the
        same lock that appends, because checking it outside and subscribing
        after leaves room for two browsers arriving together to both find
        space and both take it.

        `term` is a filter the client arrives with, which is how a tab that
        gave its stream up in the background comes back still filtering.
        Setting it afterwards would let every flow published in between
        through, and the page would have nothing to tell them apart by.
        """
        with self._lock:
            if self._closed_down:
                return None
            if limit is not None and len(self._clients) >= limit:
                return None
            client = Client(self.backlog, term)
            self._clients.append(client)
            self.active = True
            self._count_filters()
            return client

    def unsubscribe(self, client):
        """Drop a client. Safe to call twice, which a writer's finally does."""
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)
            self.active = bool(self._clients)
            self._count_filters()
        client.closed = True
        client.ready.set()

    def set_filter(self, client_id, term):
        """Change one client's filter. False when there is no such client.

        Called from a request thread, and allowed to be for the reason
        subscribing is: it changes what one browser is sent and nothing about
        what the collector is doing, and it happens under the lock every
        publish takes. That lock is also what makes the `filter` event exact.
        It goes on the client's own queue in the same breath the filter
        changes, so every flow ahead of it was sent under the old filter and
        every flow behind it under the new one, and the page's note about the
        change lands on the line where the change really is.
        """
        with self._lock:
            client = next((c for c in self._clients if c.id == client_id), None)
            if client is None:
                return False
            client.set_term(term)
            self._count_filters()
            self._put(client, ("filter", {"term": client.term}))
        return True

    def _count_filters(self):
        # Under the lock, from every place a client or its filter changes.
        self.filtering = any(c.filter is not None for c in self._clients)

    @property
    def clients(self):
        """How many browsers are attached. For the status line and the cap."""
        with self._lock:
            return len(self._clients)

    def ids(self):
        """The ids of the browsers attached now."""
        with self._lock:
            return frozenset(c.id for c in self._clients)

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
                self._put(client, event)

    @staticmethod
    def _put(client, event):
        # Under the lock. The one place an event joins a queue.
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

    def flow(self, record, terms=None):
        """One flow, to every client whose filter it passes.

        `terms` is what the flow can be matched on, from
        `display.filter_terms`, and is only worth working out when `filtering`
        says somebody is asking. Case is folded here, once, and the client's
        term was folded the same way, so there is one opinion about what a
        capital is and it is Python's.

        Answers the ids of the clients that took it, which is what the details
        ring keeps records by: a flow no browser was shown cannot be clicked,
        and one that a browser was shown has to stay clickable for as long as
        that browser could still have its row.
        """
        if not self.active:
            return ()
        event = ("flow", record)
        folded = None if terms is None else {t.casefold() for t in terms}
        takers = []
        with self._lock:
            for client in self._clients:
                if client.wants(folded):
                    self._put(client, event)
                    takers.append(client.id)
        return takers

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

    def detail(self, payload):
        """The answer to one browser's ask about a flow.

        Published to every client rather than to the one that asked, and the
        ask's own id rides on it so a page can tell its answer from somebody
        else's. Per-client publishing is the other way to do it and would mean
        the feed learning which client an ask came from, which is a thread
        boundary this deliberately does not cross; at four watchers the cost
        of the broadcast is three pages parsing a frame and dropping it.
        """
        self.publish("detail", payload)

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
