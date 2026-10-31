#!/usr/bin/env python3
"""Status report for the agent workflow: queue by state, open agent PRs with CI and review state,
recent CI failures, stale claims, and the counters the workflow is measured by.
Reads GitHub only (through `gh`). Usage: status-report.py [--repo OWNER/NAME] [--json] [--stale-days N] [--days N]
"""
import argparse
import datetime as dt
import json
import re
import subprocess
import sys

AGENT_STATES = ["agent:needs-human", "agent:blocked", "agent:in-progress", "agent:review", "agent:ready"]


def gh(*args):
    res = subprocess.run(["gh", *args], check=True, capture_output=True, text=True)
    return json.loads(res.stdout or "null")


def gh_paginated(endpoint):
    """All items of a paginated list endpoint. `gh api --paginate` prints one JSON array per page,
    concatenated; the newer gh "slurp" flag would join them but is not in gh 2.46, so decode the stream."""
    res = subprocess.run(["gh", "api", "--paginate", endpoint], check=True, capture_output=True, text=True)
    text, items, decoder, pos = res.stdout, [], json.JSONDecoder(), 0
    while True:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            break
        value, pos = decoder.raw_decode(text, pos)
        items.extend(value if isinstance(value, list) else [value])
    return items


def labels(item):
    return {lab["name"] for lab in item.get("labels", [])}


