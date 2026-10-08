"""Two simulations behind the article this repo accompanies.

Everything here is synthetic. Every rate below is an ASSUMPTION, chosen to be plausible,
not a measurement from any real org. Replace ASSUMED_RATES (experiment 1) and LOOP
(experiment 2) with rates you measure in your own CRM. The point is the shape of the
trade-off, not the exact numbers.

Experiment 1: a renewal agent proposes quote updates. Compare executing every write
(no gate) with routing each write through the field trust gate in gate.py.

Experiment 2: an agent fills in missing contact roles by inference and later sends quotes
to those contacts. Same weekly human review budget in every set-up:
  random_audit   - no provenance; reviewers spot-check stored roles at random
  check_outgoing - no provenance; reviewers check a sample of this week's outgoing quotes
  history_only   - the agent writes as its own integration user, so field history shows
                   which roles it wrote; reviewers check those outgoing quotes first, then a
                   sample of the rest. No confirmation record: a role a reviewer approved
                   without changing it still shows the agent as its last writer
  provenance_drafts_only - agent writes are stamped; every send goes through gate(), so
                   stamped guesses become drafts that reviewers check before they go out
  provenance     - as above, then any review budget left checks this week's outgoing quotes
Every contact role is assumed to be inside its freshness SLA (role_read), so the gate's
freshness check never fires in experiment 2.

Run:  python simulate.py   -> writes results/*.csv, results/*.json and prints a summary
"""
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from contract import load_contract
from gate import FieldRead, gate

HERE = Path(__file__).parent
CONTRACT = load_contract(HERE / "field_contract.yaml")
NOW = datetime.now(timezone.utc)
SEEDS = range(20)

# ---------------------------------------------------------------- Experiment 1

ASSUMED_RATES = {   # share of field reads in each condition; the rest are clean
    "arr": {
        "stale_changed": 0.04,    # billing changed, CRM copy not re-synced yet  -> wrong
        "stale_unchanged": 0.06,  # sync missed a run, value still right         -> right
        "rep_override": 0.03,     # a rep typed over the synced value            -> wrong
        "duplicate": 0.03,        # two account records match this customer      -> 50/50
        "agent_written": 0.05,    # an earlier agent run wrote it                -> 70% right
        "upstream_wrong": 0.01,   # billing itself is wrong; metadata looks clean -> wrong
    },
    "renewal_date": {
        "stale_changed": 0.02,    # amendment signed, not synced yet             -> wrong
        "manual_edit": 0.02,      # edited by hand in the CRM                    -> 50/50
    },
}
DUPLICATE_CORRECT = 0.50
AGENT_WRITTEN_CORRECT = 0.70
MANUAL_EDIT_CORRECT = 0.50

FIELD = {"arr": "Account.ARR", "renewal_date": "Opportunity.Renewal_Date"}
AUTHORITY = {"arr": ("billing", "billing_sync"), "renewal_date": ("contracts", "contract_sync")}


def sample_condition(rng, rates):
    names = list(rates) + ["clean"]
    probs = list(rates.values()) + [1.0 - sum(rates.values())]
    return str(rng.choice(names, p=probs))


def field_read(rng, field, condition, detect_rate=1.0):
    """Return (metadata the gate sees, value is right?). With detect_rate < 1, some
    defects arrive with clean-looking metadata, so the gate cannot see them."""
    source, writer = AUTHORITY[field]
    fresh = NOW - timedelta(hours=float(rng.uniform(1, 12)))
    stale = NOW - timedelta(days=float(rng.uniform(3, 10)))
    name = FIELD[field]
    clean = FieldRead(name, source, fresh, 1, writer)
    if condition == "clean":
        return clean, True
    right = {"stale_changed": False, "stale_unchanged": True, "rep_override": False,
             "upstream_wrong": False,
             "duplicate": bool(rng.random() < DUPLICATE_CORRECT),
             "agent_written": bool(rng.random() < AGENT_WRITTEN_CORRECT),
             "manual_edit": bool(rng.random() < MANUAL_EDIT_CORRECT)}[condition]
    if condition == "upstream_wrong" or rng.random() >= detect_rate:
        return clean, right
    meta = {"stale_changed": FieldRead(name, source, stale, 1, writer),
            "stale_unchanged": FieldRead(name, source, stale, 1, writer),
            "rep_override": FieldRead(name, "crm", fresh, 1, "sales_rep"),
            "duplicate": FieldRead(name, source, fresh, 2, writer),
            "agent_written": FieldRead(name, "crm", fresh, 1, "agent:renewal"),
            "manual_edit": FieldRead(name, "crm", fresh, 1, "sales_rep")}[condition]
    return meta, right


