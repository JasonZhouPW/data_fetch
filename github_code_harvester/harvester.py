from __future__ import annotations

import argparse
import csv
import datetime as dt
import html as html_lib
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Sequence


LANGUAGES = ("Go", "Java", "Python", "JavaScript", "C++")
SINCE_DATE = "2025-10-01"
SOURCE_NAME = "GitHub"
GITLAB_SOURCE_NAME = "GitLab"
STACKOVERFLOW_SOURCE_NAME = "StackOverflow"
GITLAB_BASE_URL = "https://gitlab.com"
GITLAB_PROJECTS_PER_PAGE = 10
TOKEN_CONFIG_PATH = "token.json"
STACKEXCHANGE_API_BASE_URL = "https://api.stackexchange.com/2.3"
DISCUSSION_TAGS = ("python", "java", "javascript", "go", "c++")

CODE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hpp",
    ".hxx",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".py",
}
NON_CODE_EXACT = {
    ".dockerignore",
    ".editorconfig",
    ".env",
    ".env.example",
    ".gitignore",
    "go.mod",
    "go.sum",
    "package-lock.json",
    "package.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pom.xml",
    "requirements.txt",
    "uv.lock",
    "yarn.lock",
}
NON_CODE_DIRS = {
    ".github",
    "benchmarks",
    "benchmark",
    "docs",
    "doc",
    "examples",
    "example",
    "samples",
    "sample",
    "testdata",
    "vendor",
}
EXCLUDED_REPO_TERMS = {
    "awesome",
    "course",
    "demo",
    "example",
    "examples",
    "generated",
    "hands-on",
    "learning",
    "llm",
    "sample",
    "synthetic",
    "template",
    "tutorial",
}
MAX_FILE_BYTES = 512_000


@dataclass(frozen=True)
class RepoInfo:
    full_name: str
    clone_url: str
    html_url: str
    language: str
    stargazers_count: int
    description: str
    topics: list[str]
    pushed_at: str
    source: str = SOURCE_NAME
    finished: bool = False


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    author: str
    author_date: dt.datetime
    changed_files: list[str]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch high-star GitHub projects and generate per-project JSONL code snapshots."
    )
    parser.add_argument("--output-dir", default="final_data", help="Directory for final per-repo JSONL files.")
    parser.add_argument("--work-dir", default=".cache/github_repos", help="Directory for cloned repositories.")
    parser.add_argument("--commit-work-dir", default=".cache/commit_json", help="Directory for intermediate commit JSON files.")
    parser.add_argument("--repo-csv", default="group_repo.csv", help="CSV path for selected repositories.")
    parser.add_argument("--refresh-repo-csv", action="store_true", help="Ignore an existing repo CSV and fetch a fresh repository list.")
    parser.add_argument("--append-repo-csv", action="store_true", help="Fetch new repositories not already in repo CSV, append them, and process only the new repositories.")
    parser.add_argument("--since", default=SINCE_DATE, help="Only read commits after this date, YYYY-MM-DD.")
    parser.add_argument("--languages", nargs="+", default=list(LANGUAGES), help="GitHub languages to search.")
    parser.add_argument("--min-stars", type=int, default=5_000, help="Minimum stars for repository candidates.")
    parser.add_argument("--repos-per-language", type=int, default=100, help="Candidate repositories per language.")
    parser.add_argument("--target-repos", type=int, default=100, help="Target total repositories to collect.")
    parser.add_argument("--max-repos", type=int, default=None, help="Maximum eligible repositories to process.")
    parser.add_argument("--workers", type=int, default=10, help="Commit JSON worker threads.")
    parser.add_argument("--clone-workers", type=int, default=1, help="Clone worker threads.")
    parser.add_argument("--github-token", default=None, help="Optional GitHub token. Overrides token.json and GITHUB_TOKEN.")
    parser.add_argument("--keep-repos", action="store_true", help="Keep cloned repositories after processing.")
    parser.add_argument("--repo", action="append", default=[], help="Process an explicit GitHub repo, e.g. owner/name.")
    parser.add_argument("--max-commits", type=int, default=None, help="Maximum commits to process per repository.")
    return parser.parse_args(argv)


def parse_gitlab_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch high-star GitLab projects and generate per-project JSONL code snapshots."
    )
    parser.add_argument("--output-dir", default="final_data_gitlab", help="Directory for final per-repo JSONL files.")
    parser.add_argument("--work-dir", default=".cache/gitlab_repos", help="Directory for cloned repositories.")
    parser.add_argument("--commit-work-dir", default=".cache/gitlab_commit_json", help="Directory for intermediate commit JSON files.")
    parser.add_argument("--repo-csv", default="gitlab_group_repo.csv", help="CSV path for selected repositories.")
    parser.add_argument("--refresh-repo-csv", action="store_true", help="Ignore an existing repo CSV and fetch a fresh repository list.")
    parser.add_argument("--append-repo-csv", action="store_true", help="Fetch new repositories not already in repo CSV, append them, and process the CSV.")
    parser.add_argument("--since", default=SINCE_DATE, help="Only read commits after this date, YYYY-MM-DD.")
    parser.add_argument("--languages", nargs="+", default=list(LANGUAGES), help="GitLab programming languages to search.")
    parser.add_argument("--min-stars", type=int, default=5_000, help="Minimum stars for project candidates.")
    parser.add_argument("--repos-per-language", type=int, default=100, help="Candidate projects per language.")
    parser.add_argument("--target-repos", type=int, default=100, help="Target total projects to collect.")
    parser.add_argument("--max-repos", type=int, default=None, help="Maximum eligible projects to process.")
    parser.add_argument("--workers", type=int, default=10, help="Commit JSON worker threads.")
    parser.add_argument("--clone-workers", type=int, default=1, help="Clone worker threads.")
    parser.add_argument("--gitlab-base-url", default=os.getenv("GITLAB_BASE_URL", GITLAB_BASE_URL), help="GitLab instance base URL.")
    parser.add_argument("--gitlab-token", default=None, help="Optional GitLab token. GitLab does not read token.json by default.")
    parser.add_argument("--keep-repos", action="store_true", help="Keep cloned repositories after processing.")
    parser.add_argument("--repo", action="append", default=[], help="Process an explicit GitLab project path, e.g. group/subgroup/project.")
    parser.add_argument("--max-commits", type=int, default=None, help="Maximum commits to process per repository.")
    return parser.parse_args(argv)


