"""Durable flow history on the local machine.

One row per shown flow, written as it arrives, with enough indexed facts to
answer the replay a tab back from the background gets and the older rows a tab
scrolling up asks for, without freezing any browser shape into the database.
The collector is the one writer and the receive loop is where that writing
happens, so this module owns the file, the schema and the retention pass and
answers ordinary Python methods rather than exposing SQL around the program.
"""

import argparse
import configparser
import io
import json
import os
import sqlite3
import sys
import time

SCHEMA_VERSION = 1
DB_NAME = "flows.sqlite3"
DEFAULT_PRUNE_CADENCE = 12 * 60 * 60
DEFAULT_COMMIT_CADENCE = 1.0

# A collector without any help from the reader is the point of this feature, so
# the history file lives in the same per-user place the saved config does rather
# than in the working directory.
WINDOWS_DIRS = ("APPDATA", "LOCALAPPDATA")


def default_path(platform=None, env=None, home=None):
    """Where the flow history file lives by default on this machine."""
    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    home = os.path.expanduser("~") if home is None else home
    windows = platform.startswith("win") or platform == "cygwin"
    if windows:
        for variable in WINDOWS_DIRS:
            root = env.get(variable)
            if root:
                return os.path.join(root, "nettail", DB_NAME)
        return os.path.join(home, ".nettail", DB_NAME)
    if platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "nettail",
                            DB_NAME)
    root = env.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    return os.path.join(root, "nettail", DB_NAME)


def retention_arg(text):
    """One retention period in days, off the command line or out of a file."""
    try:
        days = int(text)
    except ValueError as exc:
        raise ValueError("expected a whole number of days, not %r" % (text,)) from exc
    if days < 1:
        raise ValueError("expected at least 1 day, not %r" % (text,))
    return days


def cadence_arg(text):
    """One prune cadence in seconds, off the command line or out of a file."""
    try:
        seconds = float(text)
    except ValueError as exc:
        raise ValueError("expected seconds, not %r" % (text,)) from exc
    if not 0 < seconds < float("inf"):
        raise ValueError("expected more than 0 seconds, not %r" % (text,))
    return seconds


# The word that turns the store off. A run keeps a history unless it says
# otherwise, so the option needs a value meaning no file at all, and it is this
# string rather than None for `--save-config`'s sake: `config.render` writes a
# value of None as the default, commented out, so a run with the store off
# would save a file that turned it back on. `enabled` is the one place it is
# compared against, as `jsonout.to_stdout` is for `--json`.
OFF = "off"


def store_arg(text):
    """One `--flow-store` value: a path to write, or `off` for none at all.

    The rest of the words a settings file spells a switch with are refused
    rather than opened as a file of that name. A file's switches never reach
    here, since `config` reads them as the flag itself or as off first, so one
    arriving here was typed, and a history written to a file called `false` is
    not what it was typed for. An empty value is refused for the reason an
    empty `--config` is: it came from a shell variable nobody set.
    """
    text = text.strip()
    if not text:
        raise argparse.ArgumentTypeError(
            "needs a path to write, or %r for no flow history" % (OFF,))
    if text.lower() == OFF:
        return OFF
    if text.lower() in configparser.ConfigParser.BOOLEAN_STATES:
        raise argparse.ArgumentTypeError(
            "%r is not a path: give a path to write, or %r for no flow history"
            % (text, OFF))
    return text


def enabled(args):
    """Whether this run keeps a flow history at all."""
    return getattr(args, "flow_store", OFF) != OFF


