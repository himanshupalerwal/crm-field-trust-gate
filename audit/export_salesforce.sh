#!/usr/bin/env bash
# Export the five CSVs the audit needs from a Salesforce org, with the Salesforce CLI.
# Read-only: it runs five SOQL queries and writes CSV files on your machine.
#
#   audit/export_salesforce.sh <org-alias> [output-dir]      (output-dir defaults to data/)
#
# Use a sandbox and a read-only user, and get permission first (see audit/README.md).
# Change the field API names below to match your org. Leave ARR_SYNC_FIELD empty if you
# have no sync timestamp yet. The CLI caps a query at 10,000 records; if any export hits the
# cap, this script stops, and you use `sf data export bulk` instead (audit/README.md).
set -euo pipefail

ORG="${1:?usage: audit/export_salesforce.sh <org-alias> [output-dir]}"
OUT="${2:-data}"

ARR_FIELD="ARR__c"
ARR_SYNC_FIELD="ARR_Synced_At__c"
RENEWAL_FIELD="Renewal_Date__c"
RENEWAL_TYPE="Renewal"

FILES=(accounts.csv opportunities.csv account_history.csv opportunity_history.csv users.csv)
QUERIES=(
  "SELECT Id, Website, $ARR_FIELD, ${ARR_SYNC_FIELD:+$ARR_SYNC_FIELD, }LastModifiedDate FROM Account"
  "SELECT Id, AccountId, Type, $RENEWAL_FIELD, LastModifiedDate FROM Opportunity WHERE Type = '$RENEWAL_TYPE'"
  "SELECT AccountId, Field, CreatedById, CreatedDate FROM AccountHistory WHERE Field IN ('$ARR_FIELD')"
  "SELECT OpportunityId, Field, CreatedById, CreatedDate FROM OpportunityFieldHistory WHERE Field IN ('$RENEWAL_FIELD')"
  "SELECT Id, Username, Profile.Name FROM User"
)

mkdir -p "$OUT"
cleanup() { for f in "${FILES[@]}"; do rm -f "$OUT/.$f.tmp" "$OUT/.$f.err"; done; }
trap cleanup EXIT

# Run every query into a temp file first, so a failed or cut-short export never replaces
# a good one, and the five files always come from the same run.
for i in "${!FILES[@]}"; do
  f="${FILES[$i]}"
  if ! sf data query --target-org "$ORG" --result-format csv --query "${QUERIES[$i]}" \
       > "$OUT/.$f.tmp" 2> "$OUT/.$f.err"; then
    cat "$OUT/.$f.err" >&2
    echo "query for $f failed; nothing written" >&2
    exit 1
  fi
  if grep -q "query result is missing" "$OUT/.$f.err"; then
    cat "$OUT/.$f.err" >&2
    echo "$f hit the CLI's record cap; use sf data export bulk (see audit/README.md); nothing written" >&2
    exit 1
  fi
done
for f in "${FILES[@]}"; do
  mv "$OUT/.$f.tmp" "$OUT/$f"
  echo "wrote $OUT/$f ($(( $(wc -l < "$OUT/$f") - 1 )) rows)"
done
