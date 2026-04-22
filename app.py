"""
Meeting Notes AI
================
python3 app.py → browser → record → transcribe → AI notes + Notion

Features: auto-chunking, Notion push, editable notes, action item tracking
"""

import os, json, asyncio, tempfile, traceback, webbrowser, re
from pathlib import Path
from datetime import datetime
from typing import List

import httpx, anthropic
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from dotenv import load_dotenv

load_dotenv(override=True)

SARVAM_API_KEY      = os.getenv("SARVAM_API_KEY", "")
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
NOTION_KEY          = os.getenv("NOTION_KEY", "") or os.getenv("NOTION_API_KEY", "")
NOTION_DB_ID        = os.getenv("NOTION_DB_ID", "") or os.getenv("NOTION_DATABASE_ID", "")
NOTION_ACTION_ITEMS_DB_ID = os.getenv("NOTION_ACTION_ITEMS_DB_ID", "")

if not SARVAM_API_KEY:    print("⚠️  SARVAM_API_KEY not set — open http://localhost:8000 to configure")
if not ANTHROPIC_API_KEY: print("⚠️  ANTHROPIC_API_KEY not set — open http://localhost:8000 to configure")

BASE_DIR = Path(__file__).parent
CONTEXT_FILE = BASE_DIR / "context.md"
OUTPUT_DIR = BASE_DIR / "meetings"
OUTPUT_DIR.mkdir(exist_ok=True)


# ─── Session Tracking ─────────────────────────────────────────
current_session = {"dir": None, "chunks": [], "meta": {}}

def get_or_create_session_dir() -> Path:
    if current_session["dir"] and Path(current_session["dir"]).exists():
        return Path(current_session["dir"])
    now = datetime.now()
    d = OUTPUT_DIR / now.strftime("%Y-%m") / now.strftime("%Y-%m-%d_%H-%M")
    d.mkdir(parents=True, exist_ok=True)
    current_session["dir"] = str(d)
    current_session["chunks"] = []
    return d

def load_system_prompt() -> str:
    if CONTEXT_FILE.exists():
        return CONTEXT_FILE.read_text(encoding="utf-8")
    return "You are a meeting assistant. Produce structured notes."

# ─── Meeting Metadata Parser ──────────────────────────────────
def parse_meeting_meta(notes: str) -> dict:
    """Extract Participant, Type, and Summary — each on its own line at top of notes."""
    def _get(label):
        m = re.search(rf'^{label}:\s*(.+)$', notes, re.MULTILINE | re.IGNORECASE)
        return m.group(1).strip() if m else ""
    return {
        "participant":  _get("Participant"),
        "meeting_type": _get("Type"),
        "summary":      _get("Summary"),
    }

# ─── Action Items Parser ───────────────────────────────────────
def parse_action_items(notes: str) -> list:
    """Extract action items table from Execution Summary. Returns [{task, owner, by_when}]."""
    items = []
    try:
        # Find the All Action Items table
        match = re.search(r'###\s*All Action Items[^\n]*\n(?:\s*\n)*((?:\|[^\n]*\n?)+)', notes, re.IGNORECASE)
        if not match:
            return []
        table = match.group(1)
        for line in table.strip().splitlines():
            # Skip separator rows (---|---|---)
            if re.match(r'^\s*\|[\s\-|:]+\|\s*$', line):
                continue
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if len(cells) >= 1 and cells[0] and cells[0].lower() not in ('task', '#', ''):
                # Handle both 3-col (Task|Owner|By When) and 4-col (#|Task|Owner|By When)
                if len(cells) >= 4:
                    task, owner, by_when = cells[1], cells[2], cells[3]
                elif len(cells) == 3:
                    task, owner, by_when = cells[0], cells[1], cells[2]
                else:
                    task, owner, by_when = cells[0], "", ""
                items.append({"task": task, "owner": owner, "by_when": by_when})
    except Exception as e:
        print(f"⚠️  parse_action_items: {e}")
    return items