class FlowStore:
    """The SQLite file a collector appends shown flows to."""

    def __init__(self, path, retention_days, prune_every=DEFAULT_PRUNE_CADENCE,
                 commit_every=DEFAULT_COMMIT_CADENCE):
        self.path = path
        self.retention_days = retention_days
        self.prune_every = prune_every
        self.commit_every = commit_every
        self._next_id = 0
        self._next_prune = 0.0
        self._next_commit = 0.0
        self._dirty = False
        directory = os.path.dirname(path)
        old_umask = os.umask(0o077)
        try:
            if directory:
                os.makedirs(directory, exist_ok=True)
            self._db = sqlite3.connect(path, isolation_level=None)
        finally:
            os.umask(old_umask)
        self._db.row_factory = sqlite3.Row
        self._configure()
        self._db.execute("BEGIN")
        self._create_schema()
        self._check_schema()
        self._next_id = self._read_next_id()
        self.prune()
        self.flush()

    def _configure(self):
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA synchronous = NORMAL")
        self._db.execute("PRAGMA foreign_keys = ON")

    def _create_schema(self):
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS flows (
                ingest_id INTEGER PRIMARY KEY,
                received REAL NOT NULL,
                flow_time REAL NOT NULL,
                exporter TEXT NOT NULL,
                version INTEGER NOT NULL,
                src_addr TEXT,
                dst_addr TEXT,
                src_port INTEGER,
                dst_port INTEGER,
                proto INTEGER,
                octets INTEGER,
                packets INTEGER,
                record_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS flows_received
                ON flows(received, ingest_id);
            CREATE INDEX IF NOT EXISTS flows_src_received
                ON flows(src_addr, received, ingest_id);
            CREATE INDEX IF NOT EXISTS flows_dst_received
                ON flows(dst_addr, received, ingest_id);
            CREATE INDEX IF NOT EXISTS flows_exporter_received
                ON flows(exporter, received, ingest_id);
            CREATE TABLE IF NOT EXISTS session (
                name TEXT PRIMARY KEY,
                saved REAL NOT NULL,
                value TEXT NOT NULL
            );
        """)
        if self._db.execute("PRAGMA user_version").fetchone()[0] == 0:
            self._db.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
        self._dirty = True

    # The session table is added beside the flows rather than announced with a
    # new `SCHEMA_VERSION`, and that is deliberate. `_check_schema` refuses any
    # version it was not built for, so a bump would make the release before
    # this one refuse to start on a volume this one had touched, and rolling an
    # image back is exactly what somebody does when a new one misbehaves. An
    # older build never reads the table and is not disturbed by it being there.

    def _check_schema(self):
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            raise RuntimeError(
                "flow history schema %d is not supported by this build"
                % version)

    def _read_next_id(self):
        row = self._db.execute(
            "SELECT COALESCE(MAX(ingest_id), 0) FROM flows").fetchone()
        return int(row[0])

    def write(self, record):
        """Append one shown flow to durable history."""
        self._next_id += 1
        self._db.execute(
            """
            INSERT INTO flows (
                ingest_id, received, flow_time, exporter, version,
                src_addr, dst_addr, src_port, dst_port, proto, octets, packets,
                record_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._next_id,
                float(record["_received"]),
                float(record["_timestamp"]),
                str(record["_exporter"]),
                int(record["_version"]),
                record.get("src_addr"),
                record.get("dst_addr"),
                record.get("src_port"),
                record.get("dst_port"),
                record.get("proto"),
                record.get("octets", record.get("octets_total")),
                record.get("packets", record.get("packets_total")),
                json.dumps(record, default=str),
            ),
        )
        self._dirty = True
        return self._next_id

    def prune(self, now=None):
        """Drop rows older than the retention window."""
        now = time.time() if now is None else now
        floor = now - (self.retention_days * 24 * 60 * 60)
        self._db.execute("DELETE FROM flows WHERE received < ?", (floor,))
        self._dirty = True
        self._next_prune = now + self.prune_every
        return self._next_prune

    def prune_due(self, now=None):
        """Whether the scheduled prune time has arrived."""
        now = time.time() if now is None else now
        return now >= self._next_prune

    def flush_due(self, now=None):
        """Whether the buffered transaction should be committed now."""
        now = time.time() if now is None else now
        return self._dirty and now >= self._next_commit

    def flush(self, now=None):
        """Commit buffered writes and open the next transaction."""
        now = time.time() if now is None else now
        if self._dirty:
            self._db.commit()
            self._db.execute("BEGIN")
            self._dirty = False
        self._next_commit = now + self.commit_every
        return self._next_commit

    def count(self):
        """How many rows are in the durable history."""
        return int(self._db.execute("SELECT COUNT(*) FROM flows").fetchone()[0])

    def latest(self):
        """The newest row, for the tests."""
        row = self._db.execute(
            """
            SELECT ingest_id, received, exporter, src_addr, dst_addr, record_json
            FROM flows ORDER BY ingest_id DESC LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def since(self, ingest_id, limit=None):
        """Rows newer than this ingest id, including buffered writes."""
        sql = (
            "SELECT ingest_id, record_json FROM flows "
            "WHERE ingest_id > ? ORDER BY ingest_id"
        )
        params = [int(ingest_id)]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = self._db.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def before(self, ingest_id, limit):
        """Up to `limit` rows older than this ingest id, newest first.

        Newest first because the caller is walking backwards from what a
        browser already holds, in chunks, and the last row of one chunk is
        where the next one starts. A chunk shorter than `limit` is the start
        of the history, which the caller reads as there being no more.
        """
        rows = self._db.execute(
            "SELECT ingest_id, record_json FROM flows "
            "WHERE ingest_id < ? ORDER BY ingest_id DESC LIMIT ?",
            (int(ingest_id), int(limit))).fetchall()
        return [dict(row) for row in rows]

    def save_session(self, text, clean=False, now=None):
        """Keep the session `--resume` will pick up, in the open transaction.

        Written beside the flows, in their transaction, so that a commit puts
        the two on disk together and a crash loses both from one moment rather
        than one without the other.

        Whether it was saved on the way out is a row of its own rather than a
        field in the text, so that it is a fact about the save and not about
        the session, and is read without parsing tens of megabytes to find it.
        """
        now = time.time() if now is None else now
        self._db.execute(
            "INSERT OR REPLACE INTO session (name, saved, value) "
            "VALUES ('state', ?, ?)", (float(now), text))
        self._db.execute(
            "INSERT OR REPLACE INTO session (name, saved, value) "
            "VALUES ('clean', ?, ?)", (float(now), "1" if clean else "0"))
        self._dirty = True

    def clear_session(self):
        """Forget the saved session. Answers whether there was one.

        What a run that was not asked to resume does with one it finds, so
        that leaving `--resume` out means starting afresh now and later: a
        session left in place would come back, weeks stale, the next time the
        flag was given, without the flows counted in between.
        """
        found = self._db.execute(
            "DELETE FROM session WHERE name IN ('state', 'clean')").rowcount
        if found:
            self._dirty = True
        return bool(found)

    def load_session(self):
        """The saved session as (when, text, clean), or None for none."""
        rows = {row["name"]: row for row in self._db.execute(
            "SELECT name, saved, value FROM session "
            "WHERE name IN ('state', 'clean')")}
        state = rows.get("state")
        if state is None:
            return None
        clean = rows.get("clean")
        return (float(state["saved"]), state["value"],
                clean is not None and clean["value"] == "1")

    def close(self):
        if self._db is not None:
            self.flush()
            self._db.close()
            self._db = None


class DisabledStore:
    """A no-op history sink, kept so the hot path asks one question."""

    path = None
    retention_days = None
    prune_every = None
    commit_every = None

    def write(self, record):
        return None

    def prune(self, now=None):
        return None

    def prune_due(self, now=None):
        return False

    def flush_due(self, now=None):
        return False

    def flush(self, now=None):
        return None

    def count(self):
        return 0

    def latest(self):
        return None

    def since(self, ingest_id, limit=None):
        return []

    def before(self, ingest_id, limit):
        return []

    def save_session(self, text, clean=False, now=None):
        return None

    def clear_session(self):
        return False

    def load_session(self):
        return None

    def close(self):
        return None


def describe(path, retention_days, prune_every, commit_every):
    """One startup line naming the durable history file and its bound."""
    buffer = io.StringIO()
    print("recording shown flows in %s, keeping %d day%s, pruning every %s, "
          "committing every %s"
          % (path, retention_days, "" if retention_days == 1 else "s",
             _seconds(prune_every), _seconds(commit_every)),
          file=buffer, end="")
    return buffer.getvalue()


def _seconds(value):
    """A cadence as a short piece of prose for the startup line."""
    if value >= 3600 and value % 3600 == 0:
        hours = int(value // 3600)
        return "%d hour%s" % (hours, "" if hours == 1 else "s")
    if value >= 60 and value % 60 == 0:
        minutes = int(value // 60)
        return "%d minute%s" % (minutes, "" if minutes == 1 else "s")
    return ("%g second%s" % (value, "" if value == 1 else "s"))