def parse_failed_log_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Retry only repositories listed in failed_repos.log."
    )
    parser.add_argument("--output-dir", default="final_data_github", help="Directory containing failed_repos.log and final JSONL files.")
    parser.add_argument("--work-dir", default=".cache/github_repos", help="Directory for cloned repositories.")
    parser.add_argument("--commit-work-dir", default=".cache/commit_json", help="Directory for intermediate commit JSON files.")
    parser.add_argument("--repo-csv", default="group_repo.csv", help="CSV path for selected repositories.")
    parser.add_argument("--failed-log", default=None, help="Path to failed_repos.log. Defaults to <output-dir>/failed_repos.log.")
    parser.add_argument("--since", default=SINCE_DATE, help="Only read commits after this date, YYYY-MM-DD.")
    parser.add_argument("--workers", type=int, default=10, help="Commit JSON worker threads.")
    parser.add_argument("--clone-workers", type=int, default=1, help="Clone worker threads.")
    parser.add_argument("--keep-repos", action="store_true", help="Keep cloned repositories after processing.")
    parser.add_argument("--max-commits", type=int, default=None, help="Maximum commits to process per repository.")
    return parser.parse_args(argv)


def parse_stackoverflow_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch high-quality Stack Overflow technical Q&A discussions as JSONL."
    )
    parser.add_argument("--output-dir", default="final_data_stackoverflow", help="Directory for Stack Overflow JSONL files.")
    parser.add_argument("--site", default="stackoverflow", help="Stack Exchange site name.")
    parser.add_argument("--tags", nargs="+", default=list(DISCUSSION_TAGS), help="Tags to collect, e.g. python java go.")
    parser.add_argument("--since", default="2024-01-01", help="Only collect questions created after this date, YYYY-MM-DD.")
    parser.add_argument("--min-score", type=int, default=10, help="Minimum question score.")
    parser.add_argument("--min-answers", type=int, default=1, help="Minimum answer count.")
    parser.add_argument("--max-records", type=int, default=10_000, help="Maximum records to write across all tags.")
    parser.add_argument("--max-answers", type=int, default=3, help="Maximum answers included per question.")
    parser.add_argument("--page-size", type=int, default=100, help="Stack Exchange API page size, capped at 100.")
    parser.add_argument("--stackexchange-key", default=None, help="Optional Stack Exchange API key. Overrides token.json and STACKEXCHANGE_KEY.")
    parser.add_argument("--sleep-seconds", type=float, default=0.25, help="Sleep between API pages.")
    return parser.parse_args(argv)


