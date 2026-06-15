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
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Sequence


LANGUAGES = ("Go", "Java", "Python", "JavaScript", "C++")
SINCE_DATE = "2025-10-01"
SOURCE_NAME = "GitHub"
GITLAB_SOURCE_NAME = "GitLab"
GITEE_SOURCE_NAME = "Gitee"
STACKOVERFLOW_SOURCE_NAME = "StackOverflow"
CSDN_SOURCE_NAME = "CSDN"
ZHIHU_SOURCE_NAME = "Zhihu"
GITLAB_BASE_URL = "https://gitlab.com"
GITEE_API_BASE_URL = "https://gitee.com/api/v5"
GITHUB_SEARCH_MAX_PAGES = 10
GITLAB_PROJECTS_PER_PAGE = 10
GITLAB_MAX_SEARCH_PAGES = 100
GITEE_REPOS_PER_PAGE = 100
GITEE_MAX_SEARCH_PAGES = 100
GITEE_DEFAULT_SEED_ORGS = ("dromara", "openeuler", "mindspore", "openharmony", "openkylin")
TOKEN_CONFIG_PATH = "token.json"
STACKEXCHANGE_API_BASE_URL = "https://api.stackexchange.com/2.3"
DISCUSSION_TAGS = ("python", "java", "javascript", "go", "c++")
ARTICLE_QUERIES = ("python", "java", "javascript", "go", "c++", "代码")
ARTICLE_SEARCH_URLS = {
    "csdn": "https://so.csdn.net/so/search?q={query}&t=blog&p={page}",
    "zhihu": "https://www.zhihu.com/search?type=content&q={query}&page={page}",
}
CODE_DISCUSSION_TERMS = {
    "api",
    "bug",
    "class",
    "code",
    "commit",
    "css",
    "debug",
    "def ",
    "docker",
    "error",
    "exception",
    "function",
    "github",
    "html",
    "http",
    "java",
    "javascript",
    "json",
    "linux",
    "npm",
    "python",
    "react",
    "return ",
    "spring",
    "sql",
    "vue",
    "代码",
    "函数",
    "报错",
    "数据库",
    "编程",
    "算法",
}
ANONYMIZE_SKIP_KEYS = {
    "clone_url",
    "html_url",
    "license",
    "public_date",
    "site",
    "source",
    "source_url",
    "url",
}
DIRECT_PII_PATTERNS = (
    re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])"),
    re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
    re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"),
    re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"),
    re.compile(r"(?<!\d)(?:\+?86[\s-]?)?1[3-9]\d{9}(?!\d)"),
)
LABELED_PII_PATTERN = re.compile(
    r"(?i)(出生日期|生日|birth(?:day| date)?|dob|地址|address|passport(?: no\.?)?|护照号|"
    r"driver'?s license|驾照号|银行卡|信用卡|微信号?|wechat|weixin|微博|weibo|抖音|douyin)"
    r"([：:=\s]+)([^\s,;，。]+)"
)

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
    parser.add_argument("--star-order", choices=("asc", "desc"), default="desc", help="GitHub star sort order for repository search.")
    parser.add_argument(
        "--search-max-pages",
        type=int,
        default=GITHUB_SEARCH_MAX_PAGES,
        help="Maximum GitHub Search API pages to scan per language while looking for new repositories. GitHub search is capped at 10 pages.",
    )
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


def parse_gitee_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch high-star Gitee projects and generate per-project JSONL code snapshots."
    )
    parser.add_argument("--output-dir", default="final_data_gitee", help="Directory for final per-repo JSONL files.")
    parser.add_argument("--work-dir", default=".cache/gitee_repos", help="Directory for cloned repositories.")
    parser.add_argument("--commit-work-dir", default=".cache/gitee_commit_json", help="Directory for intermediate commit JSON files.")
    parser.add_argument("--repo-csv", default="gitee_group_repo.csv", help="CSV path for selected repositories.")
    parser.add_argument("--refresh-repo-csv", action="store_true", help="Ignore an existing repo CSV and fetch a fresh repository list.")
    parser.add_argument("--append-repo-csv", action="store_true", help="Fetch new repositories not already in repo CSV, append them, and process the CSV.")
    parser.add_argument("--since", default=SINCE_DATE, help="Only read commits after this date, YYYY-MM-DD.")
    parser.add_argument("--languages", nargs="+", default=list(LANGUAGES), help="Gitee programming languages to search.")
    parser.add_argument("--min-stars", type=int, default=50, help="Minimum stars for project candidates.")
    parser.add_argument("--repos-per-language", type=int, default=100, help="Candidate projects per language.")
    parser.add_argument("--target-repos", type=int, default=100, help="Target total projects to collect.")
    parser.add_argument("--max-repos", type=int, default=None, help="Maximum eligible projects to process.")
    parser.add_argument("--workers", type=int, default=10, help="Commit JSON worker threads.")
    parser.add_argument("--clone-workers", type=int, default=1, help="Clone worker threads.")
    parser.add_argument("--gitee-api-base-url", default=os.getenv("GITEE_API_BASE_URL", GITEE_API_BASE_URL), help="Gitee API base URL.")
    parser.add_argument("--gitee-token", default=None, help="Optional Gitee access token. Overrides token.json and GITEE_TOKEN.")
    parser.add_argument("--gitee-seed-org", action="append", default=[], help="Gitee organization path to scan when search returns no projects. Can be repeated.")
    parser.add_argument("--gitee-seed-user", action="append", default=[], help="Gitee username to scan when search returns no projects. Can be repeated.")
    parser.add_argument("--keep-repos", action="store_true", help="Keep cloned repositories after processing.")
    parser.add_argument("--repo", action="append", default=[], help="Process an explicit Gitee project path, e.g. owner/name.")
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


