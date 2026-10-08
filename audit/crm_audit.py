"""Measure how far an agent could trust your CRM, field by field, from CSV exports.

Read-only. It never connects to your CRM and never writes to it. You export a few CSVs
(records, field history, users, optionally a source-of-truth extract), describe them in a
config file, and it produces aggregate metrics only: no record ids, names or values leave
this script.

What it measures, for every field in your contract:
  - blank rate, who last wrote it (writer mix), and how often that writer is not allowed
  - age against the freshness SLA, and where that age came from (sync timestamp or last change)
  - duplicate share for the field's object (via a match key such as website domain)
  - mismatch against a source-of-truth extract, if you provide one
  - reversal rate: how often a person changes a value an integration or agent wrote

And for every agent action you describe (for example "update the renewal quote"):
  - shadow-mode gate results: the share of real records where gate() would proceed,
    draft or ask, and which checks fail most

Usage:
    python audit/crm_audit.py --config audit/sample_config.yaml --out reports/sample
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from contract import validate  # noqa: E402
from gate import FieldRead, gate  # noqa: E402

CHECKS = [("no contract", "no contract"), ("value is blank", "blank value"),
          ("authority is", "authority"), ("older than its SLA", "freshness"),
          ("records match", "identity"), ("last written by", "writer"),
          ("context only", "context tier")]
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------- loading

def load_config(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise SystemExit(f"Config {path} not found.")
    cfg = yaml.safe_load(path.read_text())
    base = path.parent

    def resolve(p):
        p = Path(p)
        return p if p.is_absolute() else (base / p).resolve()

    contract = resolve(cfg["contract"])
    if not contract.is_file():
        raise SystemExit(f"{contract} not found. Paths in the config are relative to the config file.")
    cfg["_resolve"] = resolve
    cfg["contract_data"] = yaml.safe_load(contract.read_text())
    problems = validate(cfg["contract_data"])
    if problems:
        raise SystemExit(f"{contract}:\n  " + "\n  ".join(problems))
    return cfg


def read_csv(path, empty_ok=False, columns=()):
    """Read an export as text. `empty_ok` is for field history, where no rows is a real answer.
    `columns` are the columns the config says the file has."""
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"{path} not found. Paths in the config are relative to the config file.")
    if path.stat().st_size == 0:
        raise SystemExit(f"{path} is empty (0 bytes), so the export probably failed.")
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:   # sf data query writes a bare newline for zero rows
        df = pd.DataFrame()
    if df.empty and not empty_ok:      # also a header-only file, as sf data export bulk writes
        raise SystemExit(f"{path} has no rows. Check the export query.")
    missing = [c for c in columns if c not in df.columns]
    if missing and not df.empty:
        raise SystemExit(f"{path}: column {', '.join(missing)} not found. Check the config.")
    return df


def short_id(x):
    """Salesforce 18-char ids start with the case-sensitive 15-char id; compare on that."""
    x = str(x).strip()
    return x[:15] if len(x) == 18 else x


def to_ts(series):
    return pd.to_datetime(series.replace("", None), utc=True, errors="coerce", format="mixed")


def normalize_key(v):
    v = str(v).strip().lower()
    v = re.sub(r"^https?://", "", v)
    v = re.sub(r"^www\.", "", v)
    return v.split("/")[0].strip()


class Writers:
    """Maps a user id to a writer label such as billing_sync, sales_rep or agent:renewal."""

    def __init__(self, cfg):
        w = cfg["writers"]
        self.default = w.get("default", "unmapped")
        self.by_id = {short_id(k): v for k, v in (w.get("by_user_id") or {}).items()}
        users_file = w.get("users_file")
        if users_file:
            idc, unc = w.get("id_column", "Id"), w.get("username_column", "Username")
            prc = w.get("profile_column", "Profile.Name")
            by_user, by_prof = w.get("by_username") or {}, w.get("by_profile") or {}
            users = read_csv(cfg["_resolve"](users_file),
                             columns=[idc] + ([unc] if by_user else []) + ([prc] if by_prof else []))
            for _, u in users.iterrows():
                uid = short_id(u[idc])
                if uid in self.by_id:
                    continue
                label = by_user.get(u.get(unc, "")) or by_prof.get(u.get(prc, ""))
                if label:
                    self.by_id[uid] = label
        self.systems = cfg.get("systems") or {}
        auto = set(cfg.get("automated_writers") or [])
        self.automated = lambda label: label in auto or str(label).startswith("agent:")

    def label(self, user_id):
        return self.by_id.get(short_id(user_id), self.default)

    def mapped(self, user_id):
        return short_id(user_id) in self.by_id

    def system(self, label):
        if label in self.systems:
            return self.systems[label]
        return "agent" if str(label).startswith("agent:") else "unknown"


def load_records(cfg):
    out = {}
    for obj, spec in cfg["records"].items():
        df = read_csv(cfg["_resolve"](spec["file"]),
                      columns=[spec.get("id_column", "Id")] + ([spec["match_key"]] if spec.get("match_key") else []))
        df["_id"] = df[spec.get("id_column", "Id")].map(short_id)
        key = spec.get("match_key")
        if key:
            k = df[key].map(normalize_key)
            counts = k[k != ""].value_counts()
            df["_matches"] = [int(counts.get(v, 1)) if v else 1 for v in k]
        else:
            df["_matches"] = 1
        out[obj] = (df.set_index("_id", drop=False), spec)
    return out


def load_history(cfg, writers, field_map):
    tracked = {(f["object"], f["column"].lower()): name for name, f in field_map.items()}
    frames = []
    for spec in cfg.get("history") or []:
        h = read_csv(cfg["_resolve"](spec["file"]), empty_ok=True,
                     columns=[spec.get(k, d) for k, d in (("record_column", "ParentId"), ("field_column", "Field"),
                                                           ("user_column", "CreatedById"),
                                                           ("date_column", "CreatedDate"))])
        if h.empty:                    # no history yet, for example tracking was just turned on
            continue
        df = pd.DataFrame({
            "object": spec["object"],
            "record": h[spec.get("record_column", "ParentId")].map(short_id),
            "column": h[spec.get("field_column", "Field")].str.lower(),
            "writer": h[spec.get("user_column", "CreatedById")].map(writers.label),
            "mapped": h[spec.get("user_column", "CreatedById")].map(writers.mapped),
            "at": to_ts(h[spec.get("date_column", "CreatedDate")]),
        })
        df["field"] = [tracked.get((o, c)) for o, c in zip(df["object"], df["column"])]
        frames.append(df[df["field"].notna() & df["at"].notna()])
    if not frames:
        return pd.DataFrame(columns=["object", "record", "column", "writer", "mapped", "at", "field"])
    return pd.concat(frames, ignore_index=True).sort_values(["field", "record", "at"])


# ---------------------------------------------------------------- per-field state

def field_state(cfg, records, history, writers, field_map, now):
    """For every contract field and record: value, last writer, freshness and its source."""
    last = history.groupby(["field", "record"]).tail(1)
    no_hist = cfg.get("no_history_writer", "unknown")
    state = {}
    for name, f in field_map.items():
        df, spec = records[f["object"]]
        sync_col = (spec.get("sync_columns") or {}).get(f["column"])
        lm_col = spec.get("last_modified_column")
        missing = [c for c in (f["column"], sync_col, lm_col) if c and c not in df.columns]
        if missing:
            raise SystemExit(f"{name}: {', '.join(missing)} not found in {spec['file']}. "
                             "Export it, or remove it from the config.")
        st = pd.DataFrame({"record": df["_id"].values, "value": df[f["column"]].values,
                           "matches": df["_matches"].astype(int).values})
        lh = last[last["field"] == name][["record", "writer", "at"]]
        st = st.merge(lh, on="record", how="left")
        st["has_history"] = st["writer"].notna()
        st["writer"] = st["writer"].fillna(no_hist)
        empty = pd.Series(pd.NaT, index=st.index, dtype="datetime64[ns, UTC]")
        sync = to_ts(pd.Series(df[sync_col].values)) if sync_col else empty
        lm = to_ts(pd.Series(df[lm_col].values)) if lm_col else empty
        st["at"] = pd.to_datetime(st["at"], utc=True)
        st["fresh"] = pd.to_datetime(sync.combine_first(st["at"]).combine_first(lm), utc=True)
        st["fresh_source"] = "unknown"
        st.loc[lm.notna(), "fresh_source"] = "record last modified"
        st.loc[st["at"].notna(), "fresh_source"] = "last change in field history"
        st.loc[sync.notna(), "fresh_source"] = "sync timestamp"
        st["age_hours"] = (now - st["fresh"]).dt.total_seconds() / 3600
        state[name] = st.drop(columns=["at"]).set_index("record")
    return state


def read_for(name, rid, state, writers):
    st = state[name]
    if rid is None or rid not in st.index:
        return FieldRead(name, "unknown", EPOCH, 0, "unknown"), "", "unknown"
    r = st.loc[rid]
    fresh = r["fresh"].to_pydatetime() if pd.notna(r["fresh"]) else EPOCH
    return FieldRead(name, writers.system(r["writer"]), fresh, int(r["matches"]), r["writer"]), \
        r["value"], r["fresh_source"]


# ---------------------------------------------------------------- metrics

def pct(n, d, min_n):
    if d == 0 or d < min_n:
        return None
    return round(100.0 * n / d, 1)


def field_profiles(state, contract, field_map, min_n):
    out = {}
    for name, st in state.items():
        rule = contract["fields"].get(name, {})
        n = len(st)
        has_hist = st["fresh_source"] != "unknown"
        with_writer = st[st["has_history"]]
        allowed = set(rule.get("allowed_writers", []))
        sla = rule.get("freshness_sla_hours")
        known_age = st["age_hours"].dropna()
        mix = with_writer["writer"].value_counts()
        out[name] = {
            "object": field_map[name]["object"], "records": n, "tier": rule.get("tier"),
            "blank_pct": pct((st["value"] == "").sum(), n, min_n),
            "no_field_history_pct": pct((~st["has_history"]).sum(), n, min_n),
            "writer_mix_pct": {w: pct(c, len(with_writer), min_n) for w, c in mix.items()},
            "last_writer_not_allowed_pct": pct((~with_writer["writer"].isin(allowed)).sum(),
                                               len(with_writer), min_n),
            "last_written_by_agent_pct": pct(with_writer["writer"].str.startswith("agent:").sum(),
                                             len(with_writer), min_n),
            "freshness_sla_hours": sla,
            "over_sla_pct": pct((known_age > sla).sum(), len(known_age), min_n) if sla else None,
            "median_age_days": round(float(known_age.median()) / 24, 1) if len(known_age) else None,
            "freshness_source_pct": {s: pct(c, n, min_n)
                                     for s, c in st["fresh_source"].value_counts().items()},
            "duplicate_pct": pct((st["matches"] > 1).sum(), n, min_n),
            "freshness_known_pct": pct(has_hist.sum(), n, min_n),
        }
    return out


def shadow_gate(cfg, records, state, writers, contract, min_n, now):
    out = {}
    for action, spec in (cfg.get("actions") or {}).items():
        prim_df, _ = records[spec["primary"]]
        rows = prim_df.query(spec["filter"]) if spec.get("filter") else prim_df
        lookups = spec.get("lookups") or {}
        decisions, check_hits, field_hits = [], {}, {}
        for rid, row in rows.iterrows():
            reads, blanks = [], []
            for name in spec["inputs"]:
                if name not in cfg["fields"]:
                    raise SystemExit(f"Action '{action}' uses {name}, which is not mapped under 'fields'.")
                obj = cfg["fields"][name]["object"]
                target = rid if obj == spec["primary"] else short_id(row.get(lookups.get(obj, ""), "")) or None
                fr, value, _ = read_for(name, target, state, writers)
                reads.append(fr)
                if value == "" and fr.matches > 0:
                    blanks.append(f"{name}: value is blank")
            decision, reasons = gate(reads, contract, now=now.to_pydatetime())
            if blanks:
                decision, reasons = "ask", blanks + (reasons if decision == "ask" else [])
            decisions.append(decision)
            seen = set()
            for reason in reasons:
                field = reason.split(":")[0]
                check = next((label for key, label in CHECKS if key in reason), "other")
                seen.add(check)
                field_hits.setdefault(field, {}).setdefault(check, set()).add(rid)
            for c in seen:
                check_hits[c] = check_hits.get(c, 0) + 1
        n = len(decisions)
        tiers = [contract["fields"].get(f, {}).get("tier") for f in spec["inputs"]]
        out[action] = {
            "actions_evaluated": n,
            "decision_pct": {d: pct(decisions.count(d), n, min_n) for d in ("proceed", "draft", "ask")},
            "failed_check_pct": {c: pct(k, n, min_n) for c, k in
                                 sorted(check_hits.items(), key=lambda kv: (-kv[1], kv[0]))},
            "failed_check_by_field_pct": {f: {c: pct(len(ids), n, min_n) for c, ids in checks.items()}
                                          for f, checks in field_hits.items()},
            "inputs_action_grade_by_contract": f"{tiers.count('action')} of {len(tiers)}",
        }
    return out


def reversals(history, writers, cfg, min_n):
    window = pd.Timedelta(days=cfg.get("reversal_window_days", 30))
    if history.empty:
        return {}
    h = history.copy()
    grp = h.groupby(["field", "record"])
    h["next_writer"] = grp["writer"].shift(-1)
    h["next_at"] = grp["at"].shift(-1)
    h["next_mapped"] = grp["mapped"].shift(-1)

    def kind(w, mapped):             # unmapped users are neither people nor automation
        if pd.isna(w):
            return None
        if not mapped:
            return "unmapped"
        return "automated" if writers.automated(w) else "person"

    h["next_kind"] = [kind(w, m) for w, m in zip(h["next_writer"], h["next_mapped"])]
    auto = h[h["writer"].map(writers.automated)]
    out = {}
    for (field, writer), g in auto.groupby(["field", "writer"]):
        soon = (g["next_at"] - g["at"]) <= window
        rev = (g["next_kind"] == "person") & soon
        unmapped = (g["next_kind"] == "unmapped") & soon
        out.setdefault(field, {})[writer] = {
            "writes": len(g), "reversed_pct": pct(int(rev.sum()), len(g), min_n),
            "changed_by_unmapped_user_pct": pct(int(unmapped.sum()), len(g), min_n)}
    return out


def source_mismatch(cfg, records, state, field_map, contract, min_n):
    out = {}
    for name, spec in (cfg.get("source_of_truth") or {}).items():
        src = read_csv(cfg["_resolve"](spec["file"]), columns=[spec["key_column"], spec["value_column"]])
        df, _ = records[field_map[name]["object"]]
        crm_key = spec.get("crm_key_column", "_id")
        keys = df[crm_key].map(short_id) if crm_key != "_id" else df["_id"]
        src_vals = dict(zip(src[spec["key_column"]].map(short_id), src[spec["value_column"]]))
        st = state[name]
        sla = contract["fields"].get(name, {}).get("freshness_sla_hours")
        kind, tol = spec.get("type", "text"), float(spec.get("tolerance", 0))
        compared = mism = stale_n = stale_mism = fresh_n = fresh_mism = 0
        for rid, key in zip(df["_id"], keys):
            if key not in src_vals:
                continue
            a, b = st.at[rid, "value"], src_vals[key]
            if kind == "number":
                try:
                    a, b = float(a), float(b)
                    bad = abs(a - b) > tol * max(abs(b), 1e-9)
                except ValueError:
                    bad = a != b
            elif kind == "date":
                bad = str(a)[:10] != str(b)[:10]
            else:
                bad = str(a).strip() != str(b).strip()
            compared += 1
            mism += bad
            age = st.at[rid, "age_hours"]
            if sla and pd.notna(age):
                if age > sla:
                    stale_n += 1
                    stale_mism += bad
                else:
                    fresh_n += 1
                    fresh_mism += bad
        out[name] = {"records_compared": compared,
                     "coverage_pct": pct(compared, len(df), min_n),
                     "mismatch_pct": pct(mism, compared, min_n),
                     "mismatch_when_over_sla_pct": pct(stale_mism, stale_n, min_n),
                     "mismatch_when_within_sla_pct": pct(fresh_mism, fresh_n, min_n)}
    return out


# ---------------------------------------------------------------- report

def fmt(v, unit="%"):
    if v is None:
        return "suppressed (too few)"
    return f"{v}{unit}" if isinstance(v, (int, float)) else str(v)


def main_source(p):
    src = {k: v for k, v in p["freshness_source_pct"].items() if v is not None}
    if not src:
        return "suppressed (too few)"
    k = max(src, key=src.get)
    return f"{k} ({src[k]}%)"


def caveats(profiles, history, revs):
    notes = []
    for name, p in profiles.items():
        src = p["freshness_source_pct"]
        share = src.get("last change in field history") or 0
        if share >= 10:
            notes.append(f"{name}: for {share}% of records, freshness comes from the last change, not a "
                         "sync timestamp, so values that were re-verified but unchanged look stale. "
                         "A sync timestamp on every run fixes this.")
        lm_share = src.get("record last modified") or 0
        if lm_share >= 10:
            notes.append(f"{name}: for {lm_share}% of records, freshness comes from the record's last "
                         "modified date, which an edit to any field resets, so the over-SLA share can be "
                         "wrong in either direction. A sync timestamp or field history fixes this.")
        if (p["no_field_history_pct"] or 0) > 50:
            notes.append(f"{name}: no field history for most records (tracking off, or never changed "
                         "since it was enabled), so writer checks fail by default.")
    if history.empty:
        notes.append("No field history was loaded; writer and reversal metrics are unavailable.")
    if any(r["changed_by_unmapped_user_pct"] for ws in revs.values() for r in ws.values()):
        notes.append("Some automated writes were next changed by users not mapped under 'writers'. "
                     "They are reported separately, not as reversals; map them to count them correctly.")
    return notes


def write_report(out_dir, metrics):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
    L = [f"# CRM field trust audit", "",
         f"Generated {metrics['generated_at']}. Aggregates only; groups smaller than "
         f"{metrics['min_group_size']} are suppressed.", ""]
    for action, a in metrics["shadow_gate"].items():
        L += [f"## Shadow-mode gate: {action}", "",
              f"{a['actions_evaluated']} actions evaluated. Inputs action-grade by contract: "
              f"{a['inputs_action_grade_by_contract']}.", "",
              "| Decision | Share |", "| --- | --- |"]
        L += [f"| {d} | {fmt(v)} |" for d, v in a["decision_pct"].items()]
        L += ["", "| Failed check (any input) | Share of actions |", "| --- | --- |"]
        L += [f"| {c} | {fmt(v)} |" for c, v in a["failed_check_pct"].items()]
        L += ["", "| Field | Failed checks (share of actions) |", "| --- | --- |"]
        L += [f"| {f} | " + ", ".join(f"{c} {fmt(v)}" for c, v in cs.items()) + " |"
              for f, cs in a["failed_check_by_field_pct"].items()]
        L.append("")
    L += ["## Field profiles", "",
          "| Field | Tier | Blank | Over SLA | Median age (days) | Freshness measured from | "
          "Last writer not allowed | Last written by agent | Duplicates | No field history |",
          "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for name, p in metrics["field_profiles"].items():
        L.append(f"| {name} | {p['tier']} | {fmt(p['blank_pct'])} | {fmt(p['over_sla_pct'])} | "
                 f"{fmt(p['median_age_days'], '')} | {main_source(p)} | {fmt(p['last_writer_not_allowed_pct'])} | "
                 f"{fmt(p['last_written_by_agent_pct'])} | {fmt(p['duplicate_pct'])} | "
                 f"{fmt(p['no_field_history_pct'])} |")
    L += ["", "Writer mix (last writer, among records with history):", ""]
    for name, p in metrics["field_profiles"].items():
        L.append(f"- {name}: " + (", ".join(f"{w} {fmt(v)}" for w, v in p["writer_mix_pct"].items())
                                  or "no field history"))
    if metrics["source_mismatch"]:
        L += ["", "## Mismatch against the source of truth", "",
              "| Field | Compared | Coverage | Mismatch | Mismatch when over SLA | Mismatch within SLA |",
              "| --- | --- | --- | --- | --- | --- |"]
        for name, m in metrics["source_mismatch"].items():
            L.append(f"| {name} | {m['records_compared']} | {fmt(m['coverage_pct'])} | "
                     f"{fmt(m['mismatch_pct'])} | {fmt(m['mismatch_when_over_sla_pct'])} | "
                     f"{fmt(m['mismatch_when_within_sla_pct'])} |")
    if metrics["reversals"]:
        L += ["", f"## Reversal rate (a person changed it within {metrics['reversal_window_days']} days)", "",
              "| Field | Automated writer | Writes | Reversed | Changed by an unmapped user |",
              "| --- | --- | --- | --- | --- |"]
        for field, ws in metrics["reversals"].items():
            for w, r in ws.items():
                L.append(f"| {field} | {w} | {r['writes']} | {fmt(r['reversed_pct'])} | "
                         f"{fmt(r['changed_by_unmapped_user_pct'])} |")
    if metrics["caveats"]:
        L += ["", "## Caveats", ""] + [f"- {c}" for c in metrics["caveats"]]
    (out_dir / "report.md").write_text("\n".join(L) + "\n")
    return "\n".join(L)


def run(config_path, out_dir, now=None):
    cfg = load_config(config_path)
    if now is None and cfg.get("as_of"):
        now = pd.Timestamp(cfg["as_of"])
        now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    now = now or pd.Timestamp.now(tz="UTC")
    contract = cfg["contract_data"]
    field_map = cfg["fields"]
    min_n = int(cfg.get("min_group_size", 20))
    writers = Writers(cfg)
    records = load_records(cfg)
    history = load_history(cfg, writers, field_map)
    state = field_state(cfg, records, history, writers, field_map, now)
    profiles = field_profiles(state, contract, field_map, min_n)
    revs = reversals(history, writers, cfg, min_n)
    metrics = {
        "generated_at": now.isoformat(), "min_group_size": min_n,
        "reversal_window_days": cfg.get("reversal_window_days", 30),
        "shadow_gate": shadow_gate(cfg, records, state, writers, contract, min_n, now),
        "field_profiles": profiles,
        "source_mismatch": source_mismatch(cfg, records, state, field_map, contract, min_n),
        "reversals": revs,
        "caveats": caveats(profiles, history, revs),
    }
    text = write_report(Path(out_dir), metrics)
    return metrics, text


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", default="reports/latest")
    ap.add_argument("--as-of", help="evaluate freshness as of this ISO timestamp (default: now)")
    args = ap.parse_args()
    as_of = None
    if args.as_of:
        as_of = pd.Timestamp(args.as_of)
        as_of = as_of.tz_localize("UTC") if as_of.tzinfo is None else as_of.tz_convert("UTC")
    _, report = run(args.config, args.out, as_of)
    print(report)
    print(f"\nWrote {args.out}/report.md and {args.out}/metrics.json")
