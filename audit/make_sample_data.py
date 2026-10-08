"""Create a fake CRM export (Salesforce-shaped CSVs) so the audit runs out of the box.

Every record, user and value here is invented. The defects are planted on purpose:
late syncs, reps typing over synced ARR, duplicate accounts, agent writes that people
later undo, and renewal dates without a sync timestamp.

    python audit/make_sample_data.py      -> audit/sample_data/*.csv
"""
import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).parent / "sample_data"
AS_OF = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
rng = random.Random(7)


def sid(prefix, n):
    return f"{prefix}{n:012d}AAA"          # 18 characters, Salesforce-shaped


def ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def ago(**kw):
    return AS_OF - timedelta(**kw)


def write(name, header, rows):
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / name, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main():
    users = [(sid("005", 1), "billing.sync@example.com", "Integration User"),
             (sid("005", 2), "contract.sync@example.com", "Integration User"),
             (sid("005", 3), "health.model@example.com", "Integration User"),
             (sid("005", 4), "agent.renewal@example.com", "Integration User")]
    reps = [(sid("005", 100 + i), f"rep{i}@example.com", "Sales User") for i in range(20)]
    users += reps
    BILLING, CONTRACT, MODEL, AGENT = (u[0] for u in users[:4])
    rep = lambda: rng.choice(reps)[0]
    write("users.csv", ["Id", "Username", "Profile.Name"], users)

    accounts, acct_hist, billing = [], [], []
    domains = [f"customer{i}.example" for i in range(1, 1501)]
    for i, domain in enumerate(domains, start=1):
        aid = sid("001", i)
        true_arr = rng.randrange(12, 400) * 1000
        crm_arr, synced = true_arr, ago(hours=rng.uniform(1, 30))
        acct_hist.append((aid, "ARR__c", "", true_arr, BILLING, ts(ago(days=rng.uniform(120, 400)))))
        roll = rng.random()
        if roll < 0.10:                                   # sync job missed its window
            synced = ago(days=rng.uniform(3, 15))
            if rng.random() < 0.4:                        # ...and billing changed meanwhile
                true_arr = int(true_arr * rng.choice([0.8, 1.1, 1.25]))
        elif roll < 0.13:                                 # a rep typed over the synced value
            crm_arr = int(true_arr * rng.choice([0.9, 1.15]))
            acct_hist.append((aid, "ARR__c", true_arr, crm_arr, rep(), ts(ago(days=rng.uniform(1, 30)))))
        elif roll < 0.16:                                 # an agent wrote ARR (not allowed)
            when = ago(days=rng.uniform(12, 60))
            acct_hist.append((aid, "ARR__c", true_arr, true_arr, AGENT, ts(when)))
            if rng.random() < 0.3:                        # ...and a person undid it
                acct_hist.append((aid, "ARR__c", true_arr, true_arr, rep(),
                                  ts(when + timedelta(days=rng.uniform(1, 10)))))
        score = rng.randrange(20, 100)
        acct_hist.append((aid, "Health_Score__c", "", score, MODEL, ts(ago(days=rng.uniform(0, 12)))))
        accounts.append([aid, f"Customer {i}", f"https://www.{domain}/", crm_arr, ts(synced),
                         score, ts(ago(hours=rng.uniform(1, 72)))])
        billing.append((aid, true_arr))
    for j in range(60):                                   # duplicate accounts, same website
        src = rng.choice(accounts)
        aid = sid("001", 5000 + j)
        accounts.append([aid, src[1] + " (dup)", src[2].replace("https://www.", "http://"),
                         src[3], src[4], src[5], src[6]])
    write("accounts.csv", ["Id", "Name", "Website", "ARR__c", "ARR_Synced_At__c", "Health_Score__c",
                           "LastModifiedDate"], accounts)
    write("account_history.csv", ["AccountId", "Field", "OldValue", "NewValue", "CreatedById",
                                  "CreatedDate"], acct_hist)
    write("billing_arr.csv", ["account_id", "arr"], billing)

    opps, opp_hist = [], []
    for k in range(1, 2201):
        oid = sid("006", k)
        acct = rng.choice(accounts)[0]
        renewal = rng.random() < 0.6
        date = (AS_OF + timedelta(days=rng.randrange(10, 200))).strftime("%Y-%m-%d")
        writer = rep() if rng.random() < 0.04 else CONTRACT
        changed = ago(days=rng.uniform(5, 300))
        opp_hist.append((oid, "Renewal_Date__c", "", date, writer, ts(changed)))
        synced = "" if rng.random() < 0.2 else ts(ago(hours=rng.uniform(1, 30) if rng.random() < 0.95
                                                      else rng.uniform(48, 400)))
        blank = rng.random() < 0.02
        opps.append([oid, acct, "Renewal" if renewal else "New Business", "" if blank else date,
                     synced, ts(ago(hours=rng.uniform(1, 72)))])
    write("opportunities.csv", ["Id", "AccountId", "Type", "Renewal_Date__c", "Renewal_Synced_At__c",
                                "LastModifiedDate"], opps)
    write("opportunity_history.csv", ["OpportunityId", "Field", "OldValue", "NewValue", "CreatedById",
                                      "CreatedDate"], opp_hist)
    print(f"Wrote sample export to {OUT}")


if __name__ == "__main__":
    main()
