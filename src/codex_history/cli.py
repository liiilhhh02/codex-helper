#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import subprocess
import threading
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable


CODEX_DIR = Path.home() / ".codex"
SESSION_INDEX = CODEX_DIR / "session_index.jsonl"
HISTORY_JSONL = CODEX_DIR / "history.jsonl"
SESSIONS_DIR = CODEX_DIR / "sessions"
ARCHIVED_SESSIONS_DIR = CODEX_DIR / "archived_sessions"
OUTPUT_DIR = CODEX_DIR / "memories" / "shared_history"
OUTPUT_HTML = OUTPUT_DIR / "index.html"


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
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        params = urllib.parse.parse_qs(parsed.query)
        flash = params.get("flash", [""])[0]
        body = render_html(collect_sessions(), interactive=True, flash=flash).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
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
    server = ThreadingHTTPServer(("127.0.0.1", port), HistoryHandler)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/"
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Codex shared history viewer")
    parser.add_argument("--build", action="store_true", help="Build a static HTML snapshot instead of serving")
    parser.add_argument("--output", default=str(OUTPUT_HTML), help="Output HTML path for --build")
    parser.add_argument("--serve", action="store_true", help="Serve an interactive local UI")
    parser.add_argument("--port", type=int, default=8765, help="Port for --serve (default: 8765)")
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")
    args = parser.parse_args()

    if args.build:
        output, count = build_static_html(Path(args.output).expanduser())
        print(f"wrote {output}")
        print(f"sessions: {count}")
        return 0

    if args.serve or not args.build:
        return serve_history(port=args.port, open_browser=not args.no_open)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
