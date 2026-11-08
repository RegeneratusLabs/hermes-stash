"""Inbox — durable capture store.

One JSON file, one media dir, no database. The file is deliberately plain so the agent can read
it directly (read_file) as well as through the tools, and so a human can fix it with a text editor.

Item shape:
  {id, kind, url, title, note, host, thumb, added, status, bytes?}
  kind: link | video | image | text
  status: new | reading | done | dropped
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

HOME = Path(os.environ.get("HERMES_INBOX_DIR") or (Path.home() / ".hermes" / "stash"))
ITEMS_PATH = HOME / "items.json"
MEDIA_DIR = HOME / "media"

STATUSES = ("new", "reading", "done", "dropped")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp", ".svg")
VIDEO_HOSTS = ("youtube.com", "youtu.be", "m.youtube.com", "music.youtube.com", "vimeo.com")
MAX_UPLOAD = 8 * 1024 * 1024
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"

_LOCK = threading.Lock()
_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_OG_RE = re.compile(
    r"""<meta[^>]+property=["']og:(title|image|description)["'][^>]+content=["']([^"']*)["']""",
    re.IGNORECASE,
)
_OG_RE_ALT = re.compile(
    r"""<meta[^>]+content=["']([^"']*)["'][^>]+property=["']og:(title|image|description)["']""",
    re.IGNORECASE,
)


# --- storage -------------------------------------------------------------------------------

def _ensure() -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)


def load() -> list[dict]:
    _ensure()
    try:
        raw = json.loads(ITEMS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = raw.get("items") if isinstance(raw, dict) else raw
    return [i for i in (items or []) if isinstance(i, dict)]


def save(items: list[dict]) -> None:
    _ensure()
    tmp = ITEMS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"version": 1, "items": items}, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, ITEMS_PATH)


def _mutate(fn):
    with _LOCK:
        items = load()
        result = fn(items)
        save(items)
        return result


# --- classification ------------------------------------------------------------------------