# ─── Notion Action Items DB Setup ─────────────────────────────
async def ensure_action_items_db():
    """Create the Action Items relational DB in Notion if it doesn't exist yet."""
    global NOTION_ACTION_ITEMS_DB_ID
    if not NOTION_KEY or not NOTION_DB_ID:
        return
    if NOTION_ACTION_ITEMS_DB_ID:
        print(f"✅ Action Items DB: {NOTION_ACTION_ITEMS_DB_ID[:8]}...")
        return
    print("🔧 Creating Notion Action Items DB...")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            headers = {
                "Authorization": f"Bearer {NOTION_KEY}",
                "Content-Type": "application/json",
                "Notion-Version": "2022-06-28",
            }
            # Get parent of existing Meetings DB
            r = await client.get(f"https://api.notion.com/v1/databases/{NOTION_DB_ID}", headers=headers)
            r.raise_for_status()
            parent = r.json().get("parent", {})

            # Create Action Items DB
            db_payload = {
                "parent": parent,
                "title": [{"text": {"content": "Meeting Action Items"}}],
                "properties": {
                    "Task":         {"title": {}},
                    "Owner":        {"select": {}},
                    "By When":      {"date": {}},
                    "Status":       {"select": {"options": [
                        {"name": "Pending", "color": "yellow"},
                        {"name": "Done",    "color": "green"},
                    ]}},
                    "Meeting":      {"relation": {"database_id": NOTION_DB_ID, "single_property": {}}},
                    "Meeting Type": {"select": {"options": [
                        {"name": "Deep Dive"}, {"name": "1:1"},
                        {"name": "Planned Meeting"}, {"name": "Ad-hoc"},
                    ]}},
                    "Date":         {"date": {}},
                },
            }
            r2 = await client.post("https://api.notion.com/v1/databases", headers=headers, json=db_payload)
            r2.raise_for_status()
            new_id = r2.json()["id"]
            NOTION_ACTION_ITEMS_DB_ID = new_id

            # Persist to .env
            env_path = BASE_DIR / ".env"
            env_text = env_path.read_text() if env_path.exists() else ""
            if "NOTION_ACTION_ITEMS_DB_ID" in env_text:
                env_text = re.sub(r'NOTION_ACTION_ITEMS_DB_ID=.*', f'NOTION_ACTION_ITEMS_DB_ID={new_id}', env_text)
            else:
                env_text += f'\nNOTION_ACTION_ITEMS_DB_ID={new_id}\n'
            env_path.write_text(env_text)
            print(f"✅ Action Items DB created: {new_id}")
    except Exception as e:
        print(f"⚠️  Could not create Action Items DB: {e}")

async def ensure_date_property():
    """Patch the existing Action Items DB to add the Date property if missing."""
    if not NOTION_KEY or not NOTION_ACTION_ITEMS_DB_ID:
        return
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            headers = {"Authorization": f"Bearer {NOTION_KEY}", "Content-Type": "application/json", "Notion-Version": "2022-06-28"}
            await client.patch(f"https://api.notion.com/v1/databases/{NOTION_ACTION_ITEMS_DB_ID}",
                headers=headers, json={"properties": {"Date": {"date": {}}}})
        print("✅ Action Items DB: Date property ensured")
    except Exception as e:
        print(f"⚠️  Could not patch Date property: {e}")

# ─── App ──────────────────────────────────────────────────────
PORT = int(os.getenv("PORT", 8000))

@asynccontextmanager
async def lifespan(app):
    await ensure_action_items_db()
    await ensure_date_property()
    yield

app = FastAPI(title="Meeting Notes AI", lifespan=lifespan)
# Lock CORS to localhost only — prevents malicious websites from calling our
# endpoints (e.g. overwriting API keys) while the app is running locally.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://localhost:{PORT}",
        f"http://127.0.0.1:{PORT}",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    allow_credentials=False,
)

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    return HTMLResponse((BASE_DIR / "ui.html").read_text(encoding="utf-8"))

@app.get("/config")
async def get_config():
    return JSONResponse({
        "notion_enabled": bool(NOTION_KEY and NOTION_DB_ID),
    })

# ─── Setup Endpoints ──────────────────────────────────────────
def _mask(key: str) -> str:
    """Return a display-safe masked version of an API key."""
    if not key: return ""
    if len(key) <= 8: return "●" * len(key)
    return key[:6] + "···" + key[-4:]