def run_experiment_1(seed, n=1000, rate_multiplier=1.0, detect_rate=1.0):
    rng = np.random.default_rng(seed)
    rates = {f: {c: p * rate_multiplier for c, p in r.items()} for f, r in ASSUMED_RATES.items()}
    out = {"auto_right": 0, "auto_wrong": 0, "held_wrong": 0, "held_right": 0,
           "no_gate_wrong": 0, "draft": 0, "ask": 0}
    held_right_by_condition = {}
    for _ in range(n):
        reads, right, conds = [], True, []
        for field in ("arr", "renewal_date"):
            cond = sample_condition(rng, rates[field])
            read, ok = field_read(rng, field, cond, detect_rate)
            reads.append(read)
            right = right and ok
            conds.append(f"{field}:{cond}")
        decision, _ = gate(reads, CONTRACT)
        out["no_gate_wrong"] += not right
        if decision == "proceed":
            out["auto_right" if right else "auto_wrong"] += 1
        else:
            out[decision] += 1
            out["held_right" if right else "held_wrong"] += 1
            if right:
                key = next((c for c in conds if not c.endswith("clean")), "clean")
                held_right_by_condition[key] = held_right_by_condition.get(key, 0) + 1
    out["held_right_by_condition"] = held_right_by_condition
    return out


def mean(xs):
    return round(float(np.mean(xs)), 1)


# ---------------------------------------------------------------- Experiment 2

LOOP = {
    "accounts": 1000,
    "missing_at_start": 0.30,       # contact role blank on 30% of accounts
    "human_value_wrong": 0.05,      # human-entered roles are not perfect either
    "agent_inference_right": 0.75,  # agent guesses the role from signatures, threads, etc.
    "batch_per_week": 250,          # accounts needing a quote recipient each week
    "review_budget": 60,            # human reviews available per week, all set-ups
    "review_catch_rate": 0.90,      # chance a reviewer spots a wrong value it checks
    "weeks": 12,
}
WORLDS = ("random_audit", "check_outgoing", "history_only", "provenance_drafts_only", "provenance")


def role_read(writer):
    return FieldRead("Contact.Role", "crm", NOW - timedelta(days=30), 1, writer)


def run_experiment_2(seed, world, p=LOOP):
    rng = np.random.default_rng(seed)
    n = p["accounts"]
    has_value = rng.random(n) >= p["missing_at_start"]
    right = np.where(has_value, rng.random(n) >= p["human_value_wrong"], False)
    unconfirmed_agent = np.zeros(n, dtype=bool)
    agent_origin = np.zeros(n, dtype=bool)
    agent_last = np.zeros(n, dtype=bool)            # field history shows the agent wrote it last
    res = {"cumulative": [], "from_agent": 0, "from_human": 0, "reviews_used": 0, "queue": [],
           "sent": 0}
    backlog = []

    def send(a):
        res["sent"] += 1
        if not right[a]:
            res["from_agent" if agent_origin[a] else "from_human"] += 1

    def review(a):
        res["reviews_used"] += 1
        if not right[a] and rng.random() < p["review_catch_rate"]:
            right[a] = True
            agent_last[a] = False                   # a person changed it

    for _ in range(p["weeks"]):
        budget = p["review_budget"]
        batch = rng.choice(n, size=p["batch_per_week"], replace=False)
        outgoing = []
        for a in batch:
            if not has_value[a]:                    # agent infers the role and writes it
                has_value[a] = True
                right[a] = rng.random() < p["agent_inference_right"]
                agent_origin[a] = unconfirmed_agent[a] = agent_last[a] = True
            if world.startswith("provenance"):
                writer = "agent:renewal" if unconfirmed_agent[a] else "sales_rep"
                decision, _ = gate([role_read(writer)], CONTRACT)
                if decision == "proceed":
                    outgoing.append(a)
                elif a not in backlog:
                    backlog.append(a)               # draft: a human checks it first
            else:
                outgoing.append(a)
        if world.startswith("provenance"):
            drafts, backlog = backlog[:budget], backlog[budget:]
            for a in drafts:
                review(a)
                unconfirmed_agent[a] = False        # confirmed_by / confirmed_at set
                send(a)
            spare = budget - len(drafts)
            if world == "provenance" and spare and outgoing:   # spend what is left
                checked = set(rng.choice(outgoing, size=min(spare, len(outgoing)), replace=False))
                for a in checked:
                    review(a)
            for a in outgoing:
                send(a)
        elif world == "check_outgoing":
            checked = set(rng.choice(outgoing, size=min(budget, len(outgoing)), replace=False))
            for a in outgoing:
                if a in checked:
                    review(a)
                send(a)
        elif world == "history_only":
            by_agent = [a for a in outgoing if agent_last[a]]
            others = [a for a in outgoing if not agent_last[a]]
            if len(by_agent) >= budget:
                checked = set(rng.choice(by_agent, size=budget, replace=False))
            else:
                checked = set(by_agent) | set(rng.choice(
                    others, size=min(budget - len(by_agent), len(others)), replace=False))
            for a in outgoing:
                if a in checked:
                    review(a)
                send(a)
        else:                                       # random_audit of stored values
            for a in rng.choice(np.flatnonzero(has_value), size=budget, replace=False):
                review(a)
            for a in outgoing:
                send(a)
        res["cumulative"].append(res["from_agent"] + res["from_human"])
        res["queue"].append(len(backlog))
    res["waiting_at_end"] = len(backlog)            # drafts never sent within the 12 weeks
    return res


