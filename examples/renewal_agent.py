"""The article's pattern end to end, on a tiny in-memory CRM.

A renewal agent never writes to the CRM directly. Its tools go through ToolLayer:
  1. every tool that acts on CRM data declares the fields its write is based on;
  2. ToolLayer builds a FieldRead for each of them from the CRM's own metadata: last
     writer, sync time, duplicate matches and, for agent writes, the provenance stamp;
  3. it asks gate(), and fails closed: an error inside the gate becomes "ask", never a write;
  4. proceed -> write and stamp; draft -> queue for a person; ask -> return the reasons.
One write skips the gate on purpose: fill_blank, where the evidence (an email signature) is
not a CRM field. It may only fill an empty field the contract lists this agent as a writer
for, and the stamp keeps the value advisory wherever it is used, until a person confirms it.

Reviews is the human side. Its approve() belongs in your review UI, never among the agent's
tools: the agent only gets a way to submit drafts. In a real UI the reviewer's identity comes
from their login; here it is a parameter only to keep the demo small.

Run:  python examples/renewal_agent.py
"""
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from contract import load_contract  # noqa: E402
from gate import FieldRead, gate, stamp  # noqa: E402

CONTRACT = load_contract(ROOT / "field_contract.yaml")
NOW = datetime.now(timezone.utc)
NEVER = datetime(1970, 1, 1, tzinfo=timezone.utc)
SYSTEMS = {"billing_sync": "billing", "contract_sync": "contracts", "sales_rep": "crm",
           "agent:renewal": "crm"}          # an agent speaks for the system it writes into


@dataclass
class Stored:
    value: object
    writer: str                 # writer label of the last write
    synced_at: datetime         # integrations set it on every run, even when nothing changed
    provenance: dict = None     # the stamp, on agent writes


class CRM:
    """Values plus the metadata a real CRM keeps: field history, sync timestamps, matching."""

    def __init__(self):
        self.values, self.matches = {}, {}

    def put(self, record, field, value, writer, synced_at=None, provenance=None):
        self.values[(record, field)] = Stored(value, writer, synced_at or datetime.now(timezone.utc),
                                              provenance)

    def read(self, record, field):
        """The FieldRead the gate sees for one input."""
        s = self.values.get((record, field))
        if s is None:
            return FieldRead(field, "unknown", NEVER, 0, "unknown")
        writer = s.writer
        if s.provenance and s.provenance["confirmed_by"]:
            writer = s.provenance["confirmed_by"]     # a confirmed value reads as the confirmer's
        return FieldRead(field, SYSTEMS.get(writer, "unknown"), s.synced_at,
                         self.matches.get(record, 1), writer)


def check(crm, based_on):
    reads = [crm.read(r, f) for r, f in based_on]
    try:
        decision, reasons = gate(reads, CONTRACT)
    except Exception as e:                            # fail closed
        decision, reasons = "ask", [f"gate error: {type(e).__name__}: {e}"]
    return reads, decision, reasons


def apply(crm, proposed):
    crm.put(proposed["record"], proposed["field"], proposed["value"],
            proposed["provenance"]["writer"], provenance=proposed["provenance"])


class Reviews:
    """The human side: drafts wait here for a person."""

    def __init__(self, crm):
        self.crm, self.drafts = crm, []

    def submit(self, draft):
        self.drafts.append(draft)

    def approve(self, draft, reviewer):
        """Re-check the inputs, confirm the agent-written ones, then apply the write."""
        if draft not in self.drafts:
            return "ask", ["this draft is no longer waiting for review"]
        current = self.crm.values.get((draft["record"], draft["field"]))
        if (current.value if current else None) != draft["previous"]:   # never overwrite a later change
            self.drafts.remove(draft)
            return "ask", [f"{draft['field']} changed after this draft was made"]
        _, decision, reasons = check(self.crm, draft["based_on"])
        if decision == "ask":                         # an input changed since the draft
            return "ask", reasons
        at = datetime.now(timezone.utc).isoformat()
        for record, field in draft["based_on"]:
            s = self.crm.values.get((record, field))
            if s and s.provenance and not s.provenance["confirmed_by"]:
                s.provenance.update(confirmed_by=reviewer, confirmed_at=at)
        draft["provenance"].update(confirmed_by=reviewer, confirmed_at=at)
        self.drafts.remove(draft)
        apply(self.crm, draft)
        return "approved", []


