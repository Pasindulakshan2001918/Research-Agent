"""
mcp_server.py — FastMCP server for the Agentic Research Assistant.

6 tools:
  1. web_search         — search the web for a query
  2. fetch_page_content — fetch and clean text from a URL
  3. save_finding       — persist a structured finding to SQLite
  4. list_findings      — retrieve all saved findings for the session
  5. search_youtube     — find the best YouTube videos for the topic
  6. write_report       — synthesise findings + videos into a markdown report

Patterns applied (from course notes):
  - Horizontal scaling: each tool does ONE job
  - Response-as-Instruction: every return guides the agent's next move
  - Intelligence Budget: returns only what agent needs
  - Failing Forward: errors include next_action hints
  - Validate at Source: bad inputs caught inside tool
"""

import sys, json, re, os
import httpx
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastmcp import FastMCP
from data.database import (
    init_db, save_finding as db_save_finding,
    list_findings as db_list_findings, save_report as db_save_report,
    count_findings,
)

init_db()
mcp = FastMCP("research-agent-server")

# ── Search backend (ddgs with graceful fallback) ───────────────────────────────

def _do_search(query: str, max_results: int) -> list[dict] | None:
    """Try ddgs first, fall back to duckduckgo_search legacy."""
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=max_results))
        if raw:
            return [{"title": r.get("title",""), "url": r.get("href",""),
                     "snippet": r.get("body","")[:300]} for r in raw]
    except Exception:
        pass

    try:
        from duckduckgo_search import DDGS as DDGS2
        with DDGS2() as ddgs:
            raw = list(ddgs.text(query, max_results=max_results))
        if raw:
            return [{"title": r.get("title",""), "url": r.get("href",""),
                     "snippet": r.get("body","")[:300]} for r in raw]
    except Exception:
        pass

    return None


# ── Tool 1: web_search ─────────────────────────────────────────────────────────

@mcp.tool()
def web_search(query: str, session_id: str, max_results: int = 6) -> str:
    """Search the web and return top results (title, URL, snippet).

    Use this to discover relevant sources. After searching, pick the
    most relevant URLs and call fetch_page_content to read them in depth.

    Args:
        query: The search query string
        session_id: Current research session ID
        max_results: Max results to return (1-8, default 6)
    """
    max_results = max(1, min(int(max_results), 8))
    results = _do_search(query, max_results)
    if results is None:
        return json.dumps({
            "status": "error",
            "error": "All search backends failed or returned no results.",
            "next_action": "web_search",
            "hint": "Try a different or simpler query.",
        })

    return json.dumps({
        "status": "success",
        "query": query,
        "result_count": len(results),
        "results": results,
        "next_action": "fetch_page_content",
        "hint": "Pick 2-3 relevant URLs and call fetch_page_content on each. Then save_finding.",
    }, indent=2)


# ── Tool 2: fetch_page_content ─────────────────────────────────────────────────

def _clean_html(text: str) -> str:
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL|re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL|re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@mcp.tool()
def fetch_page_content(url: str, session_id: str) -> str:
    """Fetch the full text content of a web page by URL.

    Use this after web_search to read sources in depth.
    Returns the first 4000 characters of cleaned page text.
    After reading, call save_finding to store key insights.

    Args:
        url: The full URL to fetch (must start with http:// or https://)
        session_id: Current research session ID
    """
    if not url.startswith(("http://", "https://")):
        return json.dumps({
            "status": "error",
            "error": "URL must start with http:// or https://",
            "next_action": "web_search",
            "hint": "Get a valid URL from web_search results first.",
        })

    try:
        headers = {
            "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT":             "1",
            "Connection":      "keep-alive",
        }
        response = httpx.get(url, headers=headers, timeout=10, follow_redirects=True)
        response.raise_for_status()
        text    = _clean_html(response.text)
        excerpt = text[:4000]

    except httpx.TimeoutException:
        return json.dumps({
            "status": "error", "error": "Page timed out after 10 seconds.",
            "next_action": "fetch_page_content",
            "hint": "Try a different URL from the search results.",
        })
    except httpx.HTTPStatusError as e:
        return json.dumps({
            "status": "error", "error": f"HTTP {e.response.status_code} from {url}",
            "next_action": "fetch_page_content",
            "hint": "Try a different URL from the search results.",
        })
    except Exception as e:
        return json.dumps({
            "status": "error", "error": str(e),
            "next_action": "web_search",
            "hint": "Fetch failed. Try searching for a different source.",
        })

    return json.dumps({
        "status": "success",
        "url": url,
        "content_length": len(text),
        "truncated": len(text) > 4000,
        "content": excerpt,
        "next_action": "save_finding",
        "hint": "Read the content. Extract the key insight for your research question. Then call save_finding.",
    }, indent=2)