# ---------------------------------------------------------------- run everything

def main():
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    summary = {}

    runs = [run_experiment_1(s) for s in SEEDS]
    keys = [k for k in runs[0] if k != "held_right_by_condition"]
    with open(out_dir / "exp1_per_1000_actions.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "mean", "min", "max"])
        for k in keys:
            vals = [r[k] for r in runs]
            w.writerow([k, mean(vals), min(vals), max(vals)])
    m = {k: float(np.mean([r[k] for r in runs])) for k in keys}
    conds = sorted({c for r in runs for c in r["held_right_by_condition"]})
    held = m["held_right"] + m["held_wrong"]
    runs80 = [run_experiment_1(s, detect_rate=0.8) for s in SEEDS]
    summary["exp1"] = {
        "wrong_no_gate": round(m["no_gate_wrong"], 1),
        "wrong_auto_with_gate": round(m["auto_wrong"], 1),
        "held_total": round(held, 1),
        "held_but_right": round(m["held_right"], 1),
        "share_of_right_writes_held": round(m["held_right"] / (1000 - m["no_gate_wrong"]), 3),
        "held_but_right_by_condition": {c: mean([r["held_right_by_condition"].get(c, 0) for r in runs])
                                        for c in conds},
        "if_reviewers_catch_90pct": round(m["auto_wrong"] + 0.1 * m["held_wrong"], 1),
        "if_same_reviews_spent_at_random": round(m["no_gate_wrong"] * (1 - held / 1000), 1),
        "if_metadata_reveals_80pct_of_defects": mean([r["auto_wrong"] for r in runs80]),
        "if_both_90pct_catch_and_80pct_metadata": mean([r["auto_wrong"] + 0.1 * r["held_wrong"]
                                                        for r in runs80]),
    }

    with open(out_dir / "exp1_sensitivity.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["defect_rate_multiplier", "wrong_no_gate", "wrong_with_gate",
                    "share_written_autonomously", "correct_writes_held"])
        for mult in (0.5, 1.0, 1.5, 2.0, 3.0):
            rs = [run_experiment_1(s, rate_multiplier=mult) for s in SEEDS]
            w.writerow([mult, mean([r["no_gate_wrong"] for r in rs]),
                        mean([r["auto_wrong"] for r in rs]),
                        round(float(np.mean([(r["auto_right"] + r["auto_wrong"]) / 1000 for r in rs])), 3),
                        mean([r["held_right"] for r in rs])])

    exp2 = {wd: [run_experiment_2(s, wd) for s in SEEDS] for wd in WORLDS}
    with open(out_dir / "exp2_feedback_loop.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["week", "world", "cumulative_wrong_sends", "drafts_waiting"])
        for wd in WORLDS:
            for wk in range(LOOP["weeks"]):
                w.writerow([wk + 1, wd, mean([r["cumulative"][wk] for r in exp2[wd]]),
                            mean([r["queue"][wk] for r in exp2[wd]])])
    summary["exp2"] = {wd: {"wrong_sends_12wk": mean([r["cumulative"][-1] for r in exp2[wd]]),
                            "from_agent_guesses": mean([r["from_agent"] for r in exp2[wd]]),
                            "from_human_entries": mean([r["from_human"] for r in exp2[wd]]),
                            "reviews_used": mean([r["reviews_used"] for r in exp2[wd]]),
                            "quotes_sent": mean([r["sent"] for r in exp2[wd]]),
                            "wrong_per_1000_sent": mean([1000 * r["cumulative"][-1] / r["sent"]
                                                         for r in exp2[wd]]),
                            "drafts_waiting_at_end": mean([r["waiting_at_end"] for r in exp2[wd]])}
                       for wd in WORLDS}
    summary["exp2"]["reviews_available"] = LOOP["review_budget"] * LOOP["weeks"]

    with open(out_dir / "exp2_sensitivity.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["agent_inference_right", *WORLDS])
        for acc in (0.65, 0.75, 0.85, 0.95):
            p = {**LOOP, "agent_inference_right": acc}
            w.writerow([acc, *[mean([run_experiment_2(s, wd, p)["cumulative"][-1] for s in SEEDS])
                               for wd in WORLDS]])

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (out_dir / "assumptions.json").write_text(json.dumps(
        {"seeds": len(SEEDS), "experiment_1_rates": ASSUMED_RATES,
         "duplicate_correct": DUPLICATE_CORRECT, "agent_written_correct": AGENT_WRITTEN_CORRECT,
         "manual_edit_correct": MANUAL_EDIT_CORRECT, "experiment_2": LOOP}, indent=2))
    for old in ("exp2_wrong_send_origin.json",):
        (out_dir / old).unlink(missing_ok=True)
    print(json.dumps(summary, indent=2))
    for name in ("exp1_sensitivity.csv", "exp2_sensitivity.csv"):
        print(f"\n{name}\n" + (out_dir / name).read_text())


if __name__ == "__main__":
    main()
