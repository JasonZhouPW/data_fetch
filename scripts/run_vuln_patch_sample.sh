#!/usr/bin/env bash
set -euo pipefail

START_DATE="${START_DATE:-2024-01-01}"
END_DATE="${END_DATE:-2024-01-31}"
MAX_RECORDS="${MAX_RECORDS:-10}"
RAW_JSONL="${RAW_JSONL:-nvd_raw.jsonl}"
SEED_JSONL="${SEED_JSONL:-vuln_seeds.jsonl}"
OUTPUT_DIR="${OUTPUT_DIR:-final_data_vuln_patch}"
WORK_DIR="${WORK_DIR:-.cache/vuln_patch_repos}"
QA_JSONL="${QA_JSONL:-$OUTPUT_DIR/vuln_patch_qa.jsonl}"
REQUIRE_TRIGGER="${REQUIRE_TRIGGER:-0}"
BATCH_DAYS="${BATCH_DAYS:-30}"
RECORDS_PER_BATCH="${RECORDS_PER_BATCH:-2000}"
TARGET_SEEDS="${TARGET_SEEDS:-}"
NVD_API_KEY="${NVD_API_KEY:-}"

usage() {
  cat <<'USAGE'
Usage: ./scripts/run_vuln_patch_sample.sh [options]

Options:
  --start-date YYYY-MM-DD       NVD publication start date.
  --end-date YYYY-MM-DD         NVD publication end date.
  --max-records N               Target seed/patch/QA record count.
  --target-seeds N              Number of NVD commit seed candidates to collect.
  --raw-jsonl PATH              Raw NVD JSONL output path.
  --seed-jsonl PATH             Seed candidate JSONL output path.
  --output-dir DIR              Patch-pair output directory.
  --work-dir DIR                Repository clone cache directory.
  --qa-jsonl PATH               Security QA JSONL output path.
  --batch-days N                NVD date window size per request.
  --records-per-batch N         Maximum CVE records per date window.
  --api-key KEY                 Optional NVD API key.
  --require-trigger             Keep only seeds with trigger code.
  -h, --help                    Show this help.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --start-date)
      START_DATE="$2"
      shift 2
      ;;
    --end-date)
      END_DATE="$2"
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
    --raw-jsonl)
      RAW_JSONL="$2"
      shift 2
      ;;
    --seed-jsonl)
      SEED_JSONL="$2"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --work-dir)
      WORK_DIR="$2"
      shift 2
      ;;
    --qa-jsonl)
      QA_JSONL="$2"
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
    --api-key)
      NVD_API_KEY="$2"
      shift 2
      ;;
    --require-trigger)
      REQUIRE_TRIGGER="1"
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

if [[ -z "$TARGET_SEEDS" ]]; then
  TARGET_SEEDS=$((MAX_RECORDS * 5))
fi

harvest_args=(
  harvest-nvd-seeds
  --start-date "$START_DATE" \
  --end-date "$END_DATE" \
  --raw-output "$RAW_JSONL" \
  --seed-output "$SEED_JSONL" \
  --target-seeds "$TARGET_SEEDS" \
  --batch-days "$BATCH_DAYS" \
  --records-per-batch "$RECORDS_PER_BATCH"
)

if [[ -n "$NVD_API_KEY" ]]; then
  harvest_args+=(--api-key "$NVD_API_KEY")
fi

python3 -m vuln_patch_harvester "${harvest_args[@]}"

patch_args=(
  --seed-jsonl "$SEED_JSONL"
  --output-dir "$OUTPUT_DIR"
  --work-dir "$WORK_DIR"
  --max-records "$MAX_RECORDS"
)

if [[ "$REQUIRE_TRIGGER" == "1" ]]; then
  patch_args+=(--require-trigger)
fi

python3 -m vuln_patch_harvester "${patch_args[@]}"

python3 -m vuln_patch_harvester format-qa \
  --input "$OUTPUT_DIR/vuln_patch_pairs.jsonl" \
  --output "$QA_JSONL"

printf '\nCounts:\n'
wc -l "$RAW_JSONL" "$SEED_JSONL" "$OUTPUT_DIR/vuln_patch_pairs.jsonl" "$QA_JSONL"