def _write_env(updates: dict):
    """Write key=value pairs to .env, preserving existing lines."""
    env_path = BASE_DIR / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    idx = {l.split("=", 1)[0].strip(): i for i, l in enumerate(lines) if "=" in l and not l.startswith("#")}
    for k, v in updates.items():
        if k in idx:
            lines[idx[k]] = f"{k}={v}"
        else:
            lines.append(f"{k}={v}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

@app.get("/setup-status")
async def setup_status():
    return JSONResponse({
        "sarvam":    bool(SARVAM_API_KEY),
        "anthropic": bool(ANTHROPIC_API_KEY),
        "notion":    bool(NOTION_KEY and NOTION_DB_ID),
        "ready":     bool(SARVAM_API_KEY and ANTHROPIC_API_KEY),
    })

@app.get("/masked-keys")
async def get_masked_keys():
    """Return masked key display strings — never returns real key values."""
    return JSONResponse({
        "sarvam":       _mask(SARVAM_API_KEY),
        "anthropic":    _mask(ANTHROPIC_API_KEY),
        "notion":       _mask(NOTION_KEY),
        "notion_db_id": NOTION_DB_ID,   # DB ID is not secret
    })

@app.post("/save-keys")
async def save_keys(request: dict):
    """Write API keys to .env and update in-memory vars. Never echoes key values back."""
    global SARVAM_API_KEY, ANTHROPIC_API_KEY, NOTION_KEY, NOTION_DB_ID
    updates = {}
    if request.get("sarvam", "").strip():
        SARVAM_API_KEY = request["sarvam"].strip()
        updates["SARVAM_API_KEY"] = SARVAM_API_KEY
    if request.get("anthropic", "").strip():
        ANTHROPIC_API_KEY = request["anthropic"].strip()
        updates["ANTHROPIC_API_KEY"] = ANTHROPIC_API_KEY
    if request.get("notion", "").strip():
        NOTION_KEY = request["notion"].strip()
        updates["NOTION_KEY"] = NOTION_KEY
    if request.get("notion_db_id", "").strip():
        NOTION_DB_ID = request["notion_db_id"].strip()
        updates["NOTION_DB_ID"] = NOTION_DB_ID
    if updates:
        _write_env(updates)
        print(f"🔑 Keys updated: {list(updates.keys())}")
    return JSONResponse({"success": True})  # ← never returns key values

@app.get("/get-context")
async def get_context():
    content = CONTEXT_FILE.read_text(encoding="utf-8") if CONTEXT_FILE.exists() else ""
    return JSONResponse({"content": content})

@app.post("/save-context")
async def save_context_endpoint(request: dict):
    content = request.get("content", "")
    CONTEXT_FILE.write_text(content, encoding="utf-8")
    print("✅ context.md updated")
    return JSONResponse({"success": True})


@app.get("/test")
async def test_apis():
    results = {}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post("https://api.sarvam.ai/speech-to-text",
                             headers={"API-Subscription-Key": SARVAM_API_KEY})
            results["sarvam"] = "✅ Key works" if r.status_code in (400, 422) else f"❌ {r.status_code}"
    except Exception as e:
        results["sarvam"] = f"❌ {e}"
    try:
        cl = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = cl.messages.create(model="claude-sonnet-4-20250514", max_tokens=10,
                                  messages=[{"role": "user", "content": "Say OK"}])
        results["claude"] = f"✅ {msg.content[0].text}"
    except Exception as e:
        results["claude"] = f"❌ {e}"
    results["notion"] = "✅ Configured" if (NOTION_KEY and NOTION_DB_ID) else "⚠️ Not configured (optional)"
    return JSONResponse(results)

# ─── Save Metadata (participant, type — sent before recording starts) ──
@app.post("/set-meta")
async def set_meta(request: dict):
    current_session["meta"] = request
    return JSONResponse({"success": True})

# ─── Chunk Save ───────────────────────────────────────────────
@app.post("/save-chunk")
async def save_chunk(audio: UploadFile = File(...)):
    try:
        audio_bytes = await audio.read()
        meeting_dir = get_or_create_session_dir()
        chunk_num = len(current_session["chunks"]) + 1
        chunk_name = f"chunk_{chunk_num:02d}.webm"
        (meeting_dir / chunk_name).write_bytes(audio_bytes)
        current_session["chunks"].append(chunk_name)
        print(f"💾 Chunk {chunk_num}: {len(audio_bytes)/(1024*1024):.1f} MB")
        return JSONResponse({"success": True, "chunk_num": chunk_num})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

# ─── Process Single Audio ─────────────────────────────────────
@app.post("/process")
async def process_audio(audio: UploadFile = File(...)):
    try:
        audio_bytes = await audio.read()
        filename = audio.filename or "meeting.webm"
        content_type = audio.content_type or "audio/webm"
        meta = current_session.get("meta", {})

        meeting_dir = get_or_create_session_dir()
        (meeting_dir / filename).write_bytes(audio_bytes)

        print(f"\n{'='*50}")
        print(f"🎙️  {meta.get('participant','Unknown')} — {meta.get('meeting_type','Meeting')}")
        print(f"{'='*50}")

        # Transcribe
        print(f"📤 Transcribing ({len(audio_bytes)/(1024*1024):.1f} MB)...")
        transcript = await transcribe_with_sarvam(audio_bytes, filename, content_type)
        if not transcript or not transcript.strip():
            raise Exception("Empty transcript. Audio may be too short or silent.")
        (meeting_dir / "transcript.txt").write_text(transcript, encoding="utf-8")
        print(f"✅ Transcript: {len(transcript)} chars")

        # Summarise
        print(f"🧠 Generating notes...")
        notes = await generate_notes(transcript, meta)
        (meeting_dir / "notes.md").write_text(notes, encoding="utf-8")
        print(f"✅ Notes: {len(notes)} chars")

        # Save meta
        json.dump(meta, open(meeting_dir / "meta.json", "w"), indent=2)

        # Reset
        current_session["dir"] = None
        current_session["chunks"] = []

        print(f"✅ Saved to: {meeting_dir}\n{'='*50}\n")
        return JSONResponse({"success": True, "notes": notes, "transcript": transcript,
                             "saved_to": str(meeting_dir)})
    except Exception as e:
        print(f"❌ {e}"); traceback.print_exc()
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

# ─── Process Multiple Chunks ──────────────────────────────────
@app.post("/process-chunks")
async def process_chunks():
    try:
        meeting_dir = Path(current_session["dir"]) if current_session["dir"] else None
        chunks = current_session["chunks"]
        meta = current_session.get("meta", {})
        if not meeting_dir or not chunks:
            return JSONResponse({"success": False, "error": "No chunks"}, status_code=400)

        print(f"\n{'='*50}")
        print(f"🎙️  {len(chunks)} chunks — {meta.get('participant','Unknown')}")
        print(f"{'='*50}")

        # Transcribe all in parallel
        tasks = []
        for cn in chunks:
            cp = meeting_dir / cn
            if cp.exists():
                tasks.append(transcribe_with_sarvam(cp.read_bytes(), cn, "audio/webm"))
        results = await asyncio.gather(*tasks, return_exceptions=True)

        transcripts = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                print(f"   ❌ Chunk {i+1}: {r}")
                transcripts.append(f"[Chunk {i+1} failed]")
            else:
                print(f"   ✅ Chunk {i+1}: {len(r)} chars")
                transcripts.append(r)

        combined = "\n\n".join(f"--- PART {i+1} ---\n{t}" for i, t in enumerate(transcripts)) if len(transcripts) > 1 else transcripts[0]
        if not combined.strip(): raise Exception("All transcriptions empty")

        (meeting_dir / "transcript.txt").write_text(combined, encoding="utf-8")
        for i, t in enumerate(transcripts):
            (meeting_dir / f"transcript_chunk_{i+1:02d}.txt").write_text(t, encoding="utf-8")

        notes = await generate_notes(combined, meta)
        (meeting_dir / "notes.md").write_text(notes, encoding="utf-8")
        json.dump(meta, open(meeting_dir / "meta.json", "w"), indent=2)

        current_session["dir"] = None
        current_session["chunks"] = []

        print(f"✅ Saved to: {meeting_dir}\n{'='*50}\n")
        return JSONResponse({"success": True, "notes": notes, "transcript": combined,
                             "saved_to": str(meeting_dir), "chunks_processed": len(chunks)})
    except Exception as e:
        print(f"❌ {e}"); traceback.print_exc()
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

# ─── Notion Push ──────────────────────────────────────────────
async def _get_db_title_prop(client: httpx.AsyncClient, headers: dict) -> str:
    """Return the name of the title-type property in the Meetings DB (usually 'Meeting' or 'Name')."""
    try:
        r = await client.get(f"https://api.notion.com/v1/databases/{NOTION_DB_ID}", headers=headers)
        if r.is_success:
            props = r.json().get("properties", {})
            for name, schema in props.items():
                if schema.get("type") == "title":
                    return name
    except Exception:
        pass
    return "Meeting"  # sensible default

async def _push_meeting_to_notion(notes: str, title: str, participant: str, meeting_type: str, others: list) -> dict:
    """Internal: push meeting notes to Notion Meetings DB. Returns {success, url, page_id}."""
    summary = parse_meeting_meta(notes).get("summary", "")[:2000]
    blocks = md_to_notion_blocks(notes)
    headers = {"Authorization": f"Bearer {NOTION_KEY}", "Content-Type": "application/json", "Notion-Version": "2022-06-28"}

    async with httpx.AsyncClient(timeout=30) as client:
        title_prop = await _get_db_title_prop(client, headers)

        def build_page(full: bool) -> dict:
            props = {title_prop: {"title": [{"text": {"content": title[:100]}}]}}
            if full:
                props["Date"] = {"date": {"start": datetime.now().strftime("%Y-%m-%d")}}
                if summary:
                    props["Summary"] = {"rich_text": [{"text": {"content": summary}}]}
                if participant:
                    props["Participant"] = {"select": {"name": participant}}
                if meeting_type:
                    props["Type"] = {"select": {"name": meeting_type}}
                if others:
                    props["Other Attendees"] = {"multi_select": [{"name": n} for n in others]}
            return {"parent": {"database_id": NOTION_DB_ID}, "icon": {"emoji": "📝"},
                    "properties": props, "children": blocks[:100]}

        # First attempt: all properties
        resp = await client.post("https://api.notion.com/v1/pages", headers=headers, json=build_page(full=True))
        if resp.is_success:
            data = resp.json()
            return {"success": True, "url": data.get("url", ""), "page_id": data.get("id", "")}

        # Second attempt: title only (column names in the DB may differ)
        err = resp.text[:500]
        print(f"⚠️  Notion full push failed ({resp.status_code}): {err[:120]}")
        print("   Retrying with title-only push...")
        resp2 = await client.post("https://api.notion.com/v1/pages", headers=headers, json=build_page(full=False))
        if resp2.is_success:
            data = resp2.json()
            return {"success": True, "url": data.get("url", ""), "page_id": data.get("id", ""),
                    "warning": "Some columns weren't found in your Meetings DB — only the title was saved. Check your DB column names match: Date, Summary, Participant, Type."}
        return {"success": False, "error": resp2.text[:500]}

@app.post("/push-notion")
async def push_to_notion(request: dict):
    if not NOTION_KEY or not NOTION_DB_ID:
        return JSONResponse({"success": False, "error": "Notion keys not set. Open ⚙️ Setup → API Keys to add them."}, status_code=400)
    try:
        notes = request.get("notes", "")
        # Extract participant + meeting type from Claude's notes output
        meta = parse_meeting_meta(notes)
        participant = meta["participant"]
        meeting_type = meta["meeting_type"]
        # Build title generically from whatever Claude extracted
        date_str = datetime.now().strftime("%d %b %Y")
        heading = next((l.lstrip("# ").strip() for l in notes.split("\n") if l.startswith("## ")), "")
        if participant and meeting_type:
            title = f"{meeting_type} | {participant}"
        elif participant:
            title = f"Meeting | {participant}"
        elif meeting_type:
            title = meeting_type
        else:
            title = heading or f"Meeting — {date_str}"
        print(f"📤 Pushing to Notion: {title}")
        result = await _push_meeting_to_notion(
            notes=notes,
            title=title,
            participant=participant,
            meeting_type=meeting_type,
            others=request.get("others", []),
        )
        if result["success"]:
            print(f"✅ Notion: {result['url']}")
            page_id = result.get("page_id", "")
            if page_id and NOTION_ACTION_ITEMS_DB_ID:
                action_items = parse_action_items(notes)
                if action_items:
                    await push_action_items_to_notion(action_items, page_id, meeting_type)
        return JSONResponse(result)
    except Exception as e:
        print(f"❌ Notion: {e}"); traceback.print_exc()
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

# ─── Notion Action Items Push ─────────────────────────────────
async def push_action_items_to_notion(action_items: list, meeting_page_id: str, meeting_type: str):
    """Push individual action item rows to the Action Items DB, linked to the source meeting."""
    if not NOTION_ACTION_ITEMS_DB_ID or not action_items:
        return
    headers = {"Authorization": f"Bearer {NOTION_KEY}", "Content-Type": "application/json", "Notion-Version": "2022-06-28"}
    async with httpx.AsyncClient(timeout=30) as client:
        for item in action_items:
            try:
                props = {
                    "Task":         {"title": [{"text": {"content": item["task"][:2000]}}]},
                    "Status":       {"select": {"name": "Pending"}},
                    "Date":         {"date": {"start": datetime.now().strftime("%Y-%m-%d")}},
                    "Meeting":      {"relation": [{"id": meeting_page_id}]},
                    "Meeting Type": {"select": {"name": meeting_type or "Other"}},
                }
                if item.get("owner"):
                    props["Owner"] = {"select": {"name": item["owner"][:100]}}
                by_when = item.get("by_when", "").strip()
                if by_when and by_when.lower() not in ("", "tbd", "-", "n/a", "asap"):
                    # Try to parse a date — if it fails, skip the date field
                    try:
                        from dateutil import parser as dateparser
                        parsed = dateparser.parse(by_when, dayfirst=True)
                        props["By When"] = {"date": {"start": parsed.strftime("%Y-%m-%d")}}
                    except Exception:
                        pass
                r = await client.post("https://api.notion.com/v1/pages", headers=headers,
                    json={"parent": {"database_id": NOTION_ACTION_ITEMS_DB_ID}, "properties": props})
                if not r.is_success:
                    print(f"   ⚠️  Action item push failed: {r.text[:200]}")
            except Exception as e:
                print(f"   ⚠️  Action item row error: {e}")
    print(f"✅ Action items pushed: {len(action_items)}")

@app.post("/add-context")
async def add_context(request: dict):
    text = request.get("text", "").strip()
    if not text: return JSONResponse({"error": "empty"}, status_code=400)
    with open(CONTEXT_FILE, "a") as f: f.write(f"\n- {text}")
    return JSONResponse({"success": True})


# ─── Past Meetings (Load & Push) ─────────────────────────────
@app.get("/past-meetings")
async def list_past_meetings():
    """List all past meetings that have notes.md files."""
    meetings = []
    for month_dir in sorted(OUTPUT_DIR.iterdir(), reverse=True):
        if not month_dir.is_dir():
            continue
        for meeting_dir in sorted(month_dir.iterdir(), reverse=True):
            if not meeting_dir.is_dir():
                continue
            notes_file = meeting_dir / "notes.md"
            meta_file = meeting_dir / "meta.json"
            if not notes_file.exists():
                continue
            # Extract date from folder name (YYYY-MM-DD_HH-MM)
            folder_name = meeting_dir.name
            meta = {}
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except:
                    pass
            # First heading as title
            notes_text = notes_file.read_text(encoding="utf-8")
            title_match = None
            for line in notes_text.split("\n"):
                if line.startswith("## "):
                    title_match = line.lstrip("# ").strip()
                    break
            meetings.append({
                "path": str(meeting_dir),
                "folder": folder_name,
                "date": folder_name[:10] if len(folder_name) >= 10 else folder_name,
                "time": folder_name[11:].replace("-", ":") if len(folder_name) > 11 else "",
                "title": title_match or folder_name,
                "participant": meta.get("participant", ""),
                "meeting_type": meta.get("meeting_type", ""),
                "has_transcript": (meeting_dir / "transcript.txt").exists(),
            })
    return JSONResponse({"meetings": meetings})


@app.get("/load-meeting")
async def load_meeting(path: str):
    """Load notes and transcript from a past meeting."""
    meeting_dir = Path(path)
    if not meeting_dir.exists() or not meeting_dir.is_dir():
        return JSONResponse({"error": "Meeting not found"}, status_code=404)
    # Security: make sure it's inside our meetings directory
    try:
        meeting_dir.resolve().relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        return JSONResponse({"error": "Invalid path"}, status_code=400)

    notes = ""
    transcript = ""
    meta = {}
    notes_file = meeting_dir / "notes.md"
    transcript_file = meeting_dir / "transcript.txt"
    meta_file = meeting_dir / "meta.json"

    if notes_file.exists():
        notes = notes_file.read_text(encoding="utf-8")
    if transcript_file.exists():
        transcript = transcript_file.read_text(encoding="utf-8")
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except:
            pass

    return JSONResponse({
        "success": True,
        "notes": notes,
        "transcript": transcript,
        "participant": meta.get("participant", ""),
        "meeting_type": meta.get("meeting_type", ""),
        "saved_to": str(meeting_dir),
    })

# ─── Sarvam STT ───────────────────────────────────────────────
async def transcribe_with_sarvam(audio_bytes, filename, content_type) -> str:
    if len(audio_bytes) > 100_000:
        return await _sarvam_batch(audio_bytes, filename)
    return await _sarvam_rest(audio_bytes, filename, content_type)

async def _sarvam_rest(audio_bytes, filename, content_type) -> str:
    if not SARVAM_API_KEY:
        raise Exception("Sarvam API key not set. Click ⚠️ Setup in the app to add it.")
    print(f"   REST ({len(audio_bytes)} bytes)")
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post("https://api.sarvam.ai/speech-to-text",
            headers={"API-Subscription-Key": SARVAM_API_KEY},
            files={"file": (filename, audio_bytes, content_type)},
            data={"model": "saaras:v3", "language_code": "unknown", "mode": "translate"})
        if not r.is_success: raise Exception(f"Sarvam {r.status_code}: {r.text[:300]}")
        return r.json().get("transcript", "")

async def _sarvam_batch(audio_bytes, filename) -> str:
    print(f"   Batch SDK ({len(audio_bytes)} bytes)")
    tp = Path(tempfile.mktemp(suffix=f"_{filename}"))
    tp.write_bytes(audio_bytes)
    try:
        from sarvamai import SarvamAI
        s = SarvamAI(api_subscription_key=SARVAM_API_KEY)
        job = s.speech_to_text_job.create_job(model="saaras:v3", mode="translate", language_code="unknown", with_diarization=True)
        print(f"   Job: {job.job_id}")
        job.upload_files(file_paths=[str(tp)]); job.start()
        print("   Waiting...")
        await asyncio.get_event_loop().run_in_executor(None, job.wait_until_complete)
        fr = job.get_file_results()
        if fr.get("successful"):
            od = tempfile.mkdtemp(); job.download_outputs(output_dir=od)
            fs = list(Path(od).glob("*"))
            if fs:
                raw = fs[0].read_text(encoding="utf-8")
                try:
                    d = json.loads(raw)
                    if isinstance(d, dict) and "transcript" in d: return d["transcript"]
                    if isinstance(d, list): return " ".join(i.get("transcript","") for i in d if isinstance(i, dict))
                except json.JSONDecodeError: pass
                return raw
        failed = fr.get("failed", [])
        raise Exception(f"Batch failed: {failed[0].get('error_message','?') if failed else '?'}")
    finally:
        tp.unlink(missing_ok=True)

# ─── Claude: Single-stage notes generation ──────────────────
async def generate_notes(transcript: str, meta: dict = None) -> str:
    """Single Claude call: system prompt (context.md) + optional pre-meeting context + transcript → notes."""
    if not ANTHROPIC_API_KEY:
        raise Exception("Anthropic API key not set. Click ⚠️ Setup in the app to add it.")
    cl = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    print("   Generating notes...")
    # Prepend any pre-meeting context the user typed before recording
    prefix = ""
    if meta:
        topic = meta.get("topic", "").strip()
        participants = meta.get("participants", "").strip()
        if topic or participants:
            lines = ["[Context provided by the organiser before recording:]"]
            if topic:        lines.append(f"Topic/agenda: {topic}")
            if participants: lines.append(f"Participants: {participants}")
            prefix = "\n".join(lines) + "\n\n"
    user_content = prefix + f"Meeting transcript:\n\n{transcript}"
    msg = cl.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=load_system_prompt(),
        messages=[{"role": "user", "content": user_content}],
    )
    notes = "".join(b.text for b in msg.content if b.type == "text")
    return notes

