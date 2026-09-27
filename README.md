# 🔬 Agentic Research Assistant

An autonomous AI research agent that searches the web, reads sources, curates YouTube videos, and delivers a structured report — fully autonomously, through a modern web UI.

Built with **Model Context Protocol (MCP)**, **FastMCP**, **LiteLLM**, **Gemini**, and **FastAPI**.



---

## ✨ What It Does

Give it a research question. It autonomously:

1. 🔍 Searches the web across multiple queries
2. 📄 Reads and extracts content from 6–8 sources using `trafilatura`
3. 💾 Saves structured findings to SQLite (persists across sessions)
4. ▶️ Searches YouTube for the best related videos
5. 📝 Synthesises everything into a detailed markdown report
6. 📊 Renders the report live in the browser with PDF export

**Zero human intervention between question and report.**

---

## 🖥️ Web UI

| Home Screen | Research In Progress | Completed Report |
|---|---|---|
| Question input with suggestion chips | Animated orb with live phase tracking | Full rendered markdown with stats |

- Animated orb shows exactly what the agent is doing in real time
- Phase pills (Searching → Reading → Videos → Writing) update as research progresses
- Findings progress bar fills as sources are saved
- Past sessions sidebar (completed sessions only)
- Copy to clipboard + PDF download

---

## 🏗️ Architecture

┌──────────────────────────────────────────────────────────┐
│ Web UI (ui/index.html) │
│ SSE streaming · Orb animation · PDF export │
└─────────────────────────┬────────────────────────────────┘
│ HTTP + Server-Sent Events
┌─────────────────────────▼────────────────────────────────┐
│ FastAPI Backend (server.py) │
│ Streams agent events to UI in real time │
│ /health · /api/research │
│ /api/sessions · /api/report/{id} │
└─────────────────────────┬────────────────────────────────┘
│
┌─────────────────────────▼────────────────────────────────┐
│ Agent Loop — GAME Framework (agent.py) │
│ │
│ G — Goals persona.md (Document-as-Impl.) │
│ A — Actions 6 MCP tools (Horizontal Scaling) │
│ M — Memory Pair-aware trim (Gemini-safe, 45 pairs)│
│ E — Environment TOOL_FUNCTIONS dict │
│ │
│ litellm.acompletion → gemini-3.1-flash-lite │
│ → gemini-3.5-flash-lite (fallback) │
└─────────────────────────┬────────────────────────────────┘
│ native async function calling
┌─────────────────────────▼────────────────────────────────┐
│ FastMCP Server (tools/mcp_server.py) │
│ │
│ web_search DuckDuckGo search │
│ fetch_page_content trafilatura + regex fallback │
│ save_finding SQLite persistence │
│ list_findings SQLite read │
│ search_youtube YouTube Data API v3 │
│ write_report Ground-truth table rebuild + disk │
└─────────────────────────┬────────────────────────────────┘
│
┌─────────────────────────▼────────────────────────────────┐
│ SQLite Database (data/research.db) │
│ sessions · findings · reports │
└──────────────────────────────────────────────────────────┘


---

## 🧠 Design Patterns Applied

| Pattern | Where | What It Does |
|---|---|---|
| **GAME Framework** | `agent.py` | Goals / Actions / Memory / Environment loop |
| **Horizontal Scaling** | 6 MCP tools | Each tool does exactly one job |
| **Document-as-Implementation** | `persona.md` | Agent rules editable without touching code |
| **Response-as-Instruction** | Every tool return | `next_action` + `hint` guide the agent's next step |
| **Failing Forward** | Error returns | Agent self-corrects, never crashes |
| **Validate at Source** | Inside each tool | Bad input caught and explained before execution |
| **Intelligence Budget** | Pair-aware memory trim | Gemini-safe — never orphans a tool call/result pair |
| **LLM Fallback** | `call_llm_async()` | Primary → fallback model on any failure |
| **AI Shim** | YouTube table sanitiser | Rebuilds table from ground-truth API data, bypassing LLM formatting errors |
| **Self-Prompting** | Tool hints | Every tool return tells the agent exactly what to do next |

---

