"""Xvimo investigator: a Nemotron Ultra agent that decides its own Tavily searches and returns a cited verdict.

Ultra is given two tools and a budget. It chooses what to verify, writes its own queries, reads the
results, searches again if the evidence is thin, and then gives a final verdict. Every cited URL is
checked against the pages the agent actually retrieved, so it cannot invent sources.
"""
import json
import os
import re
import time

from dotenv import load_dotenv
from openai import APIConnectionError, BadRequestError, InternalServerError, RateLimitError
from tavily import TavilyClient

from triage import RETRY_WAITS, client

load_dotenv(override=True)

ULTRA_MODEL = os.getenv("ULTRA_MODEL")
ULTRA_THINKING = os.getenv("ULTRA_THINKING", "false").lower() == "true"
MAX_SEARCH_ROUNDS = 3
tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

# Official sources the agent can search directly.
REGULATOR_DOMAINS = [
    "sec.gov.ng", "cbn.gov.ng", "efcc.gov.ng", "fccpc.gov.ng", "ncc.gov.ng",
    "nimc.gov.ng", "cac.gov.ng", "ndic.gov.ng",
]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the open web: news reports, complaints, company websites, forums such as Nairaland, "
                           "and reviews. Use for checking whether a company, platform, link or offer is real or has "
                           "been reported as a scam.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "A short, specific search query."}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_regulators",
            "description": "Search only official Nigerian regulator and government websites (SEC Nigeria, CBN, EFCC, "
                           "FCCPC, NCC, NIMC, CAC, NDIC). Use for registration status, public warnings about named "
                           "schemes, and official statements.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "A short, specific search query."}},
                "required": ["query"],
            },
        },
    },
]

SYSTEM_PROMPT = """You are the Xvimo investigator, a careful fraud analyst protecting ordinary people in Nigeria and Africa.
A fast triage model has flagged a message as uncertain or risky. Your job is to reach a final, evidence-based verdict.

You have two tools: web_search (open web) and search_regulators (official Nigerian regulator sites).
How to work:
1. Decide what actually needs verifying: named companies or platforms, links, schemes, claims about banks or agencies.
2. Search with short, specific queries. Prefer search_regulators for investment schemes and bank or agency claims.
3. If results are thin or off-target, search again with a different angle. You have at most {rounds} rounds of searching.
4. If nothing specific can be searched (no names, no links), decide from the message itself. Do not search for nothing.
5. Never treat "no results found" as proof a company is safe or a scam; say what was and was not found.

Judging rules:
- Scam: asks for money, fees, PINs, OTPs, BVN, card or login details under false pretences, promises unrealistic
  returns, or impersonates a bank, agency or relative. Unsolicited job or interview invites from unknown firms are a
  well-known Nigerian lure.
- Legitimate: OTP codes, transaction alerts, telco notices, delivery or payment confirmations, a known person asking
  to be paid for finished work, fraud warnings from banks, normal personal chats.
- Placeholders like [NAME], [PHONE], [ACCOUNT] hide private details; the action around them still counts.

When you are done, reply with ONLY this JSON object and nothing else:
{{
  "verdict": "likely_scam" | "suspicious" | "no_red_flags",
  "confidence": number between 0 and 1,
  "scam_type": "ponzi" | "impersonation" | "fake_job" | "fake_loan" | "phishing_link" | "romance" | "fake_giveaway" | "advance_fee" | "other" | "none",
  "reason": "one or two plain sentences explaining the verdict",
  "evidence": [{{"finding": "what this source shows", "url": "exact URL from your search results"}}],
  "reply": "a short WhatsApp-style message to the user (max 70 words) in {language}, with the verdict, the key reason and one safety tip"
}}
Only cite URLs that appeared in your search results. Use an empty evidence list if you did not search."""

ALLOWED_VERDICTS = {"likely_scam", "suspicious", "no_red_flags"}


# ---------------------------------------------------------------- tools
def _run_search(query: str, regulators_only: bool) -> list:
    kwargs = dict(query=query[:300], max_results=5, search_depth="basic")
    if regulators_only:
        kwargs["include_domains"] = REGULATOR_DOMAINS
    try:
        results = tavily.search(**kwargs).get("results", [])
    except Exception as exc:  # network or quota problem: tell the agent instead of crashing
        return [{"error": f"search failed: {str(exc)[:120]}"}]
    return [{"title": r.get("title", ""), "url": r.get("url", ""), "content": (r.get("content") or "")[:600]}
            for r in results]


def _execute_tool(name: str, arguments: str, trace: dict) -> str:
    try:
        query = json.loads(arguments or "{}").get("query", "")
    except json.JSONDecodeError:
        query = ""
    results = _run_search(query, regulators_only=(name == "search_regulators")) if query else []
    trace["searches"].append({"tool": name, "query": query, "results": len(results)})
    for r in results:
        if r.get("url"):
            trace["seen_urls"].add(r["url"])
    return json.dumps(results, ensure_ascii=False) if results else "[] (no results)"