# ─── Notion Helpers ───────────────────────────────────────────
def md_to_notion_blocks(md_text: str) -> list:
    blocks = []
    lines = md_text.split("\n")
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s or s == "---": i += 1; continue

        if s.startswith("## "): blocks.append({"object":"block","type":"heading_2","heading_2":{"rich_text":rt(s[3:])}})
        elif s.startswith("### "): blocks.append({"object":"block","type":"heading_3","heading_3":{"rich_text":rt(s[4:])}})
        elif s.startswith("- ") or s.startswith("• "):
            blocks.append({"object":"block","type":"bulleted_list_item","bulleted_list_item":{"rich_text":rt(s[2:])}})
        elif s.startswith("|") and s.endswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().split("|")[1:-1]]
                if not all(set(c) <= set("- :") for c in cells): rows.append(cells)
                i += 1
            i -= 1
            if rows:
                nc = max(len(r) for r in rows)
                tb = {"object":"block","type":"table","table":{"table_width":nc,"has_column_header":True,"has_row_header":False,"children":[]}}
                for row in rows:
                    while len(row) < nc: row.append("")
                    tb["table"]["children"].append({"type":"table_row","table_row":{
                        "cells":[[{"type":"text","text":{"content":c[:2000]}}] for c in row[:nc]]}})
                blocks.append(tb)
        else: blocks.append({"object":"block","type":"paragraph","paragraph":{"rich_text":rt(s)}})
        i += 1
    return blocks

