"""Xvimo MCP server: lets Claude Desktop, Cursor, VS Code or any MCP-capable AI agent check a message for scams.

Two modes:
  * Hosted (default): calls the public Xvimo beta at XVIMO_URL. No API keys needed. Same fair-use limits as the
    web demo (8 checks per 10 minutes).
  * Local: set XVIMO_LOCAL=1 plus NEBIUS_API_KEY / TAVILY_API_KEY (as in .env) to run the full Nemotron pipeline
    on your own keys.

Run it:
    pip install "mcp>=2.0" httpx
    python mcp_server.py

Claude Desktop config (claude_desktop_config.json):
    {"mcpServers": {"xvimo": {"command": "python", "args": ["C:/path/to/xvimo/mcp_server.py"]}}}
"""
import json
import os

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

XVIMO_URL = os.getenv("XVIMO_URL", "https://xvimo.onrender.com").rstrip("/")
LOCAL = os.getenv("XVIMO_LOCAL", "").strip() == "1"
MAX_CHARS = 1500
LABELS = {"likely_scam": "LIKELY SCAM", "suspicious": "SUSPICIOUS", "no_red_flags": "NO RED FLAGS FOUND"}

mcp = MCPServer(
    name="xvimo",
    title="Xvimo scam checker",
    website_url="https://xvimo.onrender.com",
    instructions=(
        "Xvimo checks SMS, WhatsApp, email or social media messages for scams, with a focus on Nigeria and Africa "
        "(Ponzi schemes, fake jobs, BVN/ATM alerts, fake loans, giveaways, romance and impersonation). It understands "
        "English, Nigerian Pidgin, Yoruba, Hausa and Igbo. Use check_message when a user shares a message and asks "
        "whether it is a scam or safe. Pass the message text exactly as received. Present the verdict, the reason and "
        "the safety tips; cite the evidence links when present. A 'no red flags' verdict is not a guarantee."
    ),
)


def _shape(r: dict) -> dict:
    """The fields an agent needs, in a stable shape for both modes."""
    verdict = r.get("verdict")
    out = {
        "verdict": verdict,
        "label": LABELS.get(verdict, verdict),
        "confidence": r.get("confidence"),
        "scam_type": r.get("scam_type"),
        "reason": r.get("reason"),
        "reply_for_user": r.get("reply"),
        "evidence": [{"finding": e.get("finding"), "url": e.get("url")} for e in (r.get("evidence") or [])[:3]],
        "decided_by": "Nemotron Nano (fast triage)" if r.get("decided_by") == "nano"
                      else ("Nemotron Ultra agent with web search" if r.get("evidence")
                            else "Nemotron Ultra agent (web search found no usable sources)"
                            if (r.get("search_count") or r.get("searches")) else "Nemotron Ultra agent"),
        "web_searches": r.get("search_count") or len(r.get("searches") or []),
        "seconds": r.get("total_latency_s"),
        "note": "Automated check, not a guarantee. When in doubt, don't send money and confirm through official channels.",
    }
    if r.get("safety"):  # only for abusive, sexual or threatening messages
        out["harassment_safety_tips"] = r["safety"]
    return out


def _check_hosted(message: str) -> dict:
    with httpx.Client(timeout=httpx.Timeout(120, connect=60)) as client:  # Render free tier may need ~50 s to wake
        with client.stream("POST", f"{XVIMO_URL}/demo/check", json={"message": message}) as resp:
            if resp.status_code != 200:
                resp.read()
                try:
                    detail = resp.json().get("detail")
                except (ValueError, AttributeError):
                    detail = resp.text[:200]
                return {"error": detail or f"Xvimo returned HTTP {resp.status_code}"}
            for line in resp.iter_lines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("type") == "result":
                    return _shape(event)
                if event.get("type") == "error":
                    return {"error": event.get("message")}
    return {"error": "Xvimo did not return a result. Please try again."}


def _check_local(message: str) -> dict:
    from safety import harm_of, safety_lines
    from xvimo import check
    result = check(message)
    result["safety"] = safety_lines(harm_of(result, result.get("triage") or result))
    return _shape(result)


@mcp.tool(
    name="check_message",
    title="Check a message for scams",
    description=(
        "Check whether a message (SMS, WhatsApp, email, DM) is a scam. Returns a verdict (likely_scam, suspicious or "
        "no_red_flags), a plain-language reason, a short reply the user can read, safety tips and, when the agent "
        "searched the web, evidence links. Tuned for Nigerian and African scams; supports English, Pidgin, Yoruba, "
        "Hausa and Igbo. Takes 1 to 30 seconds."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
)
def check_message(message: str) -> dict:
    """message: the full text of the suspicious message, exactly as received (max 1,500 characters)."""
    message = (message or "").strip()
    if not message:
        return {"error": "Please provide the message text to check."}
    if len(message) > MAX_CHARS:
        return {"error": f"Please keep the message under {MAX_CHARS} characters."}
    try:
        return _check_local(message) if LOCAL else _check_hosted(message)
    except httpx.HTTPError as exc:
        return {"error": f"Could not reach Xvimo at {XVIMO_URL} ({type(exc).__name__}). It may be waking up; try again."}


if __name__ == "__main__":
    mcp.run()  # stdio
