"""Official Munitorum Field Manual lookup for detachment point costs."""
from __future__ import annotations

import re
import sqlite3
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.config import DB_PATH

MFM_HOME = "https://mfm.warhammer-community.com/en"


class ManualError(RuntimeError):
    pass


def _fetch(url: str) -> str:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT etag, body FROM api_cache WHERE url=?", (url,)).fetchone()
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Warhammer40kLocal/0.1)", "Accept": "text/html"}
    if row and row[0]:
        headers["If-None-Match"] = row[0]
    try:
        with urlopen(Request(url, headers=headers), timeout=25) as response:
            body = response.read().decode("utf-8")
            etag = response.headers.get("ETag")
    except HTTPError as exc:
        if exc.code == 304 and row:
            return row[1]
        if row:
            return row[1]
        raise ManualError(f"El Munitorum Field Manual respondió HTTP {exc.code}.") from exc
    except (URLError, TimeoutError) as exc:
        if row:
            return row[1]
        raise ManualError("No se pudo consultar el Munitorum Field Manual y aún no hay datos en caché.") from exc
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO api_cache(url,etag,body) VALUES (?,?,?) ON CONFLICT(url) DO UPDATE SET etag=excluded.etag, body=excluded.body, cached_at=CURRENT_TIMESTAMP", (url, etag, body))
    return body


def _normal(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


class _AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.href = None
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.text = []

    def handle_data(self, data):
        if self.href:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href:
            self.links.append((self.href, " ".join(self.text).strip()))
            self.href = None


class _DetachmentParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.kind = None
        self.buffer = []
        self.title = None
        self.points = {}

    def handle_starttag(self, tag, attrs):
        if tag != "span":
            return
        classes = dict(attrs).get("class", "").split()
        if "text-xl" in classes and ({"break-all", "keep-all"} & set(classes)):
            self.kind, self.buffer = "title", []
        elif "self-end" in classes and "text-sm" in classes:
            self.kind, self.buffer = "points", []

    def handle_data(self, data):
        if self.kind:
            self.buffer.append(data)

    def handle_endtag(self, tag):
        if tag != "span" or not self.kind:
            return
        value = " ".join(" ".join(self.buffer).split())
        if self.kind == "title":
            self.title = value
        else:
            match = re.search(r"(\d+)\s*DP", value, re.I)
            if self.title and match:
                self.points[_normal(self.title)] = {
                    "name": self.title.title(),
                    "dp": int(match.group(1)),
                }
                self.title = None
        self.kind, self.buffer = None, []


def detachment_costs(faction_name: str) -> dict[str, dict]:
    home = _AnchorParser()
    home.feed(_fetch(MFM_HOME))
    target = _normal(faction_name)
    aliases = {"spacemarines": "spacemarines", "tauempire": "tauempire"}
    target = aliases.get(target, target)
    path = next((href for href, label in home.links
                 if href and href.startswith("/en/") and _normal(label) == target), None)
    if not path:
        raise ManualError(f"No encontré la página del Munitorum para {faction_name}.")
    parser = _DetachmentParser()
    parser.feed(_fetch("https://mfm.warhammer-community.com" + path))
    if not parser.points:
        raise ManualError(f"No pude leer los costos DP de {faction_name} en el Munitorum Field Manual.")
    return parser.points
