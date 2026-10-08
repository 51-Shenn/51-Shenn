"""Fetch and summarize public GitHub profile statistics."""

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
import json
import os
import re
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


def request(url, token=None, payload=None):
    headers = {"User-Agent": "51-Shenn-profile-stats", "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    with urlopen(Request(url, data=data, headers=headers), timeout=30) as response:
        return response.read().decode("utf-8")


class CalendarParser(HTMLParser):
    """Read exact daily counts from GitHub's public calendar tooltips."""

    def __init__(self):
        super().__init__()
        self.dates = {}
        self.counts = {}
        self.target = None
        self.text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "td" and "data-date" in attrs:
            self.dates[attrs["id"]] = attrs["data-date"]
        if tag == "tool-tip":
            self.target = attrs.get("for")
            self.text = []

    def handle_data(self, data):
        if self.target:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "tool-tip" and self.target:
            match = re.match(r"(No|[\d,]+) contributions? on ", "".join(self.text).strip())
            if match:
                self.counts[self.target] = 0 if match[1] == "No" else int(match[1].replace(",", ""))
            self.target = None


def public_data(login, since, today):
    # GitHub's public endpoint selects one calendar year for a dated request.
    # Fetch both years when the rolling window spans New Year.
    by_date = {}
    for year in range(since.year, today.year + 1):
        params = urlencode({"from": f"{year}-01-01", "to": f"{year}-12-31"})
        parser = CalendarParser()
        parser.feed(request(f"https://github.com/users/{quote(login)}/contributions?{params}"))
        for cell, iso in parser.dates.items():
            if since.isoformat() <= iso <= today.isoformat():
                if cell not in parser.counts:
                    raise ValueError(f"GitHub calendar count missing for {iso}; keeping previous graphics")
                by_date[iso] = parser.counts[cell]
    days = [{"date": iso, "count": count} for iso, count in sorted(by_date.items())]

    byte_totals, repo_totals = Counter(), Counter()
    page = 1
    while True:
        repos = json.loads(request(f"https://api.github.com/users/{quote(login)}/repos?type=owner&per_page=100&page={page}"))
        for repo in repos:
            if repo["fork"] or repo["private"]:
                continue
            sizes = json.loads(request(repo["languages_url"]))
            byte_totals.update(sizes)
            if sizes:
                repo_totals[min(sizes, key=lambda name: (-sizes[name], name))] += 1
        if len(repos) < 100:
            break
        page += 1
    return days, byte_totals, repo_totals


QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!, $cursor: String) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
    repositories(first: 100, after: $cursor, ownerAffiliations: OWNER,
                 privacy: PUBLIC, isFork: false) {
      pageInfo { hasNextPage endCursor }
      nodes {
        languages(first: 100, orderBy: {field: SIZE, direction: DESC}) {
          pageInfo { hasNextPage }
          edges { size node { name } }
        }
      }
    }
  }
}
"""


def authenticated_data(login, token, since, today):
    variables = {"login": login, "from": f"{since}T00:00:00Z", "to": f"{today}T23:59:59Z", "cursor": None}
    days, byte_totals, repo_totals = [], Counter(), Counter()
    while True:
        payload = json.loads(request("https://api.github.com/graphql", token, {"query": QUERY, "variables": variables}))
        if payload.get("errors"):
            raise ValueError(f"GitHub GraphQL error: {payload['errors']}")
        user = (payload.get("data") or {}).get("user")
        if not user:
            raise ValueError(f"GitHub user not found: {login}")
        if not days:
            days = [{"date": d["date"], "count": d["contributionCount"]}
                    for week in user["contributionsCollection"]["contributionCalendar"]["weeks"]
                    for d in week["contributionDays"] if since.isoformat() <= d["date"] <= today.isoformat()]
        repos = user["repositories"]
        for repo in repos["nodes"]:
            languages = repo["languages"]
            if languages["pageInfo"]["hasNextPage"]:
                raise ValueError("Repository has over 100 languages; refusing to publish incomplete totals")
            sizes = {e["node"]["name"]: e["size"] for e in languages["edges"]}
            byte_totals.update(sizes)
            if sizes:
                repo_totals[min(sizes, key=lambda name: (-sizes[name], name))] += 1
        if not repos["pageInfo"]["hasNextPage"]:
            return days, byte_totals, repo_totals
        variables["cursor"] = repos["pageInfo"]["endCursor"]


def fetch(login):
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", login):
        raise ValueError("Invalid GitHub username")
    today = datetime.now(timezone.utc).date()
    since = today - timedelta(days=364)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    fetcher = authenticated_data if token else public_data
    args = (login, token, since, today) if token else (login, since, today)
    days, byte_totals, repo_totals = fetcher(*args)
    days.sort(key=lambda d: d["date"])
    expected = [(since + timedelta(days=i)).isoformat() for i in range(365)]
    if [d["date"] for d in days] != expected:
        raise ValueError("GitHub did not return a complete 365-day calendar; keeping previous graphics")
    return {"login": login, "days": days,
            "languages": [{"name": name, "bytes": size, "repos": repo_totals[name]}
                          for name, size in sorted(byte_totals.items(), key=lambda item: (-item[1], item[0]))]}


def summarize(data):
    days = data["days"]
    weeks = []
    for day in days:
        d = date.fromisoformat(day["date"])
        sunday = (d - timedelta(days=(d.weekday() + 1) % 7)).isoformat()
        if not weeks or weeks[-1]["start"] != sunday:
            weeks.append({"start": sunday, "days": []})
        weeks[-1]["days"].append(day)
    longest = {"length": 0, "start": None, "end": None}
    run, start = 0, None
    for day in days:
        if day["count"]:
            run += 1
            start = start or day["date"]
            if run > longest["length"]:
                longest = {"length": run, "start": start, "end": day["date"]}
        else:
            run, start = 0, None
    current = {"length": 0, "start": None, "end": None}
    tail = days[:-1] if days and not days[-1]["count"] else days
    for day in reversed(tail):
        if not day["count"]:
            break
        current["length"] += 1
        current["start"] = day["date"]
        current["end"] = current["end"] or day["date"]
    weekly = [sum(d["count"] for d in week["days"]) for week in weeks]
    return {**data, "total": sum(d["count"] for d in days),
            "active": sum(d["count"] > 0 for d in days), "weekly": weekly,
            "weeks": weeks, "current": current, "longest": longest}
