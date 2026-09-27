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

## 🖥️ Web UI Features

- Animated orb shows exactly what the agent is doing in real time
- Phase pills: **Searching → Reading → Videos → Writing** — each lights up as it completes
- Findings progress bar fills as sources are saved
- Past sessions sidebar (completed sessions only)
- Copy to clipboard + PDF download

---

## 🏗️ Architecture

User (Web UI)
│
│ HTTP + Server-Sent Events (SSE)
▼
FastAPI Backend ── server.py
│
│ async function calls
▼
Agent Loop ── agent.py (GAME Framework)
│
├── G — Goals persona.md (Document-as-Implementation)
├── A — Actions 6 MCP tools (Horizontal Scaling)
├── M — Memory Pair-aware trim, 45 pairs (Gemini-safe)
└── E — Environment TOOL_FUNCTIONS dict
│
│ litellm.acompletion → gemini-2.0-flash-lite (primary)
│ → gemini-2.0-flash (fallback)
│
│ native async function calling
▼
FastMCP Server ── tools/mcp_server.py
│
├── web_search DuckDuckGo search
├── fetch_page_content trafilatura + regex fallback
├── save_finding SQLite persistence
├── list_findings SQLite read
├── search_youtube YouTube Data API v3
└── write_report Ground-truth table rebuild + disk write
│
▼
SQLite Database ── data/research.db
├── sessions
├── findings
└── reports


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
| **AI Shim** | YouTube table sanitiser | Rebuilds table from ground-truth API data |
| **Self-Prompting** | Tool hints | Every tool return tells the agent what to do next |

---

## 📁 Project Structure

Research-Agent/
├── server.py # FastAPI backend — SSE streaming, REST API
├── main.py # CLI entry point
├── requirements.txt
├── .env.example
│
├── agent/
│ ├── agent.py # GAME loop, litellm.acompletion, pair-aware memory
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
└── reports/ # Generated reports [git-ignored]
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

**Gemini API key** — free at [https://aistudio.google.com](https://aistudio.google.com)

**YouTube Data API v3 key** — free at [https://console.cloud.google.com](https://console.cloud.google.com)
- Create project → Enable APIs → search **YouTube Data API v3** → Enable
- Credentials → Create API Key → copy it

### 3. Configure

```bash
cp .env.example .env
```

Open `.env` and fill in your keys:

```env
GEMINI_API_KEY=your_gemini_key_here
YOUTUBE_API_KEY=your_youtube_key_here
```

### 4. Run

```bash
# Web UI (recommended — opens browser automatically)
python server.py

# CLI mode
python main.py "What is the future of quantum computing?"
```

---

## ⚙️ Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | ✅ | — | From Google AI Studio |
| `YOUTUBE_API_KEY` | ✅ | — | YouTube Data API v3 |
| `PRIMARY_MODEL` | ❌ | `gemini/gemini-2.0-flash-lite` | Primary LLM |
| `FALLBACK_MODEL` | ❌ | `gemini/gemini-2.0-flash` | Fallback LLM |
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

**True async LLM calls**
Uses `litellm.acompletion` (native coroutine) instead of `asyncio.to_thread`. Under 20+ concurrent users, thread-pool approaches exhaust the `ThreadPoolExecutor` and freeze the server. Native async scales freely.

**Pair-aware memory trimming**
Gemini requires every `assistant` tool-call message to be immediately followed by its `tool` result. A naive slice-based trimmer breaks this pairing and causes `400 INVALID_ARGUMENT` errors. The trimmer groups messages into atomic pairs before trimming — making it structurally impossible to orphan a call.

**Production HTML extraction**
Uses `trafilatura` instead of regex. Regex-based HTML parsing silently drops content on malformed tags and is completely blind to JS-rendered pages. Trafilatura handles boilerplate removal, complex layouts, and structured content correctly.

**YouTube table sanitiser**
The LLM occasionally misaligns table columns when video titles contain pipe characters. The `_inject_youtube_table` function replaces the agent's markdown with a table rebuilt directly from the raw YouTube API response — bypassing LLM formatting entirely.

**Thread-safe SQLite**
Uses thread-local connections to prevent the `ResourceWarning: unclosed database` flood that occurs under FastAPI's async request handling.

---


---

## 📄 License

MIT — free to use, modify, and distribute.