import sqlite3
from pathlib import Path

from flask import current_app, g  # type: ignore[import-not-found]


def get_db():
    if "db" not in g:
        db_path = current_app.config["DATABASE"]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(db_path)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _add_missing_columns(db, table, columns):
    """Apply additive SQLite migrations without deleting existing records."""
    existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def init_db():
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'student' CHECK(role IN ('student','admin')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            venue TEXT NOT NULL,
            event_date TEXT NOT NULL,
            event_time TEXT NOT NULL,
            capacity INTEGER NOT NULL CHECK(capacity > 0),
            price REAL NOT NULL DEFAULT 0 CHECK(price >= 0),
            cover TEXT NOT NULL DEFAULT 'violet',
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS registrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            ticket_code TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'confirmed' CHECK(status IN ('confirmed','waitlisted','cancelled')),
            registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            checked_in_at TEXT,
            UNIQUE(user_id, event_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS coordinators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coordinator_code TEXT NOT NULL UNIQUE COLLATE NOCASE,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS event_coordinators (
            event_id INTEGER NOT NULL,
            coordinator_id INTEGER NOT NULL,
            assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (event_id, coordinator_id),
            FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
            FOREIGN KEY (coordinator_id) REFERENCES coordinators(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS coordinator_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            coordinator_id INTEGER NOT NULL,
            message TEXT NOT NULL CHECK(length(trim(message)) BETWEEN 1 AND 1000),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
            FOREIGN KEY (coordinator_id) REFERENCES coordinators(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_events_date ON events(event_date);
        CREATE INDEX IF NOT EXISTS idx_registrations_event ON registrations(event_id);
        CREATE INDEX IF NOT EXISTS idx_event_coordinators_coordinator ON event_coordinators(coordinator_id);
        CREATE INDEX IF NOT EXISTS idx_coordinator_messages_event ON coordinator_messages(event_id, created_at);
        """
    )

    # These are additive migrations for databases created by earlier NexVents versions.
    _add_missing_columns(db, "events", {
        "accommodation_enabled": "INTEGER NOT NULL DEFAULT 0",
        "stay_price": "REAL NOT NULL DEFAULT 0",
        "food_price": "REAL NOT NULL DEFAULT 0",
        "accommodation_capacity": "INTEGER NOT NULL DEFAULT 0",
        "medical_location": "TEXT NOT NULL DEFAULT ''",
        "medical_contact": "TEXT NOT NULL DEFAULT ''",
        "security_contact": "TEXT NOT NULL DEFAULT ''",
        "emergency_contact": "TEXT NOT NULL DEFAULT ''",
    })
    _add_missing_columns(db, "registrations", {
        "stay_selected": "INTEGER NOT NULL DEFAULT 0",
        "food_selected": "INTEGER NOT NULL DEFAULT 0",
        "addon_total": "REAL NOT NULL DEFAULT 0",
    })
    db.commit()
