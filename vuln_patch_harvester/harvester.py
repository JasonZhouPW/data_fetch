from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


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


@dataclass(frozen=True)
class CommitReference:
    clone_url: str
    html_url: str
    commit: str


@dataclass(frozen=True)
class PatchSeed:
    vuln_id: str
    source: str
    repo_url: str
    clone_url: str
    html_url: str
    fix_commit: str
    language: str = "unknown"
    cwe: list[str] = field(default_factory=list)
    severity: str = ""
    description: str = ""
    trigger_code: str = ""
    trigger_type: str = "poc"
    assembly: dict = field(default_factory=dict)
    license: str = ""
    source_url: str = ""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build vulnerability patch-pair JSONL records from curated CVE/CTF/reversing seeds."
    )
    parser.add_argument("--seed-jsonl", required=True, help="Input JSONL containing commit URLs and trigger metadata.")
    parser.add_argument("--output-dir", default="final_data_vuln_patch", help="Directory for output JSONL.")
    parser.add_argument("--work-dir", default=".cache/vuln_patch_repos", help="Directory for cloned repositories.")
    parser.add_argument("--require-trigger", action="store_true", help="Skip seeds without trigger_code or trigger.code.")
    parser.add_argument("--max-records", type=int, default=None, help="Maximum patch-pair records to write.")
    parser.add_argument("--keep-repos", action="store_true", help="Keep cloned repositories after processing.")
    return parser.parse_args(argv)


def parse_commit_reference(url: str) -> CommitReference:
    parsed = urllib.parse.urlparse(url)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Unsupported commit URL: {url}")
    if "github.com" in parsed.netloc:
        if len(parts) < 4 or parts[2] != "commit":
            raise ValueError(f"Unsupported GitHub commit URL: {url}")
        repo_path = "/".join(parts[:2])
        commit = parts[3]
    elif "gitlab" in parsed.netloc:
        if "-" in parts and "commit" in parts:
            dash_index = parts.index("-")
            commit_index = parts.index("commit", dash_index)
            repo_path = "/".join(parts[:dash_index])
            commit = parts[commit_index + 1]
        elif "commit" in parts:
            commit_index = parts.index("commit")
            repo_path = "/".join(parts[:commit_index])
            commit = parts[commit_index + 1]
        else:
            raise ValueError(f"Unsupported GitLab commit URL: {url}")
    elif "gitee.com" in parsed.netloc:
        if "commit" not in parts:
            raise ValueError(f"Unsupported Gitee commit URL: {url}")
        commit_index = parts.index("commit")
        repo_path = "/".join(parts[:commit_index])
        commit = parts[commit_index + 1]
    else:
        raise ValueError(f"Unsupported commit host: {url}")
    html_url = f"{parsed.scheme}://{parsed.netloc}/{repo_path}"
    return CommitReference(
        clone_url=f"{html_url}.git",
        html_url=html_url,
        commit=commit,
    )


def read_seed_records(path: Path, require_trigger: bool = False) -> list[PatchSeed]:
    seeds: list[PatchSeed] = []
    with path.open("r", encoding="utf-8") as fp:
        for line_number, line in enumerate(fp, start=1):
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            trigger = payload.get("trigger") or {}
            trigger_code = payload.get("trigger_code") or trigger.get("code") or trigger.get("content") or ""
            if require_trigger and not trigger_code:
                continue
            reference = None
            commit_url = payload.get("commit_url") or payload.get("fix_commit_url")
            if commit_url:
                reference = parse_commit_reference(str(commit_url))
            repo_url = payload.get("repo_url") or payload.get("html_url") or (reference.html_url if reference else "")
            clone_url = payload.get("clone_url") or (reference.clone_url if reference else repo_url)
            fix_commit = payload.get("fix_commit") or payload.get("commit") or (reference.commit if reference else "")
            if not clone_url or not fix_commit:
                raise ValueError(f"Seed line {line_number} must include commit_url or clone_url plus fix_commit")
            seeds.append(
                PatchSeed(
                    vuln_id=str(payload.get("id") or payload.get("cve") or payload.get("advisory_id") or fix_commit),
                    source=str(payload.get("source") or "curated"),
                    repo_url=str(repo_url),
                    clone_url=str(clone_url),
                    html_url=str(payload.get("html_url") or repo_url),
                    fix_commit=str(fix_commit),
                    language=str(payload.get("language") or "unknown"),
                    cwe=list(payload.get("cwe") or []),
                    severity=str(payload.get("severity") or ""),
                    description=str(payload.get("description") or ""),
                    trigger_code=str(trigger_code),
                    trigger_type=str(trigger.get("type") or payload.get("trigger_type") or "poc"),
                    assembly=dict(payload.get("assembly") or {}),
                    license=str(payload.get("license") or ""),
                    source_url=str(payload.get("url") or payload.get("source_url") or commit_url or repo_url),
                )
            )
    return seeds