# ── Tool 3: save_finding ───────────────────────────────────────────────────────

@mcp.tool()
def save_finding(
    session_id: str,
    summary: str,
    source_url: str = "",
    title: str = "",
    relevance: str = "",
) -> str:
    """Save a structured research finding to persistent SQLite storage.

    Call this after reading each source to preserve key insights.
    Findings survive agent restarts and are used by write_report.

    Args:
        session_id: Current research session ID
        summary: A clear 2-5 sentence summary of the key insight
        source_url: URL the insight came from
        title: Title or label for this finding
        relevance: Why this finding matters to the research question
    """
    if len(summary.strip()) < 20:
        return json.dumps({
            "status": "error",
            "error": "Summary too short. Provide at least 20 characters.",
            "next_action": "save_finding",
            "hint": "Write a proper 2-5 sentence summary of what you learned.",
        })

    finding_id = db_save_finding(
        session_id=session_id,
        summary=summary.strip(),
        source_url=source_url.strip(),
        title=title.strip(),
        relevance=relevance.strip(),
    )
    total = count_findings(session_id)

    return json.dumps({
        "status": "success",
        "finding_id": finding_id,
        "total_findings": total,
        "next_action": "web_search" if total < 10 else "search_youtube",
        "hint": (
            f"Finding #{finding_id} saved. You now have {total} finding(s). "
            + ("Keep researching — you need at least 10 findings before moving on." if total < 10
               else "Good coverage. Now call search_youtube to find the best videos on this topic, then list_findings, then write_report.")
        ),
    }, indent=2)


# ── Tool 4: list_findings ──────────────────────────────────────────────────────

@mcp.tool()
def list_findings(session_id: str) -> str:
    """Retrieve all research findings saved so far in this session.

    Call this before write_report to review everything gathered.

    Args:
        session_id: Current research session ID
    """
    findings = db_list_findings(session_id)

    if not findings:
        return json.dumps({
            "status": "empty",
            "message": "No findings saved yet.",
            "next_action": "web_search",
            "hint": "Start searching for information relevant to your research question.",
        })

    return json.dumps({
        "status": "success",
        "session_id": session_id,
        "total": len(findings),
        "findings": findings,
        "next_action": "write_report",
        "hint": f"You have {len(findings)} finding(s). Now call write_report to synthesise them. Remember to include the YouTube videos section in the report.",
    }, indent=2)


# ── Tool 5: search_youtube ─────────────────────────────────────────────────────

def _format_count(n: int) -> str:
    """Human-readable view/like count: 1,200,000 → '1.2M'."""
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def _parse_duration(iso: str) -> str:
    """Convert ISO 8601 duration (PT1H4M53S) to human-readable (1:04:53)."""
    if not iso:
        return ""
    h = re.search(r"(\d+)H", iso)
    m = re.search(r"(\d+)M", iso)
    s = re.search(r"(\d+)S", iso)
    hours   = int(h.group(1)) if h else 0
    minutes = int(m.group(1)) if m else 0
    seconds = int(s.group(1)) if s else 0
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


# In-memory cache: session_id → list of video dicts from YouTube API
# Used by write_report to rebuild the table from ground truth,
# bypassing whatever the agent wrote in markdown.
_youtube_cache: dict[str, list[dict]] = {}


