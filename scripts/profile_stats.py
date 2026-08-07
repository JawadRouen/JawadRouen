#!/usr/bin/env python3
"""Generate factual GitHub profile statistics without publishing private details."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import html
import json
import os
import pathlib
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from typing import Any, Iterable


API_ROOT = "https://api.github.com"
GRAPHQL_URL = f"{API_ROOT}/graphql"
DEFAULT_LOGIN = "JawadRouen"
DEFAULT_YEARS = 5

LANGUAGE_SUFFIXES = {
    ".py": "Python",
    ".pyi": "Python",
    ".pyx": "Python",
    ".pxd": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".mts": "TypeScript",
    ".cts": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".r": "R",
    ".sql": "SQL",
    ".psql": "SQL",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".scala": "Scala",
    ".sc": "Scala",
    ".cs": "C#",
    ".fs": "F#",
    ".fsx": "F#",
    ".c": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".cxx": "C++",
    ".h": "C/C++",
    ".hh": "C++",
    ".hpp": "C++",
    ".hxx": "C++",
    ".swift": "Swift",
    ".rb": "Ruby",
    ".php": "PHP",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".erl": "Erlang",
    ".hrl": "Erlang",
    ".clj": "Clojure",
    ".cljs": "Clojure",
    ".cljc": "Clojure",
    ".groovy": "Groovy",
    ".dart": "Dart",
    ".jl": "Julia",
    ".lua": "Lua",
    ".pl": "Perl",
    ".pm": "Perl",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".fish": "Shell",
    ".ps1": "PowerShell",
    ".psm1": "PowerShell",
    ".tf": "HCL",
    ".hcl": "HCL",
    ".proto": "Protocol Buffer",
    ".sol": "Solidity",
    ".vue": "Vue",
    ".svelte": "Svelte",
}

SPECIAL_FILENAMES = {
    "dockerfile": "Dockerfile",
    "containerfile": "Dockerfile",
    "makefile": "Makefile",
    "gnumakefile": "Makefile",
    "cmakelists.txt": "CMake",
    "jenkinsfile": "Groovy",
}

IGNORED_PARTS = {
    "node_modules",
    "vendor",
    "vendors",
    "dist",
    "build",
    "coverage",
    ".next",
    ".nuxt",
    ".venv",
    "venv",
    "site-packages",
    "__pycache__",
    "generated",
    "fixtures",
    "snapshots",
}

IGNORED_FILENAMES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "pipfile.lock",
    "composer.lock",
    "cargo.lock",
    "go.sum",
}

LANGUAGE_COLORS = {
    "Python": "#3572A5",
    "TypeScript": "#3178C6",
    "JavaScript": "#F1E05A",
    "R": "#198CE7",
    "SQL": "#E38C00",
    "Go": "#00ADD8",
    "Rust": "#DEA584",
    "Java": "#B07219",
    "Kotlin": "#A97BFF",
    "Scala": "#C22D40",
    "C#": "#178600",
    "C++": "#F34B7D",
    "C": "#555555",
    "C/C++": "#9F4F7C",
    "Shell": "#89E051",
    "PowerShell": "#012456",
    "HCL": "#844FBA",
    "Dockerfile": "#384D54",
    "Makefile": "#427819",
    "CMake": "#DA3434",
    "Other": "#8B949E",
}


class GitHubError(RuntimeError):
    """Raised when GitHub cannot provide a complete result."""


class GitHubClient:
    def __init__(self, token: str, min_interval: float = 0.09) -> None:
        if not token:
            raise GitHubError("GITHUB_TOKEN is required")
        self.token = token
        self.min_interval = min_interval
        self._throttle_lock = threading.Lock()
        self._last_request = 0.0

    def _throttle(self) -> None:
        with self._throttle_lock:
            elapsed = time.monotonic() - self._last_request
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last_request = time.monotonic()

    def request(
        self,
        path_or_url: str,
        params: dict[str, Any] | None = None,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        authenticated: bool = True,
        allow_statuses: Iterable[int] = (),
    ) -> tuple[Any, dict[str, str]]:
        url = path_or_url if path_or_url.startswith("http") else f"{API_ROOT}{path_or_url}"
        if params:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urllib.parse.urlencode(params)}"
        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "JawadRouen-profile-stats",
        }
        if authenticated:
            headers["Authorization"] = f"Bearer {self.token}"
        if payload is not None:
            headers["Content-Type"] = "application/json"

        for attempt in range(8):
            self._throttle()
            request = urllib.request.Request(url, data=payload, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    raw = response.read()
                    data = json.loads(raw) if raw else None
                    return data, {key.lower(): value for key, value in response.headers.items()}
            except urllib.error.HTTPError as error:
                if error.code in allow_statuses:
                    return None, {key.lower(): value for key, value in error.headers.items()}
                retry_after = error.headers.get("Retry-After")
                remaining = error.headers.get("X-RateLimit-Remaining")
                reset = error.headers.get("X-RateLimit-Reset")
                retryable = error.code in {403, 429, 500, 502, 503, 504}
                if retryable and attempt < 7:
                    if remaining == "0" and reset:
                        delay = max(1, int(reset) - int(time.time()) + 2)
                        print(f"GitHub core quota exhausted; waiting {delay} seconds for reset", flush=True)
                    elif retry_after:
                        delay = min(60, max(1, int(retry_after)))
                    else:
                        delay = min(30, 2 ** attempt)
                    time.sleep(delay)
                    continue
                details = error.read().decode("utf-8", errors="replace")
                raise GitHubError(f"GitHub API returned HTTP {error.code}: {details[:300]}") from error
            except urllib.error.URLError as error:
                if attempt < 7:
                    time.sleep(min(30, 2 ** attempt))
                    continue
                raise GitHubError(f"GitHub API request failed: {error}") from error
        raise GitHubError("GitHub API retry budget exhausted")

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        data, _ = self.request(
            GRAPHQL_URL,
            method="POST",
            body={"query": query, "variables": variables},
        )
        if data.get("errors"):
            raise GitHubError(f"GitHub GraphQL errors: {data['errors']}")
        return data["data"]

    def search_total(self, endpoint: str, query: str, *, authenticated: bool = True) -> int:
        data, _ = self.request(
            f"/search/{endpoint}",
            {"q": query, "per_page": 1},
            authenticated=authenticated,
        )
        return int(data["total_count"])

    def pages(
        self,
        path: str,
        params: dict[str, Any],
        *,
        allow_statuses: Iterable[int] = (),
    ) -> Iterable[list[dict[str, Any]]]:
        page = 1
        per_page = int(params.get("per_page", 100))
        while True:
            page_params = dict(params)
            page_params["page"] = page
            data, _ = self.request(path, page_params, allow_statuses=allow_statuses)
            if data is None:
                return
            if not isinstance(data, list):
                raise GitHubError(f"Expected a list from {path}")
            yield data
            if len(data) < per_page:
                return
            page += 1


def rolling_start(today: dt.date, years: int) -> dt.date:
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


def iso_z(day: dt.date, end: bool = False) -> str:
    return f"{day.isoformat()}T{'23:59:59' if end else '00:00:00'}Z"


def format_number(value: int) -> str:
    return f"{int(value):,}"


def xml(value: Any) -> str:
    return html.escape(str(value), quote=True)


def year_ranges(start: dt.date, end: dt.date) -> list[tuple[int, dt.date, dt.date]]:
    ranges: list[tuple[int, dt.date, dt.date]] = []
    for year in range(start.year, end.year + 1):
        lower = max(start, dt.date(year, 1, 1))
        upper = min(end, dt.date(year, 12, 31))
        ranges.append((year, lower, upper))
    return ranges


def list_accessible_repositories(client: GitHubClient) -> list[dict[str, Any]]:
    repos: list[dict[str, Any]] = []
    params = {
        "visibility": "all",
        "affiliation": "owner,collaborator,organization_member",
        "sort": "full_name",
        "per_page": 100,
    }
    for page in client.pages("/user/repos", params):
        repos.extend(page)
    return repos


def collect_activity(client: GitHubClient, login: str, today: dt.date, years: int) -> dict[str, Any]:
    query = """
    query($login:String!){
      user(login:$login){
        issues{totalCount}
        pullRequests{totalCount}
        contributionsCollection{restrictedContributionsCount}
      }
    }
    """
    profile = client.graphql(query, {"login": login})["user"]
    totals = {
        "commits": client.search_total("commits", f"author:{login}"),
        "issues": int(profile["issues"]["totalCount"]),
        "pull_requests": int(profile["pullRequests"]["totalCount"]),
        "reviews": client.search_total("issues", f"is:pr reviewed-by:{login}"),
        "private_recent": int(profile["contributionsCollection"]["restrictedContributionsCount"]),
    }

    public = {
        "commits": client.search_total("commits", f"author:{login}", authenticated=False),
        "issues": client.search_total("issues", f"is:issue author:{login}", authenticated=False),
        "pull_requests": client.search_total("issues", f"is:pr author:{login}", authenticated=False),
        "reviews": client.search_total("issues", f"is:pr reviewed-by:{login}", authenticated=False),
    }
    authenticated_issue_search = client.search_total("issues", f"is:issue author:{login}")

    repos = list_accessible_repositories(client)
    private_repo_count = sum(1 for repo in repos if repo.get("private"))
    if private_repo_count == 0:
        raise GitHubError("The token cannot access any private repositories")
    if totals["private_recent"] <= 0:
        raise GitHubError("GitHub returned no restricted private contributions")
    for key in ("commits", "pull_requests", "reviews"):
        if totals[key] <= public[key]:
            raise GitHubError(f"Authenticated {key} total does not include more activity than public access")
    if authenticated_issue_search <= public["issues"]:
        raise GitHubError("Authenticated issue search does not include private activity")

    start = rolling_start(today, years)
    by_year: list[dict[str, int]] = []
    for year, lower, upper in year_ranges(start, today):
        count = client.search_total(
            "commits",
            f"author:{login} author-date:{lower.isoformat()}..{upper.isoformat()}",
        )
        by_year.append({"year": year, "commits": count})

    return {
        "login": login,
        "totals": totals,
        "public_baseline": public,
        "authenticated_issue_search": authenticated_issue_search,
        "private_repository_count": private_repo_count,
        "window_start": start.isoformat(),
        "window_end": today.isoformat(),
        "window_commits": sum(item["commits"] for item in by_year),
        "by_year": by_year,
        "updated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def language_for_path(filename: str) -> str | None:
    normalized = filename.replace("\\", "/").lower()
    parts = [part for part in normalized.split("/") if part]
    if any(part in IGNORED_PARTS for part in parts[:-1]):
        return None
    basename = parts[-1] if parts else normalized
    if basename in IGNORED_FILENAMES:
        return None
    if basename.endswith((".min.js", ".min.css", ".map", ".lock", ".ipynb")):
        return None
    if basename in SPECIAL_FILENAMES:
        return SPECIAL_FILENAMES[basename]
    suffixes = pathlib.PurePosixPath(basename).suffixes
    for suffix in reversed(suffixes):
        language = LANGUAGE_SUFFIXES.get(suffix.lower())
        if language:
            return language
    return None


def repository_commit_refs(
    client: GitHubClient,
    repo: dict[str, Any],
    login: str,
    start: dt.date,
    end: dt.date,
) -> list[dict[str, Any]]:
    full_name = repo["full_name"]
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in full_name.split("/"))
    params = {
        "sha": repo["default_branch"],
        "author": login,
        "since": iso_z(start),
        "until": iso_z(end, end=True),
        "per_page": 100,
    }
    refs: list[dict[str, Any]] = []
    for page in client.pages(f"/repos/{encoded}/commits", params, allow_statuses={404, 409}):
        for commit in page:
            refs.append(
                {
                    "sha": commit["sha"],
                    "repo": full_name,
                    "repo_id": int(repo["id"]),
                    "private": bool(repo.get("private")),
                    "date": commit["commit"]["author"]["date"],
                }
            )
    return refs


def commit_detail(client: GitHubClient, ref: dict[str, Any]) -> dict[str, Any]:
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in ref["repo"].split("/"))
    path = f"/repos/{encoded}/commits/{ref['sha']}"
    data, _ = client.request(path, {"per_page": 100, "page": 1})
    files = list(data.get("files") or [])
    page = 2
    while len(files) == (page - 1) * 100:
        extra, _ = client.request(path, {"per_page": 100, "page": page})
        extra_files = list(extra.get("files") or [])
        files.extend(extra_files)
        if len(extra_files) < 100:
            break
        page += 1
    return {
        "sha": ref["sha"],
        "repo_id": ref["repo_id"],
        "private": ref["private"],
        "date": ref["date"],
        "merge": len(data.get("parents") or []) > 1,
        "files": files,
    }


def collect_depth(
    client: GitHubClient,
    login: str,
    today: dt.date,
    years: int,
    workers: int,
    max_repos: int | None = None,
    max_commits: int | None = None,
) -> dict[str, Any]:
    start = rolling_start(today, years)
    repos = list_accessible_repositories(client)
    private_repo_count = sum(1 for repo in repos if repo.get("private"))
    if private_repo_count == 0:
        raise GitHubError("The token cannot access any private repositories")
    if max_repos is not None:
        repos = repos[:max_repos]

    print(f"Scanning {len(repos)} accessible repositories ({private_repo_count} private available)", flush=True)
    unique_refs: dict[str, dict[str, Any]] = {}
    repos_with_commits: set[int] = set()
    for index, repo in enumerate(repos, start=1):
        refs = repository_commit_refs(client, repo, login, start, today)
        if refs:
            repos_with_commits.add(int(repo["id"]))
        for ref in refs:
            existing = unique_refs.get(ref["sha"])
            if existing is None or (existing["private"] and not ref["private"]):
                unique_refs[ref["sha"]] = ref
        if index % 25 == 0 or index == len(repos):
            print(f"Repository scan {index}/{len(repos)}; {len(unique_refs)} unique authored commits found", flush=True)

    refs = sorted(unique_refs.values(), key=lambda item: item["date"])
    if max_commits is not None:
        refs = refs[-max_commits:]
    if not refs:
        raise GitHubError("No authored commits found in the selected history window")

    languages: Counter[str] = Counter()
    added_by_language: Counter[str] = Counter()
    deleted_by_language: Counter[str] = Counter()
    languages_by_year: dict[int, Counter[str]] = defaultdict(Counter)
    active_days: set[str] = set()
    repositories_analyzed: set[int] = set()
    private_repositories_analyzed: set[int] = set()
    public_repositories_analyzed: set[int] = set()
    non_merge_commits = 0
    merge_commits = 0
    private_non_merge_commits = 0
    public_non_merge_commits = 0
    private_source_line_changes = 0
    public_source_line_changes = 0
    code_file_changes = 0
    retry_refs: list[dict[str, Any]] = []

    def aggregate_detail(detail: dict[str, Any]) -> None:
        nonlocal non_merge_commits
        nonlocal merge_commits
        nonlocal private_non_merge_commits
        nonlocal public_non_merge_commits
        nonlocal private_source_line_changes
        nonlocal public_source_line_changes
        nonlocal code_file_changes

        repositories_analyzed.add(detail["repo_id"])
        if detail["private"]:
            private_repositories_analyzed.add(detail["repo_id"])
        else:
            public_repositories_analyzed.add(detail["repo_id"])
        if detail["merge"]:
            merge_commits += 1
            return
        active_days.add(detail["date"][:10])
        non_merge_commits += 1
        if detail["private"]:
            private_non_merge_commits += 1
        else:
            public_non_merge_commits += 1
        commit_line_changes = 0
        commit_year = int(detail["date"][:4])
        for changed_file in detail["files"]:
            language = language_for_path(changed_file["filename"])
            if not language:
                continue
            additions = int(changed_file.get("additions") or 0)
            deletions = int(changed_file.get("deletions") or 0)
            changed = additions + deletions
            if changed <= 0:
                continue
            languages[language] += changed
            added_by_language[language] += additions
            deleted_by_language[language] += deletions
            languages_by_year[commit_year][language] += changed
            commit_line_changes += changed
            code_file_changes += 1
        if detail["private"]:
            private_source_line_changes += commit_line_changes
        else:
            public_source_line_changes += commit_line_changes

    print(f"Analyzing {len(refs)} unique commit diffs with {workers} workers", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(commit_detail, client, ref): ref for ref in refs}
        for completed, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            ref = futures[future]
            try:
                detail = future.result()
            except Exception:  # noqa: BLE001 - retry serially, then fail closed
                retry_refs.append(ref)
            else:
                aggregate_detail(detail)
            if completed % 100 == 0 or completed == len(refs):
                print(f"Commit analysis {completed}/{len(refs)}", flush=True)

    final_failures = 0
    if retry_refs:
        print(f"Retrying {len(retry_refs)} commit details serially", flush=True)
        time.sleep(5)
        for ref in retry_refs:
            try:
                aggregate_detail(commit_detail(client, ref))
            except Exception:  # noqa: BLE001 - fail closed after the serial retry pass
                final_failures += 1
    if final_failures:
        raise GitHubError(f"Incomplete commit extraction: {final_failures} commit details remain unavailable")
    if not languages:
        raise GitHubError("No source-language changes were detected")

    search_commits = client.search_total(
        "commits",
        f"author:{login} author-date:{start.isoformat()}..{today.isoformat()}",
    )
    top_languages = []
    total_line_changes = sum(languages.values())
    for language, changes in languages.most_common():
        top_languages.append(
            {
                "language": language,
                "changes": changes,
                "additions": added_by_language[language],
                "deletions": deleted_by_language[language],
                "percentage": changes / total_line_changes * 100,
            }
        )

    yearly_languages = []
    for year in range(start.year, today.year + 1):
        counter = languages_by_year.get(year, Counter())
        year_total = sum(counter.values())
        yearly_languages.append(
            {
                "year": year,
                "changes": year_total,
                "languages": [
                    {
                        "language": language,
                        "changes": changes,
                        "percentage": (changes / year_total * 100) if year_total else 0,
                    }
                    for language, changes in counter.most_common()
                ],
            }
        )

    return {
        "login": login,
        "window_start": start.isoformat(),
        "window_end": today.isoformat(),
        "search_authored_commits": search_commits,
        "unique_commits_found": len(unique_refs),
        "commits_detailed": len(refs),
        "non_merge_commits_analyzed": non_merge_commits,
        "private_non_merge_commits": private_non_merge_commits,
        "public_non_merge_commits": public_non_merge_commits,
        "merge_commits_excluded": merge_commits,
        "repositories_with_authored_commits": len(repos_with_commits),
        "repositories_analyzed": len(repositories_analyzed),
        "private_repositories_analyzed": len(private_repositories_analyzed),
        "public_repositories_analyzed": len(public_repositories_analyzed),
        "active_coding_days": len(active_days),
        "source_lines_added": sum(added_by_language.values()),
        "source_lines_deleted": sum(deleted_by_language.values()),
        "source_line_changes": total_line_changes,
        "private_source_line_changes": private_source_line_changes,
        "public_source_line_changes": public_source_line_changes,
        "code_file_changes": code_file_changes,
        "languages": top_languages,
        "languages_by_year": yearly_languages,
        "private_repository_count_available": private_repo_count,
        "updated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "partial": max_repos is not None or max_commits is not None,
    }


def svg_header(width: int, height: int, title: str, subtitle: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        f"<title id=\"title\">{xml(title)}</title>",
        f"<desc id=\"desc\">{xml(subtitle)}</desc>",
        "<style>",
        "text{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif}",
        ".title{font-size:26px;font-weight:700;fill:#F0F6FC}.sub{font-size:14px;fill:#8B949E}",
        ".label{font-size:13px;font-weight:600;fill:#8B949E}.value{font-size:28px;font-weight:700;fill:#F0F6FC}",
        ".small{font-size:12px;fill:#8B949E}.barlabel{font-size:13px;font-weight:600;fill:#C9D1D9}",
        "</style>",
        f'<rect width="{width}" height="{height}" rx="14" fill="#0D1117"/>',
        f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="13.5" fill="none" stroke="#30363D"/>',
        f'<text class="title" x="32" y="48">{xml(title)}</text>',
        f'<text class="sub" x="32" y="74">{xml(subtitle)}</text>',
    ]


def render_activity(data: dict[str, Any], output: pathlib.Path) -> None:
    width, height = 960, 520
    totals = data["totals"]
    lines = svg_header(
        width,
        height,
        "Engineering activity",
        "Authenticated GitHub totals across accessible public and private repositories",
    )
    cards = [
        ("Authored commits", totals["commits"]),
        ("Issues opened", totals["issues"]),
        ("Pull requests opened", totals["pull_requests"]),
        ("Pull requests reviewed", totals["reviews"]),
    ]
    for index, (label, value) in enumerate(cards):
        x = 32 + index * 232
        lines.extend(
            [
                f'<rect x="{x}" y="102" width="216" height="98" rx="10" fill="#161B22" stroke="#30363D"/>',
                f'<text class="label" x="{x + 16}" y="132">{xml(label)}</text>',
                f'<text class="value" x="{x + 16}" y="174">{format_number(value)}</text>',
            ]
        )

    lines.extend(
        [
            '<rect x="32" y="220" width="896" height="62" rx="10" fill="#111D2E" stroke="#1F6FEB"/>',
            '<text class="label" x="52" y="246" fill="#58A6FF">PRIVATE CONTRIBUTION ACTIVITY</text>',
            f'<text class="value" x="52" y="270" font-size="22">{format_number(totals["private_recent"])} <tspan class="sub">contributions during the past year</tspan></text>',
        ]
    )

    chart_top, chart_bottom = 336, 456
    chart_height = chart_bottom - chart_top
    entries = data["by_year"]
    maximum = max(item["commits"] for item in entries) or 1
    slot = 860 / len(entries)
    bar_width = min(82, slot * 0.58)
    lines.extend(
        [
            '<text class="label" x="32" y="318">AUTHORED COMMITS BY YEAR</text>',
            f'<text class="small" x="928" y="318" text-anchor="end">{format_number(data["window_commits"])} in rolling five-year window</text>',
            f'<line x1="48" y1="{chart_bottom}" x2="912" y2="{chart_bottom}" stroke="#30363D"/>',
        ]
    )
    for index, item in enumerate(entries):
        center = 62 + index * slot + slot / 2
        raw_height = item["commits"] / maximum * chart_height
        bar_height = max(4, raw_height)
        x = center - bar_width / 2
        y = chart_bottom - bar_height
        lines.extend(
            [
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" rx="5" fill="#238636"/>',
                f'<text class="barlabel" x="{center:.1f}" y="{max(chart_top - 6, y - 8):.1f}" text-anchor="middle">{format_number(item["commits"])}</text>',
                f'<text class="small" x="{center:.1f}" y="480" text-anchor="middle">{item["year"]}</text>',
            ]
        )
    lines.append(
        f'<text class="small" x="928" y="504" text-anchor="end">Updated {xml(data["updated_at"][:10])} UTC</text>'
    )
    lines.append("</svg>")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_depth(data: dict[str, Any], output: pathlib.Path) -> None:
    width, height = 960, 900
    lines = svg_header(
        width,
        height,
        "Five-year engineering depth",
        f"Authored non-merge commit diffs from {data['window_start']} through {data['window_end']}",
    )
    cards = [
        ("Authored commits", data["search_authored_commits"]),
        ("Repositories touched", data["repositories_with_authored_commits"]),
        ("Active coding days", data["active_coding_days"]),
        ("Source lines changed", data["source_line_changes"]),
        ("Code file changes", data["code_file_changes"]),
    ]
    card_width = 171
    for index, (label, value) in enumerate(cards):
        x = 32 + index * 179
        lines.extend(
            [
                f'<rect x="{x}" y="102" width="{card_width}" height="94" rx="10" fill="#161B22" stroke="#30363D"/>',
                f'<text class="label" x="{x + 14}" y="130">{xml(label)}</text>',
                f'<text class="value" x="{x + 14}" y="170" font-size="25">{format_number(value)}</text>',
            ]
        )
    lines.extend(
        [
            f'<text class="small" x="32" y="224"><tspan fill="#3FB950">+{format_number(data["source_lines_added"])} added</tspan><tspan dx="20" fill="#F85149">-{format_number(data["source_lines_deleted"])} deleted</tspan><tspan dx="20">{format_number(data["non_merge_commits_analyzed"])} non-merge commits analyzed</tspan></text>',
            '<text class="label" x="32" y="266">PROGRAMMING LANGUAGES IN AUTHORED LINE CHANGES</text>',
        ]
    )

    languages = list(data["languages"])
    if len(languages) > 7:
        kept = languages[:6]
        remaining = languages[6:]
        other_changes = sum(item["changes"] for item in remaining)
        kept.append(
            {
                "language": "Other",
                "changes": other_changes,
                "percentage": other_changes / data["source_line_changes"] * 100,
            }
        )
        languages = kept

    max_changes = max(item["changes"] for item in languages) or 1
    y = 294
    for item in languages:
        language = item["language"]
        bar_width = max(4, item["changes"] / max_changes * 610)
        color = LANGUAGE_COLORS.get(language, "#8B949E")
        lines.extend(
            [
                f'<text class="barlabel" x="32" y="{y + 14}">{xml(language)}</text>',
                f'<rect x="160" y="{y}" width="610" height="18" rx="5" fill="#21262D"/>',
                f'<rect x="160" y="{y}" width="{bar_width:.1f}" height="18" rx="5" fill="{color}"/>',
                f'<text class="barlabel" x="792" y="{y + 14}">{item["percentage"]:.1f}%</text>',
                f'<text class="small" x="928" y="{y + 14}" text-anchor="end">{format_number(item["changes"])} lines</text>',
            ]
        )
        y += 36

    private_lines = int(data.get("private_source_line_changes", 0))
    total_lines = int(data["source_line_changes"])
    private_share = private_lines / total_lines * 100 if total_lines else 0
    lines.extend(
        [
            '<rect x="32" y="552" width="896" height="72" rx="10" fill="#111D2E" stroke="#1F6FEB"/>',
            '<text class="label" x="52" y="579" fill="#58A6FF">PRIVATE ENGINEERING ACTIVITY IN THE FIVE-YEAR EXTRACTION</text>',
            f'<text class="barlabel" x="52" y="607">{format_number(data.get("private_non_merge_commits", 0))} commit diffs</text>',
            f'<text class="barlabel" x="294" y="607">{format_number(data.get("private_repositories_analyzed", 0))} repositories</text>',
            f'<text class="barlabel" x="526" y="607">{format_number(private_lines)} source-line changes</text>',
            f'<text class="barlabel" x="876" y="607" text-anchor="end">{private_share:.1f}% of source changes</text>',
        ]
    )

    evolution_languages = ["Python", "TypeScript", "SQL", "JavaScript"]
    lines.extend(
        [
            '<text class="label" x="32" y="660">LANGUAGE EVOLUTION BY AUTHORED LINE CHANGES</text>',
            '<text class="small" x="928" y="660" text-anchor="end">each column totals 100%</text>',
        ]
    )
    legend_x = 390
    for language in evolution_languages + ["Other"]:
        color = LANGUAGE_COLORS.get(language, "#8B949E")
        lines.append(f'<rect x="{legend_x}" y="646" width="10" height="10" rx="2" fill="{color}"/>')
        lines.append(f'<text class="small" x="{legend_x + 15}" y="655">{xml(language)}</text>')
        legend_x += 92 if language != "TypeScript" else 110

    chart_top, chart_bottom = 690, 806
    yearly = data.get("languages_by_year", [])
    slot = 840 / max(1, len(yearly))
    bar_width = min(82, slot * 0.62)
    for index, year_data in enumerate(yearly):
        center = 60 + index * slot + slot / 2
        percentages = {item["language"]: item["percentage"] for item in year_data.get("languages", [])}
        segments = [(language, percentages.get(language, 0.0)) for language in evolution_languages]
        other = max(0.0, 100.0 - sum(value for _, value in segments))
        segments.append(("Other", other))
        cursor = chart_bottom
        for language, percentage in segments:
            segment_height = percentage / 100 * (chart_bottom - chart_top)
            if segment_height <= 0:
                continue
            cursor -= segment_height
            color = LANGUAGE_COLORS.get(language, "#8B949E")
            lines.append(
                f'<rect x="{center - bar_width / 2:.1f}" y="{cursor:.1f}" width="{bar_width:.1f}" height="{segment_height:.1f}" fill="{color}"/>'
            )
        lines.append(f'<rect x="{center - bar_width / 2:.1f}" y="{chart_top}" width="{bar_width:.1f}" height="{chart_bottom - chart_top}" rx="5" fill="none" stroke="#30363D"/>')
        top_language = year_data.get("languages", [{}])[0].get("language", "-") if year_data.get("languages") else "-"
        lines.append(f'<text class="small" x="{center:.1f}" y="828" text-anchor="middle">{year_data["year"]}</text>')
        lines.append(f'<text class="small" x="{center:.1f}" y="844" text-anchor="middle">{xml(top_language)}</text>')

    lines.extend(
        [
            f'<text class="small" x="32" y="{height - 28}">Programming and infrastructure source files; generated, vendored, lock, markup, data and notebook files excluded</text>',
            f'<text class="small" x="928" y="{height - 28}" text-anchor="end">Updated {xml(data["updated_at"][:10])} UTC</text>',
            "</svg>",
        ]
    )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_date(value: str | None) -> dt.date:
    return dt.date.fromisoformat(value) if value else dt.datetime.now(dt.timezone.utc).date()


def write_summary(data: dict[str, Any]) -> None:
    safe = {key: value for key, value in data.items() if key not in {"public_baseline"}}
    print(json.dumps(safe, indent=2, sort_keys=True), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("activity", "depth"))
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--login", default=DEFAULT_LOGIN)
    parser.add_argument("--years", default=DEFAULT_YEARS, type=int)
    parser.add_argument("--today", help="Override today's UTC date for reproducible tests")
    parser.add_argument("--workers", default=4, type=int)
    parser.add_argument("--max-repos", type=int, help="Development-only partial depth scan")
    parser.add_argument("--max-commits", type=int, help="Development-only partial depth scan")
    args = parser.parse_args()

    client = GitHubClient(os.environ.get("GITHUB_TOKEN", ""))
    today = parse_date(args.today)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.mode == "activity":
        data = collect_activity(client, args.login, today, args.years)
        render_activity(data, args.output)
    else:
        data = collect_depth(
            client,
            args.login,
            today,
            args.years,
            max(1, min(args.workers, 8)),
            args.max_repos,
            args.max_commits,
        )
        render_depth(data, args.output)
    write_summary(data)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GitHubError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
