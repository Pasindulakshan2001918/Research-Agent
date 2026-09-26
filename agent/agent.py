"""
agent.py — Agentic Research Assistant core loop.

Architecture (from course notes):
  - GAME framework: Goals / Actions / Memory / Environment
  - AgentFunctionCallingActionLanguage: native function calling
  - Horizontal scaling: coordinator calls focused MCP tools
  - Document-as-implementation: persona loaded from persona.md at runtime
  - Response-as-Instruction: tool returns guide every next step
  - Intelligence Budget: pair-aware memory trimming (Gemini-safe)

LiteLLM routes to Gemini with automatic fallback.
"""

import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import litellm
from dotenv import load_dotenv

# ── Suppress LiteLLM's noisy stderr output ────────────────────────────────────
logging.getLogger("LiteLLM").setLevel(logging.CRITICAL)
logging.getLogger("litellm").setLevel(logging.CRITICAL)
logging.getLogger("LiteLLM Router").setLevel(logging.CRITICAL)
logging.getLogger("LiteLLM Proxy").setLevel(logging.CRITICAL)
os.environ["LITELLM_LOG"] = "ERROR"

# Sibling imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from data.database import init_db, create_session, get_report
from tools.mcp_server import (
    web_search, fetch_page_content,
    save_finding, list_findings, search_youtube, write_report,
)

load_dotenv()
init_db()

# ── Model config ───────────────────────────────────────────────────────────────

PRIMARY_MODEL  = os.getenv("PRIMARY_MODEL",  "gemini/gemini-3.1-flash-lite")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "gemini/gemini-3.5-flash-lite")

litellm.set_verbose = False

# ── Document-as-implementation: load persona at runtime ───────────────────────

PERSONA_PATH = Path(__file__).parent / "persona.md"

def load_persona() -> str:
    """Read the persona file fresh — rules can change without code edit."""
    return PERSONA_PATH.read_text(encoding="utf-8")

# ── Tool registry (horizontal scaling — each tool does ONE job) ───────────────