def _host(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.replace("www.", "")
    except ValueError:
        return ""


def youtube_id(url: str) -> str:
    try:
        p = urllib.parse.urlparse(url)
    except ValueError:
        return ""
    host = p.netloc.replace("www.", "").lower()
    if host == "youtu.be":
        return p.path.lstrip("/").split("/")[0]
    if host.endswith("youtube.com"):
        if p.path.startswith("/shorts/") or p.path.startswith("/embed/") or p.path.startswith("/live/"):
            return p.path.split("/")[2] if len(p.path.split("/")) > 2 else ""
        q = urllib.parse.parse_qs(p.query)
        return (q.get("v") or [""])[0]
    return ""


def classify(text: str) -> tuple[str, str]:
    """(kind, url) for one pasted line. kind is 'text' and url '' when there is no link."""
    found = _URL_RE.search(text or "")
    if not found:
        return "text", ""
    url = found.group(0).rstrip(".,;:!?)]}")
    if youtube_id(url):
        return "video", url
    path = urllib.parse.urlparse(url).path.lower()
    if path.endswith(IMAGE_EXT):
        return "image", url
    if _host(url) in VIDEO_HOSTS:
        return "video", url
    return "link", url


def parse_paste(text: str) -> list[tuple[str, str, str]]:
    """Split a paste into (kind, url, note) entries. One item per non-empty line."""
    out: list[tuple[str, str, str]] = []
    if not (text or "").strip():
        return out
    lines = [ln.strip() for ln in re.split(r"\r?\n", text)]
    for line in lines:
        if not line or line in {"-", "---", "•"}:
            continue
        kind, url = classify(line)
        note = line
        if url:
            # keep what the human actually added around the link, minus the link itself
            note = line.replace(url, " ").strip(" -—–|\t")
        out.append((kind, url, note[:400]))
    return out


# --- enrichment ----------------------------------------------------------------------------

def _public_target(url: str) -> bool:
    """True only for http(s) whose host resolves entirely to public addresses.

    A saved url is attacker-influenced input (it came off a clipboard), so the
    title fetch must not be able to reach loopback, link-local metadata services,
    or anything else inside the network the agent runs in."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return False
    return True


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _public_target(newurl):
            raise urllib.error.URLError("blocked redirect to a non-public address")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_GuardedRedirect())


def _fetch(url: str, timeout: float = 4.0, cap: int = 400_000) -> str:
    if not _public_target(url):
        return ""
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "text/html,*/*"})
    with _OPENER.open(req, timeout=timeout) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "html" not in ctype and "xml" not in ctype and "text" not in ctype and "json" not in ctype:
            return ""
        return resp.read(cap).decode("utf-8", "replace")


def _clean(value: str) -> str:
    import html as _html

    return re.sub(r"\s+", " ", _html.unescape(value or "")).strip()


def enrich(url: str) -> dict:
    """Title / thumb / description for a URL. Never raises: an unreachable link keeps its host."""
    info: dict = {"title": "", "thumb": "", "description": ""}
    if not url:
        return info
    yt = youtube_id(url)
    if yt:
        info["thumb"] = f"https://i.ytimg.com/vi/{yt}/mqdefault.jpg"
        try:
            oembed = f"https://www.youtube.com/oembed?url={urllib.parse.quote(url, safe='')}&format=json"
            data = json.loads(_fetch(oembed, timeout=4.0, cap=200_000) or "{}")
            info["title"] = _clean(data.get("title", ""))
            info["description"] = _clean(data.get("author_name", ""))
        except Exception:
            pass
        if info["title"]:
            return info
    if classify(url)[0] == "image":
        info["thumb"] = url
    try:
        html = _fetch(url)
    except Exception:
        return info
    if not html:
        return info
    title = ""
    try:
        m = _TITLE_RE.search(html)
        if m:
            title = _clean(m.group(1))
        for regex in (_OG_RE, _OG_RE_ALT):
            for a, b in regex.findall(html):
                key, value = (a, b) if a in ("title", "image", "description") else (b, a)
                if key == "title" and not title:
                    title = _clean(value)
                if key == "image" and not info["thumb"]:
                    info["thumb"] = _clean(value)
                if key == "description" and not info["description"]:
                    info["description"] = _clean(value)
    except Exception:
        pass
    info["title"] = title
    return info


def _enrich_and_store(ids: list[str]) -> None:
    """Background pass: fill in titles/thumbs for items just added. Best-effort, never fatal."""
    for item_id in ids:
        with _LOCK:
            items = load()
            item = next((i for i in items if i.get("id") == item_id), None)
            if not item or not item.get("url"):
                continue
            url = item["url"]
        try:
            info = enrich(url)
        except Exception:
            continue
        with _LOCK:
            items = load()
            item = next((i for i in items if i.get("id") == item_id), None)
            if not item:
                continue
            if info.get("title") and not item.get("title_locked"):
                item["title"] = info["title"][:200]
            if info.get("thumb"):
                item["thumb"] = info["thumb"]
            if info.get("description"):
                item["description"] = info["description"][:400]
            save(items)
        time.sleep(0.2)


# --- mutations -----------------------------------------------------------------------------

def _provisional_title(kind: str, url: str, note: str) -> str:
    if kind == "text":
        return note[:120] or "note"
    host = _host(url) or url
    tail = urllib.parse.urlparse(url).path.strip("/")
    if kind == "video":
        return f"Video · {host}"
    if kind == "image":
        return tail.rsplit("/", 1)[-1] or f"Image · {host}"
    return f"{host}/{tail}"[:120] if tail else host


def add(text: str) -> dict:
    entries = parse_paste(text)
    if not entries:
        return {"added": [], "items": load()}
    now = time.time()
    added: list[dict] = []
    for kind, url, note in entries[:40]:
        # A plain-text capture has no title of its own; don't print the same line twice.
        shown_note = "" if (kind == "text" and len(note) <= 120) else note
        item = {
            "id": uuid.uuid4().hex[:12],
            "kind": kind,
            "url": url,
            "title": _provisional_title(kind, url, note),
            "note": shown_note,
            "host": _host(url),
            "thumb": url if kind == "image" else "",
            "added": now,
            "status": "new",
        }
        added.append(item)
    _mutate(lambda items: items.extend(added))
    needing = [i["id"] for i in added if i["url"]]
    if needing:
        threading.Thread(target=_enrich_and_store, args=(needing,), daemon=True).start()
    return {"added": added, "items": load()}


def add_image(name: str, data_url: str, note: str = "") -> dict:
    payload = data_url.split(",", 1)[-1] if "," in data_url else data_url
    raw = base64.b64decode(payload, validate=False)
    if len(raw) > MAX_UPLOAD:
        raise ValueError("image too large (max 8 MB)")
    ext = Path(name or "").suffix.lower()
    if ext not in IMAGE_EXT:
        ext = ".png"
    digest = hashlib.sha256(raw).hexdigest()[:10]
    filename = f"{digest}{ext}"
    _ensure()
    (MEDIA_DIR / filename).write_bytes(raw)
    item = {
        "id": uuid.uuid4().hex[:12],
        "kind": "image",
        "url": "",
        "title": note[:120] or (Path(name).stem or "image"),
        "note": note[:400],
        "host": "local",
        "thumb": f"/api/plugins/stash/media/{filename}",
        "media": filename,
        "bytes": len(raw),
        "added": time.time(),
        "status": "new",
    }
    _mutate(lambda items: items.append(item))
    return {"added": [item], "items": load()}


def set_status(item_id: str, status: str) -> dict:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")

    def _do(items):
        for item in items:
            if item.get("id") == item_id:
                item["status"] = status
                item["touched"] = time.time()
                return item
        return None

    updated = _mutate(_do)
    if updated is None:
        raise KeyError(item_id)
    return {"item": updated, "items": load()}


def set_note(item_id: str, note: str) -> dict:
    def _do(items):
        for item in items:
            if item.get("id") == item_id:
                item["note"] = (note or "")[:400]
                item["touched"] = time.time()
                return item
        return None

    updated = _mutate(_do)
    if updated is None:
        raise KeyError(item_id)
    return {"item": updated, "items": load()}


def delete(item_id: str) -> dict:
    def _do(items):
        kept = [i for i in items if i.get("id") != item_id]
        removed = len(items) - len(kept)
        items[:] = kept
        return removed

    removed = _mutate(_do)
    return {"removed": removed, "items": load()}


def stats() -> dict:
    items = load()
    counts = {s: 0 for s in STATUSES}
    for item in items:
        counts[item.get("status", "new")] = counts.get(item.get("status", "new"), 0) + 1
    return {"total": len(items), "counts": counts}
