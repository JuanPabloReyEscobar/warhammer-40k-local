import csv
import io
import json
import re
import sqlite3
from pathlib import Path


def _key(row: dict) -> str | None:
    for name, value in row.items():
        if name and name.strip().lower() in {"id", "datasheetid", "abilityid", "stratagemid", "uuid"} and value:
            return value.strip()
    return None


def import_csv(db_path: Path, filename: str, data: bytes) -> int:
    text = data.decode("utf-8-sig")
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        existing = conn.execute("SELECT id FROM imported_files WHERE file_name=?", (filename,)).fetchone()
        if existing:
            conn.execute("DELETE FROM imported_files WHERE id=?", (existing[0],))
        cur = conn.execute("INSERT INTO imported_files(file_name,row_count) VALUES (?,?)", (filename, len(rows)))
        file_id = cur.lastrowid
        for row in rows:
            row = {str(k or "").strip(): str(v or "").strip() for k, v in row.items()}
            payload = json.dumps(row, ensure_ascii=False)
            searchable = " ".join(v for v in row.values() if v)
            conn.execute("INSERT INTO records(file_id,record_key,searchable_text,payload) VALUES (?,?,?,?)",
                         (file_id, _key(row), searchable, payload))
    return len(rows)


def search(db_path: Path, query: str, limit: int = 100) -> list[dict]:
    query = query.strip()
    if not query:
        return []
    # FTS is fast for ordinary terms; LIKE fallback keeps punctuation/partial terms usable.
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        try:
            terms = " AND ".join('"' + t.replace('"', '""') + '"*' for t in re.findall(r"[\w'-]+", query))
            if terms:
                rows = conn.execute("SELECT r.payload,i.file_name FROM records_fts f JOIN records r ON r.id=f.rowid JOIN imported_files i ON i.id=r.file_id WHERE records_fts MATCH ? LIMIT ?", (terms, limit)).fetchall()
                if rows:
                    return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            pass
        rows = conn.execute("SELECT r.payload,i.file_name FROM records r JOIN imported_files i ON i.id=r.file_id WHERE r.searchable_text LIKE ? LIMIT ?", (f"%{query}%", limit)).fetchall()
        return [dict(r) for r in rows]
