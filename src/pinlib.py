#!/usr/bin/env python3
"""
pinlib — shared Pinterest clipping logic (importable module).

Used by the Wiki Inspi receiver and enricher to get the original-size image behind a pin.
Personal-reference use only: respect the authors' copyright.

Only https Pinterest pages and pinimg.com images are ever fetched.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

try:
    import yaml
except ImportError:  # the CLI reports this and exits 2; library users get None
    yaml = None

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
PIN_HOST_RE = re.compile(r"(^|\.)pinterest\.(com|fr|co\.uk|jp|de|es|it|nz|ca)$|^pin\.it$", re.I)


class MetaParser(HTMLParser):
    """Extract og:*, pinterestapp:*, <title>, and embedded JSON-LD blobs."""

    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self.og: dict[str, str] = {}
        self.pinapp: dict[str, str] = {}
        self.ld_blobs: list[str] = []
        self._in_title = False
        self._ld_capture = False
        self._ld_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            prop = (d.get("property") or d.get("name") or "").lower()
            content = d.get("content") or ""
            if prop.startswith("og:") and content:
                self.og.setdefault(prop[3:], content)
            elif prop.startswith("pinterestapp:") and content:
                self.pinapp.setdefault(prop[len("pinterestapp:"):], content)
        elif tag == "script" and (d.get("type") or "").lower() == "application/ld+json":
            self._ld_capture = True
            self._ld_buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._ld_capture:
            self._ld_capture = False
            blob = "".join(self._ld_buf).strip()
            if blob:
                self.ld_blobs.append(blob)

    def handle_data(self, data: str) -> None:
        if self._in_title and not self.title:
            self.title = data.strip()
        if self._ld_capture:
            self._ld_buf.append(data)


def slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s.lower()).strip()
    s = re.sub(r"[-\s]+", "-", s)
    return s[:80] or "untitled"


def normalize_board_key(name: str) -> str:
    return re.sub(r"[-\s]+", "-", re.sub(r"[^\w\s-]", "", name.lower()).strip())


def is_pinterest_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return bool(PIN_HOST_RE.search(host))


def fetch(url: str) -> tuple[bytes, str]:
    """Fetch URL, follow redirects, return (body, final_url)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(), r.url


def upgrade_to_original(img_url: str) -> str:
    """Pinterest CDN: replace size segment (/736x/, /564x474/, /originals/) with /originals/."""
    return re.sub(r"/\d+x(?:\d+)?/", "/originals/", img_url)


def extract_pin_data(html: bytes, final_url: str) -> dict:
    """Return dict with: image_url, title, description, board_name, board_url, source_link, pin_id."""
    p = MetaParser()
    try:
        p.feed(html.decode("utf-8", errors="replace"))
    except Exception:
        pass

    data: dict = {
        "image_url": p.og.get("image", ""),
        "title": p.og.get("title") or p.title or "",
        "description": p.og.get("description", ""),
        "board_url": p.pinapp.get("pinboard", ""),
        "source_link": p.pinapp.get("source", ""),
        "pin_id": p.pinapp.get("pinid", ""),
        "board_name": "",
    }

    if not data["pin_id"]:
        m = re.search(r"/pin/(\d+)", final_url)
        if m:
            data["pin_id"] = m.group(1)

    # Board name: try JSON-LD, then title-tail heuristic, then board URL slug
    for blob in p.ld_blobs:
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            continue
        candidates = obj if isinstance(obj, list) else [obj]
        for c in candidates:
            if isinstance(c, dict):
                board = c.get("isPartOf") or c.get("about")
                if isinstance(board, dict) and board.get("name"):
                    data["board_name"] = board["name"]
                    if not data["board_url"] and board.get("url"):
                        data["board_url"] = board["url"]
                    break
        if data["board_name"]:
            break

    if not data["board_name"] and p.title and "|" in p.title:
        tail = p.title.rsplit("|", 1)[-1].strip()
        tail = re.sub(r"\s*Pinterest\s*$", "", tail, flags=re.I).strip()
        if tail and tail.lower() != "pinterest":
            data["board_name"] = tail

    if not data["board_name"] and data["board_url"]:
        seg = [s for s in urllib.parse.urlparse(data["board_url"]).path.split("/") if s]
        if seg:
            data["board_name"] = seg[-1].replace("-", " ").title()

    return data


PIN_IMAGE_HOST_RE = re.compile(r"(^|\.)pinimg\.com$", re.I)


def fetch_pin_checked(url: str) -> tuple[bytes, str]:
    """fetch() for untrusted input (receiver): https + Pinterest host, before AND after redirects."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not is_pinterest_url(url):
        raise ValueError("not an https Pinterest URL")
    body, final = fetch(url)
    fp = urllib.parse.urlparse(final)
    if fp.scheme != "https" or not is_pinterest_url(final):
        raise ValueError("redirected outside Pinterest")
    return body, final


def fetch_original_bytes(image_url: str) -> bytes:
    """HD original bytes for a pin image: try /originals/ first, fall back to the og:image URL.

    Only pinimg.com hosts are fetched (the receiver must never be steered at local services).
    """
    for candidate in (upgrade_to_original(image_url), image_url):
        host = urllib.parse.urlparse(candidate).netloc.lower()
        if urllib.parse.urlparse(candidate).scheme != "https" or not PIN_IMAGE_HOST_RE.search(host):
            continue
        try:
            data, _ = fetch(candidate)
        except Exception:
            continue
        if data:
            return data
    raise ValueError("pin image download failed")
