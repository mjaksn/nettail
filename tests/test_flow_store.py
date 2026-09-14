"""Phase 1 durable flow storage: the file, its defaults, and its retention.
"""

import json
import os
import tempfile
import time

from harness import check, finish

from nettail import store

work = tempfile.mkdtemp(prefix="nettail-store-")
target = os.path.join(work, "flows.sqlite3")

# --- path choice -------------------------------------------------------------
unix = store.default_path(platform="linux", env={"XDG_DATA_HOME": "/data"},
                          home="/home/alice")
check("the default Unix path lives under XDG data",
      unix == os.path.join("/data", "nettail", "flows.sqlite3"), unix)
mac = store.default_path(platform="darwin", env={}, home="/Users/alice")
check("the default macOS path lives under Application Support",
      mac == os.path.join("/Users/alice", "Library", "Application Support",
                          "nettail", "flows.sqlite3"), mac)
windows = store.default_path(platform="win32",
                             env={"APPDATA": r"C:\Users\alice\AppData\Roaming"},
                             home=r"C:\Users\alice")
check("the default Windows path prefers APPDATA",
      windows == os.path.join(r"C:\Users\alice\AppData\Roaming", "nettail",
                              "flows.sqlite3"), windows)
check("the default prune cadence is 12 hours",
      store.DEFAULT_PRUNE_CADENCE == 12 * 60 * 60,
      str(store.DEFAULT_PRUNE_CADENCE))
check("the default commit cadence is 1 second",
      store.DEFAULT_COMMIT_CADENCE == 1.0,
      str(store.DEFAULT_COMMIT_CADENCE))

# --- one row goes in and comes back ------------------------------------------
history = store.FlowStore(target, retention_days=7)
record = {
    "_received": time.time(),
    "_timestamp": time.time() - 1,
    "_exporter": "10.0.0.1",
    "_version": 5,
    "src_addr": "192.168.1.10",
    "dst_addr": "8.8.8.8",
    "src_port": 51000,
    "dst_port": 443,
    "proto": 6,
    "octets": 1500,
    "packets": 12,
}
ingest_id = history.write(record)
latest = history.latest()
check("the first row is numbered 1", ingest_id == 1, str(ingest_id))
check("one stored flow is counted", history.count() == 1, str(history.count()))
check("the stored row keeps its exporter", latest["exporter"] == "10.0.0.1",
      str(latest))
check("and keeps the record as JSON",
      json.loads(latest["record_json"])["dst_port"] == 443, latest["record_json"])
seen = history.since(0)
check("rows since an id include the first row",
      [row["ingest_id"] for row in seen] == [1], str(seen))
history.close()

# --- writes stay buffered until the commit cadence says otherwise -------------
buffered_path = os.path.join(work, "buffered.sqlite3")
buffered = store.FlowStore(buffered_path, retention_days=7, commit_every=60.0)
base = time.time()
buffered.write({
    "_received": base,
    "_timestamp": base,
    "_exporter": "10.0.0.4",
    "_version": 5,
})
visible = buffered._db.execute("SELECT COUNT(*) FROM flows").fetchone()[0]
check("the writer sees its buffered row immediately", visible == 1, str(visible))
outside = store.sqlite3.connect(buffered_path)
try:
    hidden = outside.execute("SELECT COUNT(*) FROM flows").fetchone()[0]
finally:
    outside.close()
check("another connection does not see it before a flush", hidden == 0, str(hidden))
check("the buffered commit is not due early",
      buffered.flush_due(now=base + buffered.commit_every - 1) is False,
      str(buffered.commit_every))
check("and is due at the configured cadence",
      buffered.flush_due(now=base + buffered.commit_every),
      str(buffered.commit_every))
next_commit = buffered.flush(now=base + buffered.commit_every)
check("flushing schedules the next commit window",
      next_commit == base + (2 * buffered.commit_every), str(next_commit))
outside = store.sqlite3.connect(buffered_path)
try:
    shown = outside.execute("SELECT COUNT(*) FROM flows").fetchone()[0]
finally:
    outside.close()
check("a flushed row is visible to another connection", shown == 1, str(shown))
check("rows since an id include buffered writes from this connection",
      [row["ingest_id"] for row in buffered.since(0)] == [1],
      str(buffered.since(0)))
buffered.close()

# --- retention drops what is too old ----------------------------------------
retained = store.FlowStore(os.path.join(work, "retained.sqlite3"), retention_days=1)
now = time.time()
retained.write({
    "_received": now - (3 * 24 * 60 * 60),
    "_timestamp": now - (3 * 24 * 60 * 60),
    "_exporter": "10.0.0.2",
    "_version": 5,
})
retained.write({
    "_received": now,
    "_timestamp": now,
    "_exporter": "10.0.0.3",
    "_version": 5,
})
retained.prune(now=now)
check("retention drops rows older than the bound",
      retained.count() == 1, str(retained.count()))
check("and leaves the newest row behind",
      retained.latest()["exporter"] == "10.0.0.3", str(retained.latest()))
check("pruning schedules the next run from now",
      retained.prune_due(now=now + retained.prune_every - 1) is False,
      str(retained.prune_every))
check("and becomes due once the cadence has passed",
      retained.prune_due(now=now + retained.prune_every),
      str(retained.prune_every))
next_prune = retained.prune(now=now + retained.prune_every)
check("a due prune schedules the one after it too",
      next_prune == now + (2 * retained.prune_every),
      str(next_prune))
check("and is not immediately due again after it runs",
      retained.prune_due(now=next_prune - 1) is False, str(next_prune))
retained.close()

# --- the config type is strict about days ------------------------------------
check("retention accepts a whole number", store.retention_arg("14") == 14)
for bad in ("0", "-1", "three"):
    try:
        store.retention_arg(bad)
        ok = False
    except ValueError:
        ok = True
    check("%r is refused as a retention period" % bad, ok)

check("a prune cadence accepts seconds", store.cadence_arg("1800") == 1800)
check("and accepts fractions of a second", store.cadence_arg("0.5") == 0.5)
for bad in ("0", "-1", "never"):
    try:
        store.cadence_arg(bad)
        ok = False
    except ValueError:
        ok = True
    check("%r is refused as a prune cadence" % bad, ok)

check("the startup note names the cadence in hours",
      "12 hours" in store.describe("flows.sqlite3", 14,
                                   store.DEFAULT_PRUNE_CADENCE,
                                   store.DEFAULT_COMMIT_CADENCE),
      store.describe("flows.sqlite3", 14, store.DEFAULT_PRUNE_CADENCE,
                     store.DEFAULT_COMMIT_CADENCE))
check("and the commit cadence in seconds",
      "1 second" in store.describe("flows.sqlite3", 14,
                                   store.DEFAULT_PRUNE_CADENCE,
                                   store.DEFAULT_COMMIT_CADENCE),
      store.describe("flows.sqlite3", 14, store.DEFAULT_PRUNE_CADENCE,
                     store.DEFAULT_COMMIT_CADENCE))

finish("flow store")