## 📁 Project Structure

research_agent/
├── server.py # FastAPI backend — SSE streaming, REST API
├── main.py # CLI entry point (no UI needed)
├── requirements.txt
├── .env.example
│
├── agent/
│ ├── agent.py # GAME loop · litellm.acompletion · pair-aware memory
│ └── persona.md # Agent rules — edit without touching Python
│
├── tools/
│ └── mcp_server.py # 6 FastMCP tools + YouTube sanitiser
│
├── data/
│ ├── database.py # Thread-safe SQLite (thread-local connections)
│ └── research.db # Auto-created on first run [git-ignored]
│
├── ui/
│ └── index.html # Complete web UI — self-contained single file
│
└── reports/ # Generated .md reports [git-ignored]
└── .gitkeep


---

## ⚡ Quick Start

### 1. Clone and install

```bash
git clone https://github.com/Pasindulakshan2001918/Research-Agent.git
cd Research-Agent
pip install -r requirements.txt
```

### 2. Get API keys

**Gemini API key** — free at https://aistudio.google.com

**YouTube Data API v3 key** — free at https://console.cloud.google.com
- Create project → Enable APIs → search "YouTube Data API v3" → Credentials → Create API Key

### 3. Configure

```bash
cp .env.example .env
# Open .env and add your keys
```

```env
GEMINI_API_KEY=your_gemini_key_here
YOUTUBE_API_KEY=your_youtube_key_here
```

### 4. Run

```bash
# Web UI (recommended)
python server.py
# Opens http://localhost:8000 automatically

# CLI mode
python main.py "What is the future of quantum computing?"
```

---

## ⚙️ Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | ✅ | — | From Google AI Studio |
| `YOUTUBE_API_KEY` | ✅ | — | YouTube Data API v3 |
| `PRIMARY_MODEL` | ❌ | `gemini/gemini-3.1-flash-lite` | Primary LLM |
| `FALLBACK_MODEL` | ❌ | `gemini/gemini-3.5-flash-lite` | Fallback LLM |
| `PORT` | ❌ | `8000` | Server port |
| `DEV_MODE` | ❌ | `true` | Auto-opens browser if `true` |

---

## 🔌 API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Web UI |
| `GET` | `/health` | Health check |
| `POST` | `/api/research` | Start research session (SSE stream) |
| `GET` | `/api/sessions` | List completed sessions |
| `GET` | `/api/report/{id}` | Get session report |
| `GET` | `/api/status/{id}` | Get session status |

---

## 🛠️ Technical Highlights

**True async LLM calls** — uses `litellm.acompletion` (native coroutine), not `asyncio.to_thread`. Under 20+ concurrent users, thread-pool approaches freeze the server. Native async scales freely.

**Pair-aware memory trimming** — Gemini requires every `assistant` tool-call message to be immediately followed by its `tool` result. A naive slice-based trimmer breaks this pairing and causes `400 INVALID_ARGUMENT` errors. The trimmer here groups messages into atomic pairs before trimming, making it structurally impossible to orphan a call.

**Production HTML extraction** — uses `trafilatura` instead of regex. Regex-based HTML parsing silently drops content on malformed tags and is completely blind to JS-rendered pages. Trafilatura handles boilerplate removal, complex layouts, and structured content correctly.

**YouTube table sanitiser** — the LLM occasionally misaligns table columns when video titles contain pipe characters. The `_inject_youtube_table` function replaces the agent's markdown with a table rebuilt directly from the raw YouTube API response — bypassing LLM formatting entirely.

**Thread-safe SQLite** — uses thread-local connections to prevent the `ResourceWarning: unclosed database` flood that occurs under FastAPI's async request handling.

---

## 📚 Built From

- **AI Agents and Agentic AI Architecture in Python** — Vanderbilt University (Coursera)
- **AI Agents and Agentic AI with Python & Generative AI** — Vanderbilt University (Coursera)
- **AI Agents with Model Context Protocol** — Vanderbilt University (Coursera)
- **Introduction to Model Context Protocol** — Anthropic (Coursera)

---

## 📄 License

MIT — free to use, modify, and distribute.