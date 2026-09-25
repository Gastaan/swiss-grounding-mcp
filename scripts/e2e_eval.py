"""End-to-end evaluation: real MCP clients + real LLMs answer eval/questions.json through this server.

Mirrors the challenge setup (2 clients x 2 LLMs). Scores each answer with the question's regex checks
(correct content, cited source, asking back, declining) and records tool calls and latency.

  uv run python scripts/e2e_eval.py                       # default matrix
  uv run python scripts/e2e_eval.py --only S3,Q6 --runs claude:sonnet
Runners: claude:<model> (Claude Code CLI), opencode:<provider/model> (OpenCode CLI).
OpenCode with openai/* models needs OPENAI_API_KEY in the environment (never stored in the repo).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = [str(ROOT / ".venv" / "bin" / "swiss-grounding-mcp")]
DEFAULT_RUNS = [
    "claude:sonnet", "claude:haiku",
    "opencode:openai/gpt-5.4-mini", "opencode:openai/gpt-4.1-mini",
]


def _claude(model: str, question: str, workdir: Path, env: dict | None, mode: str) -> dict:
    """mode 'mcp': this server only; 'web': no MCP server, web search and fetch allowed (the usual way to
    answer without this server); 'base': no MCP server and no tools, the model's own knowledge only."""
    servers = {"swiss": {"type": "stdio", "command": SERVER[0], "args": [], **({"env": env} if env else {})}}
    cfg = workdir / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": servers if mode == "mcp" else {}}))
    allowed = {"mcp": ["mcp__swiss"], "web": ["WebSearch", "WebFetch"], "base": []}[mode]
    blocked = ["Bash", "Read", "Glob", "Grep"] + ([] if mode == "web" else ["WebSearch", "WebFetch"])
    cmd = ["claude", "-p", question, "--setting-sources", "project", "--model", model, "--mcp-config", str(cfg),
           "--strict-mcp-config", *(["--allowedTools", *allowed] if allowed else []), "--disallowedTools", *blocked,
           *(["--system-prompt", ASSISTANT_PROMPT] if ASSISTANT_PROMPT else []),
           "--output-format", "stream-json", "--verbose", "--no-session-persistence"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=workdir,
                         stdin=subprocess.DEVNULL).stdout
    answer, tools, web_tools = "", [], []
    for line in out.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "assistant":
            for block in ev["message"].get("content", []):
                if block.get("type") == "tool_use" and block["name"].startswith("mcp__swiss__"):
                    tools.append(block["name"].removeprefix("mcp__swiss__"))
                elif block.get("type") == "tool_use" and block["name"] in ("WebSearch", "WebFetch"):
                    web_tools.append(block["name"])
        if ev.get("type") == "result":
            answer = ev.get("result") or ""
    # "tools" counts calls to this server (the checks use it); web calls are recorded separately
    return {"answer": answer, "tools": tools, "web_tools": web_tools}


def claude(model: str, question: str, workdir: Path, env: dict | None = None) -> dict:
    return _claude(model, question, workdir, env, "mcp")


def claude_web(model: str, question: str, workdir: Path, env: dict | None = None) -> dict:
    return _claude(model, question, workdir, env, "web")


def claude_base(model: str, question: str, workdir: Path, env: dict | None = None) -> dict:
    return _claude(model, question, workdir, env, "base")


def opencode(model: str, question: str, workdir: Path, env: dict | None = None) -> dict:
    (workdir / "opencode.json").write_text(json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "mcp": {"swiss": {"type": "local", "command": SERVER, "enabled": True, "timeout": 30000,
                          **({"environment": env} if env else {})}},
        # the evaluation measures this server, so the built-in web and file tools are off
        "tools": {"webfetch": False, "websearch": False, "bash": False, "edit": False, "write": False,
                  "read": False, "grep": False, "glob": False, "list": False, "patch": False, "task": False},
    }))
    cmd = ["opencode", "run", "--pure", "--format", "json", "-m", model, "--dir", str(workdir), question]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=workdir,
                         stdin=subprocess.DEVNULL).stdout
    answer, tools = [], []
    for line in out.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        part = ev.get("part") or {}
        if part.get("type") == "tool":
            tools.append(str(part.get("tool", "")).removeprefix("swiss_"))
        elif part.get("type") == "text" and part.get("text"):
            answer.append(part["text"])
    return {"answer": "\n".join(answer), "tools": tools}


# claude-web and claude-base are baselines without this server, for comparison
ASSISTANT_PROMPT: str | None = None  # --assistant-prompt: same system prompt for every Claude run

RUNNERS = {"claude": claude, "claude-web": claude_web, "claude-base": claude_base, "opencode": opencode}


# Asking back can be a question or a polite request ("Bitte teilen Sie mir mit, wo Sie wohnen.").
ASK_REQUEST = re.compile(
    r"\?|\b(bitte (teilen|geben|nennen|sagen|schreiben)|teilen sie mir|geben sie mir|nennen sie mir|"
    r"sagen sie mir|lass(en sie)? mich wissen|(können|könnten) sie mir (mitteilen|sagen)|"
    r"kannst du mir (mitteilen|sagen)|teil mir|merci de (me )?(préciser|indiquer)|"
    r"veuillez (me )?(préciser|indiquer)|pouvez-vous (me )?(préciser|indiquer|dire)|indiquez-moi|dites-moi|"
    r"per favore (indica|dimmi|specifica)|mi (dica|indichi)|potrebbe (indicarmi|dirmi)|indicami|dimmi|"
    r"please (tell|let me know|provide|share)|let me know|could you (tell|share|provide))",
    re.I,
)


