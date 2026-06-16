#!/usr/bin/env bash
set -euo pipefail

START_YEAR="${START_YEAR:-2010}"
END_YEAR="${END_YEAR:-2025}"
MAX_RECORDS="${MAX_RECORDS:-1000000}"
TARGET_SEEDS="${TARGET_SEEDS:-100000}"
BATCH_DAYS="${BATCH_DAYS:-30}"
RECORDS_PER_BATCH="${RECORDS_PER_BATCH:-2000}"
OUTPUT_ROOT="${OUTPUT_ROOT:-final_data_vuln_patch_by_year}"
WORK_ROOT="${WORK_ROOT:-.cache/vuln_patch_repos_by_year}"
PROGRESS_INTERVAL="${PROGRESS_INTERVAL:-10}"
MERGE_OUTPUT="${MERGE_OUTPUT:-1}"
NVD_API_KEY="${NVD_API_KEY:-}"
REQUIRE_TRIGGER="${REQUIRE_TRIGGER:-0}"

usage() {
  cat <<'USAGE'
Usage: ./scripts/run_vuln_patch_yearly.sh [options]

Options:
  --start-year YYYY             First NVD publication year to process.
  --end-year YYYY               Last NVD publication year to process.
  --max-records N               Maximum final patch/QA records per year.
  --target-seeds N              Number of NVD commit seed candidates per year.
  --batch-days N                NVD date window size per request.
  --records-per-batch N         Maximum CVE records per date window.
  --output-root DIR             Root directory for yearly outputs.
  --work-root DIR               Root directory for temporary repository clones.
  --progress-interval SECONDS   Print progress every N seconds. Use 0 to disable.
  --api-key KEY                 Optional NVD API key.
  --require-trigger             Keep only seeds with trigger code.
  --no-merge                    Do not merge yearly QA files into one JSONL.
  -h, --help                    Show this help.

Outputs:
  <output-root>/<year>/nvd_raw_<year>.jsonl
  <output-root>/<year>/vuln_seeds_<year>.jsonl
  <output-root>/<year>/vuln_patch_pairs.jsonl
  <output-root>/<year>/vuln_patch_qa_<year>.jsonl
  <output-root>/<year>/run_<year>.log
  <output-root>/vuln_patch_qa_all.jsonl, unless --no-merge is set.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --start-year)
      START_YEAR="$2"
      shift 2
      ;;
    --end-year)
      END_YEAR="$2"
      shift 2
      ;;
    --max-records)
      MAX_RECORDS="$2"
      shift 2
      ;;
    --target-seeds)
      TARGET_SEEDS="$2"
      shift 2
      ;;
    --batch-days)
      BATCH_DAYS="$2"
      shift 2
      ;;
    --records-per-batch)
      RECORDS_PER_BATCH="$2"
      shift 2
      ;;
    --output-root)
      OUTPUT_ROOT="$2"
      shift 2
      ;;
    --work-root)
      WORK_ROOT="$2"
      shift 2
      ;;
    --progress-interval)
      PROGRESS_INTERVAL="$2"
      shift 2
      ;;
    --api-key)
      NVD_API_KEY="$2"
      shift 2
      ;;
    --require-trigger)
      REQUIRE_TRIGGER="1"
      shift
      ;;
    --no-merge)
      MERGE_OUTPUT="0"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if (( START_YEAR > END_YEAR )); then
  echo "error: --start-year must be <= --end-year" >&2
  exit 2
fi

mkdir -p "$OUTPUT_ROOT" "$WORK_ROOT"

for year in $(seq "$START_YEAR" "$END_YEAR"); do
  year_dir="$OUTPUT_ROOT/$year"
  mkdir -p "$year_dir"

  args=(
    --start-date "$year-01-01"
    --end-date "$year-12-31"
    --max-records "$MAX_RECORDS"
    --target-seeds "$TARGET_SEEDS"
    --batch-days "$BATCH_DAYS"
    --records-per-batch "$RECORDS_PER_BATCH"
    --raw-jsonl "$year_dir/nvd_raw_$year.jsonl"
    --seed-jsonl "$year_dir/vuln_seeds_$year.jsonl"
    --output-dir "$year_dir"
    --work-dir "$WORK_ROOT/$year"
    --qa-jsonl "$year_dir/vuln_patch_qa_$year.jsonl"
    --progress-interval "$PROGRESS_INTERVAL"
  )

  if [[ -n "$NVD_API_KEY" ]]; then
    args+=(--api-key "$NVD_API_KEY")
  fi
  if [[ "$REQUIRE_TRIGGER" == "1" ]]; then
    args+=(--require-trigger)
  fi

  printf '\n===== Processing %s =====\n' "$year"
  ./scripts/run_vuln_patch_sample.sh "${args[@]}" 2>&1 | tee "$year_dir/run_$year.log"
done

if [[ "$MERGE_OUTPUT" == "1" ]]; then
  merged="$OUTPUT_ROOT/vuln_patch_qa_all.jsonl"
  : > "$merged"
  for year in $(seq "$START_YEAR" "$END_YEAR"); do
    qa_path="$OUTPUT_ROOT/$year/vuln_patch_qa_$year.jsonl"
    if [[ -f "$qa_path" ]]; then
      cat "$qa_path" >> "$merged"
    fi
  done
  printf '\nMerged QA output: %s\n' "$merged"
  wc -l "$merged"
fi

printf '\nYearly QA counts:\n'
find "$OUTPUT_ROOT" -mindepth 2 -maxdepth 2 -name 'vuln_patch_qa_*.jsonl' -print0 | sort -z | xargs -0 wc -l