def parse_stackoverflow_dump_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stream Stack Overflow Posts.xml dump into technical Q&A JSONL."
    )
    parser.add_argument("--posts-xml", required=True, help="Path to Stack Overflow Posts.xml.")
    parser.add_argument("--output-dir", default="final_data_stackoverflow_dump", help="Directory for dump JSONL output.")
    parser.add_argument("--output-prefix", default="stackoverflow_dump", help="Output JSONL file prefix.")
    parser.add_argument("--checkpoint-file", default=None, help="Checkpoint JSON path. Defaults to <output-dir>/<output-prefix>.checkpoint.json.")
    parser.add_argument("--reset-checkpoint", action="store_true", help="Ignore any existing checkpoint and start from the beginning.")
    parser.add_argument("--tags", nargs="+", default=list(DISCUSSION_TAGS), help="Question tags to keep.")
    parser.add_argument("--since", default="", help="Optional lower bound for question creation date, YYYY-MM-DD.")
    parser.add_argument("--min-score", type=int, default=10, help="Minimum question score.")
    parser.add_argument("--min-answers", type=int, default=10, help="Minimum answer count.")
    parser.add_argument("--max-records", type=int, default=0, help="Maximum question records to write. 0 means no limit.")
    parser.add_argument("--max-answers", type=int, default=5, help="Maximum highest-score answers included per question.")
    parser.add_argument("--records-per-file", type=int, default=500, help="Number of records per output JSONL file.")
    parser.add_argument("--progress-interval", type=int, default=100_000, help="Print progress after this many parsed Posts.xml rows.")
    return parser.parse_args(argv)


def parse_public_article_args(source: str, argv: Sequence[str] | None = None) -> argparse.Namespace:
    source_label = CSDN_SOURCE_NAME if source == "csdn" else ZHIHU_SOURCE_NAME
    parser = argparse.ArgumentParser(
        description=f"Fetch public {source_label} code-related articles or answers as JSONL."
    )
    parser.add_argument("--output-dir", default=f"final_data_{source}", help=f"Directory for {source_label} JSONL files.")
    parser.add_argument("--queries", nargs="*", default=list(ARTICLE_QUERIES), help="Search keywords to collect.")
    parser.add_argument("--url", action="append", default=[], help="Explicit public article/answer URL. Can be repeated.")
    parser.add_argument("--url-file", default=None, help="Text file with one public URL per line.")
    parser.add_argument("--max-pages", type=int, default=3, help="Search result pages to scan per query.")
    parser.add_argument("--max-records", type=int, default=1000, help="Maximum records to write.")
    parser.add_argument("--min-chars", type=int, default=300, help="Minimum cleaned text characters per record.")
    parser.add_argument("--sleep-seconds", type=float, default=1.0, help="Sleep between HTTP requests.")
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
        repo
        for repo in repos
        if repo.full_name in failed and not repo.finished
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


def mask_with_x(match: re.Match[str]) -> str:
    return "x" * len(match.group(0))


def anonymize_labeled_pii(match: re.Match[str]) -> str:
    label, separator, value = match.groups()
    return f"{label}{separator}{'x' * len(value)}"


def anonymize_text(value: str) -> str:
    anonymized = value
    for pattern in DIRECT_PII_PATTERNS:
        anonymized = pattern.sub(mask_with_x, anonymized)
    return LABELED_PII_PATTERN.sub(anonymize_labeled_pii, anonymized)


