#!/usr/bin/env bash
#
# transfer-gate.sh — refuse to package an extraction output directory unless it
# has passed the residual-content audit AND been explicitly approved.
#
# Drop this into whatever step assembles the bundle that leaves the confidential
# environment. It relies only on `audit`'s exit code, so no output parsing:
#
#     0  approved and unchanged since approval  → safe to package
#     1  awaiting approval, or changed since it was approved
#     2  blocking findings (e.g. classification marking) — never packageable
#
# Usage:
#     ./transfer-gate.sh ./extracted [/path/to/bundle.tar.gz]
#
# The bundle deliberately EXCLUDES the files that must stay inside the airgap.

set -euo pipefail

OUT_DIR="${1:?usage: transfer-gate.sh <extracted-dir> [bundle.tar.gz]}"
BUNDLE="${2:-transfer-bundle.tar.gz}"
EXTRACTOR="${EXTRACTOR:-python code_extractor.py}"

# Files that decode the sanitization or are audit metadata — never transfer.
DO_NOT_TRANSFER=(
  "mapping.json"
  "REVERSE_INSTRUCTIONS.txt"
  "TRANSFER_AUDIT.txt"
  "TRANSFER_AUDIT.json"
  ".transfer_approval.json"
  ".code_extractor_reversed.json"
)

echo ">> Running transfer audit on ${OUT_DIR}"
set +e
$EXTRACTOR audit --dir "$OUT_DIR"
status=$?
set -e

case "$status" in
  0) echo ">> Audit passed and output is approved — packaging." ;;
  1) echo "!! Output is not approved (or changed since approval)."
     echo "   Review ${OUT_DIR}/TRANSFER_AUDIT.txt, then:"
     echo "     $EXTRACTOR approve --dir ${OUT_DIR} --hash <hash-from-report>"
     exit 1 ;;
  2) echo "!! BLOCKING findings — this output can never be transferred."
     echo "   Fix the flagged content and re-run \`trace\`."
     exit 2 ;;
  *) echo "!! Unexpected audit exit code: ${status}"; exit "$status" ;;
esac

# Build the exclude list for tar.
exclude_args=()
for f in "${DO_NOT_TRANSFER[@]}"; do
  exclude_args+=(--exclude="$f")
done

echo ">> Writing ${BUNDLE} (excluding: ${DO_NOT_TRANSFER[*]})"
tar "${exclude_args[@]}" -czf "$BUNDLE" -C "$OUT_DIR" .

echo ">> Done. Safe to carry out: ${BUNDLE}"
