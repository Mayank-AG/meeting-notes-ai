# 🎙️ Meeting Notes AI

Record any meeting → AI transcribes and generates structured notes → push to Notion with action items tracked automatically.

Built for teams that mix languages (Hindi/English, regional + English). Works for English-only meetings too.

**Your audio never leaves your machine.** All processing uses your own API keys — Mayank pays nothing.

---

## What it does

1. **Record** — Hit the mic button in your browser. Long meetings are automatically split into 55-minute chunks — no babysitting required.
2. **Transcribe** — Sarvam AI converts speech to English text (handles Hinglish/code-switching natively).
3. **Notes** — Claude generates structured notes: discussion points, decisions, action items, and a clean execution summary.
4. **Push to Notion** — One click pushes the full meeting page + individual action item rows to your Notion workspace.
5. **Browse past meetings** — All your previous meetings are saved locally and accessible from the app anytime.

> **Sarvam down?** Use the **Upload Audio** button to drop in a recorded file and process it manually — the full pipeline still runs.

---

## Setup (4 steps, ~4 minutes)

### Step 1 — Download
Click **Code → Download ZIP** on this page. Extract the ZIP — it will create a folder called `meeting-notes-ai-main`.

Move it somewhere permanent (Desktop is fine):
```bash
mv ~/Downloads/meeting-notes-ai-main ~/Desktop/meeting-notes-ai
```

### Step 2 — Run setup

Open Terminal and run:

```bash
cd ~/Desktop/meeting-notes-ai
./setup.sh
```

This creates a virtual environment, installs packages, and asks for your API keys. Takes about 2 minutes.

> **Windows?** Install [Python](https://python.org/downloads) + run `python -m venv venv && venv\Scripts\activate && pip install -r requirements.txt` then copy `.env.example` to `.env` and fill in your keys.

### Step 3 — Start the app

```bash
cd ~/Desktop/meeting-notes-ai
./start.sh
```

Browser opens automatically. If your keys aren't set yet, the app opens a **Setup screen** — follow the step-by-step instructions there to add your keys. No manual file editing needed.

### Step 4 — Customise for your team

Open the **⚙ Setup** panel → **Context** tab. Paste in your team members, products, and any jargon. The AI reads this before every meeting to attribute ownership, recognise names, and understand your domain.

---

## API keys

| Key | Where | Cost per meeting |
|---|---|---|
| **Sarvam AI** (required) | [sarvam.ai](https://sarvam.ai) → Dashboard → API Keys | ~₹0.40 / min of audio |
| **Anthropic Claude** (required) | [console.anthropic.com](https://console.anthropic.com) | ~$0.003 |
| **Notion** (optional) | See below | Free |

Keys are saved to a `.env` file on your machine. They are never sent anywhere except to the respective service.

---

## Notion setup (optional)

To push notes and action items to Notion:

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations) → New integration → copy the token
2. Create a **Meetings** database in Notion (or use an existing one)
3. Share the database with your integration (open the DB → top right → Connect to → your integration)
4. Copy the database ID from the URL: `notion.so/[workspace]/`**`this-long-id`**`?v=...`
5. Enter both in the app's Setup screen (or in your `.env`):
   ```
   NOTION_KEY=secret_xxx
   NOTION_DB_ID=your-database-id
   ```

**You only create one database.** A linked "Action Items" database is created automatically on your first push — you don't need to set anything else up.

---

## Port conflicts

The app auto-finds a free port starting at 8000. If 8000 is taken, it tries 8001, 8002, and so on — no crash, no manual config.

To pin a specific port (e.g. if another app always uses 8000), add this to your `.env`:

```
PORT=8001
```

---

## Where your data lives

Everything is stored on your computer inside the `meetings/` folder:

```
meeting-notes-ai/
└── meetings/
    ├── 2026-04-19_14-30/
    │   ├── recording.webm
    │   ├── transcript.txt
    │   └── notes.md
    └── ...
```

Nothing is sent to any server except the transcription (Sarvam) and note generation (Anthropic) API calls. Those services process and return your data — they do not store it for this app.

---

## Privacy & security

- **Audio**: Sent to Sarvam AI for transcription only. Stored locally in `meetings/` after processing.
- **Transcript + Notes**: Sent to Anthropic Claude for note generation. Subject to [Anthropic's privacy policy](https://www.anthropic.com/privacy).
- **API keys**: Saved to a local `.env` file (readable only by you). Displayed masked in the UI — never exposed to the browser in full.
- **CORS**: The app only accepts requests from `localhost` — other websites cannot read or modify your keys even if you have a malicious tab open.
- **Notion**: You push to your own workspace using your own credentials.

---

## Cost estimate

| Meeting length | Sarvam cost | Claude cost | Total |
|---|---|---|---|
| 30 min | ~₹12 | ~$0.003 | ~₹13 |
| 60 min | ~₹24 | ~$0.005 | ~₹25 |
| 90 min | ~₹36 | ~$0.008 | ~₹37 |

---

## Troubleshooting

**Mic not working?** → Make sure you're on `http://localhost:8000` (not `https`). Allow microphone access when the browser asks.

**Port already in use?** → The app will auto-increment to the next free port and print the URL. Or add `PORT=8001` to `.env` to fix it permanently.

**Setup fails?** → Make sure Python 3.8+ is installed: `python3 --version`

**Sarvam transcription failed?** → Use the **Upload Audio** button to manually upload the recorded file and retry.

**Notion push fails?** → Make sure you've shared the Meetings database with your Notion integration (open the DB → top-right menu → Connect to → your integration name).

---

*Built with [Sarvam AI](https://sarvam.ai) · [Anthropic Claude](https://anthropic.com) · [Notion API](https://developers.notion.com)*
