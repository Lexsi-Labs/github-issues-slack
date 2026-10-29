"""GitHub issues of the 6 Lexsi-Labs toolkit repos -> one Slack channel each.
Run by .github/workflows/issues-slack.yml every 10 minutes.

Posts new issues, new issue comments, and closed/reopened issues (pull requests are skipped).
Each run covers everything since the start of the last successful run of the workflow, so nothing
is missed when a run fails or is delayed, and no state file is kept in the repo. The very first
run only sets that starting point and posts nothing.

Each repo posts to its own Slack webhook, stored as an Actions secret (SLACK_WEBHOOK_<REPO>).
A Slack Workflow Builder webhook ("Starts with a webhook", one variable named `text`) or a
classic Slack incoming webhook both work. A repo without its secret is skipped.

Usage:  GITHUB_TOKEN=... GITHUB_REPOSITORY=owner/repo RUN_ID=... python3 scripts/issues_to_slack.py
"""
import http.client, json, os, re, time, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone

REPOS = ["aligntune", "AgentTune", "AuditKIT", "CircuitKIT", "CuratorKIT", "SafeTune"]   # all Lexsi-Labs
ORG = "Lexsi-Labs"
WORKFLOW = "issues-slack.yml"
# Timeouts, dropped connections and cut-off responses (http.client.IncompleteRead)
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException)


def request(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    for attempt in range(3):   # retry network hiccups before giving up
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
                return json.loads(body) if body.strip().startswith((b"{", b"[")) else body
        except urllib.error.HTTPError:
            raise
        except NETWORK_ERRORS:
            if attempt == 2:
                raise
            time.sleep(10 * (attempt + 1))


def gh(path):
    return request(f"https://api.github.com/{path}", headers={
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json", "User-Agent": "lexsi-hackathon-scripts/1.0"})


def gh_all(path):
    items, page = [], 1
    while True:
        batch = gh(f"{path}&per_page=100&page={page}")
        items += batch
        if len(batch) < 100:
            return items
        page += 1


def last_success_start():
    """Start time of the last successful run of this workflow (other than this one), or None."""
    runs = gh(f"repos/{os.environ['GITHUB_REPOSITORY']}/actions/workflows/{WORKFLOW}/runs"
              f"?status=success&per_page=5")["workflow_runs"]
    runs = [r for r in runs if str(r["id"]) != os.environ.get("RUN_ID")]
    return runs[0]["run_started_at"] if runs else None


LINE = "━" * 28
IST = timezone(timedelta(hours=5, minutes=30))


def ist(when):
    """GitHub's UTC timestamp shown in India time, e.g. 25 Oct 2026, 09:21 IST."""
    t = datetime.strptime(when, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(IST)
    return t.strftime("%d %b %Y, %H:%M IST")


def excerpt(text, n=700):
    """The issue text as written, line breaks kept, cut at a line break after about n characters."""
    text = re.sub(r"\n{3,}", "\n\n", (text or "").replace("\r", "")).strip()
    if len(text) <= n:
        return text
    cut = text.rfind("\n", 0, n)
    return text[:cut if cut > n // 2 else n].rstrip() + "\n…"


def message(head, repo, issue, who, when, body=None, url=None):
    """One card per post: a line above and below, blank lines between header, text and link.
    Plain text, since Workflow Builder variables don't render Slack markup."""
    tags = ", ".join(l["name"] for l in issue.get("labels", []))
    parts = [f"{head} · {repo} #{issue['number']}\n{issue['title'].strip()}\n"
             f"👤 {who}  ·  📅 {ist(when)}" + (f"  ·  🏷️ {tags}" if tags else "")]
    if body and body.strip():
        parts.append(excerpt(body, 400))
    parts.append(f"🔗 {url or issue['html_url']}")
    return f"{LINE}\n" + "\n\n".join(parts) + f"\n{LINE}"


def events(repo, since, until):
    """(time, text) for each new issue, comment, close and reopen in [since, until)."""
    out, inside = [], lambda t: t and since <= t < until
    for i in gh_all(f"repos/{ORG}/{repo}/issues?state=all&since={since}"):
        if "pull_request" in i:
            continue
        if inside(i["created_at"]):
            out.append((i["created_at"], message("🆕 New issue", repo, i, i["user"]["login"], i["created_at"], i["body"])))
        if i["state"] == "closed" and inside(i["closed_at"]):
            who = (i.get("closed_by") or {}).get("login", "someone")
            out.append((i["closed_at"], message("✅ Closed", repo, i, who, i["closed_at"])))
    for e in gh(f"repos/{ORG}/{repo}/issues/events?per_page=100"):   # newest 100, plenty for 10 minutes
        if e["event"] == "reopened" and inside(e["created_at"]) and "pull_request" not in e["issue"]:
            who = (e.get("actor") or {}).get("login", "someone")
            out.append((e["created_at"], message("🔄 Reopened", repo, e["issue"], who, e["created_at"])))
        elif e["created_at"] < since:
            break   # events come newest first
    for c in gh_all(f"repos/{ORG}/{repo}/issues/comments?since={since}"):
        if "/pull/" in c["html_url"] or not inside(c["created_at"]):
            continue
        issue = gh(c["issue_url"].split("api.github.com/", 1)[1])
        out.append((c["created_at"], message("💬 Comment", repo, issue, c["user"]["login"], c["created_at"],
                                             c["body"], c["html_url"])))
    return sorted(out)


def main():
    until = os.environ.get("RUN_STARTED_AT") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    since = last_success_start()
    if not since:
        print("First run: starting point set, nothing posted.")
        return
    print(f"Window: {since} to {until}")
    for repo in REPOS:
        hook = os.environ.get("SLACK_WEBHOOK_" + repo.upper())
        if not hook:
            print(f"{repo}: no SLACK_WEBHOOK_{repo.upper()} secret, skipped")
            continue
        posts = events(repo, since, until)
        for _, text in posts:
            request(hook, json.dumps({"text": text}).encode(), {"Content-Type": "application/json"})
        print(f"{repo}: {len(posts)} posted")


if __name__ == "__main__":
    # If anything still fails after the retries, the run fails on purpose: the next run then starts
    # from the last successful one and covers this window again, so nothing is lost.
    main()
