#!/usr/bin/env bash
set -euo pipefail

CURRENT_YEAR="$(date '+%Y')"
TODAY="$(date '+%Y-%m-%d')"
START_YEAR="${START_YEAR:-1999}"
END_YEAR="${END_YEAR:-$CURRENT_YEAR}"
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
RESUME="${RESUME:-1}"
FORCE="${FORCE:-0}"
NVD_MAX_RETRIES="${NVD_MAX_RETRIES:-3}"
NVD_RETRY_SLEEP_SECONDS="${NVD_RETRY_SLEEP_SECONDS:-10}"

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
  --nvd-max-retries N           Maximum retries for NVD rate-limit/server errors.
  --nvd-retry-sleep-seconds N   Sleep seconds before retrying NVD requests.
  --require-trigger             Keep only seeds with trigger code.
  --force                       Reprocess years even when .done exists.
  --no-resume                   Ignore .done markers and process every year.
  --no-merge                    Do not merge yearly QA files into one JSONL.
  -h, --help                    Show this help.

Outputs:
  <output-root>/<year>/nvd_raw_<year>.jsonl
  <output-root>/<year>/vuln_seeds_<year>.jsonl
  <output-root>/<year>/vuln_patch_pairs.jsonl
  <output-root>/<year>/vuln_patch_qa_<year>.jsonl
  <output-root>/<year>/run_<year>.log
  <output-root>/<year>/.done after a year completes successfully.
  <output-root>/<year>/.failed if a year fails.
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
    --nvd-max-retries)
      NVD_MAX_RETRIES="$2"
      shift 2
      ;;
    --nvd-retry-sleep-seconds)
      NVD_RETRY_SLEEP_SECONDS="$2"
      shift 2
      ;;
    --require-trigger)
      REQUIRE_TRIGGER="1"
      shift
      ;;
    --force)
      FORCE="1"
      shift
      ;;
    --no-resume)
      RESUME="0"
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
failures=()

line_count() {
  local path="$1"
  if [[ -f "$path" ]]; then
    wc -l < "$path" | tr -d ' '
  else
    printf '0'
  fi
}

for year in $(seq "$START_YEAR" "$END_YEAR"); do
  year_dir="$OUTPUT_ROOT/$year"
  mkdir -p "$year_dir"
  done_marker="$year_dir/.done"
  failed_marker="$year_dir/.failed"
  started_marker="$year_dir/.started"
  qa_path="$year_dir/vuln_patch_qa_$year.jsonl"
  year_end_date="$year-12-31"

  if [[ "$year" == "$CURRENT_YEAR" ]]; then
    year_end_date="$TODAY"
  fi

  if [[ "$RESUME" == "1" && "$FORCE" != "1" && -f "$done_marker" && -f "$qa_path" ]]; then
    printf '\n===== Skipping %s; already completed =====\n' "$year"
    wc -l "$qa_path" || true
    continue
  fi

  args=(
    --start-date "$year-01-01"
    --end-date "$year_end_date"
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
    --nvd-max-retries "$NVD_MAX_RETRIES"
    --nvd-retry-sleep-seconds "$NVD_RETRY_SLEEP_SECONDS"
  )

  if [[ -n "$NVD_API_KEY" ]]; then
    args+=(--api-key "$NVD_API_KEY")
  fi
  if [[ "$REQUIRE_TRIGGER" == "1" ]]; then
    args+=(--require-trigger)
  fi

  printf '\n===== Processing %s =====\n' "$year"
  rm -f "$done_marker" "$failed_marker"
  printf 'started_at=%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" > "$started_marker"
  printf 'year=%s\nstart_date=%s\nend_date=%s\n' "$year" "$year-01-01" "$year_end_date" >> "$started_marker"

  set +e
  ./scripts/run_vuln_patch_sample.sh "${args[@]}" 2>&1 | tee "$year_dir/run_$year.log"
  status=${PIPESTATUS[0]}
  set -e

  if [[ "$status" == "0" ]]; then
    {
      printf 'year=%s\n' "$year"
      printf 'start_date=%s\n' "$year-01-01"
      printf 'end_date=%s\n' "$year_end_date"
      printf 'completed_at=%s\n' "$(date '+%Y-%m-%d %H:%M:%S')"
      printf 'qa_records=%s\n' "$(line_count "$qa_path")"
    } > "$done_marker"
    rm -f "$failed_marker" "$started_marker"
    printf '===== Completed %s =====\n' "$year"
  else
    {
      printf 'year=%s\n' "$year"
      printf 'start_date=%s\n' "$year-01-01"
      printf 'end_date=%s\n' "$year_end_date"
      printf 'failed_at=%s\n' "$(date '+%Y-%m-%d %H:%M:%S')"
      printf 'exit_status=%s\n' "$status"
    } > "$failed_marker"
    failures+=("$year")
    printf '===== Failed %s with status %s; continuing =====\n' "$year" "$status" >&2
  fi
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
qa_files="$(find "$OUTPUT_ROOT" -mindepth 2 -maxdepth 2 -name 'vuln_patch_qa_*.jsonl' -print | sort)"
if [[ -n "$qa_files" ]]; then
  while IFS= read -r qa_file; do
    wc -l "$qa_file"
  done <<< "$qa_files"
else
  printf '0 yearly QA files found\n'
fi

if (( ${#failures[@]} > 0 )); then
  printf '\nFailed years: %s\n' "${failures[*]}" >&2
  printf 'Fix the issue and rerun the same command; completed years with .done will be skipped.\n' >&2
  exit 1
fi
