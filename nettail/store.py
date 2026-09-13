"""Durable flow history on the local machine.

Phase 1 is a write path and nothing more: one row per shown flow, written as it
arrives, with enough indexed facts to support later replay and time-window
queries without freezing any browser shape into the database. The collector is
the one writer and the receive loop is where that writing happens, so this
module owns the file, the schema and the retention pass and answers ordinary
Python methods rather than exposing SQL around the program.
"""

import io
import json
import os
import sqlite3
import sys
import time

SCHEMA_VERSION = 1
DB_NAME = "flows.sqlite3"
DEFAULT_PRUNE_CADENCE = 12 * 60 * 60

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
    if seconds <= 0:
        raise ValueError("expected more than 0 seconds, not %r" % (text,))
    return seconds


class FlowStore:
    """The SQLite file a collector appends shown flows to."""

    def __init__(self, path, retention_days, prune_every=DEFAULT_PRUNE_CADENCE):
        self.path = path
        self.retention_days = retention_days
        self.prune_every = prune_every
        self._next_id = 0
        self._next_prune = 0.0
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._configure()
        self._create_schema()
        self._check_schema()
        self._next_id = self._read_next_id()
        self.prune()

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
        """)
        if self._db.execute("PRAGMA user_version").fetchone()[0] == 0:
            self._db.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
        self._db.commit()

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
        self._db.commit()
        return self._next_id

    def prune(self, now=None):
        """Drop rows older than the retention window."""
        now = time.time() if now is None else now
        floor = now - (self.retention_days * 24 * 60 * 60)
        self._db.execute("DELETE FROM flows WHERE received < ?", (floor,))
        self._db.commit()
        self._next_prune = now + self.prune_every

    def prune_due(self, now=None):
        """Whether the scheduled prune time has arrived."""
        now = time.time() if now is None else now
        return now >= self._next_prune

    def count(self):
        """How many rows are in the durable history."""
        return int(self._db.execute("SELECT COUNT(*) FROM flows").fetchone()[0])

    def latest(self):
        """The newest row, for tests and startup notes."""
        row = self._db.execute(
            """
            SELECT ingest_id, received, exporter, src_addr, dst_addr, record_json
            FROM flows ORDER BY ingest_id DESC LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None


class DisabledStore:
    """A no-op history sink, kept so the hot path asks one question."""

    path = None
    retention_days = None
    prune_every = None

    def write(self, record):
        return None

    def prune(self, now=None):
        return None

    def prune_due(self, now=None):
        return False

    def count(self):
        return 0

    def latest(self):
        return None

    def close(self):
        return None


def describe(path, retention_days, prune_every):
    """One startup line naming the durable history file and its bound."""
    buffer = io.StringIO()
    print("recording shown flows in %s, keeping %d day%s, pruning every %s"
          % (path, retention_days, "" if retention_days == 1 else "s",
             _seconds(prune_every)),
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
