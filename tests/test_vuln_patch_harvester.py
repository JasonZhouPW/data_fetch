import json
import subprocess
from pathlib import Path

from vuln_patch_harvester.harvester import (
    PatchSeed,
    build_patch_records,
    parse_commit_reference,
    read_seed_records,
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
