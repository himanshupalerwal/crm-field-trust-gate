from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gate import FieldRead, gate, stamp  # noqa: E402

CONTRACT = yaml.safe_load((ROOT / "field_contract.yaml").read_text())
NOW = datetime.now(timezone.utc)
FRESH = NOW - timedelta(hours=2)


def arr(**kw):
    base = dict(name="Account.ARR", source_system="billing", last_synced=FRESH,
                matches=1, last_writer="billing_sync")
    base.update(kw)
    return FieldRead(**base)


DATE = FieldRead("Opportunity.Renewal_Date", "contracts", FRESH, 1, "contract_sync")


def role(writer):
    return FieldRead("Contact.Role", "crm", FRESH, 1, writer)


def test_clean_action_grade_inputs_proceed():
    assert gate([arr(), DATE], CONTRACT) == ("proceed", [])


def test_rep_entered_role_is_action_grade():
    assert gate([role("sales_rep")], CONTRACT) == ("proceed", [])


def test_agent_written_role_drops_to_draft():
    assert gate([role("agent:renewal")], CONTRACT) == ("draft", ["lowest input tier is advisory"])


def test_agent_cannot_write_a_field_it_is_not_allowed_to():
    decision, reasons = gate([arr(source_system="agent", last_writer="agent:renewal")], CONTRACT)
    assert decision == "ask"
    assert "Account.ARR: last written by agent:renewal" in reasons


def test_advisory_field_makes_whole_write_a_draft():
    health = FieldRead("Account.Health_Score", "health_model", FRESH, 1, "health_model")
    assert gate([arr(), health], CONTRACT)[0] == "draft"


def test_context_field_cannot_drive_a_write():
    desc = FieldRead("Account.Description", "crm", FRESH, 1, "sales_rep")
    assert gate([desc], CONTRACT) == ("ask", ["Account.Description: context only, cannot drive a write"])


def test_each_failed_check_is_reported():
    decision, reasons = gate([arr(source_system="crm", matches=2, last_writer="sales_rep",
                                  last_synced=NOW - timedelta(days=3))], CONTRACT)
    assert decision == "ask"
    assert len(reasons) == 4


def test_freshness_has_slack_for_a_nightly_sync():
    assert gate([arr(last_synced=NOW - timedelta(hours=30)), DATE], CONTRACT)[0] == "proceed"


def test_field_without_contract_asks():
    unknown = FieldRead("Account.Discount", "crm", FRESH, 1, "sales_rep")
    assert gate([unknown], CONTRACT) == ("ask", ["Account.Discount: no contract"])


def test_write_with_no_declared_inputs_is_a_draft():
    assert gate([], CONTRACT) == ("draft", ["no declared inputs"])


def test_stamp_records_writer_run_inputs_and_open_confirmation():
    out = stamp({"field": "Contact.Role", "value": "economic_buyer"}, [role("sales_rep")],
                "renewal", "run-1")
    p = out["provenance"]
    assert p["writer"] == "agent:renewal" and p["run_id"] == "run-1"
    assert p["inputs"] == ["Contact.Role"]
    assert p["confirmed_by"] is None and p["confirmed_at"] is None
