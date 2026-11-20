import json
import sqlite3
from pathlib import Path


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS imported_files (
            id INTEGER PRIMARY KEY,
            file_name TEXT NOT NULL,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            row_count INTEGER NOT NULL DEFAULT 0,
            UNIQUE(file_name)
        );
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY,
            file_id INTEGER NOT NULL REFERENCES imported_files(id) ON DELETE CASCADE,
            record_key TEXT,
            searchable_text TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS api_cache (
            url TEXT PRIMARY KEY,
            etag TEXT,
            body TEXT NOT NULL,
            cached_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS army_lists (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            faction_id TEXT NOT NULL,
            faction_name TEXT NOT NULL,
            points_limit INTEGER NOT NULL DEFAULT 2000,
            chapter TEXT NOT NULL DEFAULT '',
            detachment TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS army_list_units (
            id INTEGER PRIMARY KEY,
            list_id INTEGER NOT NULL REFERENCES army_lists(id) ON DELETE CASCADE,
            unit_id TEXT NOT NULL,
            unit_name TEXT NOT NULL,
            points INTEGER NOT NULL DEFAULT 0,
            quantity INTEGER NOT NULL DEFAULT 1,
            size_label TEXT NOT NULL DEFAULT '',
            wargear_json TEXT NOT NULL DEFAULT '[]',
            payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS army_list_detachments (
            id INTEGER PRIMARY KEY,
            list_id INTEGER NOT NULL REFERENCES army_lists(id) ON DELETE CASCADE,
            detachment_id TEXT NOT NULL,
            detachment_name TEXT NOT NULL,
            dp_cost INTEGER NOT NULL,
            payload TEXT NOT NULL,
            UNIQUE(list_id, detachment_id)
        );
        CREATE INDEX IF NOT EXISTS idx_records_file ON records(file_id);
        CREATE INDEX IF NOT EXISTS idx_records_key ON records(record_key);
        CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(searchable_text, content='records', content_rowid='id');
        CREATE TRIGGER IF NOT EXISTS records_ai AFTER INSERT ON records BEGIN
          INSERT INTO records_fts(rowid, searchable_text) VALUES (new.id, new.searchable_text);
        END;
        CREATE TRIGGER IF NOT EXISTS records_ad AFTER DELETE ON records BEGIN
          INSERT INTO records_fts(records_fts, rowid, searchable_text) VALUES('delete', old.id, old.searchable_text);
        END;
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(army_list_units)")}
        list_columns = {row[1] for row in conn.execute("PRAGMA table_info(army_lists)")}
        if "chapter" not in list_columns:
            conn.execute("ALTER TABLE army_lists ADD COLUMN chapter TEXT NOT NULL DEFAULT ''")
        if "size_label" not in columns:
            conn.execute("ALTER TABLE army_list_units ADD COLUMN size_label TEXT NOT NULL DEFAULT ''")
        if "wargear_json" not in columns:
            conn.execute("ALTER TABLE army_list_units ADD COLUMN wargear_json TEXT NOT NULL DEFAULT '[]'")
