"""Stash backend. Routes mount at /api/plugins/stash/ (reached through ctx.rest).

Blocking work (page fetches for titles) runs in threads; enrichment is fired and forgotten so a
paste returns immediately.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
router = APIRouter()


def _store():
    spec = importlib.util.spec_from_file_location("stash_store_api", _PLUGIN_DIR / "store.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _fail(message: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=code)


@router.get("/health")
async def health():
    st = _store()
    return {"ok": True, **await asyncio.to_thread(st.stats)}


@router.get("/items")
async def items():
    st = _store()
    return {"ok": True, "items": await asyncio.to_thread(st.load)}


@router.post("/add")
async def add(body: dict):
    text = (body or {}).get("text") or ""
    if not str(text).strip():
        return _fail("nothing to save")
    st = _store()
    result = await asyncio.to_thread(st.add, str(text))
    return {"ok": True, **result}


@router.post("/upload")
async def upload(body: dict):
    body = body or {}
    data_url = body.get("dataUrl") or ""
    name = str(body.get("name") or "image.png")
    note = str(body.get("note") or "")
    if not data_url:
        return _fail("no image data")
    st = _store()
    try:
        result = await asyncio.to_thread(st.add_image, name, data_url, note)
    except ValueError as exc:
        return _fail(str(exc))
    except Exception as exc:  # pragma: no cover - decode/copy failure
        return _fail(f"could not save image: {exc}", 500)
    return {"ok": True, **result}


@router.post("/status")
async def status(body: dict):
    body = body or {}
    item_id = str(body.get("id") or "")
    new_status = str(body.get("status") or "")
    if not item_id:
        return _fail("missing id")
    st = _store()
    try:
        result = await asyncio.to_thread(st.set_status, item_id, new_status)
    except ValueError as exc:
        return _fail(str(exc))
    except KeyError:
        return _fail("no such item", 404)
    return {"ok": True, **result}


@router.post("/note")
async def note(body: dict):
    body = body or {}
    item_id = str(body.get("id") or "")
    if not item_id:
        return _fail("missing id")
    st = _store()
    try:
        result = await asyncio.to_thread(st.set_note, item_id, str(body.get("note") or ""))
    except KeyError:
        return _fail("no such item", 404)
    return {"ok": True, **result}


@router.post("/delete")
async def delete(body: dict):
    item_id = str((body or {}).get("id") or "")
    if not item_id:
        return _fail("missing id")
    st = _store()
    result = await asyncio.to_thread(st.delete, item_id)
    return {"ok": True, **result}


@router.get("/media/{name}")
async def media(name: str):
    st = _store()
    safe = Path(name).name
    path = st.MEDIA_DIR / safe
    if not path.is_file():
        return _fail("no such file", 404)
    return FileResponse(path)
