# 🔬 Agentic Research Assistant

An autonomous research agent built with **MCP (Model Context Protocol)**, **FastMCP**, **LiteLLM**, and **Gemini**. Given a research question, the agent independently searches the web, reads sources, saves structured findings to SQLite, and produces a polished markdown report — with zero human intervention between question and report.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        User / CLI                           │
│                  "Research question: ..."                   │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                     Agent Loop (GAME)                       │
│                                                             │
│  G — Goals    persona.md loaded at runtime                  │
│               (Document-as-Implementation pattern)          │
│                                                             │
│  A — Actions  5 MCP tools via TOOL_SCHEMAS                  │
│               (Horizontal Scaling — each tool = 1 job)      │
│                                                             │
│  M — Memory   Growing messages list (trimmed at 40 msgs)    │
│               (Intelligence Budget pattern)                 │
│                                                             │
│  E — Environment  TOOL_FUNCTIONS dict executes tools        │
│                                                             │
│  LiteLLM → gemini/gemini-2.0-flash-lite (primary)          │
│          → gemini/gemini-2.0-flash       (fallback)         │
└──────────────────────────┬──────────────────────────────────┘
                           │  function calling
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   FastMCP Server                            │
│                 (tools/mcp_server.py)                       │
│                                                             │
│  ┌─────────────┐  ┌──────────────────┐  ┌──────────────┐  │
│  │ web_search  │  │ fetch_page_      │  │ save_finding │  │
│  │             │  │ content          │  │              │  │
│  │ DuckDuckGo  │  │ httpx + HTML     │  │ SQLite       │  │
│  │ search API  │  │ cleaner          │  │ persistence  │  │
│  └─────────────┘  └──────────────────┘  └──────────────┘  │
│                                                             │
│  ┌─────────────────┐   ┌──────────────────────────────┐   │
│  │  list_findings  │   │       write_report           │   │
│  │                 │   │                              │   │
│  │  Read SQLite    │   │  SQLite save + .md file     │   │
│  │  all findings   │   │  written to reports/         │   │
│  └─────────────────┘   └──────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   SQLite Database                           │
│                  (data/research.db)                         │
│                                                             │
│   sessions    findings    reports                           │
│   ─────────   ────────    ───────                           │
│   id          id          id                                │
│   question    session_id  session_id                        │
│   created_at  summary     content                           │
│   status      source_url  created_at                        │
│               title                                         │
│               relevance                                     │
│               saved_at                                      │
└─────────────────────────────────────────────────────────────┘
```

### Agent Tool Sequence

```
START
  │
  ▼
web_search("query 1")
  │
  ├─► fetch_page_content(url_1) ──► save_finding(summary_1)
  ├─► fetch_page_content(url_2) ──► save_finding(summary_2)
  │
  ▼
web_search("query 2")  ← different angle
  │
  ├─► fetch_page_content(url_3) ──► save_finding(summary_3)
  ├─► fetch_page_content(url_4) ──► save_finding(summary_4)
  │
  ▼
web_search("query 3")  ← deeper or counterargument angle
  │
  └─► fetch_page_content(url_5) ──► save_finding(summary_5)
  │
  ▼
list_findings()  ← review all gathered
  │
  ▼
write_report()   ← synthesise + save .md
  │
  ▼
DONE ✅
```

---

## Design Patterns Applied

| Pattern | Where Used | Why |
|---|---|---|
| **Horizontal Scaling** | 5 separate focused tools | Agent stays simple; new capabilities = new tool |
| **Document-as-Implementation** | `persona.md` loaded at runtime | Rules change without touching code |
| **Response-as-Instruction** | Every tool return has `next_action` + `hint` | Agent always knows what to do next |
| **Failing Forward** | Error returns include recovery instructions | Agent self-corrects without crashing |
| **Intelligence Budget** | Memory trimmed at 40 messages | Context window never floods |
| **Validate at Source** | Tools reject bad input with actionable errors | Agent can't write empty findings |
| **GAME Framework** | `agent.py` — Goals/Actions/Memory/Environment | Clean modular architecture |
| **LLM Fallback** | Primary → Fallback model on failure | Production-grade reliability |

---

## Project Structure

```
research_agent/
├── main.py                  # CLI entry point
├── .env.example             # API key template
├── README.md
│
├── agent/
│   ├── agent.py             # Core GAME loop + LiteLLM calls
│   └── persona.md           # Agent rules (Document-as-Implementation)
│
├── tools/
│   └── mcp_server.py        # FastMCP server with 5 tools
│
├── data/
│   ├── database.py          # SQLite CRUD layer
│   └── research.db          # Auto-created on first run
│
└── reports/
    └── *.md                 # Generated research reports
