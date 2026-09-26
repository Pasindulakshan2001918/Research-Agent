"""
server.py — FastAPI backend for the Research Agent Web UI.

Endpoints:
  GET  /                    → serves index.html
  GET  /health              → health check
  POST /api/research        → starts a research session (streaming SSE)
  GET  /api/sessions        → list all past sessions
  GET  /api/report/{id}     → get a specific session's report
  GET  /api/status/{id}     → get session status
"""

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))
from data.database import (
    init_db, create_session, list_sessions,
    get_report, list_findings, count_findings,
)

init_db()

app = FastAPI(title="Research Agent API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# ── Serve UI ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    html_path = Path(__file__).parent / "ui" / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.get("/health")
async def health():
    """Health check — useful for deployment platforms."""
    return {"status": "ok", "version": "1.0.0"}


# ── Research endpoint (SSE streaming) ─────────────────────────────────────────

class ResearchRequest(BaseModel):
    question: str


@app.post("/api/research")
async def start_research(req: ResearchRequest):
    """
    Streams agent progress as Server-Sent Events.

    Event types:
      session   — session created
      tool      — tool called (name, status, detail)
      finding   — new finding saved
      youtube   — YouTube search completed
      done      — report finished
      error     — something went wrong
    """
    return StreamingResponse(
        _run_agent_stream(req.question.strip()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _run_agent_stream(question: str) -> AsyncGenerator[str, None]:
    """Run the agent loop in a thread and stream events to the client."""

    def send(event_type: str, data: dict) -> str:
        return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

    if not question:
        yield send("error", {"message": "No question provided."})
        return

    # Fail fast with a clear message if API key is missing
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_key:
        yield send("error", {
            "message": (
                "GEMINI_API_KEY is not set. "
                "Add it to your .env file and restart the server."
            )
        })
        return

    try:
        from agent.agent import (
            TOOL_SCHEMAS, TOOL_FUNCTIONS,
            load_persona, trim_memory, call_llm,
        )
    except ImportError as e:
        yield send("error", {
            "message": f"Import error: {e}. Run: pip install -r requirements.txt"
        })
        return

    session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    create_session(session_id, question)
    yield send("session", {"session_id": session_id, "question": question})

    persona = load_persona()
    system_prompt = (
        f"{persona}\n\n---\n"
        f"## Current Session\n"
        f"Session ID: {session_id}\n"
        f"Research Question: {question}\n"
        f"Started: {datetime.now().isoformat()}\n\n"
        f"Begin by planning your research approach, then use your tools."
    )

    memory: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": f"Research question: {question}"},
    ]

    report_path    = None
    findings_count = 0
    max_iter       = 40

    for iteration in range(1, max_iter + 1):
        trimmed = trim_memory(memory)

        try:
            response = await asyncio.to_thread(call_llm, trimmed, TOOL_SCHEMAS)
        except Exception as e:
            yield send("error", {"message": str(e), "iteration": iteration})
            break

        msg = response.choices[0].message

        if not msg.tool_calls:
            break

        for tool_call in msg.tool_calls:
            tool_name = tool_call.function.name
            try:
                tool_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                tool_args = {}

            yield send("tool", {
                "name":      tool_name,
                "status":    "running",
                "iteration": iteration,
                "detail":    _get_running_detail(tool_name, tool_args),
            })

            # Required-arg guard
            schema   = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == tool_name)
            required = schema["function"]["parameters"].get("required", [])
            missing  = [k for k in required if k not in tool_args]

            if missing:
                result = json.dumps({
                    "status": "error",
                    "error":  f"Missing args: {missing}",
                    "next_action": tool_name,
                    "hint": f"Supply: {required}",
                })
            elif tool_name in TOOL_FUNCTIONS:
                try:
                    result = await asyncio.to_thread(
                        TOOL_FUNCTIONS[tool_name], **tool_args
                    )
                except Exception as e:
                    result = json.dumps({
                        "status": "error", "error": str(e),
                        "next_action": tool_name, "hint": str(e),
                    })
            else:
                result = json.dumps({
                    "status": "error",
                    "error":  f"Unknown tool: {tool_name}",
                    "hint":   f"Available: {list(TOOL_FUNCTIONS.keys())}",
                })

            try:
                rd     = json.loads(result)
                ok     = rd.get("status") == "success"
                detail = _get_done_detail(tool_name, rd)

                yield send("tool", {
                    "name":      tool_name,
                    "status":    "success" if ok else "error",
                    "iteration": iteration,
                    "detail":    detail,
                    "error":     rd.get("error", "") if not ok else "",
                })

                if tool_name == "save_finding" and ok:
                    findings_count = rd.get("total_findings", findings_count)
                    yield send("finding", {
                        "count":      findings_count,
                        "finding_id": rd.get("finding_id"),
                        "title":      tool_args.get("title", ""),
                        "summary":    tool_args.get("summary", "")[:120],
                    })

                elif tool_name == "search_youtube" and ok:
                    yield send("youtube", {
                        "count":  rd.get("video_count", 0),
                        "videos": rd.get("videos", []),
                    })

                elif tool_name == "write_report" and ok:
                    report_path = rd.get("report_path")
                    report_text = ""
                    if report_path:
                        try:
                            report_text = Path(report_path).read_text(encoding="utf-8")
                        except Exception:
                            pass
                    yield send("done", {
                        "session_id":  session_id,
                        "report_path": report_path,
                        "report":      report_text,
                        "findings":    findings_count,
                        "characters":  rd.get("characters", 0),
                    })

            except Exception as e:
                yield send("tool", {
                    "name": tool_name, "status": "error",
                    "iteration": iteration, "detail": str(e), "error": str(e),
                })

            memory.append({
                "role": "assistant", "content": None,
                "tool_calls": [tool_call],
            })
            memory.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

        if report_path:
            break


def _get_running_detail(tool_name: str, args: dict) -> str:
    if tool_name == "web_search":
        return args.get("query", "")[:80]
    if tool_name == "fetch_page_content":
        return args.get("url", "")[:80]
    if tool_name == "save_finding":
        return args.get("title", "")[:80]
    if tool_name == "search_youtube":
        return args.get("query", "")[:80]
    return ""


def _get_done_detail(tool_name: str, rd: dict) -> str:
    if tool_name == "web_search":
        return f"{rd.get('result_count', 0)} results found"
    if tool_name == "fetch_page_content":
        return f"{rd.get('content_length', 0):,} characters read"
    if tool_name == "save_finding":
        return f"Finding #{rd.get('finding_id')} saved · total {rd.get('total_findings')}"
    if tool_name == "list_findings":
        return f"{rd.get('total', 0)} findings retrieved"
    if tool_name == "search_youtube":
        return f"{rd.get('video_count', 0)} videos found"
    if tool_name == "write_report":
        return f"Report written · {rd.get('characters', 0):,} characters"
    return ""


# ── Sessions API ───────────────────────────────────────────────────────────────

@app.get("/api/sessions")
async def get_sessions():
    sessions = list_sessions()
    result = []
    for s in sessions:
        result.append({
            **s,
            "findings_count": count_findings(s["id"]),
            "has_report":     get_report(s["id"]) is not None,
        })
    return JSONResponse(result)


@app.get("/api/report/{session_id}")
async def get_session_report(session_id: str):
    report = get_report(session_id)
    if not report:
        return JSONResponse({"error": "Report not found"}, status_code=404)
    findings = list_findings(session_id)
    return JSONResponse({"report": report, "findings": findings})


@app.get("/api/status/{session_id}")
async def get_session_status(session_id: str):
    from data.database import get_session
    session = get_session(session_id)
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    return JSONResponse({
        **session,
        "findings_count": count_findings(session_id),
    })


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port     = int(os.getenv("PORT", 8000))
    dev_mode = os.getenv("DEV_MODE", "true").lower() == "true"

    print("\n" + "=" * 50)
    print("  Research Agent")
    print(f"  Running at: http://localhost:{port}")
    print(f"  Mode: {'Development' if dev_mode else 'Production'}")
    print("=" * 50 + "\n")

    if dev_mode:
        import threading, webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
    )