@mcp.tool()
def search_youtube(query: str, session_id: str, max_results: int = 5) -> str:
    """Search YouTube for the best videos related to the research topic.

    Uses the YouTube Data API v3. Call this once after gathering your
    written findings (at least 10) and before writing the final report.
    The returned video links must be included in the report's
    '## Recommended Videos' section.

    Requires YOUTUBE_API_KEY in environment / .env file.

    Args:
        query: Search query describing the research topic
        session_id: Current research session ID
        max_results: Number of videos to return (1-8, default 5)
    """
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        return json.dumps({
            "status": "error",
            "error": "YOUTUBE_API_KEY is not set in your .env file.",
            "next_action": "list_findings",
            "hint": (
                "Add YOUTUBE_API_KEY=your_key to .env and restart. "
                "Get a free key at https://console.cloud.google.com — "
                "enable 'YouTube Data API v3'. "
                "For now, skip to list_findings then write_report without a videos section."
            ),
        })

    max_results = max(1, min(int(max_results), 8))

    # Step 1: search for video IDs
    try:
        search_resp = httpx.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part":       "snippet",
                "q":          query,
                "type":       "video",
                "maxResults": max_results,
                "order":      "relevance",
                "key":        api_key,
            },
            timeout=10,
        )
        search_resp.raise_for_status()
        search_data = search_resp.json()
    except httpx.HTTPStatusError as e:
        body = e.response.text[:300]
        return json.dumps({
            "status": "error",
            "error": f"YouTube search API returned HTTP {e.response.status_code}: {body}",
            "next_action": "list_findings",
            "hint": "Check that YOUTUBE_API_KEY is valid and YouTube Data API v3 is enabled.",
        })
    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": str(e),
            "next_action": "list_findings",
            "hint": "YouTube search failed. Proceed to list_findings then write_report without videos.",
        })

    items = search_data.get("items", [])
    if not items:
        return json.dumps({
            "status": "error",
            "error": "YouTube returned no results for this query.",
            "next_action": "list_findings",
            "hint": "Try a broader query term, or proceed to list_findings then write_report.",
        })

    video_ids = [item["id"]["videoId"] for item in items if item.get("id", {}).get("videoId")]

    # Step 2: fetch statistics (views, duration)
    try:
        stats_resp = httpx.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "statistics,contentDetails,snippet",
                "id":   ",".join(video_ids),
                "key":  api_key,
            },
            timeout=10,
        )
        stats_resp.raise_for_status()
        stats_data = stats_resp.json()
    except Exception:
        stats_data = {"items": []}

    stats_map: dict[str, dict] = {v["id"]: v for v in stats_data.get("items", [])}

    # Step 3: build enriched video list
    videos = []
    for item in items:
        vid_id = item.get("id", {}).get("videoId", "")
        if not vid_id:
            continue

        snippet = item.get("snippet", {})
        stats   = stats_map.get(vid_id, {})
        s       = stats.get("statistics", {})
        cd      = stats.get("contentDetails", {})

        view_count = int(s.get("viewCount", 0))
        like_count = int(s.get("likeCount",  0))

        videos.append({
            "title":       snippet.get("title", ""),
            "channel":     snippet.get("channelTitle", ""),
            "published":   snippet.get("publishedAt", "")[:10],
            "url":         f"https://www.youtube.com/watch?v={vid_id}",
            "thumbnail":   snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
            "duration":    _parse_duration(cd.get("duration", "")),
            "views":       _format_count(view_count),
            "likes":       _format_count(like_count),
            "description": snippet.get("description", "")[:200],
        })

    # Cache the ground-truth video list so write_report can rebuild the
    # table accurately, regardless of what the agent writes in markdown.
    _youtube_cache[session_id] = videos

    return json.dumps({
        "status": "success",
        "query": query,
        "video_count": len(videos),
        "videos": videos,
        "next_action": "list_findings",
        "hint": (
            f"Found {len(videos)} YouTube video(s). "
            "Now call list_findings, then write_report. "
            "Include ALL video links in a '## Recommended Videos' section "
            "at the end of the report, formatted as a table with Title, Channel, Duration, Views, and Link columns."
        ),
    }, indent=2)


