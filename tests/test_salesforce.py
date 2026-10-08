"""Keep the Salesforce version in step with the Python one. CI cannot run Apex; these checks can.
The Apex tests themselves (salesforce/force-app/main/default/classes/*Test.cls) run on a scratch org."""
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

import yaml

ROOT = Path(__file__).resolve().parents[1]
SF = ROOT / "salesforce"
sys.path.insert(0, str(SF / "scripts"))
import make_gate_cases  # noqa: E402

# field_contract.yaml names -> the Salesforce API names the Custom Metadata contract uses
API_NAME = {"Account.ARR": "Account.ARR__c", "Opportunity.Renewal_Date": "Opportunity.Renewal_Date__c",
            "Account.Health_Score": "Account.Health_Score__c", "Contact.Role": "Contact.Buying_Role__c",
            "Account.Description": "Account.Description"}


def custom_metadata_contract():
    ns = {"m": "http://soap.sforce.com/2006/04/metadata"}
    rows = {}
    for path in (SF / "force-app" / "main" / "default" / "customMetadata").glob("Field_Trust_Contract.*.md-meta.xml"):
        values = {v.find("m:field", ns).text: v.find("m:value", ns).text
                  for v in ET.parse(path).getroot().findall("m:values", ns)}
        rows[values["Field__c"]] = values
    return rows


def test_parity_cases_are_current():
    committed = make_gate_cases.OUT.read_text()
    assert committed == make_gate_cases.render(), "run python salesforce/scripts/make_gate_cases.py"


def test_custom_metadata_contract_mirrors_field_contract_yaml():
    yaml_contract = yaml.safe_load((ROOT / "field_contract.yaml").read_text())["fields"]
    rows = custom_metadata_contract()
    assert set(rows) == {API_NAME[name] for name in yaml_contract}
    for name, rule in yaml_contract.items():
        row = rows[API_NAME[name]]
        assert row["Authority__c"] == rule["authority"], name
        assert float(row["Freshness_SLA_Hours__c"]) == rule["freshness_sla_hours"], name
        assert [w.strip() for w in row["Allowed_Writers__c"].split(",")] == rule["allowed_writers"], name
        assert row["Tier__c"] == rule["tier"], name


def test_apex_gate_gives_the_same_reasons_as_gate_py():
    apex = (SF / "force-app" / "main" / "default" / "classes" / "FieldTrustGate.cls").read_text()
    python = (ROOT / "gate.py").read_text()
    for reason in (": no contract", ": authority is ", ": older than its SLA", " records match, expected 1",
                   ": last written by ", ": context only, cannot drive a write", "no declared inputs",
                   "lowest input tier is "):
        assert reason in python and reason in apex, reason
    assert not re.search(r"\.sourceSystem\s*[!=]=|\.tier\s*==\s*'", apex), "compare strings with equals(), not =="
