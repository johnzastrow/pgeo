#!/usr/bin/env bash
# Build a report from saved results.
#
#   scripts/build_report.sh                    the study: docs/REPORT.md and .pdf
#   scripts/build_report.sh --doc optimization the companion: docs/OPTIMIZATION.md and .pdf
#                                              (what changed after the study - generalisation to
#                                              any US state, the build optimisation, the defects
#                                              a second region found)
#   scripts/build_report.sh --refresh          first refresh the live inputs: snapshot of both
#                                              engines (counts, sizes, versions) and the
#                                              compatibility contract test
#   scripts/build_report.sh --refresh-accuracy also re-run the accuracy and fuzz sets against
#                                              Pelias and pgeo (~15 min)
#   scripts/build_report.sh --no-pdf           skip the PDF
#   scripts/build_report.sh --no-docx          skip the Word document
#
# Inputs are listed in report/inputs.toml (load-test run ids, accuracy files). Load tests are
# separate, multi-hour runs (tests/load/run_matrix*.py); point inputs.toml at new run ids.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

refresh=0; refresh_acc=0; args=()
for a in "$@"; do
  case "$a" in
    --refresh) refresh=1 ;;
    --refresh-accuracy) refresh=1; refresh_acc=1 ;;
    --no-pdf) args+=(--no-pdf) ;;
    --no-docx) args+=(--no-docx) ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done

if [[ $refresh_acc -eq 1 ]]; then
  echo "== accuracy and fuzz sets (Pelias, pgeo pure SQL, pgeo FastAPI)"
  acc() { uv run --project pgeo python tests/accuracy/run_accuracy.py "$@" --concurrency 2; }
  acc --engine pelias --base http://127.0.0.1:4000 --label default
  acc --engine pelias --base http://127.0.0.1:4000 --label fuzz --cases tests/accuracy/fuzz_cases.json
  acc --engine pgeo --base http://127.0.0.1:4700 --label sql-rest
  acc --engine pgeo --base http://127.0.0.1:4700 --label fuzz-sql-rest --cases tests/accuracy/fuzz_cases.json
  acc --engine pgeo --base http://127.0.0.1:4500 --label api-rule --param pgeo.parse=none
  acc --engine pgeo --base http://127.0.0.1:4500 --label api-service --param pgeo.parse=service
fi
if [[ $refresh -eq 1 ]]; then
  echo "== live snapshot and compatibility contract"
  uv run --project report python report/collect.py
  uv run --project pgeo python tests/compat/compat_test.py --json report/data/compat.json || true
fi
echo "== report"
uv run --project report python report/build_report.py "${args[@]}"
