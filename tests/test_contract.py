import copy
from pathlib import Path
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from contract import load_contract, validate  # noqa: E402

GOOD = yaml.safe_load((ROOT / "field_contract.yaml").read_text())


def broken(field, **changes):
    c = copy.deepcopy(GOOD)
    for key, value in changes.items():
        if value is None:
            c["fields"][field].pop(key)
        else:
            c["fields"][field][key] = value
    return c


def test_the_repo_contract_is_valid():
    assert validate(GOOD) == []
    assert load_contract(ROOT / "field_contract.yaml") == GOOD


@pytest.mark.parametrize("changes, problem", [
    ({"tier": "Action"}, "tier must be one of context, advisory, action, not 'Action'"),
    ({"allowed_writers": None}, "missing allowed_writers"),
    ({"allowed_writer": ["billing_sync"]}, "unknown key allowed_writer"),
    ({"freshness_sla_hours": 0}, "freshness_sla_hours must be a positive number"),
    ({"freshness_sla_hours": "36"}, "freshness_sla_hours must be a positive number"),
    ({"allowed_writers": []}, "allowed_writers must be a non-empty list"),
    ({"authority": ""}, "authority must name a system"),
])
def test_each_mistake_is_reported(changes, problem):
    problems = validate(broken("Account.ARR", **changes))
    assert any(p.startswith("Account.ARR: " + problem) for p in problems), problems


def test_bad_field_names_and_empty_contracts(tmp_path):
    assert validate({"fields": {"ARR": GOOD["fields"]["Account.ARR"]}}) == [
        "ARR: field names look like Object.Field"]
    assert validate({}) == ["the contract needs a non-empty 'fields' mapping"]
    bad = tmp_path / "contract.yaml"
    bad.write_text(yaml.safe_dump(broken("Account.ARR", tier="Action")))
    with pytest.raises(ValueError, match="tier must be one of"):
        load_contract(bad)
