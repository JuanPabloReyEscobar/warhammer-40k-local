"""CSV exports published by Wahapedia and not exposed by the Matchmaker API."""
from __future__ import annotations

import csv
import io
import sqlite3
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.config import DB_PATH

STRATAGEMS_URL = "https://wahapedia.ru/wh40k11ed/Stratagems.csv"


class ExportError(RuntimeError):
    pass


def stratagems() -> list[dict[str, str]]:
    with sqlite3.connect(DB_PATH) as conn:
        cached = conn.execute("SELECT etag, body FROM api_cache WHERE url=?", (STRATAGEMS_URL,)).fetchone()
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Warhammer40kLocal/0.1)", "Accept": "text/csv"}
    if cached and cached[0]:
        headers["If-None-Match"] = cached[0]
    try:
        with urlopen(Request(STRATAGEMS_URL, headers=headers), timeout=25) as response:
            body = response.read().decode("utf-8-sig")
            etag = response.headers.get("ETag")
    except HTTPError as exc:
        if exc.code == 304 and cached:
            body = cached[1]
        elif cached:
            body = cached[1]
        else:
            raise ExportError(f"Wahapedia respondió HTTP {exc.code} al descargar las estratagemas.") from exc
    except (URLError, TimeoutError) as exc:
        if cached:
            body = cached[1]
        else:
            raise ExportError("No se pudo descargar Stratagems.csv y aún no existe una copia local.") from exc
    else:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT INTO api_cache(url,etag,body) VALUES (?,?,?) ON CONFLICT(url) DO UPDATE SET etag=excluded.etag, body=excluded.body, cached_at=CURRENT_TIMESTAMP", (STRATAGEMS_URL, etag, body))
    reader = csv.DictReader(io.StringIO(body), delimiter="|")
    return [{str(k or "").strip(): str(v or "").strip() for k, v in row.items() if k is not None}
            for row in reader]
