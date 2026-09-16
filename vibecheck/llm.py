"""Optional: an architectural review by Claude, written from VibeCheck's context pack.

Everything else in VibeCheck is deterministic and runs without a network. This step is the one
place a model reads the repository's story, and it reads the pack below rather than the raw
code: the measured facts, each with the file and line it came from. The pack is written to
disk whether or not the model is called, so it can be pasted into any assistant by hand.

Requires the official SDK (`pip install anthropic`) and credentials: ANTHROPIC_API_KEY, or a
profile from `ant auth login`.
"""
from __future__ import annotations

from typing import Dict, List

MODEL = "claude-opus-5"

REVIEW_SYSTEM = """You are reviewing the architecture of a repository that was built largely by AI coding agents.
You receive VibeCheck's context pack: measured facts about the code, each tied to a file and line. You do not
have the code itself.

Write the review a senior engineer would give the repository's owner before they hand the next feature to an
agent. Be direct and specific, and cite the pack's evidence (file:line) for every claim.

Structure:
1. What this codebase is, architecturally, in three or four sentences.
2. The three problems that will cost the most on the next agent-driven change, most expensive first. For each:
   the evidence, the failure it will cause, and the smallest change that removes it.
3. Findings in the pack you believe are false positives or overstated, and why.
4. What is genuinely well built and should be protected.
5. One paragraph: whether to hand the repository to an agent now, and under which constraints.

Do not invent files, functions or numbers that are not in the pack. When the pack cannot settle a question, say
what you would need to look at."""


def context_pack(result: dict, max_findings: int = 30, max_risk: int = 15) -> str:
    r = result
    L: List[str] = []
    repo = r["repo"]
    st = r["stats"]
    L.append("# VibeCheck context pack: %s @ %s" % (repo["name"], repo["short"]))
    L.append("")
    L.append("Commit subject: %s" % repo.get("subject", ""))
    L.append("Code: %d files, %d lines. Languages (lines): %s." % (
        st["code_files"], st["loc"], ", ".join("%s %d" % kv for kv in sorted(st["languages"].items(), key=lambda kv: -kv[1]))))
    L.append("History: %d commits, %d co-authored by AI agents (%s); %d touched docs only." % (
        st["commits"], st["ai_commits"], ", ".join("%s %d" % kv for kv in st["ai_agents"].items()) or "none", st["docs_only_commits"]))
    L.append("Tests: %s." % (", ".join(st["tests"]) or "none"))
    L.append("")
    sc = r["score"]
    L.append("## Vibe Debt Score: %.1f / 100 (%s), scoring %s" % (sc["total"], sc["tier_label"], sc["version"]))
    for c in sc["categories"].values():
        comps = "; ".join("%s: %s -> %s" % (x["name"], x["value"], x["score"] if x["score"] is not None else "n/a") for x in c["components"])
        L.append("- %s %.1f%s. %s" % (c["label"], c["score"], (" (credit %d: %s)" % (c["credit"], c["credit_note"])) if c["credit"] else "", comps))
    L.append("")
    L.append("## Findings")
    for f in r["findings"][:max_findings]:
        L.append("- [%s] %s — %s" % (f["severity"], f["title"], f["detail"]))
        for ev in f["evidence"][:3]:
            L.append("    - %s:%s %s" % (ev["file"], ev.get("line", ""), ("`%s`" % ev["text"].strip()) if ev.get("text") else ""))
    L.append("")
    L.append("## Regression risk ranking (\"Do Not Let AI Touch This\")")
    for x in r["risk"][:max_risk]:
        L.append("- %s %.0f [%s]: %s" % (x["path"], x["score"], x["tier"], "; ".join(x["reasons"])))
    L.append("")
    L.append("## Small files with outsized reach")
    for s in r["secret_controllers"]:
        L.append("- %s: %d lines, %d dependents" % (s["path"], s["loc"], s["fan_in"]))
    L.append("")
    L.append("## Directory dependencies (static and contract references between directories)")
    for e in r["architecture"]["edges"][:30]:
        L.append("- %s -> %s (%d)" % (e["s"], e["t"], e["n"]))
    L.append("")
    L.append("## String-keyed routing tables")
    for t in r["contracts"]["tables"]:
        L.append("- %s::%s — %d keys, %d sends from %d files, lookup %s%s" % (
            t["file"], t["name"], t["keys"], t["sends"], len(t["senders"]), t["access"], ", accepts runtime registration" if t["open"] else ""))
    L.append("")
    L.append("## What each central file says about itself")
    for x in r["risk"][:max_risk]:
        doc = r["metrics"].get(x["path"], {}).get("doc")
        if doc:
            L.append("- %s: %s" % (x["path"], doc))
    L.append("")
    L.append("## Agent instructions in the repo")
    for d in r["agent_docs"]:
        L.append("- %s (%d lines)" % (d["path"], d["lines"]))
    return "\n".join(L)


def review(pack: str, model: str = MODEL) -> Dict[str, str]:
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("The review step needs the Anthropic SDK: pip install anthropic") from e
    client = anthropic.Anthropic()
    try:
        with client.beta.messages.stream(
            model=model,
            max_tokens=32000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            system=REVIEW_SYSTEM,
            messages=[{"role": "user", "content": pack}],
        ) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise RuntimeError("No valid Claude credentials: set ANTHROPIC_API_KEY or run `ant auth login`.") from e
    except anthropic.RateLimitError as e:
        raise RuntimeError("Rate limited by the Claude API; retry after %s seconds." % e.response.headers.get("retry-after", "60")) from e
    except anthropic.APIStatusError as e:
        raise RuntimeError("Claude API error %s: %s" % (e.status_code, e.message)) from e
    except anthropic.APIConnectionError as e:
        raise RuntimeError("Could not reach the Claude API: %s" % e) from e
    if message.stop_reason == "refusal":
        category = message.stop_details.category if message.stop_details else None
        return {"model": message.model, "text": "", "stop_reason": "refusal", "note": "declined (category: %s)" % category}
    text = "".join(block.text for block in message.content if block.type == "text")
    return {"model": message.model, "text": text, "stop_reason": message.stop_reason or ""}
