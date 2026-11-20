"""Small cached client for the public Warhammer Matchmaker Wahapedia API."""
from __future__ import annotations

import json
import sqlite3
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.config import DB_PATH

BASE_URL = "https://warhammer-matchmaker.com/api/wahapedia/wh40k11ed"


class ApiError(RuntimeError):
    pass


def _get_json(path: str):
    url = BASE_URL + path
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT etag, body FROM api_cache WHERE url=?", (url,)).fetchone()
    headers = {"Accept": "application/json", "User-Agent": "Warhammer40kLocal/0.1"}
    if row and row[0]:
        headers["If-None-Match"] = row[0]
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=25) as response:
            body = response.read().decode("utf-8")
            etag = response.headers.get("ETag")
    except HTTPError as exc:
        if exc.code == 304 and row:
            return json.loads(row[1])
        if exc.code == 429:
            raise ApiError("La API recibió muchas solicitudes; espera un minuto y vuelve a intentar.") from exc
        raise ApiError(f"La API respondió HTTP {exc.code}.") from exc
    except (URLError, TimeoutError) as exc:
        if row:
            return json.loads(row[1])
        raise ApiError("No se pudo conectar a la API y aún no hay una copia local guardada.") from exc
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO api_cache(url,etag,body) VALUES (?,?,?) ON CONFLICT(url) DO UPDATE SET etag=excluded.etag, body=excluded.body, cached_at=CURRENT_TIMESTAMP", (url, etag, body))
    return json.loads(body)


def catalogues():
    return _get_json("/catalogues")


def catalogue(faction_id: str):
    return _get_json("/catalogues/" + faction_id)


def game_system():
    return _get_json("/gamesystem")