def load_token_config(token_path: Path = Path(TOKEN_CONFIG_PATH)) -> dict[str, str]:
    if not token_path.exists():
        return {}
    data = json.loads(token_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return {
        str(key): str(value).strip()
        for key, value in data.items()
        if value is not None and str(value).strip()
    }


def resolve_token(
    explicit_token: str | None,
    provider: str,
    env_name: str,
    token_path: Path = Path(TOKEN_CONFIG_PATH),
) -> str | None:
    if explicit_token:
        return explicit_token
    config_token = load_token_config(token_path).get(provider)
    if config_token:
        return config_token
    return os.getenv(env_name)


def is_eligible_repo(repo: RepoInfo) -> bool:
    if repo.stargazers_count <= 0:
        return False
    haystack = " ".join(
        [repo.full_name, repo.description or "", " ".join(repo.topics or [])]
    ).lower()
    tokens = set(re.split(r"[^a-z0-9+]+", haystack))
    if tokens & EXCLUDED_REPO_TERMS:
        return False
    return True


def select_eligible_repos(
    candidates: Iterable[RepoInfo],
    max_repos: int | None = None,
    seen: set[str] | None = None,
) -> list[RepoInfo]:
    selected: list[RepoInfo] = []
    seen_names = seen if seen is not None else set()
    for repo in candidates:
        if repo.full_name in seen_names or not is_eligible_repo(repo):
            continue
        selected.append(repo)
        seen_names.add(repo.full_name)
        if max_repos is not None and len(selected) >= max_repos:
            break
    return selected


def repo_author(repo: RepoInfo) -> str:
    return repo.full_name.split("/", 1)[0]


def repo_to_csv_row(repo: RepoInfo) -> dict[str, str]:
    return {
        "group_repo": repo.full_name,
        "url": repo.html_url,
        "language": repo.language,
        "star": str(repo.stargazers_count),
        "author": repo_author(repo),
        "project_type": infer_project_type(repo),
        "finished": "true" if repo.finished else "false",
    }


def write_repo_csv(repos: Sequence[RepoInfo], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["group_repo", "url", "language", "star", "author", "project_type", "finished"]
    with csv_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for repo in repos:
            writer.writerow(repo_to_csv_row(repo))


def append_repo_csv(repos: Sequence[RepoInfo], csv_path: Path) -> list[RepoInfo]:
    existing = read_repo_csv(csv_path) if csv_path.exists() else []
    seen = {repo.full_name for repo in existing}
    appended: list[RepoInfo] = []
    for repo in repos:
        if repo.full_name in seen:
            continue
        appended.append(repo)
        seen.add(repo.full_name)
    write_repo_csv([*existing, *appended], csv_path)
    return appended


def filter_unfinished_repos(repos: Sequence[RepoInfo]) -> list[RepoInfo]:
    return [repo for repo in repos if not repo.finished]


def read_repo_csv(csv_path: Path) -> list[RepoInfo]:
    repos: list[RepoInfo] = []
    with csv_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            full_name = (row.get("group_repo") or "").strip()
            html_url = (row.get("url") or f"https://github.com/{full_name}").strip()
            if not full_name:
                continue
            repos.append(
                RepoInfo(
                    full_name=full_name,
                    clone_url=f"{html_url}.git",
                    html_url=html_url,
                    language=(row.get("language") or "unknown").strip(),
                    stargazers_count=int(row.get("star") or 0),
                    description="",
                    topics=[],
                    pushed_at="",
                    source=infer_source_from_url(html_url),
                    finished=parse_bool(row.get("finished")),
                )
            )
    return repos


def parse_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y"}


def mark_repo_finished(csv_path: Path, repo_name: str) -> None:
    if not csv_path.exists():
        return
    with csv_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if "finished" not in fieldnames:
        fieldnames.append("finished")
    for row in rows:
        row.setdefault("finished", "false")
        if (row.get("group_repo") or "").strip() == repo_name:
            row["finished"] = "true"
    with csv_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def ensure_repo_csv_finished_field(csv_path: Path) -> None:
    if not csv_path.exists():
        return
    with csv_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if "finished" in fieldnames:
        return
    fieldnames.append("finished")
    for row in rows:
        row["finished"] = "false"
    with csv_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_failed_repo_names(failure_log: Path) -> set[str]:
    if not failure_log.exists():
        return set()
    failed: set[str] = set()
    with failure_log.open("r", encoding="utf-8") as fp:
        for line in fp:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[1].strip():
                failed.add(parts[1].strip())
    return failed


def load_failed_repos_from_csv(csv_path: Path, failure_log: Path) -> list[RepoInfo]:
    failed = read_failed_repo_names(failure_log)
    if not failed or not csv_path.exists():
        return []
    repos = read_repo_csv(csv_path)
    return [
        replace(repo, finished=False)
        for repo in repos
        if repo.full_name in failed
    ]


def remove_repos_from_failure_log(failure_log: Path, repo_names: set[str]) -> None:
    if not repo_names or not failure_log.exists():
        return
    remaining: list[str] = []
    with failure_log.open("r", encoding="utf-8") as fp:
        for line in fp:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[1].strip() in repo_names:
                continue
            remaining.append(line)
    if remaining:
        failure_log.write_text("".join(remaining), encoding="utf-8")
    else:
        failure_log.unlink()


def infer_source_from_url(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if "gitlab" in host:
        return GITLAB_SOURCE_NAME
    return SOURCE_NAME


def is_code_commit(
    changed_files: Sequence[str],
    code_extensions: set[str] = CODE_EXTENSIONS,
) -> bool:
    code_count = 0
    non_code_count = 0
    for file_name in changed_files:
        if is_code_path(file_name, code_extensions):
            code_count += 1
        else:
            non_code_count += 1
    return code_count > 0 and code_count >= non_code_count


def is_code_path(path: str, code_extensions: set[str] = CODE_EXTENSIONS) -> bool:
    clean = path.strip()
    if not clean:
        return False
    normalized = clean.replace("\\", "/")
    parts = [part.lower() for part in normalized.split("/")]
    if any(part in NON_CODE_DIRS for part in parts[:-1]):
        return False
    basename = parts[-1]
    if basename in NON_CODE_EXACT:
        return False
    return Path(basename).suffix.lower() in code_extensions


def infer_project_type(repo: RepoInfo, changed_files: Sequence[str] | None = None) -> str:
    text = " ".join([repo.description or "", " ".join(repo.topics or []), repo.full_name]).lower()
    files = " ".join(changed_files or []).lower()
    combined = f"{text} {files}"
    file_words = set(re.split(r"[^a-z0-9+]+", files))
    words = set(re.split(r"[^a-z0-9+]+", combined))
    if has_any_term(files, file_words, ("frontend", "react", "vue", "svelte", "ui", "web", "webapp")):
        return "网站前端"
    if has_any_term(files, file_words, ("backend", "server", "api", "database", "microservice", "proxy")):
        return "网站后端"
    if has_any_term(combined, words, ("frontend", "react", "vue", "svelte", "ui", "webapp")):
        return "网站前端"
    if has_any_term(combined, words, ("backend", "server", "api", "database", "microservice", "proxy")):
        return "网站后端"
    if has_any_term(combined, words, ("game", "unity", "engine")):
        return "小游戏"
    if has_any_term(combined, words, ("cli", "command-line", "terminal")):
        return "命令行工具"
    if has_any_term(combined, words, ("framework", "library", "sdk")):
        return "开发库/框架"
    if has_any_term(combined, words, ("model", "machine-learning", "ml", "ai")):
        return "机器学习工程"
    return "通用软件项目"


def has_any_term(combined: str, words: set[str], terms: Sequence[str]) -> bool:
    for term in terms:
        if "-" in term:
            if term in combined:
                return True
        elif term in words:
            return True
    return False


def build_record(
    repo: RepoInfo,
    commit: CommitInfo,
    text: str,
    project_type: str,
) -> dict:
    return {
        "id": commit.sha,
        "text": text,
        "meta": {
            "data_info": {
                "lang": repo.language,
                "source": repo.source,
                "url": repo.html_url,
                "type": "代码",
                "author": commit.author,
                "public_date": commit.author_date.isoformat(),
                "project_type": project_type,
            }
        },
    }


class StackOverflowHTMLTextParser(HTMLParser):
    BLOCK_TAGS = {
        "blockquote",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "ol",
        "p",
        "pre",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        raw = html_lib.unescape("".join(self.parts))
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.splitlines()]
        return "\n".join(line for line in lines if line).strip()


def html_to_text(value: str | None) -> str:
    parser = StackOverflowHTMLTextParser()
    parser.feed(value or "")
    parser.close()
    return parser.text()


def unix_timestamp_to_iso(timestamp: int | float | None) -> str:
    if timestamp is None:
        return ""
    return dt.datetime.fromtimestamp(timestamp, tz=dt.UTC).isoformat()


def stackoverflow_question_to_record(
    question: dict,
    tag: str,
    max_answers: int = 3,
    site: str = "stackoverflow",
) -> dict:
    question_id = question["question_id"]
    answers = sorted(
        question.get("answers") or [],
        key=lambda item: (bool(item.get("is_accepted")), int(item.get("score") or 0)),
        reverse=True,
    )[:max(0, max_answers)]
    text_parts = [
        f"Title: {html_to_text(question.get('title'))}",
        "",
        f"Question:\n{html_to_text(question.get('body'))}",
    ]
    for index, answer in enumerate(answers, start=1):
        label = "Accepted answer" if answer.get("is_accepted") else f"Answer {index}"
        text_parts.extend(
            [
                "",
                f"{label} (score: {int(answer.get('score') or 0)}):",
                html_to_text(answer.get("body")),
            ]
        )
    owner = question.get("owner") or {}
    return {
        "id": f"stackoverflow_question_{question_id}",
        "text": "\n".join(part for part in text_parts if part is not None).strip() + "\n",
        "meta": {
            "data_info": {
                "source": STACKOVERFLOW_SOURCE_NAME,
                "type": "技术问答",
                "url": question.get("link") or f"https://stackoverflow.com/questions/{question_id}",
                "license": "CC BY-SA 4.0",
                "site": site,
                "tag": tag,
                "tags": list(question.get("tags") or []),
                "score": int(question.get("score") or 0),
                "views": int(question.get("view_count") or 0),
                "answer_count": int(question.get("answer_count") or 0),
                "author": owner.get("display_name") or "unknown",
                "public_date": unix_timestamp_to_iso(question.get("creation_date")),
            }
        },
    }


def write_stackoverflow_records(records: Sequence[dict], output_path: Path) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fp:
        for record in records:
            fp.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(records)


def write_jsonl_for_commit(
    repo_dir: Path,
    output_path: Path,
    repo: RepoInfo,
    commit: CommitInfo,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    code_parts: list[str] = []
    code_paths: list[str] = []
    for relative_path in commit.changed_files:
        if not is_code_path(relative_path):
            continue
        file_path = repo_dir / relative_path
        if not file_path.is_file() or file_path.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            code_parts.append(file_path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            continue
        code_paths.append(relative_path)
    if not code_parts:
        return 0
    record = build_record(
        repo=repo,
        commit=commit,
        text=merge_code_parts(code_parts),
        project_type=infer_project_type(repo, code_paths),
    )
    with output_path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return 1


def write_json_for_commit(
    repo_dir: Path,
    output_path: Path,
    repo: RepoInfo,
    commit: CommitInfo,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    code_parts: list[str] = []
    code_paths: list[str] = []
    for relative_path in commit.changed_files:
        if not is_code_path(relative_path):
            continue
        file_path = repo_dir / relative_path
        if not file_path.is_file() or file_path.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            code_parts.append(file_path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            continue
        code_paths.append(relative_path)
    if not code_parts:
        return 0
    record = build_record(
        repo=repo,
        commit=commit,
        text=merge_code_parts(code_parts),
        project_type=infer_project_type(repo, code_paths),
    )
    output_path.write_text(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return 1


def merge_code_parts(code_parts: Sequence[str]) -> str:
    return "\n\n".join(part.rstrip("\n") for part in code_parts) + "\n"


def fetch_high_star_repos(
    languages: Sequence[str],
    min_stars: int,
    repos_per_language: int,
    github_token: str | None = None,
    max_repos: int | None = None,
    existing_repo_names: set[str] | None = None,
) -> list[RepoInfo]:
    repos: list[RepoInfo] = []
    seen: set[str] = set(existing_repo_names or set())
    for language in languages:
        page = 1
        language_selected = 0
        while language_selected < repos_per_language and page <= 10:
            query = f"language:{language} stars:>{min_stars} archived:false mirror:false"
            params = urllib.parse.urlencode(
                {
                    "q": query,
                    "sort": "stars",
                    "order": "desc",
                    "per_page": min(100, max(10, repos_per_language * 3)),
                    "page": page,
                }
            )
            url = f"https://api.github.com/search/repositories?{params}"
            payload = github_get_json(url, github_token)
            items = payload.get("items", [])
            if not items:
                break
            candidates = [
                RepoInfo(
                    full_name=item["full_name"],
                    clone_url=item["clone_url"],
                    html_url=item["html_url"],
                    language=item.get("language") or language,
                    stargazers_count=int(item.get("stargazers_count") or 0),
                    description=item.get("description") or "",
                    topics=list(item.get("topics") or []),
                    pushed_at=item.get("pushed_at") or "",
                )
                for item in items
            ]
            remaining_for_language = repos_per_language - language_selected
            remaining_total = None if max_repos is None else max_repos - len(repos)
            limit = remaining_for_language
            if remaining_total is not None:
                limit = min(limit, remaining_total)
            selected = select_eligible_repos(candidates, max_repos=limit, seen=seen)
            repos.extend(selected)
            language_selected += len(selected)
            if max_repos is not None and len(repos) >= max_repos:
                return repos
            page += 1
    return repos


def fetch_repo(repo_name: str, github_token: str | None = None) -> RepoInfo:
    encoded = urllib.parse.quote(repo_name.strip(), safe="/")
    item = github_get_json(f"https://api.github.com/repos/{encoded}", github_token)
    return RepoInfo(
        full_name=item["full_name"],
        clone_url=item["clone_url"],
        html_url=item["html_url"],
        language=item.get("language") or "unknown",
        stargazers_count=int(item.get("stargazers_count") or 0),
        description=item.get("description") or "",
        topics=list(item.get("topics") or []),
        pushed_at=item.get("pushed_at") or "",
    )


def normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def gitlab_project_to_repo(item: dict, language: str | None = None) -> RepoInfo:
    full_name = item["path_with_namespace"]
    topics = item.get("topics")
    if topics is None:
        topics = item.get("tag_list") or []
    return RepoInfo(
        full_name=full_name,
        clone_url=item.get("http_url_to_repo") or f"{item['web_url']}.git",
        html_url=item["web_url"],
        language=item.get("language") or language or "unknown",
        stargazers_count=int(item.get("star_count") or 0),
        description=item.get("description") or "",
        topics=list(topics or []),
        pushed_at=item.get("last_activity_at") or item.get("updated_at") or "",
        source=GITLAB_SOURCE_NAME,
    )


def fetch_high_star_gitlab_projects(
    languages: Sequence[str],
    min_stars: int,
    repos_per_language: int,
    gitlab_base_url: str = GITLAB_BASE_URL,
    gitlab_token: str | None = None,
    max_repos: int | None = None,
    existing_repo_names: set[str] | None = None,
) -> list[RepoInfo]:
    repos: list[RepoInfo] = []
    seen: set[str] = set(existing_repo_names or set())
    base_url = normalize_base_url(gitlab_base_url)
    for language in languages:
        page = 1
        language_selected = 0
        while language_selected < repos_per_language and page <= 10:
            params = urllib.parse.urlencode(
                {
                    "visibility": "public",
                    "archived": "false",
                    "order_by": "star_count",
                    "sort": "desc",
                    "simple": "true",
                    "per_page": GITLAB_PROJECTS_PER_PAGE,
                    "page": page,
                    "with_programming_language": language,
                }
            )
            payload = gitlab_get_json(f"{base_url}/api/v4/projects?{params}", gitlab_token)
            if not isinstance(payload, list) or not payload:
                break
            candidates = [
                gitlab_project_to_repo(item, language=language)
                for item in payload
                if is_gitlab_search_candidate(item, min_stars)
            ]
            remaining_for_language = repos_per_language - language_selected
            remaining_total = None if max_repos is None else max_repos - len(repos)
            limit = remaining_for_language
            if remaining_total is not None:
                limit = min(limit, remaining_total)
            selected = select_eligible_repos(candidates, max_repos=limit, seen=seen)
            repos.extend(selected)
            language_selected += len(selected)
            if max_repos is not None and len(repos) >= max_repos:
                return repos
            page += 1
    return repos


def is_gitlab_search_candidate(item: dict, min_stars: int) -> bool:
    return (
        int(item.get("star_count") or 0) > min_stars
        and not bool(item.get("archived"))
        and not bool(item.get("mirror"))
    )


def fetch_gitlab_project(
    project_path: str,
    gitlab_base_url: str = GITLAB_BASE_URL,
    gitlab_token: str | None = None,
) -> RepoInfo:
    encoded = urllib.parse.quote(project_path.strip(), safe="")
    base_url = normalize_base_url(gitlab_base_url)
    item = gitlab_get_json(f"{base_url}/api/v4/projects/{encoded}", gitlab_token)
    return gitlab_project_to_repo(item)


def github_get_json(url: str, github_token: str | None) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "github-code-harvester",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def gitlab_get_json(url: str, gitlab_token: str | None) -> dict | list:
    headers = {
        "Accept": "application/json",
        "User-Agent": "gitlab-code-harvester",
    }
    if gitlab_token:
        headers["PRIVATE-TOKEN"] = gitlab_token
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def stackexchange_get_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "discussion-harvester",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def stackexchange_api_url(path: str, params: dict[str, object]) -> str:
    clean = {
        key: value
        for key, value in params.items()
        if value is not None and value != ""
    }
    return f"{STACKEXCHANGE_API_BASE_URL}{path}?{urllib.parse.urlencode(clean)}"


def parse_since_timestamp(since: str) -> int:
    parsed = dt.datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=dt.UTC)
    return int(parsed.timestamp())


def fetch_stackoverflow_answers(
    question_ids: Sequence[int],
    site: str,
    stackexchange_key: str | None = None,
    page_size: int = 100,
) -> dict[int, list[dict]]:
    if not question_ids:
        return {}
    ids = ";".join(str(question_id) for question_id in question_ids)
    url = stackexchange_api_url(
        f"/questions/{ids}/answers",
        {
            "order": "desc",
            "sort": "votes",
            "site": site,
            "pagesize": min(100, max(1, page_size)),
            "filter": "withbody",
            "key": stackexchange_key,
        },
    )
    payload = stackexchange_get_json(url)
    by_question: dict[int, list[dict]] = {}
    for answer in payload.get("items") or []:
        question_id = int(answer.get("question_id") or 0)
        by_question.setdefault(question_id, []).append(answer)
    if payload.get("backoff"):
        time.sleep(float(payload["backoff"]))
    return by_question


def fetch_stackoverflow_questions_for_tag(
    tag: str,
    site: str,
    since: str,
    min_score: int,
    min_answers: int,
    max_records: int,
    stackexchange_key: str | None = None,
    page_size: int = 100,
    sleep_seconds: float = 0.25,
) -> list[dict]:
    questions: list[dict] = []
    page = 1
    fromdate = parse_since_timestamp(since)
    while len(questions) < max_records:
        url = stackexchange_api_url(
            "/search/advanced",
            {
                "order": "desc",
                "sort": "votes",
                "site": site,
                "tagged": tag,
                "fromdate": fromdate,
                "min": min_score,
                "answers": min_answers,
                "pagesize": min(100, max(1, page_size)),
                "page": page,
                "filter": "withbody",
                "key": stackexchange_key,
            },
        )
        payload = stackexchange_get_json(url)
        items = list(payload.get("items") or [])
        if not items:
            break
        question_ids = [int(item["question_id"]) for item in items if item.get("question_id")]
        answers_by_question = fetch_stackoverflow_answers(
            question_ids=question_ids,
            site=site,
            stackexchange_key=stackexchange_key,
            page_size=page_size,
        )
        for item in items:
            item["answers"] = answers_by_question.get(int(item.get("question_id") or 0), [])
            questions.append(item)
            if len(questions) >= max_records:
                break
        if not payload.get("has_more"):
            break
        if payload.get("backoff"):
            time.sleep(float(payload["backoff"]))
        elif sleep_seconds > 0:
            time.sleep(sleep_seconds)
        page += 1
    return questions


def clone_or_update_repo(repo: RepoInfo, work_dir: Path, since: str | None = None) -> Path:
    target = work_dir / safe_repo_name(repo.full_name)
    if target.exists():
        fetch_args = ["fetch", "--prune", "--tags"]
        if since:
            fetch_args.append(f"--shallow-since={since}")
        try:
            run_git(fetch_args, cwd=target)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            return clone_or_update_repo(repo, work_dir, since=None)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        clone_args = ["clone"]
        if since:
            clone_args.append(f"--shallow-since={since}")
        clone_args.extend([repo.clone_url, str(target)])
        try:
            run_git(clone_args, cwd=None)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            if since:
                return clone_or_update_repo(repo, work_dir, since=None)
            raise
    checkout_origin_head(target)
    return target


def list_commits_since(repo_dir: Path, since: str) -> list[str]:
    output = run_git(
        ["log", f"--since={since}", "--format=%H", "--no-merges"],
        cwd=repo_dir,
        capture=True,
    )
    return [line.strip() for line in output.splitlines() if line.strip()]


def get_commit_info(repo_dir: Path, sha: str) -> CommitInfo | None:
    meta = run_git(["show", "-s", "--format=%an%x00%aI", sha], cwd=repo_dir, capture=True)
    parts = meta.rstrip("\n").split("\x00")
    if len(parts) != 2:
        return None
    files = run_git(
        ["diff-tree", "--no-commit-id", "--name-only", "-r", sha],
        cwd=repo_dir,
        capture=True,
    )
    changed_files = [line.strip() for line in files.splitlines() if line.strip()]
    if not changed_files:
        return None
    return CommitInfo(
        sha=sha,
        author=parts[0] or "unknown",
        author_date=dt.datetime.fromisoformat(parts[1].replace("Z", "+00:00")),
        changed_files=changed_files,
    )


def checkout_commit(repo_dir: Path, sha: str) -> None:
    clean_git_worktree(repo_dir)
    run_git(["checkout", "--force", "--quiet", sha], cwd=repo_dir)


def checkout_origin_head(repo_dir: Path) -> None:
    clean_git_worktree(repo_dir)
    remote_head = run_git(["symbolic-ref", "refs/remotes/origin/HEAD", "--short"], cwd=repo_dir, capture=True)
    branch = remote_head.strip()
    if branch:
        run_git(["checkout", "--force", "--quiet", branch], cwd=repo_dir)


def clean_git_worktree(repo_dir: Path) -> None:
    run_git(["reset", "--hard", "--quiet"], cwd=repo_dir)
    run_git(["clean", "-fdx", "--quiet"], cwd=repo_dir)


def process_repo(
    repo: RepoInfo,
    repo_dir: Path,
    output_dir: Path,
    since: str,
    max_commits: int | None = None,
) -> int:
    output_path = output_dir / f"{safe_repo_name(repo.full_name)}.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    output_path.touch()
    total = 0
    processed_commits = 0
    for sha in list_commits_since(repo_dir, since):
        if max_commits is not None and processed_commits >= max_commits:
            break
        commit = get_commit_info(repo_dir, sha)
        if commit is None or not is_code_commit(commit.changed_files):
            continue
        checkout_commit(repo_dir, commit.sha)
        total += write_jsonl_for_commit(repo_dir, output_path, repo, commit)
        processed_commits += 1
    return total


def process_repo_to_commit_jsons(
    repo: RepoInfo,
    repo_dir: Path,
    commit_dir: Path,
    since: str,
    max_commits: int | None = None,
) -> int:
    if commit_dir.exists():
        shutil.rmtree(commit_dir)
    commit_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    processed_commits = 0
    for sha in list_commits_since(repo_dir, since):
        if max_commits is not None and processed_commits >= max_commits:
            break
        commit = get_commit_info(repo_dir, sha)
        if commit is None or not is_code_commit(commit.changed_files):
            continue
        checkout_commit(repo_dir, commit.sha)
        total += write_json_for_commit(
            repo_dir=repo_dir,
            output_path=commit_dir / f"{commit.sha}.json",
            repo=repo,
            commit=commit,
        )
        processed_commits += 1
    return total


def merge_commit_jsons_to_jsonl(commit_dir: Path, final_dir: Path, repo: RepoInfo) -> Path | None:
    final_dir.mkdir(parents=True, exist_ok=True)
    final_path = final_dir / f"{safe_repo_name(repo.full_name)}.jsonl"
    commit_jsons = sorted(commit_dir.glob("*.json"))
    if not commit_jsons:
        final_path.unlink(missing_ok=True)
        return None
    with final_path.open("w", encoding="utf-8") as out:
        for commit_json in commit_jsons:
            text = commit_json.read_text(encoding="utf-8")
            json.loads(text)
            out.write(text + "\n")
    return final_path


def cleanup_project_workspace(repo_dir: Path, commit_dir: Path) -> None:
    shutil.rmtree(repo_dir, ignore_errors=True)
    shutil.rmtree(commit_dir, ignore_errors=True)


def run_pipeline(
    repos: Sequence[RepoInfo],
    work_dir: Path,
    output_dir: Path,
    commit_work_dir: Path,
    since: str,
    jsonl_workers: int,
    clone_workers: int = 1,
    keep_repos: bool = False,
    max_commits: int | None = None,
    repo_csv_path: Path | None = None,
) -> None:
    original_count = len(repos)
    repos = filter_unfinished_repos(repos)
    skipped_count = original_count - len(repos)
    if skipped_count:
        print(f"Skipped {skipped_count} repositories already marked finished in CSV", flush=True)
    if not repos:
        print("No new repositories to process", flush=True)
        return

    clone_queue: queue.Queue[RepoInfo | None] = queue.Queue()
    process_queue: queue.Queue[tuple[RepoInfo, Path] | None] = queue.Queue()
    errors: list[tuple[str, str, str]] = []
    error_lock = threading.Lock()
    csv_lock = threading.Lock()

    for repo in repos:
        clone_queue.put(repo)
    for _ in range(clone_workers):
        clone_queue.put(None)

    def record_error(repo: RepoInfo, stage: str, exc: Exception) -> None:
        with error_lock:
            errors.append((repo.full_name, stage, str(exc)))

    def clone_worker() -> None:
        while True:
            repo = clone_queue.get()
            try:
                if repo is None:
                    return
                repo_dir = clone_or_update_repo(repo, work_dir, since=since)
                process_queue.put((repo, repo_dir))
            except Exception as exc:  # pragma: no cover - operational logging path
                record_error(repo, "clone", exc)
            finally:
                clone_queue.task_done()

    def process_worker() -> None:
        while True:
            item = process_queue.get()
            try:
                if item is None:
                    return
                repo, repo_dir = item
                commit_dir = commit_work_dir / safe_repo_name(repo.full_name)
                count = process_repo_to_commit_jsons(
                    repo=repo,
                    repo_dir=repo_dir,
                    commit_dir=commit_dir,
                    since=since,
                    max_commits=max_commits,
                )
                final_path = merge_commit_jsons_to_jsonl(commit_dir, output_dir, repo)
                if final_path is None:
                    print(f"{repo.full_name}: wrote 0 records; no final jsonl created", flush=True)
                else:
                    print(f"{repo.full_name}: wrote {count} records", flush=True)
                if keep_repos:
                    shutil.rmtree(commit_dir, ignore_errors=True)
                else:
                    cleanup_project_workspace(repo_dir, commit_dir)
                if repo_csv_path is not None:
                    with csv_lock:
                        mark_repo_finished(repo_csv_path, repo.full_name)
                        remove_repos_from_failure_log(output_dir / "failed_repos.log", {repo.full_name})
            except Exception as exc:  # pragma: no cover - operational logging path
                repo_name = item[0].full_name if item is not None else "unknown"
                error_repo = item[0] if item is not None else RepoInfo(
                    full_name=repo_name,
                    clone_url="",
                    html_url="",
                    language="unknown",
                    stargazers_count=0,
                    description="",
                    topics=[],
                    pushed_at="",
                )
                record_error(error_repo, "process", exc)
            finally:
                process_queue.task_done()

    clone_threads = [threading.Thread(target=clone_worker, daemon=True) for _ in range(clone_workers)]
    process_threads = [threading.Thread(target=process_worker, daemon=True) for _ in range(jsonl_workers)]
    for thread in clone_threads + process_threads:
        thread.start()
    clone_queue.join()
    for _ in process_threads:
        process_queue.put(None)
    process_queue.join()
    for thread in clone_threads + process_threads:
        thread.join(timeout=1)
    if errors:
        write_failure_log(errors, output_dir)
        print(f"{len(errors)} repositories failed; see {output_dir / 'failed_repos.log'}", flush=True)
    if not keep_repos:
        remove_empty_dir(work_dir)
        remove_empty_dir(commit_work_dir)


def write_failure_log(errors: Sequence[tuple[str, str, str]], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "failed_repos.log"
    with path.open("a", encoding="utf-8") as fp:
        for repo_name, stage, error in errors:
            fp.write(f"{dt.datetime.now(dt.UTC).isoformat()}\t{repo_name}\t{stage}\t{error}\n")
    return path


def remove_empty_dir(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


def run_git(args: Sequence[str], cwd: Path | None, capture: bool = False) -> str:
    command = ["git", *args]
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    return result.stdout if capture else ""


def safe_repo_name(full_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", full_name)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    start = time.time()
    repo_csv_path = Path(args.repo_csv)
    output_dir = Path(args.output_dir)
    ensure_repo_csv_finished_field(repo_csv_path)
    github_token = resolve_token(args.github_token, "github", "GITHUB_TOKEN")
    if args.repo:
        repos = [fetch_repo(repo_name, github_token) for repo_name in args.repo]
    elif args.append_repo_csv:
        existing = read_repo_csv(repo_csv_path) if repo_csv_path.exists() else []
        existing_names = {repo.full_name for repo in existing}
        target_new_repos = args.max_repos if args.max_repos is not None else args.target_repos
        repos = fetch_high_star_repos(
            languages=args.languages,
            min_stars=args.min_stars,
            repos_per_language=args.repos_per_language,
            github_token=github_token,
            max_repos=target_new_repos,
            existing_repo_names=existing_names,
        )
        repos = append_repo_csv(repos, repo_csv_path)
        print(
            f"Appended {len(repos)} new repositories to {args.repo_csv}; existing count was {len(existing)}",
            flush=True,
        )
        repos = read_repo_csv(repo_csv_path)
        print(f"Loaded {len(repos)} total repositories from {args.repo_csv}", flush=True)
    elif repo_csv_path.exists() and not args.refresh_repo_csv:
        repos = read_repo_csv(repo_csv_path)
        print(f"Loaded {len(repos)} repositories from {args.repo_csv}", flush=True)
    else:
        max_repos = args.max_repos if args.max_repos is not None else args.target_repos
        repos = fetch_high_star_repos(
            languages=args.languages,
            min_stars=args.min_stars,
            repos_per_language=args.repos_per_language,
            github_token=github_token,
            max_repos=max_repos,
        )
    print(f"Selected {len(repos)} repositories", flush=True)
    if args.repo or args.refresh_repo_csv or (not args.append_repo_csv and not repo_csv_path.exists()):
        write_repo_csv(repos, repo_csv_path)
        print(f"Wrote repo CSV to {args.repo_csv}", flush=True)
    run_pipeline(
        repos=repos,
        work_dir=Path(args.work_dir),
        output_dir=output_dir,
        commit_work_dir=Path(args.commit_work_dir),
        since=args.since,
        jsonl_workers=max(1, args.workers),
        clone_workers=max(1, args.clone_workers),
        keep_repos=args.keep_repos,
        max_commits=args.max_commits,
        repo_csv_path=repo_csv_path,
    )
    print(f"Done in {time.time() - start:.1f}s", flush=True)
    return 0


def gitlab_main(argv: Sequence[str] | None = None) -> int:
    args = parse_gitlab_args(argv)
    start = time.time()
    repo_csv_path = Path(args.repo_csv)
    output_dir = Path(args.output_dir)
    ensure_repo_csv_finished_field(repo_csv_path)
    gitlab_token = args.gitlab_token
    if args.repo:
        repos = [
            fetch_gitlab_project(
                repo_name,
                gitlab_base_url=args.gitlab_base_url,
                gitlab_token=gitlab_token,
            )
            for repo_name in args.repo
        ]
    elif args.append_repo_csv:
        existing = read_repo_csv(repo_csv_path) if repo_csv_path.exists() else []
        existing_names = {repo.full_name for repo in existing}
        target_new_repos = args.max_repos if args.max_repos is not None else args.target_repos
        repos = fetch_high_star_gitlab_projects(
            languages=args.languages,
            min_stars=args.min_stars,
            repos_per_language=args.repos_per_language,
            gitlab_base_url=args.gitlab_base_url,
            gitlab_token=gitlab_token,
            max_repos=target_new_repos,
            existing_repo_names=existing_names,
        )
        repos = append_repo_csv(repos, repo_csv_path)
        print(
            f"Appended {len(repos)} new projects to {args.repo_csv}; existing count was {len(existing)}",
            flush=True,
        )
        repos = read_repo_csv(repo_csv_path)
        print(f"Loaded {len(repos)} total projects from {args.repo_csv}", flush=True)
    elif repo_csv_path.exists() and not args.refresh_repo_csv:
        repos = read_repo_csv(repo_csv_path)
        print(f"Loaded {len(repos)} projects from {args.repo_csv}", flush=True)
    else:
        max_repos = args.max_repos if args.max_repos is not None else args.target_repos
        repos = fetch_high_star_gitlab_projects(
            languages=args.languages,
            min_stars=args.min_stars,
            repos_per_language=args.repos_per_language,
            gitlab_base_url=args.gitlab_base_url,
            gitlab_token=gitlab_token,
            max_repos=max_repos,
        )
    print(f"Selected {len(repos)} projects", flush=True)
    if args.repo or args.refresh_repo_csv or (not args.append_repo_csv and not repo_csv_path.exists()):
        write_repo_csv(repos, repo_csv_path)
        print(f"Wrote repo CSV to {args.repo_csv}", flush=True)
    run_pipeline(
        repos=repos,
        work_dir=Path(args.work_dir),
        output_dir=output_dir,
        commit_work_dir=Path(args.commit_work_dir),
        since=args.since,
        jsonl_workers=max(1, args.workers),
        clone_workers=max(1, args.clone_workers),
        keep_repos=args.keep_repos,
        max_commits=args.max_commits,
        repo_csv_path=repo_csv_path,
    )
    print(f"Done in {time.time() - start:.1f}s", flush=True)
    return 0


def failed_log_main(argv: Sequence[str] | None = None) -> int:
    args = parse_failed_log_args(argv)
    start = time.time()
    repo_csv_path = Path(args.repo_csv)
    output_dir = Path(args.output_dir)
    failure_log = Path(args.failed_log) if args.failed_log else output_dir / "failed_repos.log"
    ensure_repo_csv_finished_field(repo_csv_path)
    repos = load_failed_repos_from_csv(repo_csv_path, failure_log)
    print(f"Loaded {len(repos)} failed repositories from {failure_log}", flush=True)
    if not repos:
        print("No failed repositories to process", flush=True)
        print(f"Done in {time.time() - start:.1f}s", flush=True)
        return 0
    run_pipeline(
        repos=repos,
        work_dir=Path(args.work_dir),
        output_dir=output_dir,
        commit_work_dir=Path(args.commit_work_dir),
        since=args.since,
        jsonl_workers=max(1, args.workers),
        clone_workers=max(1, args.clone_workers),
        keep_repos=args.keep_repos,
        max_commits=args.max_commits,
        repo_csv_path=repo_csv_path,
    )
    print(f"Done in {time.time() - start:.1f}s", flush=True)
    return 0


def stackoverflow_main(argv: Sequence[str] | None = None) -> int:
    args = parse_stackoverflow_args(argv)
    start = time.time()
    output_dir = Path(args.output_dir)
    stackexchange_key = resolve_token(args.stackexchange_key, "stackexchange", "STACKEXCHANGE_KEY")
    remaining = max(0, args.max_records)
    seen_questions: set[int] = set()
    total_written = 0
    for tag in args.tags:
        if remaining <= 0:
            break
        questions = fetch_stackoverflow_questions_for_tag(
            tag=tag,
            site=args.site,
            since=args.since,
            min_score=args.min_score,
            min_answers=args.min_answers,
            max_records=remaining,
            stackexchange_key=stackexchange_key,
            page_size=args.page_size,
            sleep_seconds=args.sleep_seconds,
        )
        records: list[dict] = []
        for question in questions:
            question_id = int(question.get("question_id") or 0)
            if not question_id or question_id in seen_questions:
                continue
            seen_questions.add(question_id)
            records.append(
                stackoverflow_question_to_record(
                    question,
                    tag=tag,
                    max_answers=args.max_answers,
                    site=args.site,
                )
            )
            remaining -= 1
            if remaining <= 0:
                break
        output_path = output_dir / f"{args.site}_{safe_repo_name(tag)}.jsonl"
        written = write_stackoverflow_records(records, output_path)
        total_written += written
        print(f"{tag}: wrote {written} records to {output_path}", flush=True)
    print(f"Wrote {total_written} Stack Overflow records", flush=True)
    print(f"Done in {time.time() - start:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