class ToolLayer:
    """The agent's tools. It can submit drafts for review, but nothing here can approve them."""

    def __init__(self, crm, submit_draft, agent_id="renewal", run_id="run-1"):
        self.crm, self.submit_draft, self.agent_id, self.run_id = crm, submit_draft, agent_id, run_id

    def update_quote(self, quote, amount, based_on):
        return self.write(quote, "Quote.Amount", amount, based_on)

    def send_quote(self, quote, contact):
        return self.write(quote, "Quote.Recipient", contact, based_on=[(contact, "Contact.Role")])

    def write(self, record, field, value, based_on):
        """Every tool that acts on CRM data ends here, except fill_blank (see its docstring).
        `based_on` lists the (record, field) inputs the write depends on."""
        reads, decision, reasons = check(self.crm, based_on)
        proposed = stamp({"record": record, "field": field, "value": value,
                          "based_on": based_on}, reads, self.agent_id, self.run_id)
        if decision == "proceed":
            apply(self.crm, proposed)
        elif decision == "draft":
            current = self.crm.values.get((record, field))
            proposed["previous"] = current.value if current else None   # what the reviewer's approval replaces
            self.submit_draft(proposed)
        return decision, reasons

    def fill_blank(self, record, field, value, evidence):
        """The one write that skips the gate: its evidence is not a CRM field. Only a blank
        field the contract lets this agent write; stamped, so it reads as advisory."""
        allowed = CONTRACT["fields"].get(field, {}).get("allowed_writers", [])
        if f"agent:{self.agent_id}" not in allowed or (record, field) in self.crm.values:
            return "ask", [f"{field}: not a blank field this agent may write"]
        proposed = stamp({"record": record, "field": field, "value": value}, [],
                         self.agent_id, self.run_id)
        proposed["provenance"]["evidence"] = evidence
        apply(self.crm, proposed)
        return "written", []


def seed():
    """A small CRM with one clean account and two problem ones."""
    crm = CRM()
    crm.put("acct-1", "Account.ARR", 120_000, "billing_sync", NOW - timedelta(hours=6))
    crm.put("opp-1", "Opportunity.Renewal_Date", "2027-01-15", "contract_sync", NOW - timedelta(hours=8))
    crm.put("acct-2", "Account.ARR", 95_000, "sales_rep", NOW - timedelta(days=3))
    crm.matches["acct-2"] = 2                         # two account records match this customer
    crm.put("opp-2", "Opportunity.Renewal_Date", "2026-12-01", "contract_sync", NOW - timedelta(hours=8))
    crm.put("acct-3", "Account.ARR", 80_000, "billing_sync", datetime(2026, 10, 1))  # naive time
    return crm


def demo():
    crm = seed()
    reviews = Reviews(crm)
    tools = ToolLayer(crm, reviews.submit)
    steps = []

    def step(label, result):
        steps.append((label, *result))

    step("Update quote-1 from billing ARR and the contract renewal date",
         tools.update_quote("quote-1", 126_000,
                            based_on=[("acct-1", "Account.ARR"), ("opp-1", "Opportunity.Renewal_Date")]))
    step("Update quote-2 from ARR a rep typed, on a duplicated account",
         tools.update_quote("quote-2", 99_750,
                            based_on=[("acct-2", "Account.ARR"), ("opp-2", "Opportunity.Renewal_Date")]))
    step("Fill a blank contact role, inferred from an email signature",
         tools.fill_blank("contact-1", "Contact.Role", "economic_buyer", evidence="email signature"))
    step("Send quote-1 to that contact", tools.send_quote("quote-1", "contact-1"))
    step("A rep reviews the draft and approves it", reviews.approve(reviews.drafts[0], reviewer="sales_rep"))
    tools.run_id = "run-2"
    step("Next run: send quote-3 to the same contact", tools.send_quote("quote-3", "contact-1"))
    step("Update quote-4 from ARR with a broken timestamp",
         tools.update_quote("quote-4", 84_000, based_on=[("acct-3", "Account.ARR")]))
    return steps, crm


if __name__ == "__main__":
    steps, crm = demo()
    for i, (label, decision, reasons) in enumerate(steps, 1):
        print(f"{i}. {label}\n   -> {decision}")
        for r in reasons:
            print(f"      {r}")
    p = crm.values[("contact-1", "Contact.Role")].provenance
    print(f"\nContact.Role provenance: written by {p['writer']} from {p['evidence']}, "
          f"confirmed by {p['confirmed_by']}")
