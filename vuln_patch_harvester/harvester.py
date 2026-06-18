from __future__ import annotations

import argparse
import datetime as dt
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from github_code_harvester.harvester import anonymize_record


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
COMMIT_URL_RE = re.compile(
    r"https?://(?:github\.com/[^/\s\"')]+/[^/\s\"')]+/commit/[0-9A-Fa-f]+"
    r"|gitlab[^/\s\"')]+/(?:[^/\s\"')]+/)+(?:-/)?commit/[0-9A-Fa-f]+"
    r"|gitee\.com/(?:[^/\s\"')]+/)+commit/[0-9A-Fa-f]+)"
)


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
    subparsers = parser.add_subparsers(dest="command")
    build_seed_parser = subparsers.add_parser(
        "build-seeds",
        help="Extract seed candidates from local NVD/OSV/CVE JSON or JSONL files.",
    )
    build_seed_parser.add_argument("--input", action="append", required=True, help="Input JSON/JSONL file or directory. Can be repeated.")
    build_seed_parser.add_argument("--output", default="vuln_seeds.jsonl", help="Output seed candidate JSONL path.")
    build_seed_parser.add_argument("--require-trigger", action="store_true", help="Only write candidates that already include trigger_code.")
    format_parser = subparsers.add_parser(
        "format-qa",
        help="Convert patch-pair JSONL records into security QA JSONL records.",
    )
    format_parser.add_argument("--input", required=True, help="Input vuln_patch_pairs.jsonl path.")
    format_parser.add_argument("--output", required=True, help="Output QA JSONL path.")
    nvd_parser = subparsers.add_parser(
        "fetch-nvd",
        help="Download CVE records from the NVD 2.0 API into local JSONL.",
    )
    nvd_parser.add_argument("--start-date", required=True, help="Publication start date, YYYY-MM-DD.")
    nvd_parser.add_argument("--end-date", required=True, help="Publication end date, YYYY-MM-DD.")
    nvd_parser.add_argument("--output", default="nvd_raw.jsonl", help="Output NVD raw JSONL path.")
    nvd_parser.add_argument("--api-key", default=None, help="Optional NVD API key. Defaults to NVD_API_KEY env var.")
    nvd_parser.add_argument("--results-per-page", type=int, default=2000, help="NVD page size, capped at 2000.")
    nvd_parser.add_argument("--max-records", type=int, default=None, help="Maximum CVE records to download.")
    nvd_parser.add_argument("--max-retries", type=int, default=3, help="Maximum retries for NVD rate-limit/server errors.")
    nvd_parser.add_argument("--retry-sleep-seconds", type=float, default=10.0, help="Sleep seconds before retrying NVD requests.")
    nvd_parser.add_argument(
        "--request-interval-seconds",
        type=float,
        default=None,
        help="Minimum seconds between NVD requests. Defaults to 7 without an API key, 0.7 with a key.",
    )
    harvest_parser = subparsers.add_parser(
        "harvest-nvd-seeds",
        help="Download NVD records in date batches until commit seed candidates are found.",
    )
    harvest_parser.add_argument("--start-date", required=True, help="Publication start date, YYYY-MM-DD.")
    harvest_parser.add_argument("--end-date", required=True, help="Publication end date, YYYY-MM-DD.")
    harvest_parser.add_argument("--raw-output", default="nvd_raw.jsonl", help="Output NVD raw JSONL path.")
    harvest_parser.add_argument("--seed-output", default="vuln_seeds.jsonl", help="Output seed candidate JSONL path.")
    harvest_parser.add_argument("--target-seeds", type=int, default=10, help="Stop after finding this many seed candidates.")
    harvest_parser.add_argument("--batch-days", type=int, default=30, help="NVD query window size in days.")
    harvest_parser.add_argument("--records-per-batch", type=int, default=2000, help="Maximum CVE records per date batch.")
    harvest_parser.add_argument("--api-key", default=None, help="Optional NVD API key. Defaults to NVD_API_KEY env var.")
    harvest_parser.add_argument("--max-retries", type=int, default=3, help="Maximum retries for NVD rate-limit/server errors.")
    harvest_parser.add_argument("--retry-sleep-seconds", type=float, default=10.0, help="Sleep seconds before retrying NVD requests.")
    harvest_parser.add_argument(
        "--request-interval-seconds",
        type=float,
        default=None,
        help="Minimum seconds between NVD requests. Defaults to 7 without an API key, 0.7 with a key.",
    )
    parser.add_argument("--seed-jsonl", help="Input JSONL containing commit URLs and trigger metadata.")
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