```

---

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/your-username/research-agent
cd research_agent

pip install litellm fastmcp ddgs httpx python-dotenv
```

### 2. Configure your API key

```bash
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY
```

Get a free Gemini API key at: https://aistudio.google.com/

### 3. Run

```bash
# Interactive mode
python main.py

# Pass question directly
python main.py "What is the current state of quantum computing?"

# List past sessions
python main.py --list-sessions

# View a past report
python main.py --session session_20241201_143022_abc123
```

---

## Example Output

**Input:** `"What are the main challenges in deploying LLMs in production?"`

**Agent behaviour (what you see in terminal):**
```
============================================================
  RESEARCH AGENT
  Session : session_20241201_143022_abc123
  Question: What are the main challenges in deploying LLMs in production?
============================================================

[Iteration 1]
  → Tool: web_search
    Args: {"query": "LLM production deployment challenges 2024", ...}
    ✓ success — Pick 2-3 relevant URLs and call fetch_page_content.

[Iteration 2]
  → Tool: fetch_page_content
    Args: {"url": "https://example.com/llm-production"}
    ✓ success — Read the content. Extract key insight. Then call save_finding.

[Iteration 3]
  → Tool: save_finding
    Args: {"title": "Latency and Cost at Scale", ...}
    ✓ success — Finding #1 saved. Keep researching.

... (continues across ~15-20 iterations) ...

[Iteration 19]
  → Tool: write_report
    ✓ success — Report saved to reports/session_20241201_143022_abc123.md

============================================================
  ✅ Research complete!
  Report saved to: reports/session_20241201_143022_abc123.md
============================================================
```

**Output report structure:**
```markdown
# LLM Production Deployment: A Research Report

## Executive Summary
...

## Key Findings

### Finding 1: Latency and Infrastructure Costs
...

### Finding 2: Hallucination and Reliability
...

## Synthesis
...

## Conclusion
...

## Limitations
...

## Sources
- [Title](URL)
```

---

## Stack

| Component | Technology |
|---|---|
| Agent framework | Custom GAME loop (Python) |
| MCP server | FastMCP |
| LLM routing | LiteLLM |
| Primary model | `gemini/gemini-2.0-flash-lite` |
| Fallback model | `gemini/gemini-2.0-flash` |
| Web search | DuckDuckGo (ddgs) |
| Page fetching | httpx |
| Persistence | SQLite (built-in, no DB server needed) |
| Config | python-dotenv |

---

## Key Design Decisions

**Why FastMCP?** It handles tool schema generation, server lifecycle, and the MCP protocol automatically — so the code stays focused on business logic, not plumbing.

**Why LiteLLM?** One unified interface to any LLM. Swapping from Gemini to Claude or GPT-4 is a one-line change in `.env`.

**Why SQLite?** Zero infrastructure. Findings persist across sessions and agent restarts without a database server. Runs anywhere.

**Why `persona.md` as a file?** The agent's rules (research process, report structure, quality standards) live in a plain text file. A non-developer can update what the agent does without touching Python code. This is the Document-as-Implementation pattern from MCP course notes.

**Why `next_action` in every tool response?** Each tool tells the agent exactly what to do next — this is the Response-as-Instruction pattern. The agent never has to guess its next step, which dramatically reduces hallucination and wasted iterations.