def anonymize_record(value, key: str | None = None):
    if isinstance(value, str):
        if key in ANONYMIZE_SKIP_KEYS:
            return value
        return anonymize_text(value)
    if isinstance(value, list):
        return [anonymize_record(item, key=key) for item in value]
    if isinstance(value, dict):
        return {
            item_key: anonymize_record(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    return value


def build_record(
    repo: RepoInfo,
    commit: CommitInfo,
    text: str,
    project_type: str,
) -> dict:
    return anonymize_record({
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
    })


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


class PublicArticleHTMLParser(HTMLParser):
    TEXT_TAGS = {
        "article",
        "blockquote",
        "code",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "main",
        "p",
        "pre",
        "section",
        "span",
        "td",
        "th",
    }
    SKIP_TAGS = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "a" and attrs_dict.get("href"):
            self.links.append(attrs_dict["href"])
        if tag == "meta":
            key = (
                attrs_dict.get("property")
                or attrs_dict.get("name")
                or attrs_dict.get("itemprop")
                or ""
            ).lower()
            content = attrs_dict.get("content") or ""
            if key and content:
                self.meta.setdefault(key, content)
        if tag in self.TEXT_TAGS:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        if tag in self.TEXT_TAGS:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        self.text_parts.append(data)

    def title(self) -> str:
        return normalize_discussion_text("".join(self.title_parts))

    def text(self) -> str:
        return normalize_discussion_text("".join(self.text_parts))


def normalize_discussion_text(value: str) -> str:
    raw = html_lib.unescape(value or "")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in raw.splitlines()]
    compact: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if not previous_blank:
                compact.append("")
            previous_blank = True
            continue
        compact.append(line)
        previous_blank = False
    return "\n".join(compact).strip()


def parse_public_article_html(html: str) -> tuple[str, str, dict[str, str], list[str]]:
    parser = PublicArticleHTMLParser()
    parser.feed(html)
    parser.close()
    return parser.title(), parser.text(), parser.meta, parser.links


def canonical_public_article_url(url: str, base_url: str = "") -> str:
    absolute = urllib.parse.urljoin(base_url, html_lib.unescape(url.strip()))
    parsed = urllib.parse.urlparse(absolute)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    clean = parsed._replace(fragment="", query="")
    return urllib.parse.urlunparse(clean)


def article_url_source(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path
    if "blog.csdn.net" in host and "/article/details/" in path:
        return "csdn"
    if host == "zhuanlan.zhihu.com" and path.startswith("/p/"):
        return "zhihu"
    if host.endswith("zhihu.com") and re.search(r"/question/\d+(/answer/\d+)?", path):
        return "zhihu"
    return None


def extract_public_article_urls(html: str, source: str, base_url: str = "") -> list[str]:
    _, _, _, links = parse_public_article_html(html)
    urls: list[str] = []
    seen: set[str] = set()
    for link in links:
        url = canonical_public_article_url(link, base_url=base_url)
        if not url or article_url_source(url) != source or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def public_article_id(source: str, url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    slug = safe_repo_name(f"{parsed.netloc}{parsed.path}").strip("_")
    return f"{source}_{slug}"


def public_article_author(meta: dict[str, str]) -> str:
    for key in ("author", "article:author", "og:article:author", "profile:username"):
        if meta.get(key):
            return meta[key].strip()
    return "unknown"


def public_article_date(meta: dict[str, str]) -> str:
    for key in (
        "article:published_time",
        "datepublished",
        "publishdate",
        "pubdate",
        "weibo:article:create_at",
        "og:release_date",
    ):
        if meta.get(key):
            return meta[key].strip()
    return ""


def is_code_related_text(text: str) -> bool:
    lowered = text.lower()
    if any(term in lowered for term in CODE_DISCUSSION_TERMS):
        return True
    return bool(re.search(r"[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*\)\s*[{;]?", text))


def public_article_to_record(
    source: str,
    url: str,
    html: str,
    min_chars: int = 300,
) -> dict | None:
    title, body, meta, _ = parse_public_article_html(html)
    title = meta.get("og:title") or meta.get("twitter:title") or title
    text = normalize_discussion_text(f"Title: {title}\n\n{body}")
    if len(text) < min_chars or not is_code_related_text(text):
        return None
    source_name = CSDN_SOURCE_NAME if source == "csdn" else ZHIHU_SOURCE_NAME
    return anonymize_record({
        "id": public_article_id(source, url),
        "text": text + "\n",
        "meta": {
            "data_info": {
                "source": source_name,
                "type": "技术文章",
                "url": url,
                "author": public_article_author(meta),
                "public_date": public_article_date(meta),
            }
        },
    })


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
    return anonymize_record({
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
    })


def write_stackoverflow_records(records: Sequence[dict], output_path: Path) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fp:
        for record in records:
            fp.write(json.dumps(anonymize_record(record), ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(records)


def parse_stackoverflow_tags(value: str | None) -> list[str]:
    return re.findall(r"<([^>]+)>", value or "")


def parse_stackoverflow_dump_date(value: str | None) -> dt.datetime:
    if not value:
        return dt.datetime.min.replace(tzinfo=dt.UTC)
    normalized = value.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed


def stackoverflow_dump_is_code_analysis_question(question: dict[str, str]) -> bool:
    body = question.get("Body") or ""
    title = question.get("Title") or ""
    if re.search(r"</?(pre|code)\b", body, re.I):
        return True
    text = html_to_text(f"{title}\n{body}")
    return bool(
        re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*\)", text)
        or re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\s*=", text)
        or re.search(r"\b(class|def|function|return|import|select|insert|update|delete)\b", text, re.I)
        or re.search(r"\b(error|exception|traceback|stack trace|null pointer|undefined)\b", text, re.I)
    )


def stackoverflow_dump_question_to_record(
    question: dict[str, str],
    answers: Sequence[dict[str, str]],
    matching_tag: str,
) -> dict:
    question_id = question["Id"]
    selected_answers = sorted(
        answers,
        key=lambda answer: int(answer.get("Score") or 0),
        reverse=True,
    )
    text_parts = [
        f"Title: {html_to_text(question.get('Title'))}",
        "",
        f"Question:\n{html_to_text(question.get('Body'))}",
    ]
    for index, answer in enumerate(selected_answers, start=1):
        label = f"Answer {index}"
        text_parts.extend(
            [
                "",
                f"{label} (score: {int(answer.get('Score') or 0)}):",
                html_to_text(answer.get("Body")),
            ]
        )
    creation_date = parse_stackoverflow_dump_date(question.get("CreationDate"))
    return anonymize_record({
        "id": f"stackoverflow_question_{question_id}",
        "text": "\n".join(part for part in text_parts if part is not None).strip() + "\n",
        "meta": {
            "data_info": {
                "source": STACKOVERFLOW_SOURCE_NAME,
                "type": "技术问答",
                "url": f"https://stackoverflow.com/questions/{question_id}",
                "license": "CC BY-SA 4.0",
                "site": "stackoverflow",
                "tag": matching_tag,
                "tags": parse_stackoverflow_tags(question.get("Tags")),
                "score": int(question.get("Score") or 0),
                "views": int(question.get("ViewCount") or 0),
                "answer_count": int(question.get("AnswerCount") or 0),
                "author": question.get("OwnerDisplayName") or question.get("OwnerUserId") or "unknown",
                "public_date": creation_date.isoformat(timespec="seconds"),
            }
        },
    })


def iter_stackoverflow_post_rows(posts_xml: Path) -> Iterable[dict[str, str]]:
    for event, elem in ET.iterparse(posts_xml, events=("end",)):
        if elem.tag == "row":
            yield dict(elem.attrib)
        elem.clear()


def select_stackoverflow_dump_questions(
    posts_xml: Path,
    tags: Sequence[str],
    since: str,
    min_score: int,
    min_answers: int,
    max_records: int | None,
    start_after_id: int = 0,
    progress_interval: int = 100_000,
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    tag_set = {tag.lower() for tag in tags}
    since_date = dt.datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=dt.UTC) if since else None
    questions: dict[str, dict[str, str]] = {}
    matching_tags: dict[str, str] = {}
    scanned_rows = 0
    scanned_questions = 0
    for row in iter_stackoverflow_post_rows(posts_xml):
        scanned_rows += 1
        if progress_interval > 0 and scanned_rows % progress_interval == 0:
            print(
                f"Posts.xml question scan: rows={scanned_rows} questions={scanned_questions} selected={len(questions)} last_id={row.get('Id', '')}",
                flush=True,
            )
        if row.get("PostTypeId") != "1":
            continue
        scanned_questions += 1
        question_id = row.get("Id")
        if not question_id:
            continue
        if int(question_id) <= start_after_id:
            continue
        question_tags = parse_stackoverflow_tags(row.get("Tags"))
        matched = next((tag for tag in question_tags if tag.lower() in tag_set), None)
        if matched is None:
            continue
        if not stackoverflow_dump_is_code_analysis_question(row):
            continue
        if since_date is not None and parse_stackoverflow_dump_date(row.get("CreationDate")) < since_date:
            continue
        if int(row.get("Score") or 0) < min_score:
            continue
        if int(row.get("AnswerCount") or 0) < min_answers:
            continue
        questions[question_id] = row
        matching_tags[question_id] = matched
        if max_records is not None and len(questions) >= max_records:
            break
    print(
        f"Posts.xml question scan done: rows={scanned_rows} questions={scanned_questions} selected={len(questions)}",
        flush=True,
    )
    return questions, matching_tags


def collect_stackoverflow_dump_answers(
    posts_xml: Path,
    questions: dict[str, dict[str, str]],
    max_answers: int,
    progress_interval: int = 100_000,
) -> dict[str, list[dict[str, str]]]:
    question_ids = set(questions)
    answers: dict[str, list[dict[str, str]]] = {question_id: [] for question_id in question_ids}
    scanned_rows = 0
    matched_answers = 0
    for row in iter_stackoverflow_post_rows(posts_xml):
        scanned_rows += 1
        if progress_interval > 0 and scanned_rows % progress_interval == 0:
            print(
                f"Posts.xml answer scan: rows={scanned_rows} matched_answers={matched_answers}",
                flush=True,
            )
        if row.get("PostTypeId") != "2":
            continue
        parent_id = row.get("ParentId")
        if parent_id not in question_ids:
            continue
        matched_answers += 1
        bucket = answers.setdefault(parent_id, [])
        bucket.append(row)
        bucket.sort(key=lambda answer: int(answer.get("Score") or 0), reverse=True)
        del bucket[max_answers:]
    print(
        f"Posts.xml answer scan done: rows={scanned_rows} matched_answers={matched_answers}",
        flush=True,
    )
    return answers


def read_stackoverflow_dump_checkpoint(path: Path) -> dict:
    if not path.exists():
        return {"last_question_id": 0, "next_file_index": 1, "written_records": 0}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "last_question_id": int(data.get("last_question_id") or 0),
        "next_file_index": int(data.get("next_file_index") or 1),
        "written_records": int(data.get("written_records") or 0),
    }


def write_stackoverflow_dump_checkpoint(
    path: Path,
    last_question_id: int,
    next_file_index: int,
    written_records: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "last_question_id": last_question_id,
                "next_file_index": next_file_index,
                "written_records": written_records,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def stackoverflow_dump_output_path(output_dir: Path, output_prefix: str, file_index: int) -> Path:
    return output_dir / f"{output_prefix}_{file_index:06d}.jsonl"


def write_stackoverflow_dump_batches(
    posts_xml: Path,
    output_dir: Path,
    output_prefix: str,
    checkpoint_file: Path,
    tags: Sequence[str],
    since: str,
    min_score: int,
    min_answers: int,
    max_answers: int,
    max_records: int | None,
    records_per_file: int,
    progress_interval: int = 100_000,
    reset_checkpoint: bool = False,
) -> int:
    checkpoint = {"last_question_id": 0, "next_file_index": 1, "written_records": 0}
    if not reset_checkpoint:
        checkpoint = read_stackoverflow_dump_checkpoint(checkpoint_file)
    start_after_id = int(checkpoint["last_question_id"])
    questions, matching_tags = select_stackoverflow_dump_questions(
        posts_xml=posts_xml,
        tags=tags,
        since=since,
        min_score=min_score,
        min_answers=min_answers,
        max_records=max_records,
        start_after_id=start_after_id,
        progress_interval=progress_interval,
    )
    if not questions:
        return 0
    answers = collect_stackoverflow_dump_answers(
        posts_xml=posts_xml,
        questions=questions,
        max_answers=max_answers,
        progress_interval=progress_interval,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    file_index = int(checkpoint["next_file_index"])
    total_written = int(checkpoint["written_records"])
    batch: list[dict] = []
    batch_last_question_id = start_after_id
    current_run_written = 0

    def flush_batch() -> None:
        nonlocal batch, batch_last_question_id, file_index, total_written, current_run_written
        if not batch:
            return
        output_path = stackoverflow_dump_output_path(output_dir, output_prefix, file_index)
        with output_path.open("w", encoding="utf-8") as fp:
            for record in batch:
                fp.write(json.dumps(anonymize_record(record), ensure_ascii=False, separators=(",", ":")) + "\n")
        total_written += len(batch)
        current_run_written += len(batch)
        write_stackoverflow_dump_checkpoint(
            checkpoint_file,
            last_question_id=batch_last_question_id,
            next_file_index=file_index + 1,
            written_records=total_written,
        )
        print(f"Wrote {len(batch)} records to {output_path}; checkpoint question id {batch_last_question_id}", flush=True)
        file_index += 1
        batch = []

    for question_id, question in questions.items():
        record = stackoverflow_dump_question_to_record(
            question,
            answers.get(question_id, []),
            matching_tag=matching_tags[question_id],
        )
        batch.append(record)
        batch_last_question_id = int(question_id)
        if len(batch) >= max(1, records_per_file):
            flush_batch()
    flush_batch()
    return current_run_written


def write_stackoverflow_dump_jsonl(
    posts_xml: Path,
    output_path: Path,
    tags: Sequence[str],
    since: str,
    min_score: int,
    min_answers: int,
    max_answers: int,
    max_records: int,
    progress_interval: int = 0,
) -> int:
    questions, matching_tags = select_stackoverflow_dump_questions(
        posts_xml=posts_xml,
        tags=tags,
        since=since,
        min_score=min_score,
        min_answers=min_answers,
        max_records=max_records,
        progress_interval=progress_interval,
    )
    answers = collect_stackoverflow_dump_answers(
        posts_xml=posts_xml,
        questions=questions,
        max_answers=max_answers,
        progress_interval=progress_interval,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with output_path.open("w", encoding="utf-8") as fp:
        for question_id, question in questions.items():
            record = stackoverflow_dump_question_to_record(
                question,
                answers.get(question_id, []),
                matching_tag=matching_tags[question_id],
            )
            fp.write(json.dumps(anonymize_record(record), ensure_ascii=False, separators=(",", ":")) + "\n")
            written += 1
    return written


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
        fp.write(json.dumps(anonymize_record(record), ensure_ascii=False, separators=(",", ":")) + "\n")
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
        json.dumps(anonymize_record(record), ensure_ascii=False, separators=(",", ":")),
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
    search_max_pages: int = 10,
    star_order: str = "desc",
) -> list[RepoInfo]:
    repos: list[RepoInfo] = []
    seen: set[str] = set(existing_repo_names or set())
    max_pages = min(max(1, search_max_pages), GITHUB_SEARCH_MAX_PAGES)
    for language in languages:
        page = 1
        language_selected = 0
        while language_selected < repos_per_language and page <= max_pages:
            query = f"language:{language} stars:>{min_stars} archived:false mirror:false"
            params = urllib.parse.urlencode(
                {
                    "q": query,
                    "sort": "stars",
                    "order": star_order,
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


def fetch_gitee_project(
    repo_name: str,
    gitee_api_base_url: str = GITEE_API_BASE_URL,
    gitee_token: str | None = None,
) -> RepoInfo:
    encoded = urllib.parse.quote(repo_name.strip(), safe="/")
    item = gitee_get_json(f"{normalize_base_url(gitee_api_base_url)}/repos/{encoded}", gitee_token)
    if not isinstance(item, dict):
        raise ValueError(f"Unexpected Gitee project payload for {repo_name}")
    return gitee_project_to_repo(item)


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


def gitee_project_to_repo(item: dict, language: str | None = None) -> RepoInfo:
    owner = item.get("owner") or item.get("namespace") or {}
    owner_path = owner.get("path") or owner.get("login") or owner.get("name") or ""
    repo_path = item.get("path") or item.get("name") or ""
    full_name = (
        item.get("full_name")
        or item.get("human_name")
        or item.get("path_with_namespace")
        or (f"{owner_path}/{repo_path}" if owner_path and repo_path else repo_path)
    )
    raw_html_url = str(item.get("html_url") or item.get("url") or f"https://gitee.com/{full_name}")
    html_url = raw_html_url[:-4] if raw_html_url.endswith(".git") else raw_html_url
    clone_url = (
        item.get("clone_url")
        or item.get("html_url_to_repo")
        or item.get("http_url_to_repo")
        or (raw_html_url if raw_html_url.endswith(".git") else f"{html_url.rstrip('/')}.git")
    )
    topics = item.get("topics") or item.get("tags") or []
    if isinstance(topics, str):
        topics = [topics]
    return RepoInfo(
        full_name=str(full_name).strip(),
        clone_url=str(clone_url),
        html_url=str(html_url),
        language=item.get("language") or language or "unknown",
        stargazers_count=int(item.get("stars_count") or item.get("stargazers_count") or item.get("watchers_count") or 0),
        description=item.get("description") or "",
        topics=list(topics or []),
        pushed_at=item.get("pushed_at") or item.get("updated_at") or "",
        source=GITEE_SOURCE_NAME,
    )


def append_query_param(url: str, name: str, value: str | None) -> str:
    if not value:
        return url
    separator = "&" if urllib.parse.urlparse(url).query else "?"
    return f"{url}{separator}{urllib.parse.urlencode({name: value})}"


def is_gitee_search_candidate(item: dict, language: str, min_stars: int) -> bool:
    if bool(item.get("private")):
        return False
    if bool(item.get("fork")):
        return False
    stars = int(item.get("stars_count") or item.get("stargazers_count") or item.get("watchers_count") or 0)
    if stars <= min_stars:
        return False
    item_language = item.get("language")
    if item_language and str(item_language).lower() != language.lower():
        return False
    return True


def fetch_gitee_owner_repos(
    owner: str,
    owner_type: str,
    gitee_api_base_url: str,
    gitee_token: str | None,
    max_pages: int,
) -> list[dict]:
    base_url = normalize_base_url(gitee_api_base_url)
    encoded_owner = urllib.parse.quote(owner.strip(), safe="")
    items: list[dict] = []
    for page in range(1, max_pages + 1):
        if owner_type == "org":
            path = f"{base_url}/orgs/{encoded_owner}/repos"
            params = {
                "type": "public",
                "per_page": GITEE_REPOS_PER_PAGE,
                "page": page,
            }
        else:
            path = f"{base_url}/users/{encoded_owner}/repos"
            params = {
                "type": "all",
                "sort": "pushed",
                "direction": "desc",
                "per_page": GITEE_REPOS_PER_PAGE,
                "page": page,
            }
        url = f"{path}?{urllib.parse.urlencode(params)}"
        try:
            payload = gitee_get_json(url, gitee_token)
        except Exception as exc:
            print(f"Gitee seed {owner_type} {owner}: API request failed ({exc}); skipping", flush=True)
            break
        if not isinstance(payload, list) or not payload:
            break
        items.extend(item for item in payload if isinstance(item, dict))
        if len(payload) < GITEE_REPOS_PER_PAGE:
            break
    return items


def fetch_gitee_seed_projects(
    languages: Sequence[str],
    min_stars: int,
    repos_per_language: int,
    gitee_api_base_url: str,
    gitee_token: str | None,
    max_repos: int | None,
    existing_repo_names: set[str] | None,
    seed_orgs: Sequence[str],
    seed_users: Sequence[str],
) -> list[RepoInfo]:
    repos: list[RepoInfo] = []
    seen: set[str] = set(existing_repo_names or set())
    language_set = {language.lower() for language in languages}
    seeds = [("org", org) for org in seed_orgs] + [("user", user) for user in seed_users]
    max_pages = min(GITEE_MAX_SEARCH_PAGES, max(1, repos_per_language // max(1, GITEE_REPOS_PER_PAGE) + 1))
    for owner_type, owner in seeds:
        owner_items = fetch_gitee_owner_repos(
            owner=owner,
            owner_type=owner_type,
            gitee_api_base_url=gitee_api_base_url,
            gitee_token=gitee_token,
            max_pages=max_pages,
        )
        candidates = []
        for item in owner_items:
            if bool(item.get("private")):
                continue
            if item.get("public") is False:
                continue
            stars = int(item.get("stars_count") or item.get("stargazers_count") or item.get("watchers_count") or 0)
            if stars <= min_stars:
                continue
            item_language = item.get("language")
            if item_language and str(item_language).lower() not in language_set:
                continue
            candidates.append(gitee_project_to_repo(item, language=item_language or "unknown"))
        candidates.sort(key=lambda repo: repo.stargazers_count, reverse=True)
        selected = select_eligible_repos(candidates, max_repos=max_repos, seen=seen)
        repos.extend(selected)
        if max_repos is not None and len(repos) >= max_repos:
            return repos[:max_repos]
    repos.sort(key=lambda repo: repo.stargazers_count, reverse=True)
    return repos[:max_repos] if max_repos is not None else repos


def fetch_high_star_gitee_projects(
    languages: Sequence[str],
    min_stars: int,
    repos_per_language: int,
    gitee_api_base_url: str = GITEE_API_BASE_URL,
    gitee_token: str | None = None,
    max_repos: int | None = None,
    existing_repo_names: set[str] | None = None,
    seed_orgs: Sequence[str] | None = None,
    seed_users: Sequence[str] | None = None,
) -> list[RepoInfo]:
    repos: list[RepoInfo] = []
    seen: set[str] = set(existing_repo_names or set())
    base_url = normalize_base_url(gitee_api_base_url)
    max_pages = min(GITEE_MAX_SEARCH_PAGES, max(10, repos_per_language))
    for language in languages:
        page = 1
        language_selected = 0
        while language_selected < repos_per_language and page <= max_pages:
            params = urllib.parse.urlencode(
                {
                    "q": language,
                    "language": language,
                    "sort": "stars_count",
                    "order": "desc",
                    "per_page": GITEE_REPOS_PER_PAGE,
                    "page": page,
                }
            )
            payload = gitee_get_json(f"{base_url}/search/repositories?{params}", gitee_token)
            items = payload.get("items", payload) if isinstance(payload, dict) else payload
            if not isinstance(items, list) or not items:
                break
            candidates = [
                gitee_project_to_repo(item, language=language)
                for item in items
                if is_gitee_search_candidate(item, language, min_stars)
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
    if repos:
        return repos
    fallback_seed_orgs = tuple(seed_orgs or GITEE_DEFAULT_SEED_ORGS)
    return fetch_gitee_seed_projects(
        languages=languages,
        min_stars=min_stars,
        repos_per_language=repos_per_language,
        gitee_api_base_url=gitee_api_base_url,
        gitee_token=gitee_token,
        max_repos=max_repos,
        existing_repo_names=existing_repo_names,
        seed_orgs=fallback_seed_orgs,
        seed_users=tuple(seed_users or ()),
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
        max_pages = min(
            GITLAB_MAX_SEARCH_PAGES,
            max(10, repos_per_language),
        )
        while language_selected < repos_per_language and page <= max_pages:
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
            if max((int(item.get("star_count") or 0) for item in payload), default=0) <= min_stars:
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


def repo_has_commit_since_via_api(repo: RepoInfo, since: str) -> bool | None:
    latest_commit_date = fetch_latest_commit_date_via_api(repo)
    if latest_commit_date is None:
        return None
    since_date = dt.datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=dt.UTC)
    return latest_commit_date >= since_date


def fetch_latest_commit_date_via_api(repo: RepoInfo) -> dt.datetime | None:
    host = urllib.parse.urlparse(repo.html_url).netloc.lower()
    if "github.com" in host:
        github_token = resolve_token(None, "github", "GITHUB_TOKEN")
        encoded = urllib.parse.quote(repo.full_name.strip(), safe="/")
        payload = github_get_json(f"https://api.github.com/repos/{encoded}/commits?per_page=1", github_token)
        if not isinstance(payload, list) or not payload:
            return None
        date_value = (((payload[0].get("commit") or {}).get("author") or {}).get("date"))
        if not date_value:
            return None
        return dt.datetime.fromisoformat(date_value.replace("Z", "+00:00"))
    if "gitlab" in host:
        gitlab_token = os.getenv("GITLAB_TOKEN")
        project_path = repo.full_name.strip()
        encoded = urllib.parse.quote(project_path, safe="")
        base_url = f"{urllib.parse.urlparse(repo.html_url).scheme}://{host}"
        payload = gitlab_get_json(f"{base_url}/api/v4/projects/{encoded}/repository/commits?per_page=1", gitlab_token)
        if not isinstance(payload, list) or not payload:
            return None
        date_value = payload[0].get("committed_date") or payload[0].get("created_at")
        if not date_value:
            return None
        return dt.datetime.fromisoformat(str(date_value).replace("Z", "+00:00"))
    if "gitee" in host:
        gitee_token = resolve_token(None, "gitee", "GITEE_TOKEN")
        project_path = repo.full_name.strip()
        encoded = urllib.parse.quote(project_path, safe="/")
        base_url = f"{urllib.parse.urlparse(repo.html_url).scheme}://{host}/api/v5"
        payload = gitee_get_json(f"{base_url}/repos/{encoded}/commits?per_page=1", gitee_token)
        items = payload.get("items", payload) if isinstance(payload, dict) else payload
        if not isinstance(items, list) or not items:
            return None
        commit = items[0].get("commit") or {}
        author = commit.get("author") if isinstance(commit, dict) else {}
        date_value = (
            items[0].get("committed_date")
            or items[0].get("created_at")
            or items[0].get("date")
            or (author or {}).get("date")
        )
        if not date_value:
            return None
        return dt.datetime.fromisoformat(str(date_value).replace("Z", "+00:00"))
    return None


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


def gitee_get_json(url: str, gitee_token: str | None) -> dict | list:
    headers = {
        "Accept": "application/json",
        "User-Agent": "gitee-code-harvester",
    }
    request_url = append_query_param(url, "access_token", gitee_token)
    request = urllib.request.Request(request_url, headers=headers)
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


def public_article_get_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "discussion-harvester (+public technical article collection)",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
        content_type = response.headers.get("Content-Type", "")
    charset_match = re.search(r"charset=([\w.-]+)", content_type, re.I)
    encoding = charset_match.group(1) if charset_match else "utf-8"
    try:
        return raw.decode(encoding, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


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
    metadata_missing = 0
    non_code_commits = 0
    empty_records = 0
    commits = list_commits_since(repo_dir, since)
    print(f"{repo.full_name}: found {len(commits)} commits since {since}", flush=True)
    for sha in commits:
        if max_commits is not None and processed_commits >= max_commits:
            break
        commit = get_commit_info(repo_dir, sha)
        if commit is None:
            metadata_missing += 1
            continue
        if not is_code_commit(commit.changed_files):
            non_code_commits += 1
            continue
        checkout_commit(repo_dir, commit.sha)
        written = write_json_for_commit(
            repo_dir=repo_dir,
            output_path=commit_dir / f"{commit.sha}.json",
            repo=repo,
            commit=commit,
        )
        if written:
            total += written
        else:
            empty_records += 1
        processed_commits += 1
    print(
        f"{repo.full_name}: commit scan summary "
        f"scanned={min(len(commits), max_commits) if max_commits is not None else len(commits)} "
        f"metadata_missing={metadata_missing} "
        f"non_code={non_code_commits} "
        f"eligible={processed_commits} "
        f"written={total} "
        f"empty_records={empty_records}",
        flush=True,
    )
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
    print(
        f"Processing {len(repos)} repositories with "
        f"clone_workers={max(1, clone_workers)}, workers={max(1, jsonl_workers)}, since={since}",
        flush=True,
    )

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
                try:
                    has_recent_commit = repo_has_commit_since_via_api(repo, since)
                except Exception as exc:
                    has_recent_commit = None
                    print(f"{repo.full_name}: API precheck failed ({exc}); continuing with clone", flush=True)
                if has_recent_commit is False:
                    print(f"{repo.full_name}: API precheck found no commits since {since}; skipping clone", flush=True)
                    if repo_csv_path is not None:
                        with csv_lock:
                            mark_repo_finished(repo_csv_path, repo.full_name)
                            remove_repos_from_failure_log(output_dir / "failed_repos.log", {repo.full_name})
                    continue
                if has_recent_commit is None:
                    print(f"{repo.full_name}: API precheck unavailable; continuing with clone", flush=True)
                else:
                    print(f"{repo.full_name}: API precheck found recent commits since {since}", flush=True)
                print(f"{repo.full_name}: clone start", flush=True)
                repo_dir = clone_or_update_repo(repo, work_dir, since=since)
                print(f"{repo.full_name}: clone done at {repo_dir}", flush=True)
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
                print(f"{repo.full_name}: process start", flush=True)
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
                    print(f"{repo.full_name}: wrote {count} records to {final_path}", flush=True)
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
            search_max_pages=args.search_max_pages,
            star_order=args.star_order,
        )
        repos = append_repo_csv(repos, repo_csv_path)
        print(
            f"Appended {len(repos)} new repositories to {args.repo_csv}; existing count was {len(existing)}",
            flush=True,
        )
        if len(repos) < target_new_repos:
            search_pages_scanned = min(max(1, args.search_max_pages), GITHUB_SEARCH_MAX_PAGES)
            print(
                f"Warning: requested {target_new_repos} new repositories, but only found {len(repos)} after scanning up to {search_pages_scanned} pages per language.",
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
            search_max_pages=args.search_max_pages,
            star_order=args.star_order,
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


def gitee_main(argv: Sequence[str] | None = None) -> int:
    args = parse_gitee_args(argv)
    start = time.time()
    repo_csv_path = Path(args.repo_csv)
    output_dir = Path(args.output_dir)
    ensure_repo_csv_finished_field(repo_csv_path)
    gitee_token = resolve_token(args.gitee_token, "gitee", "GITEE_TOKEN")
    if args.repo:
        repos = [
            fetch_gitee_project(
                repo_name,
                gitee_api_base_url=args.gitee_api_base_url,
                gitee_token=gitee_token,
            )
            for repo_name in args.repo
        ]
    elif args.append_repo_csv:
        existing = read_repo_csv(repo_csv_path) if repo_csv_path.exists() else []
        existing_names = {repo.full_name for repo in existing}
        target_new_repos = args.max_repos if args.max_repos is not None else args.target_repos
        repos = fetch_high_star_gitee_projects(
            languages=args.languages,
            min_stars=args.min_stars,
            repos_per_language=args.repos_per_language,
            gitee_api_base_url=args.gitee_api_base_url,
            gitee_token=gitee_token,
            max_repos=target_new_repos,
            existing_repo_names=existing_names,
            seed_orgs=args.gitee_seed_org or GITEE_DEFAULT_SEED_ORGS,
            seed_users=args.gitee_seed_user,
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
        repos = fetch_high_star_gitee_projects(
            languages=args.languages,
            min_stars=args.min_stars,
            repos_per_language=args.repos_per_language,
            gitee_api_base_url=args.gitee_api_base_url,
            gitee_token=gitee_token,
            max_repos=max_repos,
            seed_orgs=args.gitee_seed_org or GITEE_DEFAULT_SEED_ORGS,
            seed_users=args.gitee_seed_user,
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


def stackoverflow_dump_main(argv: Sequence[str] | None = None) -> int:
    args = parse_stackoverflow_dump_args(argv)
    start = time.time()
    posts_xml = Path(args.posts_xml)
    output_dir = Path(args.output_dir)
    checkpoint_file = Path(args.checkpoint_file) if args.checkpoint_file else output_dir / f"{args.output_prefix}.checkpoint.json"
    max_records = None if args.max_records <= 0 else args.max_records
    written = write_stackoverflow_dump_batches(
        posts_xml=posts_xml,
        output_dir=output_dir,
        output_prefix=args.output_prefix,
        checkpoint_file=checkpoint_file,
        tags=args.tags,
        since=args.since,
        min_score=args.min_score,
        min_answers=args.min_answers,
        max_answers=args.max_answers,
        max_records=max_records,
        records_per_file=args.records_per_file,
        progress_interval=args.progress_interval,
        reset_checkpoint=args.reset_checkpoint,
    )
    print(f"Wrote {written} Stack Overflow dump records to {output_dir}", flush=True)
    print(f"Checkpoint: {checkpoint_file}", flush=True)
    print(f"Done in {time.time() - start:.1f}s", flush=True)
    return 0


def read_url_file(path: Path) -> list[str]:
    urls: list[str] = []
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            value = line.strip()
            if value and not value.startswith("#"):
                urls.append(value)
    return urls


def discover_public_article_urls(
    source: str,
    queries: Sequence[str],
    max_pages: int,
    sleep_seconds: float = 1.0,
) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    template = ARTICLE_SEARCH_URLS[source]
    for query in queries:
        for page in range(1, max(1, max_pages) + 1):
            search_url = template.format(
                query=urllib.parse.quote(query),
                page=page,
            )
            try:
                html = public_article_get_text(search_url)
            except urllib.error.HTTPError as exc:
                print(f"{source} search {query} page {page}: HTTP {exc.code}; skipping page", flush=True)
                continue
            except urllib.error.URLError as exc:
                print(f"{source} search {query} page {page}: {exc}; skipping page", flush=True)
                continue
            page_urls = extract_public_article_urls(html, source=source, base_url=search_url)
            new_count = 0
            for url in page_urls:
                if url in seen:
                    continue
                seen.add(url)
                urls.append(url)
                new_count += 1
            print(f"{source} search {query} page {page}: found {new_count} new urls", flush=True)
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
    return urls


def collect_public_article_records(
    source: str,
    urls: Sequence[str],
    max_records: int,
    min_chars: int,
    sleep_seconds: float = 1.0,
) -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()
    for raw_url in urls:
        if len(records) >= max_records:
            break
        url = canonical_public_article_url(raw_url)
        if not url or url in seen:
            continue
        seen.add(url)
        detected_source = article_url_source(url)
        if detected_source != source:
            print(f"{url}: skipped unsupported {source} URL shape", flush=True)
            continue
        try:
            html = public_article_get_text(url)
            record = public_article_to_record(source=source, url=url, html=html, min_chars=min_chars)
        except urllib.error.HTTPError as exc:
            print(f"{url}: HTTP {exc.code}; skipped", flush=True)
            continue
        except urllib.error.URLError as exc:
            print(f"{url}: {exc}; skipped", flush=True)
            continue
        if record is None:
            print(f"{url}: skipped empty, short, or non-code article", flush=True)
            continue
        records.append(record)
        print(f"{url}: collected", flush=True)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    return records


def public_article_main(source: str, argv: Sequence[str] | None = None) -> int:
    args = parse_public_article_args(source, argv)
    start = time.time()
    output_dir = Path(args.output_dir)
    urls: list[str] = []
    urls.extend(args.url or [])
    if args.url_file:
        urls.extend(read_url_file(Path(args.url_file)))
    if args.queries and len(urls) < args.max_records:
        urls.extend(
            discover_public_article_urls(
                source=source,
                queries=args.queries,
                max_pages=args.max_pages,
                sleep_seconds=args.sleep_seconds,
            )
        )
    records = collect_public_article_records(
        source=source,
        urls=urls,
        max_records=max(0, args.max_records),
        min_chars=max(0, args.min_chars),
        sleep_seconds=args.sleep_seconds,
    )
    output_path = output_dir / f"{source}_articles.jsonl"
    written = write_stackoverflow_records(records, output_path)
    print(f"Wrote {written} {source} records to {output_path}", flush=True)
    print(f"Done in {time.time() - start:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
