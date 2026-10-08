"""Write the cases the Apex gate must answer exactly as gate.py does.

The Apex test FieldTrustGateTest reads them from the FieldTrustGateCases static resource and
compares every decision and every reason. tests/test_salesforce.py checks this file is current.

    python salesforce/scripts/make_gate_cases.py
"""
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from contract import load_contract  # noqa: E402
from gate import FieldRead, gate  # noqa: E402

OUT = ROOT / "salesforce" / "force-app" / "main" / "default" / "staticresources" / "FieldTrustGateCases.json"
HOURS = [1, 2, 12, 30, 40, 100, 200, 3000, 5000, 9000]   # none equals an SLA, so no boundary cases
SYSTEMS = ["crm", "billing", "contracts", "health_model", "agent", "unknown"]
WRITERS = ["sales_rep", "billing_sync", "contract_sync", "health_model", "agent:renewal", "agent:other",
           "unknown", "unmapped"]


def cases():
    contract = load_contract(ROOT / "field_contract.yaml")
    names = list(contract["fields"])
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rng = random.Random(2026)
    specs = [   # the three worked examples first: clean, an agent's guess, and a rep's stale ARR
        [("Account.ARR", "billing", 2, 1, "billing_sync"), ("Opportunity.Renewal_Date", "contracts", 2, 1, "contract_sync")],
        [("Contact.Role", "crm", 168, 1, "agent:renewal")],
        [("Account.ARR", "crm", 72, 2, "sales_rep")],
        [],
    ]
    for _ in range(400):
        reads = []
        for _ in range(rng.choice([1, 1, 2, 2, 3])):
            name = rng.choice(names + ["Account.Discount"])
            rule = contract["fields"].get(name)
            authority = rule["authority"] if rule else "crm"
            allowed = rule["allowed_writers"] if rule else ["sales_rep"]
            if rule and rng.random() < 0.6:          # a clean read: right source, fresh, one match
                fresh = [h for h in HOURS if h < rule["freshness_sla_hours"]]
                reads.append((name, authority, rng.choice(fresh), 1, rng.choice(allowed)))
                continue
            source = rng.choice([authority] * 5 + SYSTEMS + [authority.capitalize()])
            writer = rng.choice(allowed * 4 + WRITERS + ["Sales_Rep", "Agent:renewal"])   # case must matter
            reads.append((name, source, rng.choice(HOURS), rng.choice([1, 1, 1, 1, 0, 2, 3]), writer))
        specs.append(reads)
    out = []
    for spec in specs:
        reads = [FieldRead(n, s, now - timedelta(hours=h), m, w) for n, s, h, m, w in spec]
        decision, reasons = gate(reads, contract, now=now)
        out.append({"reads": [{"name": n, "source_system": s, "hours_ago": h, "matches": m, "last_writer": w}
                              for n, s, h, m, w in spec],
                    "decision": decision, "reasons": reasons})
    return {"generated_by": "salesforce/scripts/make_gate_cases.py", "contract": contract, "cases": out}


def render():
    return json.dumps(cases(), indent=1, sort_keys=True) + "\n"


if __name__ == "__main__":
    OUT.write_text(render())
    print(f"wrote {OUT.relative_to(ROOT)}")
