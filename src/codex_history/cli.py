#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import subprocess
import threading
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from typing import Iterable


CODEX_DIR = Path.home() / ".codex"
SESSION_INDEX = CODEX_DIR / "session_index.jsonl"
HISTORY_JSONL = CODEX_DIR / "history.jsonl"
SESSIONS_DIR = CODEX_DIR / "sessions"
ARCHIVED_SESSIONS_DIR = CODEX_DIR / "archived_sessions"
OUTPUT_DIR = CODEX_DIR / "memories" / "shared_history"
OUTPUT_HTML = OUTPUT_DIR / "index.html"

CSWITCH_STATE_FILE = Path.home() / ".local" / "state" / "cswitch" / "current_profile"
CSWITCH_PROFILES_FILE = CODEX_DIR / "cswitch_profiles.json"
DEFAULT_PROFILE_ORDER = ["dashuichi", "codex", "tokenflux"]


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def safe_write_text(path: Path, content: str, *, mode: int | None = None) -> None:
    ensure_parent_dir(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def safe_write_json(path: Path, obj: object, *, mode: int | None = None) -> None:
    safe_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n", mode=mode)


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def validate_profiles_data(data: dict) -> tuple[list[str], dict[str, dict]]:
    if not isinstance(data, dict):
        raise ValueError("profiles JSON must be an object")
    order = data.get("order", DEFAULT_PROFILE_ORDER)
    profiles = data.get("profiles", {})
    if not isinstance(order, list) or not all(isinstance(x, str) and x.strip() for x in order):
        raise ValueError("profiles JSON 'order' must be a list of strings")
    if not isinstance(profiles, dict):
        raise ValueError("profiles JSON 'profiles' must be an object")
    validated: dict[str, dict] = {}
    for name in order:
        item = profiles.get(name)
        if not isinstance(item, dict):
            raise ValueError(f"missing profile: {name}")
        base_url = item.get("base_url")
        api_key = item.get("api_key")
        model = item.get("model")
        if not isinstance(base_url, str):
            raise ValueError(f"{name}.base_url must be a string")
        if not isinstance(api_key, str):
            raise ValueError(f"{name}.api_key must be a string")
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ValueError(f"{name}.model must be a string when present")
        validated[name] = {"base_url": base_url.strip(), "api_key": api_key.strip()}
        if isinstance(model, str) and model.strip():
            validated[name]["model"] = model.strip()
    return [x.strip() for x in order], validated


def read_api_key_from_auth(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    value = data.get("OPENAI_API_KEY")
    return value.strip() if isinstance(value, str) else ""


def read_base_url_from_config(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    match = re.search(r'^\s*base_url\s*=\s*"([^"]+)"\s*$', text, flags=re.M)
    return match.group(1).strip() if match else ""


def read_model_from_config(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    match = re.search(r'^\s*model\s*=\s*"([^"]+)"\s*$', text, flags=re.M)
    return match.group(1).strip() if match else ""


def resolve_alt_auth(codex_dir: Path) -> Path | None:
    auth1 = codex_dir / "auth1.json"
    suth1 = codex_dir / "suth1.json"
    if auth1.exists() and not suth1.exists():
        return auth1
    if suth1.exists() and not auth1.exists():
        return suth1
    return None


def read_current_profile(order: list[str]) -> str:
    if not CSWITCH_STATE_FILE.exists():
        return order[0] if order else DEFAULT_PROFILE_ORDER[0]
    value = CSWITCH_STATE_FILE.read_text(encoding="utf-8").strip()
    return value if value in order else (order[0] if order else DEFAULT_PROFILE_ORDER[0])


def write_current_profile(profile: str) -> None:
    ensure_parent_dir(CSWITCH_STATE_FILE)
    safe_write_text(CSWITCH_STATE_FILE, profile + "\n")


def infer_profiles_from_existing() -> dict:
    codex_dir = CODEX_DIR
    primary_auth = codex_dir / "auth.json"
    primary_config = codex_dir / "config.toml"
    alt_auth = resolve_alt_auth(codex_dir)
    alt_config = codex_dir / "config1.toml"

    state_value = CSWITCH_STATE_FILE.read_text(encoding="utf-8").strip() if CSWITCH_STATE_FILE.exists() else ""
    current_name = state_value if state_value else "primary"
    other_name = "alt"
    if current_name in {"dashuichi", "codex"}:
        other_name = "dashuichi" if current_name == "codex" else "codex"

    profiles: dict[str, dict[str, str]] = {}

    base_url = read_base_url_from_config(primary_config)
    api_key = read_api_key_from_auth(primary_auth)
    model = read_model_from_config(primary_config)
    if base_url and api_key:
        profiles[current_name] = {"base_url": base_url, "api_key": api_key}
        if model:
            profiles[current_name]["model"] = model

    if alt_auth and alt_config.exists():
        base_url = read_base_url_from_config(alt_config)
        api_key = read_api_key_from_auth(alt_auth)
        model = read_model_from_config(alt_config)
        if base_url and api_key:
            profiles[other_name] = {"base_url": base_url, "api_key": api_key}
            if model:
                profiles[other_name]["model"] = model

    # Optional tokenflux canonical files (created by some setups)
    tokenflux_auth = codex_dir / "auth.tokenflux.json"
    tokenflux_config = codex_dir / "config.tokenflux.toml"
    if tokenflux_auth.exists() and tokenflux_config.exists():
        base_url = read_base_url_from_config(tokenflux_config)
        api_key = read_api_key_from_auth(tokenflux_auth)
        model = read_model_from_config(tokenflux_config)
        if base_url and api_key:
            profiles["tokenflux"] = {"base_url": base_url, "api_key": api_key}
            if model:
                profiles["tokenflux"]["model"] = model

    order = [name for name in DEFAULT_PROFILE_ORDER if name in profiles]
    if current_name in profiles and current_name not in order:
        order.insert(0, current_name)
    if other_name in profiles and other_name not in order:
        order.append(other_name)

    if not order:
        order = [current_name]
        profiles[current_name] = {"base_url": "", "api_key": ""}

    return {"order": order, "profiles": profiles}


def ensure_profiles_file() -> dict:
    data = load_json(CSWITCH_PROFILES_FILE)
    if data:
        return data
    inferred = infer_profiles_from_existing()
    safe_write_json(CSWITCH_PROFILES_FILE, inferred, mode=0o600)
    return inferred


def render_next_config(template_text: str, *, next_profile: str, base_url: str, model: str | None) -> str:
    text = template_text
    text = re.sub(
        r'^\s*model_provider\s*=\s*"[^"]+"\s*$',
        f'model_provider = "{next_profile}"',
        text,
        count=1,
        flags=re.M,
    )
    text = re.sub(
        r"^\s*\[model_providers\.[^\]]+\]\s*$",
        f"[model_providers.{next_profile}]",
        text,
        count=1,
        flags=re.M,
    )
    text = re.sub(
        r'^\s*name\s*=\s*"[^"]+"\s*$',
        f'name = "{next_profile}"',
        text,
        count=1,
        flags=re.M,
    )
    text = re.sub(
        r'^\s*base_url\s*=\s*"[^"]+"\s*$',
        f'base_url = "{base_url}"',
        text,
        count=1,
        flags=re.M,
    )
    if model:
        text = re.sub(
            r'^\s*model\s*=\s*"[^"]+"\s*$',
            f'model = "{model}"',
            text,
            count=1,
            flags=re.M,
        )
    return text


def apply_profile(profiles_data: dict, next_profile: str) -> None:
    order, profiles = validate_profiles_data(profiles_data)
    if next_profile not in order:
        raise ValueError(f"unknown profile: {next_profile}")
    profile = profiles[next_profile]
    if not profile.get("base_url"):
        raise ValueError(f"profile '{next_profile}' has empty base_url")
    if not profile.get("api_key"):
        raise ValueError(f"profile '{next_profile}' has empty api_key")
    template = (CODEX_DIR / "config.toml").read_text(encoding="utf-8")
    new_config = render_next_config(
        template,
        next_profile=next_profile,
        base_url=profile["base_url"],
        model=profile.get("model"),
    )
    safe_write_text(CODEX_DIR / "config.toml", new_config, mode=0o600)
    safe_write_json(CODEX_DIR / "auth.json", {"OPENAI_API_KEY": profile["api_key"]}, mode=0o600)
    write_current_profile(next_profile)


def load_threads() -> dict[str, dict]:
    threads: dict[str, dict] = {}
    if not SESSION_INDEX.exists():
        return threads
    with SESSION_INDEX.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            session_id = row.get("id")
            if not isinstance(session_id, str) or not session_id:
                continue
            threads[session_id] = {
                "id": session_id,
                "title": row.get("thread_name") or "Untitled thread",
                "updated_iso": row.get("updated_at") or "",
            }
    return threads


def iter_session_files() -> Iterable[Path]:
    if not SESSIONS_DIR.exists():
        return []
    return sorted(SESSIONS_DIR.rglob("rollout-*.jsonl"), reverse=True)


def extract_text_blocks(content) -> list[str]:
    blocks: list[str] = []
    if not isinstance(content, list):
        return blocks
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            blocks.append(text.strip())
    return blocks


def stringify(value) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def looks_like_injected_context(role: str, text: str) -> bool:
    if role != "user":
        return False
    stripped = text.lstrip()
    return stripped.startswith("# AGENTS.md instructions") or stripped.startswith("<environment_context>")


def parse_session(path: Path, thread_meta: dict | None) -> dict | None:
    session_meta = {}
    transcript: list[dict[str, str]] = []
    record_timestamp = ""

    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if not record_timestamp:
                    timestamp = record.get("timestamp")
                    if isinstance(timestamp, str):
                        record_timestamp = timestamp

                if record.get("type") == "session_meta":
                    payload = record.get("payload", {})
                    if isinstance(payload, dict):
                        session_meta = payload
                    continue

                if record.get("type") != "response_item":
                    continue

                payload = record.get("payload", {})
                ptype = payload.get("type")

                if ptype != "message":
                    continue

                role = payload.get("role")
                if role not in {"user", "assistant"}:
                    continue
                blocks = extract_text_blocks(payload.get("content"))
                text = "\n\n".join(blocks).strip()
                if not text:
                    continue
                if looks_like_injected_context(role, text):
                    continue
                phase = payload.get("phase", "")
                if role == "assistant" and phase:
                    role = f"assistant:{phase}"
                transcript.append({"role": role, "text": text})
    except OSError:
        return None

    if not transcript and not thread_meta:
        return None

    session_id = stringify((session_meta.get("id") if isinstance(session_meta, dict) else None) or (thread_meta or {}).get("id")) or path.stem
    title = stringify((thread_meta or {}).get("title") or session_meta.get("title") or "Untitled thread")
    cwd = stringify((thread_meta or {}).get("cwd") or (session_meta.get("cwd") if isinstance(session_meta, dict) else "") or "")
    provider = stringify((thread_meta or {}).get("model_provider") or (session_meta.get("model_provider") if isinstance(session_meta, dict) else "") or "")
    source = stringify((thread_meta or {}).get("source") or (session_meta.get("source") if isinstance(session_meta, dict) else "") or "")
    updated_iso = stringify((thread_meta or {}).get("updated_iso") or "")
    if not updated_iso and record_timestamp:
        updated_iso = record_timestamp.replace("T", " ").replace("Z", "")

    if title == "Untitled thread":
        for item in transcript:
            if item["role"] == "user":
                title = item["text"].splitlines()[0][:80]
                break

    user_messages = [item["text"] for item in transcript if item["role"] == "user"]
    assistant_messages = [item["text"] for item in transcript if item["role"].startswith("assistant")]
    preview = ""
    if user_messages:
        preview = user_messages[0].splitlines()[0][:180]
    elif assistant_messages:
        preview = assistant_messages[0].splitlines()[0][:180]

    search_parts = [title, cwd, provider, source, preview, *user_messages[:4], *assistant_messages[:2]]
    search_blob = "\n".join(stringify(part) for part in search_parts if stringify(part)).lower()

    return {
        "id": session_id,
        "title": title,
        "cwd": cwd,
        "provider": provider,
        "source": source,
        "updated_iso": updated_iso,
        "path": str(path),
        "preview": preview,
        "search_blob": search_blob,
        "transcript": transcript,
    }


def collect_sessions() -> list[dict]:
    thread_map = load_threads()
    sessions: list[dict] = []
    seen_ids: set[str] = set()

    for path in iter_session_files():
        session = parse_session(path, None)
        if not session:
            continue
        meta = thread_map.get(session["id"])
        if meta:
            session["title"] = stringify(meta.get("title") or session["title"])
            session["updated_iso"] = stringify(meta.get("updated_iso") or session["updated_iso"])
            session["search_blob"] = "\n".join(
                part
                for part in [session["search_blob"], session["title"], session["updated_iso"]]
                if part
            ).lower()
        if session["id"] in seen_ids:
            continue
        seen_ids.add(session["id"])
        sessions.append(session)

    sessions.sort(key=lambda item: item["updated_iso"], reverse=True)
    return sessions


def role_label(role: str) -> tuple[str, str]:
    mapping = {
        "user": ("User", "user"),
        "assistant": ("Assistant", "assistant"),
        "assistant:commentary": ("Assistant commentary", "assistant"),
        "assistant:final_answer": ("Assistant final", "assistant"),
    }
    return mapping.get(role, (role, "other"))


def render_session_card(session: dict, interactive: bool) -> str:
    transcript_html: list[str] = []
    for item in session["transcript"]:
        label, cls = role_label(item["role"])
        transcript_html.append(
            "\n".join(
                [
                    f'<div class="msg {cls}">',
                    f'<div class="msg-role">{html.escape(label)}</div>',
                    f'<pre>{html.escape(item["text"])}</pre>',
                    "</div>",
                ]
            )
        )

    controls = ""
    if interactive:
        controls = "\n".join(
            [
                '<div class="controls">',
                f'<button type="button" class="neutral rename-button" data-session-id="{html.escape(session["id"], quote=True)}">Rename title</button>',
                f'<form method="post" action="/delete" class="delete-form" data-session-id="{html.escape(session["id"], quote=True)}">',
                f'<input type="hidden" name="session_id" value="{html.escape(session["id"], quote=True)}" />',
                '<button type="submit" class="danger">Delete record</button>',
                "</form>",
                "</div>",
            ]
        )

    data_search = html.escape(session["search_blob"], quote=True)
    meta = " | ".join(part for part in [session["updated_iso"], session["provider"], session["source"], session["cwd"]] if part)
    body = "".join(transcript_html) or '<div class="empty">No transcript captured.</div>'

    return "\n".join(
        [
            f'<details class="session" data-search="{data_search}" data-session-id="{html.escape(session["id"], quote=True)}">',
            "<summary>",
            f'<span class="title" data-role="title">{html.escape(session["title"])}</span>',
            f'<span class="meta">{html.escape(meta)}</span>',
            "</summary>",
            f'<div class="preview">{html.escape(session["preview"])}</div>' if session["preview"] else '<div class="preview muted">No preview</div>',
            f'<div class="path">{html.escape(session["path"])}</div>',
            controls,
            body,
            "</details>",
        ]
    )


def render_html(sessions: list[dict], interactive: bool, flash: str = "") -> str:
    cards = "\n".join(render_session_card(session, interactive=interactive) for session in sessions)
    generated_at = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    flash_html = (
        f'<div id="flash" class="flash">{html.escape(flash)}</div>'
        if flash
        else '<div id="flash" class="flash hidden"></div>'
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Codex Shared History</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #0b1020;
      --panel: #11182d;
      --text: #e5e7eb;
      --muted: #94a3b8;
      --border: #334155;
      --user: #153e75;
      --assistant: #1f5137;
      --danger: #ef4444;
      --danger-bg: rgba(127, 29, 29, 0.35);
      --flash-bg: rgba(3, 105, 161, 0.25);
      --flash-border: rgba(56, 189, 248, 0.45);
    }}
    body {{
      margin: 0;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      background: linear-gradient(180deg, #08111f, #0f172a 30%, #111827);
      color: var(--text);
    }}
    .wrap {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 24px;
    }}
	    .top {{
	      position: sticky;
	      top: 0;
	      backdrop-filter: blur(12px);
	      background: rgba(8, 17, 31, 0.88);
	      border-bottom: 1px solid var(--border);
	      padding: 16px 0 18px;
	      z-index: 5;
	    }}
	    .nav {{
	      display: flex;
	      gap: 12px;
	      margin: 12px 0 14px;
	    }}
	    .nav a {{
	      color: var(--muted);
	      text-decoration: none;
	      border: 1px solid var(--border);
	      padding: 6px 10px;
	      border-radius: 10px;
	      font-size: 13px;
	    }}
	    .nav a.active {{
	      color: var(--text);
	      border-color: rgba(56, 189, 248, 0.7);
	      background: rgba(3, 105, 161, 0.18);
	    }}
	    h1 {{
	      margin: 0 0 8px;
	      font-size: 28px;
	    }}
    .sub, .stats, .meta, .preview, .path, .empty {{
      color: var(--muted);
      font-size: 13px;
    }}
    input[type="search"] {{
      width: min(720px, 100%);
      box-sizing: border-box;
      border: 1px solid var(--border);
      background: #0f172a;
      color: var(--text);
      border-radius: 10px;
      padding: 12px 14px;
      font: inherit;
    }}
    .stats {{
      margin-top: 10px;
    }}
    .flash {{
      margin-top: 14px;
      border: 1px solid var(--flash-border);
      background: var(--flash-bg);
      padding: 10px 12px;
      border-radius: 10px;
      color: #dbeafe;
    }}
    .session {{
      border: 1px solid var(--border);
      background: rgba(17, 24, 45, 0.94);
      border-radius: 14px;
      padding: 0 16px 16px;
      margin: 18px 0;
    }}
    summary {{
      list-style: none;
      cursor: pointer;
      padding: 16px 0 12px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }}
    summary::-webkit-details-marker {{
      display: none;
    }}
    .title {{
      font-size: 17px;
      font-weight: 700;
    }}
    .path {{
      margin: 8px 0 14px;
      word-break: break-all;
    }}
    .preview {{
      margin-bottom: 8px;
    }}
    .controls {{
      display: flex;
      justify-content: flex-end;
      align-items: center;
      gap: 10px;
      margin: 8px 0 14px;
    }}
    form {{
      margin: 0;
    }}
    .neutral, .danger {{
      border-radius: 8px;
      padding: 8px 12px;
      font: inherit;
      cursor: pointer;
    }}
    .neutral {{
      border: 1px solid rgba(148, 163, 184, 0.35);
      background: rgba(30, 41, 59, 0.55);
      color: #e2e8f0;
    }}
    .neutral:hover {{
      background: rgba(51, 65, 85, 0.7);
    }}
    .danger {{
      border: 1px solid rgba(248, 113, 113, 0.4);
      background: var(--danger-bg);
      color: #fecaca;
    }}
    .danger:hover {{
      background: rgba(127, 29, 29, 0.55);
    }}
    .msg {{
      border-radius: 10px;
      padding: 10px 12px;
      margin: 10px 0;
      border: 1px solid rgba(255,255,255,0.08);
    }}
    .msg.user {{
      background: rgba(21, 62, 117, 0.35);
    }}
    .msg.assistant {{
      background: rgba(31, 81, 55, 0.35);
    }}
    .msg.other {{
      background: rgba(71, 85, 105, 0.25);
    }}
    .msg-role {{
      color: #cbd5e1;
      font-size: 12px;
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font: inherit;
      line-height: 1.5;
    }}
    .hidden {{
      display: none;
    }}
  </style>
</head>
<body>
	  <div class="top">
	    <div class="wrap">
	      <h1>Codex Shared History</h1>
	      <div class="sub">Local aggregate view across all sessions on this machine. Generated at {html.escape(generated_at)}.</div>
	      <div class="nav">
	        <a class="active" href="/">History</a>
	        <a href="/profiles">Profiles</a>
	      </div>
	      <input id="search" type="search" placeholder="Search title, cwd, provider, prompt, or reply..." />
	      <div class="stats"><span id="visible-count">{len(sessions)}</span> / {len(sessions)} sessions visible</div>
	      {flash_html}
	    </div>
	  </div>
  <div class="wrap" id="sessions">
    {cards}
  </div>
  <script>
    const input = document.getElementById('search');
    const sessions = Array.from(document.querySelectorAll('.session'));
    const visibleCount = document.getElementById('visible-count');
    const flash = document.getElementById('flash');

    function setFlash(message, isError = false) {{
      flash.textContent = message;
      flash.classList.remove('hidden');
      flash.style.borderColor = isError ? 'rgba(248, 113, 113, 0.45)' : '';
      flash.style.background = isError ? 'rgba(127, 29, 29, 0.25)' : '';
      flash.style.color = isError ? '#fecaca' : '';
    }}

    function applyFilter() {{
      const q = input.value.trim().toLowerCase();
      let count = 0;
      for (const el of sessions) {{
        const hay = el.dataset.search || '';
        const show = !q || hay.includes(q);
        el.classList.toggle('hidden', !show);
        if (show) count += 1;
      }}
      visibleCount.textContent = String(count);
    }}

    function updateSessionSearch(card, newTitle) {{
      const current = card.dataset.search || '';
      const currentTitleEl = card.querySelector('[data-role="title"]');
      const currentTitle = currentTitleEl ? currentTitleEl.textContent || '' : '';
      if (currentTitle && current.includes(currentTitle.toLowerCase())) {{
        card.dataset.search = current.replace(currentTitle.toLowerCase(), newTitle.toLowerCase());
      }} else {{
        card.dataset.search = `${{newTitle.toLowerCase()}}\n${{current}}`;
      }}
    }}

    input.addEventListener('input', applyFilter);

    for (const button of document.querySelectorAll('.rename-button')) {{
      button.addEventListener('click', async () => {{
        const sessionId = button.dataset.sessionId || '';
        const card = button.closest('.session');
        if (!sessionId || !card) {{
          setFlash('Missing session id', true);
          return;
        }}

        const titleEl = card.querySelector('[data-role="title"]');
        const currentTitle = titleEl ? (titleEl.textContent || '').trim() : '';
        const nextTitle = window.prompt('Rename record title', currentTitle);
        if (nextTitle === null) {{
          return;
        }}
        const trimmedTitle = nextTitle.trim();
        if (!trimmedTitle) {{
          setFlash('Title cannot be empty', true);
          return;
        }}
        if (trimmedTitle === currentTitle) {{
          return;
        }}

        button.disabled = true;
        const originalText = button.textContent || 'Rename title';
        button.textContent = 'Renaming...';
        try {{
          const response = await fetch('/rename', {{
            method: 'POST',
            headers: {{
              'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
              'Accept': 'application/json'
            }},
            body: new URLSearchParams({{
              session_id: sessionId,
              title: trimmedTitle
            }}).toString()
          }});
          const payload = await response.json();
          if (!response.ok || !payload.ok) {{
            throw new Error(payload.flash || `Rename failed (${{response.status}})`);
          }}

          if (titleEl) {{
            titleEl.textContent = trimmedTitle;
          }}
          updateSessionSearch(card, trimmedTitle);
          applyFilter();
          setFlash(payload.flash || `Renamed ${{sessionId}}`);
        }} catch (error) {{
          setFlash(error.message || 'Rename failed', true);
        }} finally {{
          button.disabled = false;
          button.textContent = originalText;
        }}
      }});
    }}

    for (const form of document.querySelectorAll('.delete-form')) {{
      form.addEventListener('submit', async (event) => {{
        event.preventDefault();
        const sessionId = form.dataset.sessionId || '';
        if (!sessionId) {{
          setFlash('Missing session id', true);
          return;
        }}
        if (!window.confirm(`Delete local record ${{sessionId}}?`)) {{
          return;
        }}

        const button = form.querySelector('button[type="submit"]');
        if (button) {{
          button.disabled = true;
          button.dataset.originalText = button.textContent || 'Delete record';
          button.textContent = 'Deleting...';
        }}

        try {{
          const response = await fetch('/delete', {{
            method: 'POST',
            headers: {{
              'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
              'Accept': 'application/json'
            }},
            body: new URLSearchParams({{ session_id: sessionId }}).toString()
          }});

          const payload = await response.json();
          if (!response.ok || !payload.ok) {{
            throw new Error(payload.flash || `Delete failed (${{response.status}})`);
          }}

          const card = form.closest('.session');
          if (card) {{
            card.remove();
            const idx = sessions.indexOf(card);
            if (idx >= 0) {{
              sessions.splice(idx, 1);
            }}
          }}
          applyFilter();
          setFlash(payload.flash || `Deleted ${{sessionId}}`);
        }} catch (error) {{
          setFlash(error.message || 'Delete failed', true);
          if (button) {{
            button.disabled = false;
            button.textContent = button.dataset.originalText || 'Delete record';
          }}
          return;
        }}
      }});
    }}
  </script>
</body>
</html>
"""


def render_profiles_html(flash: str = "") -> str:
    generated_at = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    flash_html = (
        f'<div id="flash" class="flash">{html.escape(flash)}</div>'
        if flash
        else '<div id="flash" class="flash hidden"></div>'
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Codex Profiles</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #0b1020;
      --panel: #11182d;
      --text: #e5e7eb;
      --muted: #94a3b8;
      --border: #334155;
      --danger-bg: rgba(127, 29, 29, 0.35);
      --flash-bg: rgba(3, 105, 161, 0.25);
      --flash-border: rgba(56, 189, 248, 0.45);
    }}
    body {{
      margin: 0;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      background: linear-gradient(180deg, #08111f, #0f172a 30%, #111827);
      color: var(--text);
    }}
    .wrap {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 24px;
    }}
    .top {{
      position: sticky;
      top: 0;
      backdrop-filter: blur(12px);
      background: rgba(8, 17, 31, 0.88);
      border-bottom: 1px solid var(--border);
      padding: 16px 0 18px;
      z-index: 5;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 28px;
    }}
    .sub, .meta {{
      color: var(--muted);
      font-size: 13px;
    }}
    .nav {{
      display: flex;
      gap: 12px;
      margin: 12px 0 14px;
    }}
    .nav a {{
      color: var(--muted);
      text-decoration: none;
      border: 1px solid var(--border);
      padding: 6px 10px;
      border-radius: 10px;
      font-size: 13px;
    }}
    .nav a.active {{
      color: var(--text);
      border-color: rgba(56, 189, 248, 0.7);
      background: rgba(3, 105, 161, 0.18);
    }}
    .flash {{
      margin-top: 14px;
      border: 1px solid var(--flash-border);
      background: var(--flash-bg);
      padding: 10px 12px;
      border-radius: 10px;
      color: #dbeafe;
    }}
    .panel {{
      border: 1px solid var(--border);
      background: rgba(17, 24, 45, 0.94);
      border-radius: 14px;
      padding: 16px;
      margin: 18px 0;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    th, td {{
      text-align: left;
      border-bottom: 1px solid rgba(51, 65, 85, 0.6);
      padding: 10px 8px;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
    }}
    input[type="text"], input[type="password"] {{
      width: 100%;
      box-sizing: border-box;
      border: 1px solid var(--border);
      background: #0f172a;
      color: var(--text);
      border-radius: 10px;
      padding: 10px 12px;
      font: inherit;
      font-size: 13px;
    }}
    .row-actions {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    button {{
      border: 1px solid var(--border);
      background: #0f172a;
      color: var(--text);
      border-radius: 10px;
      padding: 8px 10px;
      cursor: pointer;
      font: inherit;
      font-size: 13px;
    }}
    button.primary {{
      border-color: rgba(56, 189, 248, 0.7);
      background: rgba(3, 105, 161, 0.18);
    }}
    button.danger {{
      border-color: rgba(239, 68, 68, 0.7);
      background: var(--danger-bg);
    }}
    code {{
      font-family: inherit;
      font-size: 12px;
      padding: 1px 6px;
      border: 1px solid rgba(51, 65, 85, 0.65);
      border-radius: 8px;
      background: rgba(15, 23, 42, 0.8);
    }}
    .hidden {{ display: none; }}
  </style>
</head>
<body>
  <div class="top">
    <div class="wrap">
      <h1>Codex Profiles</h1>
      <div class="sub">Generated at {html.escape(generated_at)} · Writes to <code>{html.escape(str(CSWITCH_PROFILES_FILE))}</code></div>
      <div class="nav">
        <a href="/">History</a>
        <a class="active" href="/profiles">Profiles</a>
      </div>
      {flash_html}
    </div>
  </div>
  <div class="wrap">
    <div class="panel">
      <div class="row-actions" style="margin-bottom: 12px;">
        <button id="add" type="button">Add profile</button>
        <button id="save" class="primary" type="button">Save</button>
      </div>
      <div class="meta" id="status"></div>
      <div style="overflow-x:auto;">
        <table>
          <thead>
            <tr>
              <th style="width: 130px;">Name</th>
              <th>Base URL</th>
              <th>API Key</th>
              <th style="width: 140px;">Model</th>
              <th style="width: 220px;">Actions</th>
            </tr>
          </thead>
          <tbody id="rows"></tbody>
        </table>
      </div>
      <div class="meta" style="margin-top: 10px;">API keys are stored in plain text on disk. This UI binds to <code>127.0.0.1</code> only.</div>
    </div>
  </div>
  <script>
    const $ = (sel) => document.querySelector(sel);
    const rowsEl = $("#rows");
    const statusEl = $("#status");
    const flashEl = $("#flash");

    function setFlash(msg, ok=true) {{
      flashEl.textContent = msg;
      flashEl.classList.remove("hidden");
      flashEl.style.borderColor = ok ? "rgba(56, 189, 248, 0.45)" : "rgba(239, 68, 68, 0.7)";
      flashEl.style.background = ok ? "rgba(3, 105, 161, 0.25)" : "rgba(127, 29, 29, 0.35)";
      flashEl.style.color = ok ? "#dbeafe" : "#fecaca";
    }}

    function escapeHtml(text) {{
      const div = document.createElement("div");
      div.innerText = text;
      return div.innerHTML;
    }}

    function mkRow(name, profile) {{
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><input type="text" class="name" value="${{escapeHtml(name)}}" /></td>
        <td><input type="text" class="base_url" value="${{escapeHtml(profile.base_url || "")}}" placeholder="https://example.com/v1" /></td>
        <td><input type="password" class="api_key" value="${{escapeHtml(profile.api_key || "")}}" placeholder="sk-..." /></td>
        <td><input type="text" class="model" value="${{escapeHtml(profile.model || "")}}" placeholder="gpt-5.4" /></td>
        <td>
          <div class="row-actions">
            <button type="button" class="up">Up</button>
            <button type="button" class="down">Down</button>
            <button type="button" class="apply primary">Apply</button>
            <button type="button" class="remove danger">Remove</button>
          </div>
        </td>`;
      return tr;
    }}

    function readTable() {{
      const order = [];
      const profiles = {{}};
      for (const tr of rowsEl.querySelectorAll("tr")) {{
        const name = tr.querySelector(".name").value.trim();
        if (!name) continue;
        order.push(name);
        profiles[name] = {{
          base_url: tr.querySelector(".base_url").value.trim(),
          api_key: tr.querySelector(".api_key").value.trim(),
        }};
        const model = tr.querySelector(".model").value.trim();
        if (model) profiles[name].model = model;
      }}
      return {{ order, profiles }};
    }}

    async function load() {{
      const res = await fetch("/api/profiles");
      const data = await res.json();
      rowsEl.innerHTML = "";
      for (const name of data.order || []) {{
        const profile = (data.profiles || {{}})[name] || {{}};
        rowsEl.appendChild(mkRow(name, profile));
      }}
      statusEl.textContent = `Current: ${{data.current || ""}}`;
    }}

    rowsEl.addEventListener("click", async (ev) => {{
      const btn = ev.target.closest("button");
      if (!btn) return;
      const tr = ev.target.closest("tr");
      if (!tr) return;

      if (btn.classList.contains("remove")) {{
        tr.remove();
        return;
      }}
      if (btn.classList.contains("up")) {{
        const prev = tr.previousElementSibling;
        if (prev) rowsEl.insertBefore(tr, prev);
        return;
      }}
      if (btn.classList.contains("down")) {{
        const next = tr.nextElementSibling;
        if (next) rowsEl.insertBefore(next, tr);
        return;
      }}
      if (btn.classList.contains("apply")) {{
        const name = tr.querySelector(".name").value.trim();
        if (!name) return;
        const res = await fetch("/api/profiles/apply", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ profile: name }}),
        }});
        const out = await res.json();
        if (out.ok) {{
          statusEl.textContent = `Current: ${{name}}`;
          setFlash(out.flash || `Applied ${{name}}`, true);
        }} else {{
          setFlash(out.flash || "Apply failed", false);
        }}
      }}
    }});

    $("#add").addEventListener("click", () => {{
      const name = `profile-${{rowsEl.querySelectorAll("tr").length + 1}}`;
      rowsEl.appendChild(mkRow(name, {{ base_url: "", api_key: "" }}));
    }});

    $("#save").addEventListener("click", async () => {{
      const payload = readTable();
      const res = await fetch("/api/profiles", {{
        method: "PUT",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(payload),
      }});
      const out = await res.json();
      if (out.ok) {{
        setFlash(out.flash || "Saved", true);
        await load();
      }} else {{
        setFlash(out.flash || "Save failed", false);
      }}
    }});

    load().catch((err) => {{
      setFlash(String(err), false);
    }});
  </script>
</body>
</html>
"""


def rewrite_jsonl_without_session(path: Path, session_id: str) -> int:
    if not path.exists():
        return 0

    removed = 0
    kept_lines: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                kept_lines.append(raw_line)
                continue
            if row.get("id") == session_id or row.get("session_id") == session_id:
                removed += 1
                continue
            kept_lines.append(raw_line)

    if removed:
        with path.open("w", encoding="utf-8") as handle:
            handle.writelines(kept_lines)
    return removed


def rename_session_title(path: Path, session_id: str, new_title: str) -> bool:
    if not path.exists():
        return False

    changed = False
    rewritten: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                rewritten.append(raw_line)
                continue

            if row.get("id") == session_id:
                row["thread_name"] = new_title
                rewritten.append(json.dumps(row, ensure_ascii=False) + "\n")
                changed = True
            else:
                rewritten.append(raw_line)

    if changed:
        with path.open("w", encoding="utf-8") as handle:
            handle.writelines(rewritten)
    return changed


def delete_session(session_id: str) -> dict[str, int]:
    deleted_files = 0
    for base in [SESSIONS_DIR, ARCHIVED_SESSIONS_DIR]:
        if not base.exists():
            continue
        for path in base.rglob(f"*{session_id}*.jsonl"):
            try:
                path.unlink()
                deleted_files += 1
            except FileNotFoundError:
                continue

    removed_index = rewrite_jsonl_without_session(SESSION_INDEX, session_id)
    removed_history = rewrite_jsonl_without_session(HISTORY_JSONL, session_id)
    return {
        "deleted_files": deleted_files,
        "removed_index": removed_index,
        "removed_history": removed_history,
    }


def build_static_html(output: Path) -> tuple[Path, int]:
    output.parent.mkdir(parents=True, exist_ok=True)
    sessions = collect_sessions()
    output.write_text(render_html(sessions, interactive=False), encoding="utf-8")
    return output, len(sessions)


class HistoryHandler(BaseHTTPRequestHandler):
    def read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def write_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        flash = params.get("flash", [""])[0]
        if parsed.path == "/":
            body = render_html(collect_sessions(), interactive=True, flash=flash).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/profiles":
            body = render_profiles_html(flash=flash).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/profiles":
            profiles_data = ensure_profiles_file()
            try:
                order, profiles = validate_profiles_data(profiles_data)
                current = read_current_profile(order)
                self.write_json({"ok": True, "order": order, "profiles": profiles, "current": current})
            except Exception as exc:
                raw_order = profiles_data.get("order", [])
                raw_profiles = profiles_data.get("profiles", {})
                current = raw_order[0] if isinstance(raw_order, list) and raw_order else ""
                self.write_json(
                    {
                        "ok": False,
                        "flash": f"Invalid profiles file: {exc}",
                        "order": raw_order if isinstance(raw_order, list) else [],
                        "profiles": raw_profiles if isinstance(raw_profiles, dict) else {},
                        "current": current,
                    },
                    status=400,
                )
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/profiles/apply":
            data = self.read_json_body()
            profile = str(data.get("profile", "")).strip()
            if not profile:
                self.write_json({"ok": False, "flash": "Missing profile"}, status=400)
                return
            try:
                profiles_data = ensure_profiles_file()
                apply_profile(profiles_data, profile)
            except Exception as exc:
                self.write_json({"ok": False, "flash": f"Apply failed: {exc}"}, status=400)
                return
            self.write_json({"ok": True, "flash": f"Applied profile: {profile}"})
            return

        if parsed.path not in {"/delete", "/rename"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        form = urllib.parse.parse_qs(body)
        session_id = form.get("session_id", [""])[0].strip()
        if not session_id:
            self.respond_result("Missing session id", ok=False)
            return

        if parsed.path == "/rename":
            title = form.get("title", [""])[0].strip()
            if not title:
                self.respond_result("Title cannot be empty", ok=False)
                return
            changed = rename_session_title(SESSION_INDEX, session_id, title)
            if not changed:
                self.respond_result(f"Session not found in session_index: {session_id}", ok=False)
                return
            self.respond_result(f"Renamed {session_id} to: {title}", ok=True)
            return

        result = delete_session(session_id)
        flash = (
            f"Deleted {session_id}: files={result['deleted_files']}, "
            f"session_index rows={result['removed_index']}, history rows={result['removed_history']}"
        )
        self.respond_result(flash, ok=True)

    def do_PUT(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/profiles":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = self.read_json_body()
        try:
            order, profiles = validate_profiles_data(data)
        except Exception as exc:
            self.write_json({"ok": False, "flash": f"Invalid profiles: {exc}"}, status=400)
            return
        safe_write_json(CSWITCH_PROFILES_FILE, {"order": order, "profiles": profiles}, mode=0o600)
        self.write_json({"ok": True, "flash": "Saved profiles"})

    def redirect_with_flash(self, message: str) -> None:
        location = "/?flash=" + urllib.parse.quote(message)
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()

    def wants_json(self) -> bool:
        accept = self.headers.get("Accept", "")
        return "application/json" in accept

    def respond_result(self, message: str, ok: bool) -> None:
        if self.wants_json():
            payload = json.dumps({"ok": ok, "flash": message}, ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if ok:
            self.redirect_with_flash(message)
            return
        self.redirect_with_flash(message)

    def log_message(self, format: str, *args) -> None:
        return


def maybe_open_browser(url: str) -> None:
    try:
        subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    except Exception:
        pass
    webbrowser.open(url)


def serve_history(port: int, open_browser: bool) -> int:
    return serve_ui(port=port, open_browser=open_browser, open_path="/")


def serve_ui(port: int, open_browser: bool, open_path: str) -> int:
    server = ThreadingHTTPServer(("127.0.0.1", port), HistoryHandler)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}{open_path}"
    print(f"serving {url}")
    if open_browser:
        threading.Timer(0.2, lambda: maybe_open_browser(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


def main_history(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Local Codex shared history viewer")
    parser.add_argument("--build", action="store_true", help="Build a static HTML snapshot instead of serving")
    parser.add_argument("--output", default=str(OUTPUT_HTML), help="Output HTML path for --build")
    parser.add_argument("--serve", action="store_true", help="Serve an interactive local UI")
    parser.add_argument("--port", type=int, default=8765, help="Port for --serve (default: 8765)")
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")
    args = parser.parse_args(argv)

    if args.build:
        output, count = build_static_html(Path(args.output).expanduser())
        print(f"wrote {output}")
        print(f"sessions: {count}")
        return 0

    return serve_ui(port=args.port, open_browser=not args.no_open, open_path="/")


def main_profiles(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(description="Local Codex profile editor")
    parser.add_argument("--port", type=int, default=8766, help="Port for UI (default: 8766)")
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")
    args = parser.parse_args(argv)
    return serve_ui(port=args.port, open_browser=not args.no_open, open_path="/profiles")


def main_cswitch(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(prog="cswitch", description="Switch Codex config by profile")
    parser.add_argument(
        "action",
        nargs="?",
        choices=("switch", "status", "list", "set", "init"),
        default="switch",
        help="switch: go to next profile; status: show current; list: show all; set: apply a profile; init: create profiles JSON",
    )
    parser.add_argument("profile", nargs="?", help="Profile name for 'set'")
    args = parser.parse_args(argv)

    profiles_data = ensure_profiles_file()
    order, _profiles = validate_profiles_data(profiles_data)

    if args.action == "init":
        print(str(CSWITCH_PROFILES_FILE))
        return 0

    current = read_current_profile(order)

    if args.action == "status":
        print(f"当前使用的配置：{current}")
        return 0

    if args.action == "list":
        for name in order:
            marker = "*" if name == current else " "
            print(f"{marker} {name}")
        return 0

    if args.action == "set":
        if not args.profile:
            parser.error("missing profile name for 'set'")
        apply_profile(profiles_data, args.profile)
        print(f"当前使用的配置：{args.profile}")
        return 0

    next_profile = order[(order.index(current) + 1) % len(order)]
    apply_profile(profiles_data, next_profile)
    print(f"当前使用的配置：{next_profile}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if argv and argv[0] == "profiles":
        return main_profiles(argv[1:])
    if argv and argv[0] == "cswitch":
        return main_cswitch(argv[1:])
    if argv and argv[0] == "history":
        return main_history(argv[1:])
    return main_history(argv)


if __name__ == "__main__":
    raise SystemExit(main())
