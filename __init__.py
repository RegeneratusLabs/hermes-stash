"""inbox — a capture bin in the sidebar, readable by the agent later.

Wires: three tools (list / add / update) and a native desktop page with its own router.
The store is a plain JSON file on purpose: it survives everything, and the agent can read it even
if the tools are off.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from pathlib import Path

logger = logging.getLogger(__name__)
_HERE = Path(__file__).resolve().parent

import importlib.util

def _store():
    spec = importlib.util.spec_from_file_location("inbox_store_tools", _HERE / "store.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _err(exc: Exception, hint: str) -> str:
    return json.dumps({"error": str(exc), "hint": hint})


def _row(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "kind": item.get("kind"),
        "title": item.get("title"),
        "url": item.get("url"),
        "note": item.get("note"),
        "status": item.get("status"),
        "added": item.get("added"),
        "age_hours": round((time.time() - float(item.get("added") or time.time())) / 3600, 1),
    }


def _handle_list(params: Optional[dict] = None, **kwargs: Any) -> str:
    params = params or {}
    status = (params.get("status") or "open").strip().lower()
    limit = int(params.get("limit") or 30)
    try:
        items = _store().load()
    except Exception as exc:  # pragma: no cover - unreadable store
        return _err(exc, "the inbox store could not be read; check ~/.hermes/inbox/items.json")
    if status == "open":
        rows = [i for i in items if i.get("status") in ("new", "reading")]
    elif status == "all":
        rows = items
    else:
        rows = [i for i in items if i.get("status") == status]
    rows.sort(key=lambda i: i.get("added") or 0, reverse=True)
    return json.dumps({"count": len(rows), "items": [_row(i) for i in rows[:limit]]}, ensure_ascii=False)


def _handle_add(params: Optional[dict] = None, **kwargs: Any) -> str:
    params = params or {}
    text = (params.get("text") or "").strip()
    if not text:
        return json.dumps({"error": "nothing to save", "hint": "pass the link or note in `text`"})
    try:
        result = _store().add(text)
    except Exception as exc:
        return _err(exc, "the inbox store could not be written; check ~/.hermes/inbox/items.json")
    return json.dumps({"added": [_row(i) for i in result.get("added", [])]}, ensure_ascii=False)


def _handle_update(params: Optional[dict] = None, **kwargs: Any) -> str:
    params = params or {}
    item_id = (params.get("id") or "").strip()
    status = (params.get("status") or "").strip().lower()
    if not item_id or not status:
        return json.dumps({"error": "need both id and status", "hint": "status is new | reading | done | dropped"})
    try:
        result = _store().set_status(item_id, status)
    except KeyError:
        return json.dumps({"error": f"no item {item_id}", "hint": "call stash_list for real ids"})
    except Exception as exc:
        return _err(exc, "status must be new | reading | done | dropped")
    return json.dumps({"item": _row(result.get("item", {}))}, ensure_ascii=False)


SCHEMAS = [
    {
        "name": "stash_list",
        "description": (
            "List what the user saved to their Stash for later — links, videos, images and notes that "
            "never entered a chat. Defaults to open items (status new/reading) newest first. Use this "
            "when asked to triage or pick up saved things."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["open", "new", "reading", "done", "dropped", "all"],
                           "description": "Which items. Default open (new + reading)."},
                "limit": {"type": "integer", "description": "Max rows (default 30)."},
            },
            "required": [],
        },
    },
    {
        "name": "stash_add",
        "description": (
            "Save a link, YouTube url or plain note to the user's Stash so it is waiting for them later "
            "instead of living in this chat. One item per line; titles and thumbnails fill in on their own."
        ),
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "The url(s) or note to save."}},
            "required": ["text"],
        },
    },
    {
        "name": "stash_update",
        "description": "Move a Stash item along: new, reading, done or dropped. Use it to close items out after picking them up.",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Item id from stash_list."},
                "status": {"type": "string", "enum": ["new", "reading", "done", "dropped"]},
            },
            "required": ["id", "status"],
        },
    },
]

HANDLERS = {
    "stash_list": _handle_list,
    "stash_add": _handle_add,
    "stash_update": _handle_update,
}


def register(ctx) -> None:
    for schema in SCHEMAS:
        ctx.register_tool(
            name=schema["name"],
            toolset="stash",
            schema=schema,
            handler=HANDLERS[schema["name"]],
            description=schema["description"].split(".")[0],
            emoji="\U0001F4E5",
        )
    logger.info("inbox: registered %d tools", len(SCHEMAS))