# ── YouTube table sanitiser ────────────────────────────────────────────────────

def _rebuild_youtube_table(videos: list[dict]) -> str:
    """
    Build a guaranteed-correct 5-column markdown table from video dicts.
    Strips any pipe characters from text fields so columns never shift.
    """
    def clean(s: str) -> str:
        return str(s).replace("|", "-").replace("\n", " ").strip()

    lines = [
        "| Title | Channel | Duration | Views | Link |",
        "|-------|---------|----------|-------|------|",
    ]
    for v in videos:
        title    = clean(v.get("title",    ""))
        channel  = clean(v.get("channel",  ""))
        duration = clean(v.get("duration", ""))
        views    = clean(v.get("views",    ""))
        url      = v.get("url", "").strip()
        link     = f"[Watch]({url})" if url else "N/A"
        lines.append(f"| {title} | {channel} | {duration} | {views} | {link} |")

    return "\n".join(lines)


def _inject_youtube_table(report_content: str, session_id: str) -> str:
    """
    Replace whatever the agent wrote under ## Recommended Videos
    with a guaranteed-correct table built from _youtube_cache.
    If no cached videos exist, return the report unchanged.
    """
    videos = _youtube_cache.get(session_id)
    if not videos:
        return report_content

    correct_table = _rebuild_youtube_table(videos)

    # Replace everything between ## Recommended Videos and the next ## (or EOF)
    pattern = re.compile(
        r"(##\s+Recommended Videos\s*\n)"
        r".*?"
        r"(?=\n##\s|\Z)",
        re.DOTALL | re.IGNORECASE,
    )

    replacement = r"\g<1>" + correct_table + "\n"
    new_content = pattern.sub(replacement, report_content)

    # If section not found at all, append it
    if new_content == report_content and "## Recommended Videos" not in report_content:
        new_content = report_content.rstrip() + "\n\n## Recommended Videos\n\n" + correct_table + "\n"

    return new_content


# ── Tool 6: write_report ───────────────────────────────────────────────────────

@mcp.tool()
def write_report(session_id: str, research_question: str, report_content: str) -> str:
    """Synthesise all saved findings into a markdown research report and save to disk.

    This is the FINAL step. Call after gathering at least 10 findings
    and after calling search_youtube.

    The report_content MUST include these sections in order:
    # Title
    ## Executive Summary
    ## Key Findings  (one ### subsection per finding)
    ## Synthesis
    ## Conclusion
    ## Limitations
    ## Sources
    ## Recommended Videos  (table: Title | Channel | Duration | Views | Link)

    Args:
        session_id: Current research session ID
        research_question: The original research question being answered
        report_content: Full markdown report synthesising all findings and videos
    """
    if len(report_content.strip()) < 200:
        return json.dumps({
            "status": "error",
            "error": "Report content too short. Write a proper structured report.",
            "next_action": "write_report",
            "hint": "Include all required sections including ## Recommended Videos.",
        })

    findings = db_list_findings(session_id)
    if len(findings) < 3:
        return json.dumps({
            "status": "error",
            "error": f"Only {len(findings)} finding(s) saved. Need at least 3.",
            "next_action": "web_search",
            "hint": "Continue researching. Call save_finding after each source.",
        })

    # Permanently fix the YouTube table before saving —
    # replace whatever the agent wrote with ground-truth data from the cache.
    report_content = _inject_youtube_table(report_content, session_id)

    db_save_report(session_id, report_content)

    reports_dir = Path(__file__).parent.parent / "reports"
    reports_dir.mkdir(exist_ok=True)
    safe_name   = re.sub(r"[^\w\-]", "_", session_id)
    report_path = reports_dir / f"{safe_name}.md"
    report_path.write_text(report_content, encoding="utf-8")

    return json.dumps({
        "status": "success",
        "session_id": session_id,
        "report_path": str(report_path),
        "findings_used": len(findings),
        "characters": len(report_content),
        "next_action": "terminate",
        "hint": f"Report saved to {report_path}. Research complete. Summarise for the user.",
    }, indent=2)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()