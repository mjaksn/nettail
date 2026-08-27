"""The event bus on its own: what it publishes, and what it drops.

No sockets and no HTTP here. `feed.Feed` was split out of the server precisely
so a whole session can be driven through it and read back, and this is the
suite that takes it up on that.
"""
from harness import check, finish

from nettail.feed import CLIENT_BACKLOG, PROSE_KINDS, Feed

# -- an unwatched bus costs nothing -------------------------------------

bus = Feed()
check("a fresh bus is inactive", bus.active is False)
check("and has no clients", bus.clients == 0)

# The hot path asks `active` before it builds anything, so a publish into an
# unwatched bus has to be harmless rather than merely cheap.
bus.publish("flow", {"anything": 1})
bus.flow({"anything": 1})
bus.status({"snap": {}})
bus.prose("notice", "something happened")
bus.clear()
check("publishing into an unwatched bus does nothing", bus.clients == 0)

# -- subscribing ---------------------------------------------------------

client = bus.subscribe()
check("subscribing hands back a client", client is not None)
check("and the bus becomes active", bus.active is True)
check("which is what the hot path reads", bus.clients == 1)

bus.flow({"src": "192.0.2.1"})
events, dropped = bus.drain(client)
check("a flow arrives as one event", len(events) == 1)
check("under its own name", events[0][0] == "flow")
check("carrying what was published", events[0][1] == {"src": "192.0.2.1"})
check("with nothing dropped", dropped == 0)

check("draining empties the queue", bus.drain(client) == ([], 0))

# -- prose ---------------------------------------------------------------

bus.prose("summary", "the report")
events, _dropped = bus.drain(client)
check("prose carries its kind", events[0][1]["kind"] == "summary")
check("and its text", events[0][1]["text"] == "the report")

bus.prose("notice", "")
check("empty prose is not published at all", bus.drain(client) == ([], 0))

for kind in PROSE_KINDS:
    bus.prose(kind, "x")
events, _dropped = bus.drain(client)
check("every documented prose kind publishes",
      len(events) == len(PROSE_KINDS),
      "%d of %d" % (len(events), len(PROSE_KINDS)))

# -- overflow drops the oldest and counts it -----------------------------

small = Feed(backlog=4)
watcher = small.subscribe()
for n in range(10):
    small.flow({"n": n})
events, dropped = small.drain(watcher)
check("a full queue keeps only its maximum", len(events) == 4)
check("and keeps the newest", [e[1]["n"] for e in events] == [6, 7, 8, 9])
check("counting what it threw away", dropped == 6)
check("and the count resets once reported", small.drain(watcher)[1] == 0)
check("the default backlog is the documented one", CLIENT_BACKLOG == 4000)

# -- more than one watcher -----------------------------------------------

second = bus.subscribe()
check("a second client is allowed", second is not None)
check("and both are counted", bus.clients == 2)
bus.flow({"seen": "by both"})
check("a publish reaches the first", len(bus.drain(client)[0]) == 1)
check("and the second", len(bus.drain(second)[0]) == 1)

# The cap has to be applied where the append happens, or two browsers arriving
# together both find room and both take the last place.
capped = Feed()
first = capped.subscribe(limit=1)
check("subscribing under the limit works", first is not None)
check("and at the limit is refused", capped.subscribe(limit=1) is None)

# -- unsubscribing -------------------------------------------------------

bus.unsubscribe(second)
check("unsubscribing drops the client", bus.clients == 1)
check("but the bus is still active", bus.active is True)
bus.unsubscribe(second)
check("unsubscribing twice is harmless", bus.clients == 1)
bus.unsubscribe(client)
check("the last one out makes it inactive", bus.active is False)

# -- the greeting a late arrival gets ------------------------------------

late = Feed()
late.set_hello({"nettail": "0.1.2", "columns": [{"name": "TIME"}]})
watcher = late.subscribe()
late.status({"shown": {"flows": "12"}})
late.drain(watcher)
greeting = late.hello()
check("the greeting carries what was set", greeting["nettail"] == "0.1.2")
check("and the columns with it", greeting["columns"][0]["name"] == "TIME")
check("with the status as it is now, not as it was",
      greeting["status"]["shown"]["flows"] == "12")

late.status({"shown": {"flows": "99"}})
check("so a later arrival sees the later figures",
      late.hello()["status"]["shown"]["flows"] == "99")

# -- shutting down -------------------------------------------------------

closing = Feed()
watcher = closing.subscribe()
closing.prose("summary", "the last word")
closing.close()
check("closing marks the client", watcher.closed is True)
check("and wakes it", watcher.ready.is_set() is True)
# The writer drains before it looks at the flag, which is what makes the exit
# summary the last thing a browser is handed rather than the first thing lost.
events, _dropped = closing.drain(watcher)
check("what was published before the close is still there",
      len(events) == 1 and events[0][1]["text"] == "the last word")
check("and nothing new may subscribe", closing.subscribe() is None)

finish("web feed")