def age_days(iso, now):
    return (now - dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() / 86400


SUCCESS_STATES = {"SUCCESS", "NEUTRAL", "SKIPPED"}
PENDING_STATES = {"", "PENDING", "QUEUED", "IN_PROGRESS", "EXPECTED", "WAITING", "REQUESTED"}


def ci_state(pr):
    """failure if any check did not succeed, pending if any is still running, success only when every
    check succeeded. Unknown conclusions count as failure: a report that calls a red PR green is worse
    than one that asks a human to look."""
    checks = pr.get("statusCheckRollup") or []
    if not checks:
        return "none"
    states = [(c.get("conclusion") or c.get("state") or c.get("status") or "").upper() for c in checks]
    if any(s not in SUCCESS_STATES and s not in PENDING_STATES for s in states):
        return "failure"
    if any(s in PENDING_STATES for s in states):
        return "pending"
    return "success"


REVIEW_HEADER = re.compile(r"^### Independent review \(Codex CLI, read-only\) of `([0-9a-fA-F]+)`")
REVIEW_VERDICT = re.compile(r"verdict=(approve|request_changes|needs_human)\b")


REVIEW_AUTHOR = "github-actions[bot]"


def codex_review(comments, head_sha):
    """Verdict of the latest "Independent review" comment (posted by .github/workflows/codex-review.yml).
    Returns (verdict, answered): verdict is approve | request_changes | needs_human | stale | none; a
    comment for another head SHA is stale and does not count. Only comments by the workflow identity
    (github-actions[bot]) count as reviews; anyone can paste the header. answered is True when an agent comment
    (`**agent:...**`, written by audit-log.sh) follows the counted review."""
    reviews = [(i, REVIEW_HEADER.match(c.get("body") or "")) for i, c in enumerate(comments)
               if (c.get("user") or {}).get("login") == REVIEW_AUTHOR]
    reviews = [(i, m) for i, m in reviews if m]
    if not reviews:
        return "none", False
    i, m = reviews[-1]
    if not (head_sha and head_sha.lower().startswith(m.group(1).lower())):
        return "stale", False
    v = REVIEW_VERDICT.search(comments[i]["body"])
    answered = any((c.get("body") or "").lstrip().startswith("**agent:") for c in comments[i + 1:])
    return (v.group(1) if v else "none"), answered


def is_agent_pr(pr):
    return pr.get("headRefName", "").startswith("v2/issue-") or "agent:review" in labels(pr)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--stale-days", type=float, default=2.0)
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args()
    repo = a.repo or gh("repo", "view", "--json", "nameWithOwner")["nameWithOwner"]
    now = dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(days=a.days)).date().isoformat()

    issues = gh("issue", "list", "-R", repo, "--state", "open", "--limit", "200",
                "--json", "number,title,labels,updatedAt,createdAt,url")
    prs = gh("pr", "list", "-R", repo, "--state", "open", "--limit", "100",
             "--json", "number,title,isDraft,headRefName,headRefOid,baseRefName,labels,statusCheckRollup,reviewDecision,updatedAt,url")
    failed_runs = gh("run", "list", "-R", repo, "--status", "failure", "--limit", "10",
                     "--json", "databaseId,name,headBranch,conclusion,createdAt,url,event")
    merged = gh("pr", "list", "-R", repo, "--state", "merged", "--limit", "100", "--search", f"merged:>={since}",
                "--json", "number,title,headRefName,mergedAt,url")
    closed = gh("pr", "list", "-R", repo, "--state", "closed", "--limit", "100", "--search", f"closed:>={since}",
                "--json", "number,title,headRefName,closedAt,url")

    by_state = {s: [i for i in issues if s in labels(i)] for s in AGENT_STATES}
    decisions = [i for i in issues if "needs:decision" in labels(i)]
    agent_prs = [p for p in prs if is_agent_pr(p)]
    for p in agent_prs:
        p["ci"] = ci_state(p)
        # A failed fetch must not read as "no review": let it abort the report (see the handler below).
        comments = gh_paginated(f"repos/{repo}/issues/{p['number']}/comments?per_page=100")
        p["codex"], p["codex_answered"] = codex_review(comments, p.get("headRefOid"))
    stale = [i for i in by_state["agent:in-progress"] if age_days(i["updatedAt"], now) > a.stale_days]
    red_prs = [p for p in agent_prs if p["ci"] == "failure"]
    changes_requested = [p for p in agent_prs if p.get("reviewDecision") == "CHANGES_REQUESTED"]
    codex_blocking = [p for p in agent_prs if p["codex"] == "needs_human"
                      or (p["codex"] == "request_changes" and not p["codex_answered"])]
    merged_agent = [p for p in merged if p["headRefName"].startswith("v2/issue-")]
    closed_agent = [p for p in closed if p["headRefName"].startswith("v2/issue-")]

    # First-run gate pass rate: the first CI run on each merged agent branch (capped to keep gh calls bounded).
    first_pass = []
    for p in merged_agent[:20]:
        try:
            runs = gh("run", "list", "-R", repo, "--branch", p["headRefName"], "--workflow", "CI",
                      "--limit", "50", "--json", "conclusion,createdAt")
        except subprocess.CalledProcessError:
            runs = []
        if runs:
            first = sorted(runs, key=lambda r: r["createdAt"])[0]
            first_pass.append(first.get("conclusion") == "success")

    report = {
        "repo": repo, "generated": now.isoformat(timespec="seconds"),
        "needs_human": {
            "issues": [i["number"] for i in by_state["agent:needs-human"]],
            "decisions": [i["number"] for i in decisions],
            "changes_requested_prs": [p["number"] for p in changes_requested],
            "red_ci_prs": [p["number"] for p in red_prs],
            "codex_verdict_prs": [p["number"] for p in codex_blocking],
            "stale_claims": [i["number"] for i in stale],
        },
        "in_progress": [{"issue": i["number"], "title": i["title"], "age_days": round(age_days(i["updatedAt"], now), 1)} for i in by_state["agent:in-progress"]],
        "review": [{"pr": p["number"], "title": p["title"], "draft": p["isDraft"], "ci": p["ci"], "review": p.get("reviewDecision") or "none", "codex": p["codex"], "url": p["url"]} for p in agent_prs],
        "blocked": [{"issue": i["number"], "title": i["title"]} for i in by_state["agent:blocked"]],
        "ready": [{"issue": i["number"], "title": i["title"], "risk": next((lab for lab in labels(i) if lab.startswith("risk:")), "risk:?"),
                   "local_ok": "exec:local-ok" in labels(i), "age_days": round(age_days(i["createdAt"], now), 1)}
                  for i in sorted(by_state["agent:ready"], key=lambda i: i["createdAt"])],
        "recent_failures": [{"run": r["databaseId"], "workflow": r["name"], "branch": r["headBranch"], "event": r["event"], "at": r["createdAt"], "url": r["url"]} for r in failed_runs],
        "counters": {
            "days": a.days, "merged_agent_prs": len(merged_agent), "closed_unmerged_agent_prs": len(closed_agent),
            "escalations_open": len(by_state["agent:needs-human"]),
            "first_run_gate_pass_rate": (round(100 * sum(first_pass) / len(first_pass)) if first_pass else None),
            "first_run_sample": len(first_pass),
        },
    }
    if a.json:
        print(json.dumps(report, indent=2))
        return 0

    def issue_line(i):
        return f"- #{i['number']} {i['title']}  ({', '.join(sorted(labels(i)))})"

    out = [f"# Agent status — {repo} — {now.strftime('%Y-%m-%d %H:%M UTC')}", ""]
    out.append("## 1. Needs a human")
    nh = report["needs_human"]
    if not any(nh.values()):
        out.append("- nothing")
    for i in by_state["agent:needs-human"]:
        out.append(issue_line(i) + " — escalated")
    for i in decisions:
        out.append(issue_line(i) + " — decision open")
    for p in changes_requested:
        out.append(f"- PR #{p['number']} {p['title']} — changes requested")
    for p in red_prs:
        out.append(f"- PR #{p['number']} {p['title']} — CI red ({p['url']})")
    for p in codex_blocking:
        out.append(f"- PR #{p['number']} {p['title']} — Codex review {p['codex']} ({p['url']})")
    for i in stale:
        out.append(issue_line(i) + f" — claimed {age_days(i['updatedAt'], now):.1f} d ago with no update")
    out += ["", "## 2. In flight"]
    out += [f"- #{x['issue']} {x['title']} — in progress, last update {x['age_days']} d ago" for x in report["in_progress"]] or ["- no claims"]
    out += [f"- PR #{x['pr']} {x['title']} — {'draft' if x['draft'] else 'ready'}, CI {x['ci']}, review {x['review']}, review codex={x['codex']}" for x in report["review"]] or ["- no agent PRs open"]
    out += ["", "## 3. Blocked"]
    out += [issue_line(i) for i in by_state["agent:blocked"]] or ["- none"]
    out += ["", "## 4. Ready queue"]
    out += [f"- #{x['issue']} {x['title']} — {x['risk']}{', local-ok' if x['local_ok'] else ''}, waiting {x['age_days']} d" for x in report["ready"]] or ["- empty"]
    out += ["", "## 5. Recent CI failures"]
    out += [f"- {r['workflow']} on {r['branch']} ({r['event']}) at {r['at']} — {r['url']}" for r in report["recent_failures"]] or ["- none"]
    c = report["counters"]
    rate = "n/a" if c["first_run_gate_pass_rate"] is None else f"{c['first_run_gate_pass_rate']}% of {c['first_run_sample']}"
    out += ["", f"## 6. Counters (last {c['days']} days)",
            f"- merged agent PRs: {c['merged_agent_prs']}", f"- agent PRs closed unmerged: {c['closed_unmerged_agent_prs']}",
            f"- first-run CI pass rate on merged agent PRs: {rate}", f"- open escalations: {c['escalations_open']}"]
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"gh failed: {' '.join(e.cmd)}\n{e.stderr}")
        sys.exit(1)
