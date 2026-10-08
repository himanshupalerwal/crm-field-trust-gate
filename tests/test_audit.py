import csv
from pathlib import Path
import re
import sys

import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "audit"))
import crm_audit  # noqa: E402
import make_sample_data  # noqa: E402

CONFIG = ROOT / "audit" / "sample_config.yaml"


def run(tmp_path):
    if not (ROOT / "audit" / "sample_data" / "accounts.csv").exists():
        make_sample_data.main()
    metrics, report = crm_audit.run(CONFIG, tmp_path)
    return metrics, report


def test_helpers():
    assert crm_audit.short_id("001000000000001AAA") == "001000000000001"
    assert crm_audit.normalize_key("https://www.Customer1.example/about") == "customer1.example"
    assert crm_audit.normalize_key("http://customer1.example") == "customer1.example"


def test_shadow_gate_decisions_add_up(tmp_path):
    metrics, _ = run(tmp_path)
    a = metrics["shadow_gate"]["update_renewal_quote"]
    assert a["actions_evaluated"] > 1000
    assert abs(sum(a["decision_pct"].values()) - 100) < 0.2
    assert "freshness" in a["failed_check_pct"] and "identity" in a["failed_check_pct"]


def test_field_profiles_find_planted_defects(tmp_path):
    p = run(tmp_path)[0]["field_profiles"]["Account.ARR"]
    assert 9 <= p["over_sla_pct"] <= 15            # about 10% of syncs were planted late
    assert 4 <= p["last_writer_not_allowed_pct"] <= 7
    assert 6 <= p["duplicate_pct"] <= 9            # 60 duplicates and their originals


def test_stale_values_mismatch_far_more_than_fresh_ones(tmp_path):
    m = run(tmp_path)[0]["source_mismatch"]["Account.ARR"]
    assert m["mismatch_when_over_sla_pct"] > 5 * m["mismatch_when_within_sla_pct"]


def test_agent_reversals_are_counted(tmp_path):
    r = run(tmp_path)[0]["reversals"]["Account.ARR"]["agent:renewal"]
    assert r["writes"] > 20 and r["reversed_pct"] > 0


def changed_config(tmp_path, change):
    """The sample config with absolute paths, after `change(cfg)`."""
    run(tmp_path)                                  # makes sure the sample data exists
    cfg = yaml.safe_load(CONFIG.read_text())
    absolute = lambda p: str((CONFIG.parent / p).resolve())  # noqa: E731
    cfg["contract"] = absolute(cfg["contract"])
    cfg["writers"]["users_file"] = absolute(cfg["writers"]["users_file"])
    for spec in [*cfg["records"].values(), *cfg["source_of_truth"].values(), *cfg["history"]]:
        spec["file"] = absolute(spec["file"])
    change(cfg)
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return path


def test_empty_history_export_means_no_history(tmp_path):
    empty = tmp_path / "history.csv"
    empty.write_text("\n")                         # what the Salesforce CLI writes for zero rows

    def change(cfg):
        for spec in cfg["history"]:
            spec["file"] = str(empty)

    metrics, _ = crm_audit.run(changed_config(tmp_path, change), tmp_path / "out")
    assert metrics["reversals"] == {}
    assert any("No field history" in c for c in metrics["caveats"])


def test_any_other_empty_export_stops_with_a_message(tmp_path):
    empty = tmp_path / "users.csv"
    empty.write_text("\n")
    path = changed_config(tmp_path, lambda cfg: cfg["writers"].update(users_file=str(empty)))
    with pytest.raises(SystemExit, match="has no rows"):
        crm_audit.run(path, tmp_path / "out")


def test_header_only_files_missing_columns_and_missing_config_stop(tmp_path):
    users = tmp_path / "users.csv"
    users.write_text("Id,Username,Profile.Name\n")          # sf data export bulk, zero rows
    path = changed_config(tmp_path, lambda cfg: cfg["writers"].update(users_file=str(users)))
    with pytest.raises(SystemExit, match="has no rows"):
        crm_audit.run(path, tmp_path / "out")
    users.write_text("Id,Username\n005000000000001AAA,someone@example.com\n")
    with pytest.raises(SystemExit, match="Profile.Name not found"):
        crm_audit.run(path, tmp_path / "out")
    with pytest.raises(SystemExit, match="not found"):
        crm_audit.run(tmp_path / "no_such_config.yaml", tmp_path / "out")


def test_an_invalid_contract_stops_the_audit(tmp_path):
    bad = yaml.safe_load((ROOT / "field_contract.yaml").read_text())
    bad["fields"]["Account.ARR"]["tier"] = "Action"
    (tmp_path / "contract.yaml").write_text(yaml.safe_dump(bad))
    path = changed_config(tmp_path, lambda cfg: cfg.update(contract=str(tmp_path / "contract.yaml")))
    with pytest.raises(SystemExit, match="tier must be one of"):
        crm_audit.run(path, tmp_path / "out")


def test_unmapped_users_are_not_mistaken_for_missing_history(tmp_path):
    base = run(tmp_path)[0]["field_profiles"]["Account.ARR"]
    path = changed_config(tmp_path, lambda cfg: cfg["writers"].update(by_profile={}))
    p = crm_audit.run(path, tmp_path / "out")[0]["field_profiles"]["Account.ARR"]
    assert p["no_field_history_pct"] == base["no_field_history_pct"]
    assert "unmapped" in p["writer_mix_pct"] and "sales_rep" not in p["writer_mix_pct"]


def test_unmapped_users_are_not_counted_as_reversals():
    # default label equals a real one, so only the mapping, not the label, tells them apart
    writers = crm_audit.Writers({"writers": {"default": "sales_rep"}, "systems": {}})
    t = pd.Timestamp("2026-10-01", tz="UTC")
    history = pd.DataFrame({
        "field": ["Account.ARR"] * 4, "record": ["r1", "r1", "r2", "r2"],
        "writer": ["agent:renewal", "sales_rep", "agent:renewal", "sales_rep"],
        "mapped": [True, False, True, True],
        "at": [t, t + pd.Timedelta(hours=1), t, t + pd.Timedelta(days=2)]})
    r = crm_audit.reversals(history, writers, {}, min_n=1)["Account.ARR"]["agent:renewal"]
    assert r == {"writes": 2, "reversed_pct": 50.0, "changed_by_unmapped_user_pct": 50.0}


def test_report_contains_no_ids_names_usernames_or_websites(tmp_path):
    _, report = run(tmp_path)
    json_text = (tmp_path / "metrics.json").read_text()
    private = set()                    # every id, name, username, email and website in the input
    for f in (ROOT / "audit" / "sample_data").glob("*.csv"):
        with open(f, newline="") as fh:
            for row in csv.DictReader(fh):
                for col, v in row.items():
                    if re.fullmatch(r"[0-9A-Za-z]{15}([0-9A-Za-z]{3})?", v) and not v.isdigit():
                        private.update({v, v[:15]})
                    elif col in ("Name", "Username") or "@" in v:
                        private.add(v)
                    elif v.startswith("http"):
                        private.update({v, crm_audit.normalize_key(v)})
    assert len(private) > 1000
    for text in (report, json_text):
        assert "@" not in text, "email leaked"
        leaked = [v for v in private if v in text]
        assert not leaked, f"leaked: {leaked[:3]}"
