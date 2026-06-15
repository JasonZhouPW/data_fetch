#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="${1:-/data}"

paths=(
  "data_fetch5/data_fetch/final_data_github"
  "data_fetch2/data_fetch/final_data_github"
  "data_fetch3/data_fetch/final_data_github"
  "data_fetch4/data_fetch/final_data_gitlab"
  "data_fetch/data_fetch/final_data_github"
)

printf 'Root: %s\n\n' "$ROOT_DIR"

for rel_path in "${paths[@]}"; do
  abs_path="${ROOT_DIR%/}/$rel_path"
  if [[ -e "$abs_path" ]]; then
    du -sh "$abs_path"
  else
    printf 'MISSING\t%s\n' "$abs_path"
  fi
done
