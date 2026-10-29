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
import http.client, json, os, time, urllib.request, urllib.error

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


def events(repo, since, until):
    """(time, text) for each new issue, comment, close and reopen in [since, until)."""
    out, inside = [], lambda t: t and since <= t < until
    for i in gh_all(f"repos/{ORG}/{repo}/issues?state=all&since={since}"):
        if "pull_request" in i:
            continue
        link, who = f"<{i['html_url']}|#{i['number']} {i['title']}>", i["user"]["login"]
        if inside(i["created_at"]):
            out.append((i["created_at"], f"*New issue* in {repo}: {link} by {who}"))
        if i["state"] == "closed" and inside(i["closed_at"]):
            out.append((i["closed_at"], f"*Closed* in {repo}: {link}"))
    for e in gh(f"repos/{ORG}/{repo}/issues/events?per_page=100"):   # newest 100, plenty for 10 minutes
        if e["event"] == "reopened" and inside(e["created_at"]) and "pull_request" not in e["issue"]:
            i = e["issue"]
            out.append((e["created_at"], f"*Reopened* in {repo}: <{i['html_url']}|#{i['number']} {i['title']}> "
                                         f"by {(e.get('actor') or {}).get('login', 'someone')}"))
        elif e["created_at"] < since:
            break   # events come newest first
    for c in gh_all(f"repos/{ORG}/{repo}/issues/comments?since={since}"):
        if "/pull/" in c["html_url"] or not inside(c["created_at"]):
            continue
        body = " ".join(c["body"].split())
        body = body[:300] + ("…" if len(body) > 300 else "")
        out.append((c["created_at"], f"*Comment* in {repo} by {c['user']['login']} on <{c['html_url']}|"
                                     f"#{c['issue_url'].rsplit('/', 1)[1]}>: {body}"))
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
