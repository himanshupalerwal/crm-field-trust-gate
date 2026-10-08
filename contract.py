"""Load a field trust contract and check it before the gate relies on it.

gate() trusts the contract it is given: a tier typed as "Action" or a misspelled key
quietly changes what it decides. load_contract() stops on those mistakes instead.
"""
import re
from pathlib import Path

import yaml

TIERS = ("context", "advisory", "action")
KEYS = ("authority", "freshness_sla_hours", "allowed_writers", "tier")


def validate(contract):
    """Return a list of problems; an empty list means the contract is usable."""
    if not isinstance(contract, dict) or not isinstance(contract.get("fields"), dict) \
            or not contract["fields"]:
        return ["the contract needs a non-empty 'fields' mapping"]
    problems = []
    for name, rule in contract["fields"].items():
        if not re.fullmatch(r"[A-Za-z]\w*\.[A-Za-z]\w*", str(name)):
            problems.append(f"{name}: field names look like Object.Field")
        if not isinstance(rule, dict):
            problems.append(f"{name}: expected {', '.join(KEYS)}")
            continue
        problems += [f"{name}: missing {k}" for k in KEYS if k not in rule]
        problems += [f"{name}: unknown key {k}" for k in rule if k not in KEYS]
        if "tier" in rule and rule["tier"] not in TIERS:
            problems.append(f"{name}: tier must be one of {', '.join(TIERS)}, not {rule['tier']!r}")
        sla = rule.get("freshness_sla_hours")
        if "freshness_sla_hours" in rule and (isinstance(sla, bool) or not isinstance(sla, (int, float))
                                              or sla <= 0):
            problems.append(f"{name}: freshness_sla_hours must be a positive number of hours")
        writers = rule.get("allowed_writers")
        if "allowed_writers" in rule and (not isinstance(writers, list) or not writers
                                          or not all(isinstance(w, str) and w for w in writers)):
            problems.append(f"{name}: allowed_writers must be a non-empty list of writer labels")
        if "authority" in rule and not (isinstance(rule["authority"], str) and rule["authority"]):
            problems.append(f"{name}: authority must name a system")
    return problems


def load_contract(path):
    """Read a contract file and raise ValueError listing every problem in it."""
    contract = yaml.safe_load(Path(path).read_text())
    problems = validate(contract)
    if problems:
        raise ValueError(f"{path}:\n  " + "\n  ".join(problems))
    return contract