TOOL_FUNCTIONS: dict[str, Any] = {
    "web_search":         web_search,
    "fetch_page_content": fetch_page_content,
    "save_finding":       save_finding,
    "list_findings":      list_findings,
    "search_youtube":     search_youtube,
    "write_report":       write_report,
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for a query and return the top results (title, URL, snippet). "
                "Use this to discover relevant sources before fetching full content. "
                "Returns up to 8 results. After searching, choose the most relevant URLs "
                "and call fetch_page_content to read them in depth."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query":       {"type": "string",  "description": "The search query string"},
                    "max_results": {"type": "integer", "description": "Max results (1-8)", "default": 6},
                    "session_id":  {"type": "string",  "description": "Current research session ID"},
                },
                "required": ["query", "session_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page_content",
            "description": (
                "Fetch the full text content of a web page by URL. "
                "Use this after web_search to read sources in depth. "
                "Returns the first 4000 characters of cleaned page text. "
                "After reading, call save_finding to store key insights."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url":        {"type": "string", "description": "Full URL (http:// or https://)"},
                    "session_id": {"type": "string", "description": "Current research session ID"},
                },
                "required": ["url", "session_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_finding",
            "description": (
                "Save a structured research finding to persistent storage. "
                "Call this after reading each source to preserve key insights. "
                "Findings survive agent restarts and are used by write_report."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Current research session ID"},
                    "summary":    {"type": "string", "description": "Clear 2-5 sentence summary of the key insight"},
                    "source_url": {"type": "string", "description": "URL the insight came from"},
                    "title":      {"type": "string", "description": "Title or label for this finding"},
                    "relevance":  {"type": "string", "description": "Why this finding matters to the research question"},
                },
                "required": ["session_id", "summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_findings",
            "description": (
                "Retrieve all research findings saved so far in this session. "
                "Call this before write_report to review what has been gathered."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Current research session ID"},
                },
                "required": ["session_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_youtube",
            "description": (
                "Search YouTube for the best videos related to the research topic. "
                "Call this ONCE after you have saved at least 5 findings, and BEFORE write_report. "
                "The returned video links must appear in the report's '## Recommended Videos' section."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query":       {"type": "string",  "description": "Search query describing the research topic"},
                    "session_id":  {"type": "string",  "description": "Current research session ID"},
                    "max_results": {"type": "integer", "description": "Number of videos to return (1-8, default 5)"},
                },
                "required": ["query", "session_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_report",
            "description": (
                "Synthesise all saved findings into a structured markdown research report "
                "and save it to disk. This is the final step. Call it after gathering "
                "at least 5 findings and after calling search_youtube."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id":        {"type": "string", "description": "Current research session ID"},
                    "research_question": {"type": "string", "description": "The original research question"},
                    "report_content":    {
                        "type": "string",
                        "description": (
                            "Full markdown report synthesising all findings and videos. "
                            "Must include ALL of these sections in order: "
                            "# Title, ## Executive Summary, ## Key Findings, "
                            "## Synthesis, ## Conclusion, ## Limitations, ## Sources, "
                            "## Recommended Videos (markdown table: Title | Channel | Duration | Views | Link)."
                        ),
                    },
                },
                "required": ["session_id", "research_question", "report_content"],
            },
        },
    },
]

# ── LLM call with fallback ─────────────────────────────────────────────────────

def call_llm(messages: list[dict], tools: list[dict]) -> Any:
    """
    Call LiteLLM with primary model; fall back silently to secondary on failure.
    Short delay before fallback to respect rate limits.
    """
    try:
        return litellm.completion(
            model=PRIMARY_MODEL,
            messages=messages,
            tools=tools,
            max_tokens=4096,
        )
    except Exception:
        time.sleep(2)
        print("  [→ fallback model]")
        return litellm.completion(
            model=FALLBACK_MODEL,
            messages=messages,
            tools=tools,
            max_tokens=4096,
        )

# ── Pair-aware memory trimming (Intelligence Budget, Gemini-safe) ──────────────
#
# Gemini enforces a strict rule:
#   every assistant message that contains tool_calls MUST be immediately
#   followed by a tool result message for EACH of those calls.
#
# The naive trimmer (keep last N messages) can break this by slicing off
# an assistant tool-call message while keeping its orphaned tool result,
# or vice versa — causing the 400 INVALID_ARGUMENT crash seen in production.
#
# Fix: trim in *pairs* (assistant tool-call + its tool result) so the
# conversation structure Gemini requires is always intact.

MAX_PAIRS = 16  # keep system + first user + last N call/result pairs


def trim_memory(memory: list[dict]) -> list[dict]:
    """
    Gemini-safe memory trim.

    Strategy:
      1. Always keep the system message and the first user message.
      2. Group the remaining messages into atomic units:
           - A plain assistant text message          → unit of 1
           - An assistant tool-call + its tool
             result(s)                               → unit of 2+
      3. Keep only the last MAX_PAIRS units.

    This guarantees every assistant tool-call in the trimmed window is
    immediately followed by its tool result — the invariant Gemini requires.
    """
    if not memory:
        return memory

    system_msgs = [m for m in memory if m["role"] == "system"]
    rest        = [m for m in memory if m["role"] != "system"]

    if not rest:
        return system_msgs

    # Always keep the first user message (the original research question)
    first_user = rest[0:1]
    tail       = rest[1:]

    # Group tail into atomic units
    units: list[list[dict]] = []
    i = 0
    while i < len(tail):
        msg = tail[i]
        if msg["role"] == "assistant" and msg.get("tool_calls"):
            # This call + all immediately following tool results = one unit
            unit = [msg]
            i += 1
            while i < len(tail) and tail[i]["role"] == "tool":
                unit.append(tail[i])
                i += 1
            units.append(unit)
        else:
            units.append([msg])
            i += 1

    # Keep only the last MAX_PAIRS units
    kept_units = units[-MAX_PAIRS:]
    kept_msgs  = [msg for unit in kept_units for msg in unit]

    return system_msgs + first_user + kept_msgs


# ── Main agent loop ────────────────────────────────────────────────────────────

def run_research_agent(question: str, max_iterations: int = 40) -> str:
    """
    Run the full agentic research loop for a given research question.

    GAME framework:
      G — Goals:       loaded from persona.md (document-as-implementation)
      A — Actions:     TOOL_SCHEMAS (horizontal scaling)
      M — Memory:      growing messages list, pair-aware trimming
      E — Environment: TOOL_FUNCTIONS dict (executes tools)

    Returns the path to the saved markdown report.
    """
    session_id = f"session_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    create_session(session_id, question)

    print(f"\n{'='*60}")
    print(f"  RESEARCH AGENT")
    print(f"  Session : {session_id}")
    print(f"  Question: {question}")
    print(f"{'='*60}\n")

    # G: Goals — load persona (Document-as-Implementation)
    persona = load_persona()
    system_prompt = (
        f"{persona}\n\n"
        f"---\n"
        f"## Current Session\n"
        f"Session ID: {session_id}\n"
        f"Research Question: {question}\n"
        f"Started: {datetime.utcnow().isoformat()}\n\n"
        f"Begin by planning your research approach, then use your tools."
    )

    # M: Memory — system + initial user message
    memory: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": f"Research question: {question}"},
    ]

    report_path = None

    # Agent loop: PERCEIVE → DECIDE → ACT → OBSERVE → REPEAT
    for iteration in range(1, max_iterations + 1):
        print(f"[Iteration {iteration}/{max_iterations}]")

        # Trim memory (pair-aware, Gemini-safe)
        trimmed_memory = trim_memory(memory)

        # DECIDE — call LLM
        try:
            response = call_llm(trimmed_memory, TOOL_SCHEMAS)
        except Exception as e:
            print(f"  [error] LLM call failed: {e}")
            break

        msg = response.choices[0].message

        if msg.tool_calls:
            for tool_call in msg.tool_calls:
                tool_name = tool_call.function.name
                try:
                    tool_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}

                print(f"  → Tool: {tool_name}")
                if tool_args:
                    display_args = {
                        k: v for k, v in tool_args.items()
                        if k not in ("report_content",) and len(str(v)) < 120
                    }
                    print(f"    Args: {json.dumps(display_args, ensure_ascii=False)}")

                # ACT — required-arg guard, then execute (E: Environment)
                if tool_name in TOOL_FUNCTIONS:
                    schema   = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == tool_name)
                    required = schema["function"]["parameters"].get("required", [])
                    missing  = [k for k in required if k not in tool_args]

                    if missing:
                        result = json.dumps({
                            "status": "error",
                            "error": f"Missing required argument(s): {missing}",
                            "next_action": tool_name,
                            "hint": (
                                f"You must supply these fields when calling {tool_name}: "
                                f"{required}. You only provided: {list(tool_args.keys())}."
                            ),
                        })
                    else:
                        try:
                            result = TOOL_FUNCTIONS[tool_name](**tool_args)
                        except Exception as e:
                            result = json.dumps({
                                "status": "error",
                                "error": str(e),
                                "next_action": tool_name,
                                "hint": f"Tool raised an exception: {e}. Check your arguments.",
                            })
                else:
                    result = json.dumps({
                        "status": "error",
                        "error": f"Unknown tool: {tool_name}",
                        "hint": f"Available tools: {list(TOOL_FUNCTIONS.keys())}",
                    })

                # Display result
                try:
                    result_data = json.loads(result)
                    status = result_data.get("status", "?")
                    if status == "success":
                        hint = result_data.get("hint", "")
                        print(f"    ✓ {status} — {hint[:80]}" if hint else f"    ✓ {status}")
                    else:
                        print(f"    ✗ {status}: {result_data.get('error', '')[:80]}")

                    if tool_name == "write_report" and status == "success":
                        report_path = result_data.get("report_path")

                except Exception:
                    print(f"    Result: {str(result)[:80]}")

                # OBSERVE — append as matched pair (assistant call + tool result)
                memory.append({
                    "role":       "assistant",
                    "content":    None,
                    "tool_calls": [tool_call],
                })
                memory.append({
                    "role":         "tool",
                    "tool_call_id": tool_call.id,
                    "content":      result,
                })

            if report_path:
                print(f"\n{'='*60}")
                print(f"  Research complete!")
                print(f"  Report saved to: {report_path}")
                print(f"{'='*60}\n")
                break

        else:
            # Plain text response — agent is done
            final_text = msg.content or ""
            print(f"\n  Agent message: {final_text[:200]}")
            memory.append({"role": "assistant", "content": final_text})
            break

    else:
        print(f"\n[warn] Reached max iterations ({max_iterations}) without completing.")

    return report_path or ""


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Agentic Research Assistant")
    parser.add_argument("question", nargs="?", help="Research question to investigate")
    args = parser.parse_args()

    if args.question:
        question = args.question
    else:
        print("Agentic Research Assistant")
        print("-" * 40)
        question = input("Enter your research question: ").strip()
        if not question:
            print("No question provided. Exiting.")
            sys.exit(1)

    path = run_research_agent(question)
    if path:
        print(f"\nYour report is at: {path}")
        show = input("Show report in terminal? [y/N] ").strip().lower()
        if show == "y":
            print("\n" + "=" * 60)
            print(Path(path).read_text(encoding="utf-8"))