# ---------------------------------------------------------------- model calls
def _chat(messages: list, use_tools: bool, force_answer: bool = False):
    params = dict(model=ULTRA_MODEL, messages=messages, temperature=0.1, max_tokens=3000,
                  extra_body={"chat_template_kwargs": {"enable_thinking": ULTRA_THINKING}})
    if use_tools:
        params["tools"] = TOOLS
        params["tool_choice"] = "none" if force_answer else "auto"
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            return client.chat.completions.create(**params)
        except (RateLimitError, InternalServerError, APIConnectionError):
            if attempt == len(RETRY_WAITS):
                raise
            print(f"      Ultra busy, retrying in {RETRY_WAITS[attempt]}s...")
            time.sleep(RETRY_WAITS[attempt])


def _extract_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON in reply")
    return json.loads(text[start:end + 1])


def _add_usage(trace: dict, response) -> None:
    usage = getattr(response, "usage", None)
    trace["input_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
    trace["output_tokens"] += getattr(usage, "completion_tokens", 0) or 0


# ---------------------------------------------------------------- agent loops
def _loop_native_tools(messages: list, trace: dict) -> str:
    """Agent loop using the model's built-in tool calling."""
    for round_no in range(MAX_SEARCH_ROUNDS + 1):
        force = round_no == MAX_SEARCH_ROUNDS
        if force:
            messages.append({"role": "user", "content": "Search budget used. Give your final JSON now."})
        response = _chat(messages, use_tools=True, force_answer=force)
        _add_usage(trace, response)
        msg = response.choices[0].message
        if not msg.tool_calls:
            return msg.content or ""
        messages.append({
            "role": "assistant", "content": msg.content or "",
            "tool_calls": [{"id": tc.id, "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                           for tc in msg.tool_calls],
        })
        for tc in msg.tool_calls:
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": _execute_tool(tc.function.name, tc.function.arguments, trace)})
    return ""


def _loop_json_actions(messages: list, trace: dict) -> str:
    """Fallback agent loop for endpoints without tool calling: the model replies with JSON actions."""
    messages[0]["content"] += (
        '\n\nTOOL PROTOCOL: to use a tool, reply with ONLY {"action": "web_search" or "search_regulators", '
        '"query": "..."}. You will receive the results, then continue. When finished, reply with the final JSON.'
    )
    for round_no in range(MAX_SEARCH_ROUNDS + 1):
        if round_no == MAX_SEARCH_ROUNDS:
            messages.append({"role": "user", "content": "Search budget used. Give your final JSON now."})
        response = _chat(messages, use_tools=False)
        _add_usage(trace, response)
        content = response.choices[0].message.content or ""
        try:
            data = _extract_json(content)
        except (ValueError, json.JSONDecodeError):
            return content
        if data.get("action") in ("web_search", "search_regulators") and round_no < MAX_SEARCH_ROUNDS:
            results = _execute_tool(data["action"], json.dumps({"query": data.get("query", "")}), trace)
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": f"Results for {data['action']}: {results}"})
            continue
        return content
    return ""


# ---------------------------------------------------------------- public entry point
def investigate(message: str, triage_result: dict) -> dict:
    """Investigate an escalated message and return a final, cited verdict."""
    started = time.time()
    language = triage_result.get("language") or "english"
    trace = {"searches": [], "seen_urls": set(), "input_tokens": 0, "output_tokens": 0, "mode": "tools"}

    triage_summary = {k: triage_result.get(k) for k in
                      ("verdict", "scam_type", "organizations", "urls", "promised_returns", "red_flags")}
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(rounds=MAX_SEARCH_ROUNDS, language=language)},
        {"role": "user", "content": f"Message to investigate:\n\"\"\"\n{message}\n\"\"\"\n\n"
                                    f"Triage notes (may be wrong): {json.dumps(triage_summary, ensure_ascii=False)}"},
    ]

    try:
        raw = _loop_native_tools(messages, trace)
    except BadRequestError:
        trace["mode"] = "json_actions"
        messages = messages[:2]
        raw = _loop_json_actions(messages, trace)

    try:
        result = _extract_json(raw)
        if result.get("verdict") not in ALLOWED_VERDICTS:
            result["verdict"] = "suspicious"
        result["parse_ok"] = True
    except (ValueError, json.JSONDecodeError):
        result = {"verdict": "suspicious", "confidence": 0.5, "reason": "Investigation could not be completed.",
                  "evidence": [], "reply": "", "parse_ok": False}

    # Keep only citations the agent actually retrieved, so it cannot invent sources.
    evidence = result.get("evidence") or []
    result["evidence"] = [e for e in evidence if isinstance(e, dict) and e.get("url") in trace["seen_urls"]]
    result["dropped_citations"] = len(evidence) - len(result["evidence"])

    result.update({
        "searches": trace["searches"], "search_count": len(trace["searches"]), "agent_mode": trace["mode"],
        "latency_s": round(time.time() - started, 2), "model": ULTRA_MODEL,
        "input_tokens": trace["input_tokens"], "output_tokens": trace["output_tokens"],
    })
    return result
