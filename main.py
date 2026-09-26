"""
main.py — Entry point for the Agentic Research Assistant.

Usage:
    python main.py
    python main.py "What is the current state of quantum computing?"
    python main.py --list-sessions
    python main.py --session SESSION_ID  (view existing report)
"""

import sys
import argparse
from pathlib import Path

# Make subpackages importable from project root
sys.path.insert(0, str(Path(__file__).parent))

from data.database import init_db, list_sessions, get_report
from agent.agent import run_research_agent


def cmd_list_sessions() -> None:
    sessions = list_sessions()
    if not sessions:
        print("No research sessions found.")
        return
    print(f"\n{'ID':<40} {'Status':<12} {'Question'}")
    print("-" * 100)
    for s in sessions:
        q = s["question"][:55] + "..." if len(s["question"]) > 55 else s["question"]
        print(f"{s['id']:<40} {s['status']:<12} {q}")


def cmd_view_session(session_id: str) -> None:
    report = get_report(session_id)
    if not report:
        print(f"No completed report found for session: {session_id}")
        print("The session may still be in progress or no report was written.")
        return
    print(f"\n{'='*60}")
    print(report)


def main() -> None:
    init_db()

    parser = argparse.ArgumentParser(
        description="Agentic Research Assistant — powered by MCP + Gemini"
    )
    parser.add_argument(
        "question", nargs="?",
        help="Research question to investigate (wrap in quotes)"
    )
    parser.add_argument(
        "--list-sessions", action="store_true",
        help="List all past research sessions"
    )
    parser.add_argument(
        "--session", metavar="SESSION_ID",
        help="View the report from a past session"
    )
    args = parser.parse_args()

    if args.list_sessions:
        cmd_list_sessions()
        return

    if args.session:
        cmd_view_session(args.session)
        return

    if args.question:
        question = args.question
    else:
        print("\n🔬 Agentic Research Assistant")
        print("─" * 40)
        print("Powered by: FastMCP · LiteLLM · Gemini · SQLite")
        print("─" * 40)
        question = input("\nEnter your research question: ").strip()
        if not question:
            print("No question provided. Exiting.")
            sys.exit(1)

    report_path = run_research_agent(question)

    if report_path:
        print(f"\n✅ Report saved to: {report_path}")
        show = input("\nDisplay report now? [y/N] ").strip().lower()
        if show == "y":
            print("\n" + "=" * 60 + "\n")
            print(Path(report_path).read_text(encoding="utf-8"))
    else:
        print("\n⚠  Research completed but no report file was generated.")
        print("   Check the reports/ folder and SQLite DB for partial results.")


if __name__ == "__main__":
    main()
