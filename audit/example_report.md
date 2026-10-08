*Example output on the fake sample org, from `python audit/make_sample_data.py` and `python audit/crm_audit.py --config audit/sample_config.yaml --out reports/sample`.*

# CRM field trust audit

Generated 2026-10-07T12:00:00+00:00. Aggregates only; groups smaller than 20 are suppressed.

## Shadow-mode gate: update_renewal_quote

1334 actions evaluated. Inputs action-grade by contract: 2 of 2.

| Decision | Share |
| --- | --- |
| proceed | 52.1% |
| draft | 0.0% |
| ask | 47.9% |

| Failed check (any input) | Share of actions |
| --- | --- |
| freshness | 34.3% |
| authority | 13.2% |
| writer | 13.2% |
| identity | 8.5% |
| blank value | 2.5% |

| Field | Failed checks (share of actions) |
| --- | --- |
| Account.ARR | authority 8.7%, identity 8.5%, writer 8.7%, freshness 12.2% |
| Opportunity.Renewal_Date | freshness 24.6%, authority 5.2%, writer 5.2%, blank value 2.5% |

## Field profiles

| Field | Tier | Blank | Over SLA | Median age (days) | Freshness measured from | Last writer not allowed | Last written by agent | Duplicates | No field history |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Account.ARR | action | 0.0% | 12.0% | 0.7 | sync timestamp (100.0%) | 5.0% | 2.1% | 7.7% | 3.8% |
| Account.Health_Score | advisory | 0.0% | 39.9% | 5.9 | last change in field history (96.2%) | 0.0% | 0.0% | 7.7% | 3.8% |
| Opportunity.Renewal_Date | action | 2.6% | 23.8% | 0.8 | sync timestamp (80.7%) | 5.4% | 0.0% | 0.0% | 0.0% |

Writer mix (last writer, among records with history):

- Account.ARR: billing_sync 95.0%, sales_rep 2.9%, agent:renewal 2.1%
- Account.Health_Score: health_model 100.0%
- Opportunity.Renewal_Date: contract_sync 94.6%, sales_rep 5.4%

## Mismatch against the source of truth

| Field | Compared | Coverage | Mismatch | Mismatch when over SLA | Mismatch within SLA |
| --- | --- | --- | --- | --- | --- |
| Account.ARR | 1500 | 96.2% | 7.5% | 41.7% | 2.8% |

## Reversal rate (a person changed it within 30 days)

| Field | Automated writer | Writes | Reversed | Changed by an unmapped user |
| --- | --- | --- | --- | --- |
| Account.ARR | agent:renewal | 38 | 15.8% | 0.0% |
| Account.ARR | billing_sync | 1500 | 0.0% | 0.0% |
| Account.Health_Score | health_model | 1500 | 0.0% | 0.0% |
| Opportunity.Renewal_Date | contract_sync | 2082 | 0.0% | 0.0% |

## Caveats

- Account.Health_Score: for 96.2% of records, freshness comes from the last change, not a sync timestamp, so values that were re-verified but unchanged look stale. A sync timestamp on every run fixes this.
- Opportunity.Renewal_Date: for 19.3% of records, freshness comes from the last change, not a sync timestamp, so values that were re-verified but unchanged look stale. A sync timestamp on every run fixes this.
