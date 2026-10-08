from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

TIER_RANK = {"context": 0, "advisory": 1, "action": 2}

@dataclass
class FieldRead:
    name: str             # e.g. "Account.ARR"
    source_system: str    # where this value actually came from
    last_synced: datetime # timezone-aware
    matches: int          # records that matched the entity key
    last_writer: str      # e.g. "billing_sync", "sales_rep", "agent:renewal"

def gate(reads, contract, now=None):
    """Return ('proceed' | 'draft' | 'ask', reasons) for a proposed write.
    `reads` are the fields the write tool declares as its inputs."""
    if not reads:
        return "draft", ["no declared inputs"]
    now = now or datetime.now(timezone.utc)
    failures, tiers = [], []
    for r in reads:
        rule = contract["fields"].get(r.name)
        if rule is None:
            failures.append(f"{r.name}: no contract")
            continue
        if r.source_system != rule["authority"]:
            failures.append(f"{r.name}: authority is {rule['authority']}, not {r.source_system}")
        if now - r.last_synced > timedelta(hours=rule["freshness_sla_hours"]):
            failures.append(f"{r.name}: older than its SLA")
        if r.matches != 1:
            failures.append(f"{r.name}: {r.matches} records match, expected 1")
        if r.last_writer not in rule["allowed_writers"]:
            failures.append(f"{r.name}: last written by {r.last_writer}")
        if rule["tier"] == "context":
            failures.append(f"{r.name}: context only, cannot drive a write")
        tier = rule["tier"]
        if r.last_writer.startswith("agent:"):
            tier = min(tier, "advisory", key=TIER_RANK.get)  # unconfirmed agent value
        tiers.append(tier)
    if failures:
        return "ask", failures
    lowest = min(tiers, key=TIER_RANK.get)
    if lowest == "action":
        return "proceed", []
    return "draft", [f"lowest input tier is {lowest}"]

def stamp(write, reads, agent_id, run_id):
    """Attach provenance to an agent write before it reaches the CRM."""
    return {**write, "provenance": {
        "writer": f"agent:{agent_id}", "run_id": run_id,
        "inputs": [r.name for r in reads],
        "written_at": datetime.now(timezone.utc).isoformat(),
        "confirmed_by": None, "confirmed_at": None}}