def score(q: dict, answer: str, tools: list[str] | None = None) -> dict:
    text = answer or ""
    content = any(re.search(p, text, re.I) for p in q["expect_any"]) if q.get("expect_any") else True
    content = content and all(re.search(p, text, re.I) for p in q.get("expect_all", []))
    content = content and not any(re.search(p, text, re.I) for p in q.get("expect_none", []))
    cited = any(re.search(p, text, re.I) for p in q["expect_cite"]) if q.get("expect_cite") else None
    if q["behavior"] == "ask_back":
        content = content and bool(ASK_REQUEST.search(text))
    if q["behavior"] == "general":  # not about Switzerland: the challenge's right response is to say so
        content = content and not tools  # (expect_any), without calling the Swiss server at all
    return {"content_ok": bool(content), "cited": cited, "pass": bool(content) and cited is not False}


def population_pattern(name: str) -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from swiss_grounding_mcp.places import register

    digits = str(next(m for m in register()["by_bfs"].values() if m.name == name).population)
    return digits[:-3] + r"[’'., ]?" + digits[-3:]


def run_one(runner: str, q: dict) -> dict:
    kind, model = runner.split(":", 1)
    with tempfile.TemporaryDirectory() as tmp:
        started = time.monotonic()
        try:
            res = RUNNERS[kind](model, q["question"], Path(tmp), q.get("server_env"))
        except subprocess.TimeoutExpired:
            res = {"answer": "", "tools": [], "error": "timeout"}
        res["seconds"] = round(time.monotonic() - started, 1)
    if LIMIT.search(res["answer"] or ""):  # the client was blocked, not wrong: keep it out of the scores
        res["error"] = "usage limit"
    scored = score(q, res["answer"], res["tools"])
    if res.get("error"):
        scored["pass"] = False
    return {"runner": runner, "id": q["id"], **res, **scored}


LIMIT = re.compile(r"hit your (session|usage|weekly|monthly spend|spend) limit|usage limit reached|rate limit exceeded", re.I)


def load_questions(path: Path = ROOT / "eval" / "questions.json") -> list[dict]:
    questions = json.loads(path.read_text())
    for q in questions:
        q["expect_any"] = [p.replace("{bellinzona_population}", population_pattern("Bellinzona"))
                           for p in q.get("expect_any", [])]
    return questions


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=",".join(DEFAULT_RUNS))
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--questions", default=str(ROOT / "eval" / "questions.json"),
                    help="question file (e.g. eval/practice_questions.json)")
    ap.add_argument("--assistant-prompt", default=None,
                    help="replace Claude Code's coding-assistant system prompt for every Claude run, so runs with "
                         "and without this server differ only in their tools (used for the baseline comparison)")
    ap.add_argument("--rescore", metavar="RESULTS_JSON",
                    help="apply the current checks to recorded answers (no model calls) and rewrite the report")
    args = ap.parse_args()
    global ASSISTANT_PROMPT
    ASSISTANT_PROMPT = args.assistant_prompt
    questions = load_questions(Path(args.questions))
    if args.rescore:
        path = Path(args.rescore)
        results = json.loads(path.read_text())
        by_id = {q["id"]: q for q in questions}
        for x in results:
            x.update(score(by_id[x["id"]], x["answer"], x["tools"]))
            if LIMIT.search(x["answer"] or ""):  # blocked by the client's usage limit, not a wrong answer
                x["error"], x["pass"] = "usage limit", False
        runs = list(dict.fromkeys(x["runner"] for x in results))
        write_report(results, runs, [q for q in questions if any(x["id"] == q["id"] for x in results)],
                     path.stem, "re-scored with the current checks; answers unchanged")
        return
    runs = args.runs.split(",")
    if args.only:
        questions = [q for q in questions if q["id"] in args.only.split(",")]
    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lambda job: run_one(*job), [(r, q) for r in runs for q in questions]))
    write_report(results, runs, questions, datetime.now().strftime("%Y-%m-%dT%H%M%S"))


def write_report(results: list[dict], runs: list[str], questions: list[dict], stamp: str, note: str = "") -> None:
    out_dir = ROOT / "eval" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stamp}.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
    lines = [f"# End-to-end evaluation {stamp}", "", *([f"_{note}_", ""] if note else []),
             "| run | pass | avg tool calls | avg seconds |", "|---|---|---|---|"]
    for r in runs:
        rows = [x for x in results if x["runner"] == r]
        calls = sum(len(x["tools"]) + len(x.get("web_tools", [])) for x in rows)
        errors = sum(1 for x in rows if x.get("error"))
        lines.append(f"| {r} | {sum(x['pass'] for x in rows)}/{len(rows) - errors}"
                     + (f" ({errors} blocked)" if errors else "") + " | "
                     f"{calls / len(rows):.1f} | "
                     f"{sum(x['seconds'] for x in rows) / len(rows):.0f} |")
    lines += ["", "| id | " + " | ".join(runs) + " |", "|---|" + "---|" * len(runs)]
    for q in questions:
        cells = []
        for r in runs:
            x = next(x for x in results if x["runner"] == r and x["id"] == q["id"])
            n = len(x["tools"]) + len(x.get("web_tools", []))
            cells.append(("✅" if x["pass"] else "❌") + f" {n} calls")
        lines.append(f"| {q['id']} | " + " | ".join(cells) + " |")
    report = "\n".join(lines)
    (out_dir / f"{stamp}.md").write_text(report + "\n")
    print(report)


if __name__ == "__main__":
    main()