def nvd_datetime(value: str) -> str:
    if "T" in value:
        return value
    return f"{value}T00:00:00.000Z"


def retry_after_seconds(error: urllib.error.HTTPError, fallback: float) -> float:
    retry_after = error.headers.get("Retry-After") if error.headers else None
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            return fallback
    return fallback


def fetch_json(
    url: str,
    headers: dict[str, str] | None = None,
    max_retries: int = 3,
    retry_sleep_seconds: float = 10.0,
) -> dict:
    request = urllib.request.Request(url, headers=headers or {})
    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if not retryable or attempt >= max_retries:
                raise
            sleep_seconds = retry_after_seconds(exc, retry_sleep_seconds)
            print(
                f"NVD request got HTTP {exc.code}; retrying in {sleep_seconds:g}s "
                f"({attempt + 1}/{max_retries})",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(sleep_seconds)
            attempt += 1
        except http.client.IncompleteRead as exc:
            if attempt >= max_retries:
                raise
            print(
                f"NVD request read incomplete response; retrying in {retry_sleep_seconds:g}s "
                f"({attempt + 1}/{max_retries})",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(retry_sleep_seconds)
            attempt += 1
        except (TimeoutError, urllib.error.URLError) as exc:
            if attempt >= max_retries:
                raise
            print(
                f"NVD request failed with {exc.__class__.__name__}; retrying in {retry_sleep_seconds:g}s "
                f"({attempt + 1}/{max_retries})",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(retry_sleep_seconds)
            attempt += 1


def fetch_nvd_cves(
    start_date: str,
    end_date: str,
    results_per_page: int = 2000,
    max_records: int | None = None,
    api_key: str | None = None,
    max_retries: int = 3,
    retry_sleep_seconds: float = 10.0,
    request_interval_seconds: float | None = None,
) -> list[dict]:
    records: list[dict] = []
    start_index = 0
    page_size = min(2000, max(1, results_per_page))
    headers = {"User-Agent": "vuln-patch-harvester"}
    if api_key:
        headers["apiKey"] = api_key
    if request_interval_seconds is None:
        request_interval_seconds = 0.7 if api_key else 7.0
    while True:
        params = urllib.parse.urlencode(
            {
                "pubStartDate": nvd_datetime(start_date),
                "pubEndDate": nvd_datetime(end_date),
                "resultsPerPage": page_size,
                "startIndex": start_index,
            }
        )
        if request_interval_seconds > 0:
            print(
                f"Waiting {request_interval_seconds:g}s before NVD request "
                f"({start_date} to {end_date}, startIndex={start_index})",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(request_interval_seconds)
        payload = fetch_json(
            f"https://services.nvd.nist.gov/rest/json/cves/2.0?{params}",
            headers=headers,
            max_retries=max_retries,
            retry_sleep_seconds=retry_sleep_seconds,
        )
        vulnerabilities = payload.get("vulnerabilities") or []
        if not isinstance(vulnerabilities, list) or not vulnerabilities:
            break
        records.extend(item for item in vulnerabilities if isinstance(item, dict))
        if max_records is not None and len(records) >= max_records:
            return records[:max_records]
        total_results = int(payload.get("totalResults") or len(records))
        results_returned = int(payload.get("resultsPerPage") or len(vulnerabilities))
        start_index += results_returned
        if start_index >= total_results:
            break
    return records


def write_nvd_cves(records: Sequence[dict], output_path: Path) -> int:
    return write_jsonl(records, output_path)


def parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def harvest_nvd_seed_candidates(
    start_date: str,
    end_date: str,
    target_seeds: int = 10,
    batch_days: int = 30,
    records_per_batch: int = 2000,
    api_key: str | None = None,
    max_retries: int = 3,
    retry_sleep_seconds: float = 10.0,
    request_interval_seconds: float | None = None,
) -> tuple[list[dict], list[dict]]:
    candidates: list[dict] = []
    raw_records: list[dict] = []
    seen_candidates: set[tuple[str, str]] = set()
    current = parse_date(start_date)
    end = parse_date(end_date)
    step_days = max(1, batch_days)
    while current < end and len(candidates) < target_seeds:
        batch_end = min(current + dt.timedelta(days=step_days), end)
        batch_records = fetch_nvd_cves(
            start_date=current.isoformat(),
            end_date=batch_end.isoformat(),
            max_records=records_per_batch,
            api_key=api_key,
            max_retries=max_retries,
            retry_sleep_seconds=retry_sleep_seconds,
            request_interval_seconds=request_interval_seconds,
        )
        raw_records.extend(batch_records)
        for candidate in build_seed_candidates_from_payloads(batch_records):
            key = (candidate["id"], candidate["commit_url"])
            if key in seen_candidates:
                continue
            seen_candidates.add(key)
            candidates.append(candidate)
            if len(candidates) >= target_seeds:
                break
        current = batch_end
    return candidates[:target_seeds], raw_records


def iter_json_values(path: Path):
    if path.is_dir():
        for child in sorted(path.rglob("*")):
            if child.is_file() and child.suffix.lower() in {".json", ".jsonl"}:
                yield from iter_json_values(child)
        return
    if path.suffix.lower() == ".jsonl":
        with path.open("r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if line:
                    yield json.loads(line)
        return
    with path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    if isinstance(payload, list):
        yield from payload
    elif isinstance(payload, dict):
        containers = (
            payload.get("vulnerabilities"),
            payload.get("CVE_Items"),
            payload.get("items"),
            payload.get("results"),
        )
        for container in containers:
            if isinstance(container, list):
                yield from container
                return
        yield payload


def walk_values(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_values(child)


def extract_commit_urls(payload: dict) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for value in walk_values(payload):
        if isinstance(value, str):
            candidates = COMMIT_URL_RE.findall(value)
            if value.startswith("http"):
                candidates.append(value)
            for candidate in candidates:
                candidate = candidate.rstrip(".,;")
                try:
                    parse_commit_reference(candidate)
                except ValueError:
                    continue
                if candidate not in seen:
                    seen.add(candidate)
                    urls.append(candidate)
    return urls


def payload_id(payload: dict) -> str:
    cve = payload.get("cve")
    if isinstance(cve, dict):
        metadata = cve.get("CVE_data_meta") or {}
        if metadata.get("ID"):
            return str(metadata["ID"])
        if cve.get("id"):
            return str(cve["id"])
    for key in ("id", "cve", "advisory_id", "ghsa_id"):
        if payload.get(key):
            return str(payload[key])
    return "unknown"


def payload_description(payload: dict) -> str:
    for key in ("summary", "details", "description"):
        if payload.get(key):
            return str(payload[key])
    cve = payload.get("cve")
    if isinstance(cve, dict):
        descriptions = ((cve.get("description") or {}).get("description_data") or [])
        if descriptions and descriptions[0].get("value"):
            return str(descriptions[0]["value"])
    return ""


def payload_severity(payload: dict) -> str:
    database_specific = payload.get("database_specific")
    if isinstance(database_specific, dict) and database_specific.get("severity"):
        return str(database_specific["severity"])
    metrics = payload.get("metrics")
    if isinstance(metrics, dict):
        for values in metrics.values():
            if isinstance(values, list) and values:
                severity = ((values[0].get("cvssData") or {}).get("baseSeverity") or values[0].get("baseSeverity"))
                if severity:
                    return str(severity)
    impact = payload.get("impact")
    if isinstance(impact, dict):
        metric = impact.get("baseMetricV3") or impact.get("baseMetricV2") or {}
        severity = ((metric.get("cvssV3") or {}).get("baseSeverity") or metric.get("severity"))
        if severity:
            return str(severity)
    return ""


def payload_cwe(payload: dict) -> list[str]:
    text = json.dumps(payload, ensure_ascii=False)
    return sorted(set(re.findall(r"CWE-\d+", text)))


def payload_trigger_code(payload: dict) -> str:
    trigger = payload.get("trigger")
    if isinstance(trigger, dict):
        return str(trigger.get("code") or trigger.get("content") or "")
    return str(payload.get("trigger_code") or "")


def payload_assembly(payload: dict) -> dict:
    assembly = payload.get("assembly")
    return dict(assembly) if isinstance(assembly, dict) else {"arch": "", "disassembly": "", "comments": ""}


def build_seed_candidates_from_payloads(payloads, require_trigger: bool = False) -> list[dict]:
    candidates: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        trigger_code = payload_trigger_code(payload)
        if require_trigger and not trigger_code:
            continue
        for commit_url in extract_commit_urls(payload):
            reference = parse_commit_reference(commit_url)
            key = (payload_id(payload), commit_url)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "id": payload_id(payload),
                    "source": str(payload.get("source") or "seed_builder"),
                    "commit_url": commit_url,
                    "clone_url": reference.clone_url,
                    "html_url": reference.html_url,
                    "fix_commit": reference.commit,
                    "language": str(payload.get("language") or "unknown"),
                    "cwe": payload_cwe(payload),
                    "severity": payload_severity(payload),
                    "description": payload_description(payload),
                    "trigger_code": trigger_code,
                    "assembly": payload_assembly(payload),
                    "license": str(payload.get("license") or ""),
                    "url": str(payload.get("url") or commit_url),
                    "needs_enrichment": not bool(trigger_code and payload_assembly(payload).get("comments")),
                }
            )
    return candidates


def build_seed_candidates(paths: Sequence[Path], require_trigger: bool = False) -> list[dict]:
    payloads = []
    for path in paths:
        payloads.extend(payload for payload in iter_json_values(path) if isinstance(payload, dict))
    return build_seed_candidates_from_payloads(payloads, require_trigger=require_trigger)


def write_seed_candidates(candidates: Sequence[dict], output_path: Path) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fp:
        for candidate in candidates:
            fp.write(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(candidates)


def code_xml(file_path: str, content: str) -> str:
    return (
        "<result>\n"
        "    <code>\n"
        "        <path>\n"
        f"            {file_path}\n"
        "        </path>\n"
        "        <content>\n"
        "            <![CDATA[\n"
        f"{content.rstrip()}\n"
        "        ]]>\n"
        "        </content>\n"
        "    </code>\n"
        "</result>"
    )


def build_security_qa_record(record: dict) -> dict:
    vuln = record.get("vuln") or {}
    code = record.get("code") or {}
    repo = record.get("repo") or {}
    trigger = record.get("trigger") or {}
    assembly = record.get("assembly") or {}
    vuln_id = vuln.get("id") or record.get("id") or "unknown"
    severity = vuln.get("severity") or "unknown"
    cwe = ", ".join(vuln.get("cwe") or []) or "未标注"
    description = vuln.get("description") or "该样本来自真实漏洞修复记录。"
    vulnerable_code = code.get("vulnerable") or ""
    fixed_code = code.get("fixed") or ""
    diff = code.get("diff") or ""
    trigger_code = trigger.get("code") or ""
    assembly_comments = assembly.get("comments") or ""
    file_path = code.get("file_path") or "unknown"
    text = f"以下代码文件存在何种风险隐患？```xml\n{code_xml(file_path, vulnerable_code)}\n```"
    response_parts = [
        f"该代码存在真实漏洞风险，漏洞编号为 {vuln_id}，严重性为 {severity}，漏洞类型/风险领域为 {cwe}。",
        description,
    ]
    if trigger_code:
        response_parts.append(
            "漏洞触发代码或触发输入如下：\n"
            f"```text\n{trigger_code.rstrip()}\n```"
        )
    response_parts.append(
        "修复后安全代码如下：\n"
        f"```{(repo.get('language') or '').lower() or 'text'}\n{fixed_code.rstrip()}\n```"
    )
    if diff:
        response_parts.append(
            "漏洞原文代码与修复代码的 Patch Pair 差异如下：\n"
            f"```diff\n{diff.rstrip()}\n```"
        )
    response_parts.append(
        "修复思路：对漏洞触发路径中的不安全逻辑增加边界检查、输入校验、权限/范围限制或安全 API 替换，并确保修复 commit 中的行为与漏洞触发条件不再匹配。"
    )
    response_parts.append(
        "安全规范解释：不要直接信任外部输入；对危险操作执行 allowlist 校验、最小权限限制、错误处理和回归测试；涉及网络、文件、命令、反序列化、内存访问等风险点时，应优先使用安全封装。"
    )
    if assembly_comments:
        response_parts.append(f"汇编代码注释：{assembly_comments}")
    return {
        "id": str(record.get("id") or vuln_id),
        "text": text,
        "response": "\n\n".join(response_parts),
        "label": "漏洞检测",
        "source": str(record.get("source") or "unknown"),
        "meta": {
            "vuln": vuln,
            "repo": repo,
            "trigger_safe_to_run": bool(trigger.get("safe_to_run", False)),
            "source_url": (record.get("meta") or {}).get("url", ""),
            "anonymized": False,
        },
    }


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                yield json.loads(line)


def format_security_qa_jsonl(input_path: Path, output_path: Path) -> int:
    records = [build_security_qa_record(record) for record in iter_jsonl(input_path)]
    return write_jsonl(records, output_path)


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
        env=git_noninteractive_env(),
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    return result.stdout if capture else ""


def try_git(repo_dir: Path, args: Sequence[str]) -> str | None:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        text=True,
        env=git_noninteractive_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        return None
    return result.stdout


def safe_repo_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", value.strip())


def git_noninteractive_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_ASKPASS", "/bin/false")
    env.setdefault("SSH_ASKPASS", "/bin/false")
    return env


def clone_or_fetch_repo(seed: PatchSeed, work_dir: Path) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = work_dir / safe_repo_name(seed.html_url or seed.clone_url)
    if repo_dir.exists():
        run_git(repo_dir, ["fetch", "--all", "--tags", "--prune"], capture=False)
        return repo_dir
    subprocess.run(
        ["git", "clone", "--quiet", seed.clone_url, str(repo_dir)],
        check=True,
        env=git_noninteractive_env(),
    )
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
            fp.write(json.dumps(anonymize_record(record), ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(records)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "build-seeds":
        candidates = build_seed_candidates(
            [Path(value) for value in args.input],
            require_trigger=args.require_trigger,
        )
        written = write_seed_candidates(candidates, Path(args.output))
        print(f"Wrote {written} vulnerability seed candidates to {args.output}", flush=True)
        return 0
    if args.command == "format-qa":
        written = format_security_qa_jsonl(Path(args.input), Path(args.output))
        print(f"Wrote {written} security QA records to {args.output}", flush=True)
        return 0
    if args.command == "fetch-nvd":
        records = fetch_nvd_cves(
            start_date=args.start_date,
            end_date=args.end_date,
            results_per_page=args.results_per_page,
            max_records=args.max_records,
            api_key=args.api_key or os.getenv("NVD_API_KEY"),
            max_retries=args.max_retries,
            retry_sleep_seconds=args.retry_sleep_seconds,
            request_interval_seconds=args.request_interval_seconds,
        )
        written = write_nvd_cves(records, Path(args.output))
        print(f"Wrote {written} NVD CVE records to {args.output}", flush=True)
        return 0
    if args.command == "harvest-nvd-seeds":
        candidates, raw_records = harvest_nvd_seed_candidates(
            start_date=args.start_date,
            end_date=args.end_date,
            target_seeds=args.target_seeds,
            batch_days=args.batch_days,
            records_per_batch=args.records_per_batch,
            api_key=args.api_key or os.getenv("NVD_API_KEY"),
            max_retries=args.max_retries,
            retry_sleep_seconds=args.retry_sleep_seconds,
            request_interval_seconds=args.request_interval_seconds,
        )
        raw_written = write_nvd_cves(raw_records, Path(args.raw_output))
        seed_written = write_seed_candidates(candidates, Path(args.seed_output))
        print(f"Wrote {raw_written} NVD CVE records to {args.raw_output}", flush=True)
        print(f"Wrote {seed_written} vulnerability seed candidates to {args.seed_output}", flush=True)
        return 0
    if not args.seed_jsonl:
        raise SystemExit("error: --seed-jsonl is required unless using the build-seeds subcommand")
    seeds = read_seed_records(Path(args.seed_jsonl), require_trigger=args.require_trigger)
    output_dir = Path(args.output_dir)
    work_dir = Path(args.work_dir)
    records: list[dict] = []
    for seed in seeds:
        if args.max_records is not None and len(records) >= args.max_records:
            break
        try:
            repo_dir = clone_or_fetch_repo(seed, work_dir)
            seed_records = build_patch_records(seed, repo_dir)
        except (OSError, subprocess.CalledProcessError, ValueError) as exc:
            print(f"Skipped {seed.vuln_id} from {seed.clone_url}: {exc}", file=sys.stderr, flush=True)
            continue
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
