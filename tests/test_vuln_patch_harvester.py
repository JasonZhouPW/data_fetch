import json
import http.client
import subprocess
import urllib.error
import urllib.parse
from pathlib import Path

from vuln_patch_harvester.harvester import (
    PatchSeed,
    build_patch_records,
    build_security_qa_record,
    build_seed_candidates,
    extract_commit_urls,
    fetch_nvd_cves,
    harvest_nvd_seed_candidates,
    main,
    parse_commit_reference,
    read_seed_records,
    write_jsonl,
    write_nvd_cves,
    write_seed_candidates,
)


def git(repo_dir: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def commit_all(repo_dir: Path, message: str) -> str:
    git(repo_dir, "add", ".")
    git(repo_dir, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", message)
    return git(repo_dir, "rev-parse", "HEAD")


def test_parse_commit_reference_supports_github_gitlab_and_gitee():
    github = parse_commit_reference("https://github.com/owner/project/commit/abc123")
    gitlab = parse_commit_reference("https://gitlab.com/group/project/-/commit/def456")
    gitee = parse_commit_reference("https://gitee.com/owner/project/commit/789abc")

    assert github.clone_url == "https://github.com/owner/project.git"
    assert github.html_url == "https://github.com/owner/project"
    assert github.commit == "abc123"
    assert gitlab.clone_url == "https://gitlab.com/group/project.git"
    assert gitlab.commit == "def456"
    assert gitee.clone_url == "https://gitee.com/owner/project.git"
    assert gitee.commit == "789abc"


def test_read_seed_records_accepts_commit_url_and_required_trigger(tmp_path: Path):
    seed_path = tmp_path / "seeds.jsonl"
    seed_path.write_text(
        json.dumps(
            {
                "id": "CVE-2099-0001",
                "source": "NVD",
                "commit_url": "https://github.com/owner/project/commit/abc123",
                "trigger_code": "crash();",
                "assembly": {"arch": "x86_64", "disassembly": "call crash", "comments": "calls vulnerable path"},
            }
        )
        + "\n"
        + json.dumps(
            {
                "id": "CVE-2099-0002",
                "commit_url": "https://github.com/owner/project/commit/def456",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    seeds = read_seed_records(seed_path, require_trigger=True)

    assert len(seeds) == 1
    assert seeds[0].vuln_id == "CVE-2099-0001"
    assert seeds[0].trigger_code == "crash();"
    assert seeds[0].assembly["comments"] == "calls vulnerable path"


def test_build_patch_records_extracts_vulnerable_and_fixed_code(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    git(repo_dir, "init")
    source_path = repo_dir / "src" / "vuln.c"
    source_path.parent.mkdir()
    source_path.write_text("int copy(char *dst, char *src) { return sprintf(dst, src); }\n", encoding="utf-8")
    commit_all(repo_dir, "add vulnerable code")
    source_path.write_text("int copy(char *dst, char *src) { return snprintf(dst, 64, \"%s\", src); }\n", encoding="utf-8")
    fix_commit = commit_all(repo_dir, "fix unsafe copy")

    seed = PatchSeed(
        vuln_id="CVE-2099-0003",
        source="NVD",
        repo_url=str(repo_dir),
        clone_url=str(repo_dir),
        html_url=str(repo_dir),
        fix_commit=fix_commit,
        trigger_code="copy(buf, payload);",
        assembly={"arch": "x86_64", "disassembly": "call sprintf", "comments": "unsafe call before patch"},
    )

    records = build_patch_records(seed, repo_dir)

    assert len(records) == 1
    record = records[0]
    assert record["vuln"]["id"] == "CVE-2099-0003"
    assert record["code"]["file_path"] == "src/vuln.c"
    assert "sprintf" in record["code"]["vulnerable"]
    assert "snprintf" in record["code"]["fixed"]
    assert "-int copy" in record["code"]["diff"]
    assert record["trigger"]["code"] == "copy(buf, payload);"
    assert record["assembly"]["comments"] == "unsafe call before patch"


def test_extract_commit_urls_from_osv_and_nvd_like_payloads():
    payload = {
        "id": "CVE-2099-0004",
        "references": [
            {"url": "https://github.com/owner/project/commit/abc123"},
            {"url": "https://example.com/advisory"},
        ],
        "cve": {
            "references": {
                "referenceData": [
                    {"url": "https://gitlab.com/group/project/-/commit/def456"},
                    {"url": "https://gitee.com/owner/project/commit/789abc"},
                ]
            }
        },
    }

    assert extract_commit_urls(payload) == [
        "https://github.com/owner/project/commit/abc123",
        "https://gitlab.com/group/project/-/commit/def456",
        "https://gitee.com/owner/project/commit/789abc",
    ]


def test_build_seed_candidates_writes_enrichment_candidates(tmp_path: Path):
    input_path = tmp_path / "osv.json"
    output_path = tmp_path / "vuln_seeds.jsonl"
    input_path.write_text(
        json.dumps(
            {
                "id": "CVE-2099-0005",
                "summary": "Unsafe parser.",
                "database_specific": {"severity": "HIGH"},
                "references": [{"url": "https://github.com/owner/project/commit/abc123"}],
            }
        ),
        encoding="utf-8",
    )

    candidates = build_seed_candidates([input_path])
    written = write_seed_candidates(candidates, output_path)

    assert written == 1
    row = json.loads(output_path.read_text(encoding="utf-8"))
    assert row["id"] == "CVE-2099-0005"
    assert row["commit_url"] == "https://github.com/owner/project/commit/abc123"
    assert row["fix_commit"] == "abc123"
    assert row["needs_enrichment"] is True
    assert row["trigger_code"] == ""
    assert row["assembly"]["comments"] == ""


def test_build_security_qa_record_matches_prompt_response_shape():
    patch_record = {
        "id": "CVE-2099-0006:repo:commit:main.go",
        "source": "NVD",
        "vuln": {
            "id": "CVE-2099-0006",
            "cwe": ["CWE-918"],
            "severity": "HIGH",
            "description": "Server-side request forgery in URL preview endpoint.",
        },
        "repo": {
            "url": "https://example.com/repo",
            "language": "Go",
            "vulnerable_commit": "old",
            "fixed_commit": "new",
        },
        "code": {
            "file_path": "main.go",
            "vulnerable": "http.Get(req.URL)\n",
            "fixed": "if !isSafeURL(req.URL) { return err }\n",
            "diff": "-http.Get(req.URL)\n+isSafeURL(req.URL)\n",
        },
        "trigger": {"code": "http://127.0.0.1:8080/admin", "safe_to_run": False},
        "assembly": {"comments": "network call is reachable from user-controlled input"},
        "meta": {"url": "https://nvd.nist.gov/vuln/detail/CVE-2099-0006"},
    }

    qa = build_security_qa_record(patch_record)

    assert qa["id"] == "CVE-2099-0006:repo:commit:main.go"
    assert qa["label"] == "漏洞检测"
    assert qa["source"] == "NVD"
    assert "以下代码文件存在何种风险隐患" in qa["text"]
    assert "<path>\n            main.go\n        </path>" in qa["text"]
    assert "http.Get(req.URL)" in qa["text"]
    assert "CVE-2099-0006" in qa["response"]
    assert "修复后安全代码" in qa["response"]
    assert "isSafeURL" in qa["response"]


def test_write_jsonl_anonymizes_unique_identifiers(tmp_path: Path):
    output = tmp_path / "records.jsonl"
    records = [
        {
            "id": "record-1",
            "text": "email alice@example.com phone 13800138000",
            "meta": {"url": "https://example.com/source", "author": "alice@example.com"},
        }
    ]

    assert write_jsonl(records, output) == 1

    row = json.loads(output.read_text(encoding="utf-8"))
    assert row["meta"]["url"] == "https://example.com/source"
    assert "alice@example.com" not in row["text"]
    assert "13800138000" not in row["text"]
    assert row["meta"]["author"] == "xxxxxxxxxxxxxxxxx"


def test_fetch_nvd_cves_paginates_api(monkeypatch):
    calls: list[str] = []

    class FakeResponse:
        def __init__(self, payload: dict):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self) -> bytes:
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(request, timeout: int):
        calls.append(request.full_url)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        start_index = int(query["startIndex"][0])
        if start_index == 0:
            return FakeResponse(
                {
                    "totalResults": 2,
                    "resultsPerPage": 1,
                    "vulnerabilities": [{"cve": {"id": "CVE-2099-0007"}}],
                }
            )
        return FakeResponse(
            {
                "totalResults": 2,
                "resultsPerPage": 1,
                "vulnerabilities": [{"cve": {"id": "CVE-2099-0008"}}],
            }
        )

    monkeypatch.setattr("vuln_patch_harvester.harvester.urllib.request.urlopen", fake_urlopen)

    records = fetch_nvd_cves(
        start_date="2026-01-01",
        end_date="2026-01-02",
        results_per_page=1,
        max_records=2,
        api_key="secret",
        request_interval_seconds=0,
    )

    assert [record["cve"]["id"] for record in records] == ["CVE-2099-0007", "CVE-2099-0008"]
    assert len(calls) == 2
    first_query = urllib.parse.parse_qs(urllib.parse.urlparse(calls[0]).query)
    assert first_query["pubStartDate"] == ["2026-01-01T00:00:00.000Z"]
    assert first_query["pubEndDate"] == ["2026-01-02T00:00:00.000Z"]
    assert first_query["resultsPerPage"] == ["1"]
    assert first_query["startIndex"] == ["0"]


def test_fetch_nvd_cves_retries_rate_limit(monkeypatch):
    calls = 0
    sleeps: list[float] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "totalResults": 1,
                    "resultsPerPage": 1,
                    "vulnerabilities": [{"cve": {"id": "CVE-2099-0099"}}],
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout: int):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, None)
        return FakeResponse()

    monkeypatch.setattr("vuln_patch_harvester.harvester.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("vuln_patch_harvester.harvester.time.sleep", sleeps.append)

    records = fetch_nvd_cves(
        start_date="2026-01-01",
        end_date="2026-01-02",
        results_per_page=1,
        max_records=1,
        retry_sleep_seconds=1,
        request_interval_seconds=0,
    )

    assert [record["cve"]["id"] for record in records] == ["CVE-2099-0099"]
    assert calls == 2
    assert sleeps == [1]


def test_fetch_nvd_cves_retries_incomplete_read(monkeypatch):
    calls = 0
    sleeps: list[float] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "totalResults": 1,
                    "resultsPerPage": 1,
                    "vulnerabilities": [{"cve": {"id": "CVE-2099-0101"}}],
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout: int):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise http.client.IncompleteRead(b"{", 10)
        return FakeResponse()

    monkeypatch.setattr("vuln_patch_harvester.harvester.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("vuln_patch_harvester.harvester.time.sleep", sleeps.append)

    records = fetch_nvd_cves(
        start_date="2026-01-01",
        end_date="2026-01-02",
        results_per_page=1,
        max_records=1,
        retry_sleep_seconds=1,
        request_interval_seconds=0,
    )

    assert [record["cve"]["id"] for record in records] == ["CVE-2099-0101"]
    assert calls == 2
    assert sleeps == [1]


def test_fetch_nvd_cves_retries_timeout(monkeypatch):
    calls = 0
    sleeps: list[float] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "totalResults": 1,
                    "resultsPerPage": 1,
                    "vulnerabilities": [{"cve": {"id": "CVE-2099-0102"}}],
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout: int):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("The read operation timed out")
        return FakeResponse()

    monkeypatch.setattr("vuln_patch_harvester.harvester.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("vuln_patch_harvester.harvester.time.sleep", sleeps.append)

    records = fetch_nvd_cves(
        start_date="2026-01-01",
        end_date="2026-01-02",
        results_per_page=1,
        max_records=1,
        retry_sleep_seconds=1,
        request_interval_seconds=0,
    )

    assert [record["cve"]["id"] for record in records] == ["CVE-2099-0102"]
    assert calls == 2
    assert sleeps == [1]


def test_fetch_nvd_cves_throttles_without_api_key(monkeypatch):
    sleeps: list[float] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "totalResults": 1,
                    "resultsPerPage": 1,
                    "vulnerabilities": [{"cve": {"id": "CVE-2099-0100"}}],
                }
            ).encode("utf-8")

    monkeypatch.setattr("vuln_patch_harvester.harvester.urllib.request.urlopen", lambda request, timeout: FakeResponse())
    monkeypatch.setattr("vuln_patch_harvester.harvester.time.sleep", sleeps.append)

    fetch_nvd_cves(
        start_date="2026-01-01",
        end_date="2026-01-02",
        results_per_page=1,
        max_records=1,
    )

    assert sleeps == [7.0]


def test_write_nvd_cves_outputs_jsonl(tmp_path: Path):
    output_path = tmp_path / "nvd_raw.jsonl"
    records = [{"cve": {"id": "CVE-2099-0009"}}, {"cve": {"id": "CVE-2099-0010"}}]

    written = write_nvd_cves(records, output_path)

    assert written == 2
    assert [json.loads(line)["cve"]["id"] for line in output_path.read_text(encoding="utf-8").splitlines()] == [
        "CVE-2099-0009",
        "CVE-2099-0010",
    ]


def test_harvest_nvd_seed_candidates_fetches_until_target(monkeypatch):
    calls: list[tuple[str, str, int | None]] = []

    def fake_fetch_nvd_cves(
        start_date,
        end_date,
        results_per_page=2000,
        max_records=None,
        api_key=None,
        max_retries=3,
        retry_sleep_seconds=10.0,
        request_interval_seconds=None,
    ):
        calls.append((start_date, end_date, max_records))
        if len(calls) == 1:
            return [{"cve": {"id": "CVE-2099-0011", "references": {"referenceData": []}}}]
        return [
            {
                "cve": {
                    "id": "CVE-2099-0012",
                    "references": {
                        "referenceData": [
                            {"url": "https://github.com/owner/project/commit/abc123"},
                            {"url": "https://github.com/owner/project/commit/def456"},
                        ]
                    },
                }
            }
        ]

    monkeypatch.setattr("vuln_patch_harvester.harvester.fetch_nvd_cves", fake_fetch_nvd_cves)

    candidates, raw_records = harvest_nvd_seed_candidates(
        start_date="2023-01-01",
        end_date="2023-01-03",
        target_seeds=2,
        batch_days=1,
        records_per_batch=10,
    )

    assert len(candidates) == 2
    assert len(raw_records) == 2
    assert calls == [
        ("2023-01-01", "2023-01-02", 10),
        ("2023-01-02", "2023-01-03", 10),
    ]


def test_main_skips_failed_seed_and_continues(tmp_path: Path, monkeypatch):
    seed_path = tmp_path / "seeds.jsonl"
    output_dir = tmp_path / "out"
    work_dir = tmp_path / "repos"
    seed_path.write_text(
        json.dumps({"id": "CVE-2099-0013", "clone_url": "https://example.com/bad.git", "fix_commit": "bad"})
        + "\n"
        + json.dumps({"id": "CVE-2099-0014", "clone_url": "https://example.com/good.git", "fix_commit": "good"})
        + "\n",
        encoding="utf-8",
    )

    def fake_clone_or_fetch_repo(seed, work_dir):
        if seed.vuln_id == "CVE-2099-0013":
            raise subprocess.CalledProcessError(128, ["git", "clone"])
        return tmp_path

    def fake_build_patch_records(seed, repo_dir):
        return [{"id": seed.vuln_id, "source": seed.source}]

    monkeypatch.setattr("vuln_patch_harvester.harvester.clone_or_fetch_repo", fake_clone_or_fetch_repo)
    monkeypatch.setattr("vuln_patch_harvester.harvester.build_patch_records", fake_build_patch_records)

    assert main(["--seed-jsonl", str(seed_path), "--output-dir", str(output_dir), "--work-dir", str(work_dir)]) == 0

    rows = [json.loads(line) for line in (output_dir / "vuln_patch_pairs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows == [{"id": "CVE-2099-0014", "source": "curated"}]