def run_git(repo_dir: Path, args: Sequence[str], capture: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    return result.stdout if capture else ""


def try_git(repo_dir: Path, args: Sequence[str]) -> str | None:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        return None
    return result.stdout


def safe_repo_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", value.strip())


def clone_or_fetch_repo(seed: PatchSeed, work_dir: Path) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = work_dir / safe_repo_name(seed.html_url or seed.clone_url)
    if repo_dir.exists():
        run_git(repo_dir, ["fetch", "--all", "--tags", "--prune"], capture=False)
        return repo_dir
    subprocess.run(["git", "clone", "--quiet", seed.clone_url, str(repo_dir)], check=True)
    return repo_dir


def is_code_path(path: str) -> bool:
    return Path(path).suffix.lower() in CODE_EXTENSIONS


def commit_parent(repo_dir: Path, commit: str) -> str:
    return run_git(repo_dir, ["rev-parse", f"{commit}^"]).strip()


def changed_code_files(repo_dir: Path, parent: str, commit: str) -> list[str]:
    output = run_git(repo_dir, ["diff", "--name-only", parent, commit])
    return [line.strip() for line in output.splitlines() if line.strip() and is_code_path(line.strip())]


def git_show_file(repo_dir: Path, commit: str, file_path: str) -> str | None:
    return try_git(repo_dir, ["show", f"{commit}:{file_path}"])


def build_patch_records(seed: PatchSeed, repo_dir: Path) -> list[dict]:
    parent = commit_parent(repo_dir, seed.fix_commit)
    records: list[dict] = []
    for file_path in changed_code_files(repo_dir, parent, seed.fix_commit):
        vulnerable = git_show_file(repo_dir, parent, file_path)
        fixed = git_show_file(repo_dir, seed.fix_commit, file_path)
        if vulnerable is None or fixed is None or vulnerable == fixed:
            continue
        diff = run_git(repo_dir, ["diff", parent, seed.fix_commit, "--", file_path])
        records.append(
            {
                "id": f"{seed.vuln_id}:{safe_repo_name(seed.html_url or seed.clone_url)}:{seed.fix_commit}:{file_path}",
                "task_type": "patch_pair",
                "source": seed.source,
                "vuln": {
                    "id": seed.vuln_id,
                    "cwe": seed.cwe,
                    "severity": seed.severity,
                    "description": seed.description,
                },
                "repo": {
                    "url": seed.html_url or seed.repo_url,
                    "clone_url": seed.clone_url,
                    "language": seed.language,
                    "vulnerable_commit": parent,
                    "fixed_commit": seed.fix_commit,
                },
                "code": {
                    "file_path": file_path,
                    "vulnerable": vulnerable,
                    "fixed": fixed,
                    "diff": diff,
                },
                "trigger": {
                    "type": seed.trigger_type,
                    "code": seed.trigger_code,
                    "safe_to_run": False,
                },
                "assembly": seed.assembly,
                "meta": {
                    "license": seed.license,
                    "url": seed.source_url,
                    "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                },
            }
        )
    return records


def write_jsonl(records: Sequence[dict], output_path: Path) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fp:
        for record in records:
            fp.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(records)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    seeds = read_seed_records(Path(args.seed_jsonl), require_trigger=args.require_trigger)
    output_dir = Path(args.output_dir)
    work_dir = Path(args.work_dir)
    records: list[dict] = []
    for seed in seeds:
        if args.max_records is not None and len(records) >= args.max_records:
            break
        repo_dir = clone_or_fetch_repo(seed, work_dir)
        seed_records = build_patch_records(seed, repo_dir)
        records.extend(seed_records)
        if args.max_records is not None:
            records = records[: args.max_records]
    output_path = output_dir / "vuln_patch_pairs.jsonl"
    written = write_jsonl(records, output_path)
    print(f"Wrote {written} vulnerability patch-pair records to {output_path}", flush=True)
    if not args.keep_repos:
        for child in work_dir.iterdir() if work_dir.exists() else []:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
        try:
            work_dir.rmdir()
        except OSError:
            pass
    return 0