def rt(text: str) -> list:
    parts = re.split(r'(\*\*.*?\*\*)', text)
    result = []
    for p in parts:
        if p.startswith("**") and p.endswith("**"):
            result.append({"type":"text","text":{"content":p[2:-2]},"annotations":{"bold":True}})
        elif p:
            result.append({"type":"text","text":{"content":p}})
    return result or [{"type":"text","text":{"content":text}}]

# ─── Run ──────────────────────────────────────────────────────
def get_local_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]; s.close(); return ip
    except: return "unknown"

if __name__ == "__main__":
    ip = get_local_ip()
    print("="*50)
    print("🎙️  Meeting Notes AI")
    print("="*50)
    if SARVAM_API_KEY:    print(f"✅ Sarvam:  {SARVAM_API_KEY[:6]}···{SARVAM_API_KEY[-4:]}")
    else:                 print(f"⚠️  Sarvam:  not set — configure at http://localhost:{PORT}")
    if ANTHROPIC_API_KEY: print(f"✅ Claude:  {ANTHROPIC_API_KEY[:6]}···{ANTHROPIC_API_KEY[-4:]}")
    else:                 print(f"⚠️  Claude:  not set — configure at http://localhost:{PORT}")
    print(f"{'✅' if NOTION_KEY else '⚠️'} Notion: {'configured' if NOTION_KEY else 'not configured (optional)'}")
    if CONTEXT_FILE.exists(): print(f"✅ Context: {len(CONTEXT_FILE.read_text().splitlines())} lines")
    print(f"📁 Saves: {OUTPUT_DIR}")
    print(f"\n🖥️  http://localhost:{PORT}")
    print(f"📱 http://{ip}:{PORT}")
    print(f"🧪 http://localhost:{PORT}/test")
    print(f"\nCtrl+C to stop")
    print("="*50)
    import threading
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
