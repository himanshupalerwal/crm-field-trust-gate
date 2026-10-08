"""End to end on a real org: an Agentforce agent calls the gated actions, and the records prove it.

It deploys the gate and runs its Apex tests, sets up users who write the CRM through the REST API
the way integrations and people do (so field history records real writers), seeds renewals under
private sharing, publishes an Agent Script agent with the three gated actions, and talks to it with
live actions. Every step is checked against the records, not the agent's wording. Last, it checks
that a user with the agent's permission set can neither confirm a value nor approve a draft.

    python salesforce/scripts/e2e.py --dev-hub <alias>               # new 1-day scratch org, deleted after
    python salesforce/scripts/e2e.py --dev-hub <alias> --keep        # the same, kept for a look around
    python salesforce/scripts/e2e.py --org <alias>                   # an org made from e2e/config/scratch-def.json

Needs the Salesforce CLI with the agent commands, and a Dev Hub that can create orgs with the
Einstein1AIPlatform feature. The agent and its test files live in a separate project
(salesforce/e2e), so nothing is generated in force-app. Uses fake data only.
"""
import argparse
import json
import random
import shutil
import string
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
E2E_PROJECT = PROJECT / "e2e"
E2E = E2E_PROJECT / "source" / "main" / "default"
API = json.loads((PROJECT / "sfdx-project.json").read_text())["sourceApiVersion"]
FAILURES = []
TMP = None


def sf(*args, ok=True, cwd=PROJECT, timeout=1800):
    try:
        out = subprocess.run(["sf", *args, "--json"], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        if ok:
            raise SystemExit(f"sf {' '.join(args[:3])}: no answer in {timeout // 60} minutes")
        return {"status": 1, "message": f"no answer in {timeout // 60} minutes"}
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        if ok:
            raise SystemExit(f"sf {' '.join(args[:3])}: no JSON output\n{out.stderr[-2000:]}")
        return {"status": 1, "message": out.stderr[-500:]}
    if ok and data.get("status") != 0:
        raise SystemExit(f"sf {' '.join(args[:3])} failed: {data.get('message') or data}")
    return data


def temp_file(name, text):
    path = Path(TMP) / name
    path.write_text(text)
    return str(path)


def apex(org, code):
    """Run anonymous Apex as the admin and return what it logged after 'E2E ', as JSON."""
    r = sf("apex", "run", "--file", temp_file("run.apex", code), "--target-org", org)["result"]
    if not r.get("success"):
        raise SystemExit(f"Apex failed: {r.get('compileProblem') or r.get('exceptionMessage')}")
    for line in (r.get("logs") or "").splitlines():
        if "|USER_DEBUG|" in line and "E2E " in line:
            return json.loads(line.split("E2E ", 1)[1])
    return {}


def rest(org, path, method="GET", body=None):
    """A REST API call as that org's user, the way an integration or a flow calls: (HTTP status, body)."""
    args = ["api", "request", "rest", f"/services/data/v{API}/{path}", "--method", method, "--target-org", org]
    if body is not None:
        args += ["--body", json.dumps(body)]
    r = sf(*args, ok=False).get("result") or {}
    return r.get("statusCode"), r.get("body")


def update(org, sobject, record_id, values):
    """Update one record as that org's user, through the REST API, and stop if it fails."""
    status, body = rest(org, f"sobjects/{sobject}/{record_id}", "PATCH", values)
    if status != 204:
        raise SystemExit(f"updating {sobject} {record_id} as {org} failed: {status} {body}")


def errors(body):
    return [e.get("errorCode") for e in body] if isinstance(body, list) else []


def query(org, soql):
    return sf("data", "query", "--query", soql, "--target-org", org)["result"]["records"]


def check(name, condition, detail=""):
    print(("  PASS " if condition else "  FAIL ") + name + ("" if condition else f"  ({detail})"))
    if not condition:
        FAILURES.append(name)


def step(text):
    print(f"\n== {text}")


def create_user(org, key, profile, permsets, suffix):
    username = f"fieldtrust.{key}.{suffix}@example.com"
    definition = {"Username": username, "LastName": key.title(), "FirstName": "Field Trust",
                  "Email": "noreply@example.com", "Alias": f"ft{key}"[:8], "TimeZoneSidKey": "America/Los_Angeles",
                  "LocaleSidKey": "en_US", "EmailEncodingKey": "UTF-8", "LanguageLocaleKey": "en_US",
                  "profileName": profile, "permsets": permsets, "generatePassword": False}
    alias = f"ft-{key}-{suffix}"
    sf("org", "create", "user", "--definition-file", temp_file(f"{key}.json", json.dumps(definition)),
       "--target-org", org, "--set-alias", alias)
    return username, alias


def writer_rows(rows):
    folder = E2E / "customMetadata"
    folder.mkdir(parents=True, exist_ok=True)
    for dev, (match_value, label, system) in rows.items():
        values = {"Match_On__c": "Username", "Match_Value__c": match_value, "Writer_Label__c": label, "System__c": system}
        body = "".join(f'\n    <values>\n        <field>{k}</field>\n        <value xsi:type="xsd:string">{v}</value>\n    </values>'
                       for k, v in values.items())
        (folder / f"Field_Trust_Writer.{dev}.md-meta.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<CustomMetadata xmlns="http://soap.sforce.com/2006/04/metadata" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">\n'
            f"    <label>{dev.replace('_', ' ')}</label>\n    <protected>false</protected>{body}\n</CustomMetadata>\n")


def wait_for_private_sharing(org, minutes=10):
    """Salesforce applies a sharing default change in the background; wait until it has."""
    for _ in range(minutes * 4):
        sharing = query(org, "SELECT DefaultAccountAccess, DefaultOpportunityAccess FROM Organization")[0]
        if (sharing["DefaultAccountAccess"], sharing["DefaultOpportunityAccess"]) == ("None", "None"):
            return
        time.sleep(15)
    raise SystemExit(f"Account and Opportunity sharing did not become Private: {sharing}")


def remove_generated(bundle):
    """Delete only what this run generated in the e2e project, then any folders it left empty."""
    for kind, pattern in (("customMetadata", "Field_Trust_Writer.E2E_*"), ("aiAuthoringBundles", bundle),
                          ("bots", f"{bundle}*"), ("genAiPlannerBundles", f"{bundle}*")):
        for path in (E2E / kind).glob(pattern):
            shutil.rmtree(path) if path.is_dir() else path.unlink()
        if (E2E / kind).is_dir() and not any((E2E / kind).iterdir()):
            (E2E / kind).rmdir()


def talk(org, bundle, session, utterance):
    r = sf("agent", "preview", "send", "--target-org", org, "--authoring-bundle", bundle,
           "--session-id", session, "--utterance", utterance, cwd=E2E_PROJECT)["result"]
    reply = " ".join(m.get("message", "") for m in r.get("messages", []) if m.get("message"))
    print(f"  user:  {utterance}\n  agent: {reply[:400]}")
    return reply


def main():
    global TMP
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dev-hub", help="create a 1-day scratch org from this Dev Hub")
    ap.add_argument("--org", help="use this org instead")
    ap.add_argument("--keep", action="store_true", help="keep the scratch org afterwards")
    args = ap.parse_args()
    if not (args.dev_hub or args.org):
        ap.error("give --dev-hub or --org")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    TMP = tempfile.mkdtemp(prefix="fieldtrust-e2e-")
    org = args.org
    try:
        if not org:
            step("Creating a scratch org with Agentforce and private sharing")
            org = f"fieldtrust-e2e-{suffix}"
            sf("org", "create", "scratch", "--definition-file", "config/scratch-def.json", "--alias", org,
               "--duration-days", "1", "--target-dev-hub", args.dev_hub, "--wait", "20", cwd=E2E_PROJECT)
        run(org, suffix)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
        if org and not args.org and not args.keep:
            sf("org", "delete", "scratch", "--target-org", org, "--no-prompt", ok=False)
            print(f"\nDeleted scratch org {org}")
    print(f"\n{'ALL PASSED' if not FAILURES else f'{len(FAILURES)} FAILED: ' + ', '.join(FAILURES)}")
    sys.exit(1 if FAILURES else 0)


def run(org, suffix):
    step("Deploying the field trust gate, private sharing, and running the Apex tests")
    sf("project", "deploy", "start", "--source-dir", "force-app", "--target-org", org, "--wait", "20")
    sf("project", "deploy", "start", "--source-dir", "source", "--target-org", org, "--wait", "20", "--ignore-conflicts",
       cwd=E2E_PROJECT)
    wait_for_private_sharing(org)
    tests = sf("apex", "run", "test", "--test-level", "RunLocalTests", "--code-coverage", "--wait", "30",
               "--target-org", org, ok=False).get("result") or {}
    summary = tests.get("summary") or {}
    failed = [f"{t.get('ApexClass', {}).get('Name')}.{t.get('MethodName')}: {t.get('Message')}"
              for t in tests.get("tests") or [] if t.get("Outcome") != "Pass"]
    print(f"  {summary.get('testsRan')} tests, {summary.get('passing')} passing, "
          f"coverage {summary.get('testRunCoverage')} (org-wide {summary.get('orgWideCoverage')})")
    check("the Apex tests pass", summary.get("outcome") == "Passed" and not failed, failed or summary)
    if FAILURES:
        return   # the gate itself is broken: nothing to learn from talking to an agent

    admin = sf("org", "display", "--target-org", org)["result"]["username"]
    # the admin reviews drafts: the shipped Reviewer row maps the FieldTrust_Reviewer set to sales_rep
    # (an assignment the org already has fails harmlessly; step 5 checks the admin can approve)
    for permset in ("FieldTrust_Reviewer", "AgentforceServiceAgentBuilder", "AgentPlatformBuilder", "CopilotSalesforceAdmin"):
        sf("org", "assign", "permset", "--target-org", org, "--name", permset, ok=False, timeout=300)

    step("Creating the writers: billing and contract integrations, a sales rep and the renewal agent")
    billing, billing_alias = create_user(org, "billing", "Standard Platform User", ["FieldTrust_E2E_Writers"], suffix)
    contracts, contracts_alias = create_user(org, "contract", "Standard User", ["FieldTrust_E2E_Writers"], suffix)
    rep, rep_alias = create_user(org, "rep", "Standard Platform User", ["FieldTrust_E2E_Writers"], suffix)
    agent, _ = create_user(org, "agent", "Einstein Agent User", ["AgentforceServiceAgentUser", "FieldTrust_Agent"], suffix)
    users = {u["Username"]: u["Id"] for u in query(
        org, f"SELECT Id, Username FROM User WHERE Username IN ('{admin}', '{billing}', '{contracts}', '{rep}', '{agent}')")}
    bundle = f"Field_Trust_Renewals_{suffix}"
    try:
        # integrations and the rep are mapped by username; the agent and the reviewer by the shipped rows
        writer_rows({"E2E_Billing": (billing, "billing_sync", "billing"),
                     "E2E_Contracts": (contracts, "contract_sync", "contracts"), "E2E_Rep": (rep, "sales_rep", "crm")})
        sf("project", "deploy", "start", "--source-dir", "source", "--target-org", org, "--wait", "20", "--ignore-conflicts",
           cwd=E2E_PROJECT)
        ids = seed(org, {"billing": users[billing], "contracts": users[contracts], "rep": users[rep], "agent": users[agent]},
                   billing_alias, contracts_alias, rep_alias, rep, contracts)
        publish_and_talk(org, bundle, ids, users[admin], agent)
        agent_permissions_hold(org, contracts, contracts_alias, users[contracts], ids)
    finally:   # remove what this run generated, including the agent metadata publishing pulls back
        remove_generated(bundle)


def seed(org, user_ids, billing_alias, contracts_alias, rep_alias, rep, contracts):
    step("Seeding renewals under private sharing, written as those users so field history records them")
    ids = apex(org, f"""
        Account alpha = new Account(Name = 'Alpha Example', Website = 'https://www.alpha.example/');
        Account beta = new Account(Name = 'Beta Example', Website = 'https://www.beta.example/');
        // a duplicate the rep owns and the agent cannot see; it still counts against the match key
        Account betaDup = new Account(Name = 'Beta Example (duplicate)', Website = 'http://beta.example', OwnerId = '{user_ids["rep"]}');
        insert new List<Account>{{ alpha, beta, betaDup }};
        Opportunity oppA2 = new Opportunity(Name = 'Alpha renewal, second product', AccountId = alpha.Id, Type = 'Renewal',
            StageName = 'Prospecting', CloseDate = Date.today().addDays(60), Amount = 40000, Renewal_Date__c = Date.today().addDays(90));
        Opportunity oppB = new Opportunity(Name = 'Beta renewal', AccountId = beta.Id, Type = 'Renewal', StageName = 'Prospecting',
            CloseDate = Date.today().addDays(60), Amount = 100000, Renewal_Date__c = Date.today().addDays(80));
        insert new List<Opportunity>{{ oppA2, oppB }};
        Contact c = new Contact(LastName = 'Example Buyer', AccountId = alpha.Id);
        insert c;
        // manual shares: the agent and each writer get the records they work on, nothing more
        List<AccountShare> shares = new List<AccountShare>();
        for (List<Object> s : new List<List<Object>>{{
                new List<Object>{{ alpha.Id, '{user_ids["agent"]}', 'Edit', 'Edit' }},
                new List<Object>{{ beta.Id, '{user_ids["agent"]}', 'Edit', 'Edit' }},
                new List<Object>{{ alpha.Id, '{user_ids["billing"]}', 'Edit', 'None' }},
                new List<Object>{{ beta.Id, '{user_ids["billing"]}', 'Edit', 'None' }},
                new List<Object>{{ alpha.Id, '{user_ids["contracts"]}', 'Read', 'Edit' }},
                new List<Object>{{ beta.Id, '{user_ids["contracts"]}', 'Read', 'Edit' }},
                new List<Object>{{ beta.Id, '{user_ids["rep"]}', 'Edit', 'None' }} }}) {{
            shares.add(new AccountShare(AccountId = (Id) s[0], UserOrGroupId = (Id) s[1], AccountAccessLevel = (String) s[2],
                OpportunityAccessLevel = (String) s[3], CaseAccessLevel = 'None'));
        }}
        insert shares;
        System.debug(LoggingLevel.ERROR, 'E2E ' + JSON.serialize(new Map<String, Id>{{
            'alpha' => alpha.Id, 'beta' => beta.Id, 'betaDup' => betaDup.Id, 'oppA2' => oppA2.Id,
            'oppB' => oppB.Id, 'contact' => c.Id }}));
    """)
    # the integrations and the rep write through the REST API, as each of them, so field history records them
    now = datetime.now(timezone.utc)
    stamp = lambda hours_ago: (now - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # the contract sync creates one renewal with its date set, and corrects the other's date
    status, body = rest(contracts_alias, "sobjects/Opportunity", "POST", {
        "Name": "Alpha renewal", "AccountId": ids["alpha"], "Type": "Renewal", "StageName": "Prospecting",
        "CloseDate": (now.date() + timedelta(days=60)).isoformat(), "Amount": 100000,
        "Renewal_Date__c": (now.date() + timedelta(days=91)).isoformat(), "Renewal_Synced_At__c": stamp(0)})
    if status != 201:
        raise SystemExit(f"the contract sync could not create a renewal: {status} {body}")
    ids["oppA"] = body["id"]
    update(contracts_alias, "Opportunity", ids["oppB"],
           {"Renewal_Date__c": (now.date() + timedelta(days=81)).isoformat(), "Renewal_Synced_At__c": stamp(0)})
    update(billing_alias, "Account", ids["alpha"], {"ARR__c": 120000, "ARR_Synced_At__c": stamp(0)})
    update(billing_alias, "Account", ids["beta"], {"ARR__c": 95000, "ARR_Synced_At__c": stamp(72)})
    update(rep_alias, "Account", ids["beta"], {"ARR__c": 99000})   # a rep types over billing
    history = query(org, f"SELECT CreatedBy.Username FROM AccountHistory WHERE AccountId = '{ids['beta']}' "
                         "AND Field = 'ARR__c' ORDER BY CreatedDate DESC, Id DESC LIMIT 1")
    check("field history records the rep as the last ARR writer", history and history[0]["CreatedBy"]["Username"] == rep)
    created = query(org, f"SELECT Field, CreatedBy.Username FROM OpportunityFieldHistory WHERE OpportunityId = '{ids['oppA']}'")
    check("a renewal date set at creation has only the record's created row, by the contract sync",
          [(r["Field"], r["CreatedBy"]["Username"]) for r in created] == [("created", contracts)], created)
    access = {r["RecordId"]: r for r in query(
        org, f"SELECT RecordId, HasReadAccess, HasEditAccess FROM UserRecordAccess WHERE UserId = '{user_ids['agent']}' "
             f"AND RecordId IN ('{ids['betaDup']}', '{ids['oppA']}', '{ids['oppB']}')")}
    check("the agent cannot see the duplicate account", access[ids["betaDup"]]["HasReadAccess"] is False, access)
    check("and can edit the renewals it works on", access[ids["oppA"]]["HasEditAccess"] and access[ids["oppB"]]["HasEditAccess"],
          access)
    return ids


def publish_and_talk(org, bundle, ids, admin_id, agent):
    step("Publishing an Agent Script agent with the three gated actions")
    folder = E2E / "aiAuthoringBundles" / bundle   # the CLI publishes from the project's default package folder
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{bundle}.bundle-meta.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<AiAuthoringBundle xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        "  <bundleType>AGENT</bundleType>\n</AiAuthoringBundle>\n")
    (folder / f"{bundle}.agent").write_text((E2E_PROJECT / "templates" / "renewal-agent.agent").read_text()
                                           .replace("__API_NAME__", bundle).replace("__LABEL__", bundle.replace("_", " "))
                                           .replace("__AGENT_USER__", agent))
    validated = sf("agent", "validate", "authoring-bundle", "--target-org", org, "--api-name", bundle, ok=False, cwd=E2E_PROJECT)
    check("Agent Script validates", validated.get("result", {}).get("success") is True, validated.get("message"))
    published = sf("agent", "publish", "authoring-bundle", "--target-org", org, "--api-name", bundle, ok=False, cwd=E2E_PROJECT)
    check("agent publishes", published.get("result", {}).get("success") is True, published.get("message"))
    if published.get("result", {}).get("success") is not True:
        return
    step("Talking to the agent, with live actions")
    session = sf("agent", "preview", "start", "--target-org", org, "--authoring-bundle", bundle,
                 "--use-live-actions", cwd=E2E_PROJECT)["result"]["sessionId"]
    try:
        conversation(org, bundle, session, ids, admin_id)
    finally:
        sf("agent", "preview", "end", "--target-org", org, "--authoring-bundle", bundle, "--session-id", session,
           ok=False, cwd=E2E_PROJECT)


def writes(org, record, field):
    return query(org, f"SELECT Id, Status__c, Reasons__c, Evidence__c, Confirmed_By__c, Agent__c FROM Agent_Write__c "
                      f"WHERE Record_Id__c = '{record}' AND Field__c = '{field}' ORDER BY CreatedDate DESC, Id DESC")


def recipient(org, opp):
    return query(org, f"SELECT Quote_Recipient__c FROM Opportunity WHERE Id = '{opp}'")[0]["Quote_Recipient__c"]


def conversation(org, bundle, session, ids, admin_id):
    print("\n-- 1. a renewal whose inputs are clean")
    talk(org, bundle, session, f"Update the renewal quote on opportunity {ids['oppA']} to 126000.")
    amount = query(org, f"SELECT Amount FROM Opportunity WHERE Id = '{ids['oppA']}'")[0]["Amount"]
    rows = writes(org, ids["oppA"], "Opportunity.Amount")
    check("the clean quote is written", amount == 126000, amount)
    check("and stamped Written by agent:renewal", rows and rows[0]["Status__c"] == "Written"
          and rows[0]["Agent__c"] == "agent:renewal", rows)

    print("\n-- 2. a renewal on ARR a rep typed over, three days stale, on a duplicated account")
    reply = talk(org, bundle, session, f"Update the renewal quote on opportunity {ids['oppB']} to 99750.")
    amount = query(org, f"SELECT Amount FROM Opportunity WHERE Id = '{ids['oppB']}'")[0]["Amount"]
    rows = writes(org, ids["oppB"], "Opportunity.Amount")
    expected = ["Account.ARR__c: authority is billing, not crm", "Account.ARR__c: older than its SLA",
                "Account.ARR__c: 2 records match, expected 1", "Account.ARR__c: last written by sales_rep"]
    check("the bad quote is not written", amount == 100000, amount)
    check("it is held with the four reasons, the duplicate it cannot see included", rows and rows[0]["Status__c"] == "Held"
          and (rows[0]["Reasons__c"] or "").splitlines() == expected, rows)
    if not any(w in reply.lower() for w in ("billing", "sla", "duplicate", "match", "rep")):
        print("  note: the agent's reply did not quote the reasons; the records above are what count")

    print("\n-- 3. the agent fills a blank buying role from an email signature")
    talk(org, bundle, session, f"Contact {ids['contact']} has the buying role Economic Buyer; the evidence is their email signature.")
    role = query(org, f"SELECT Buying_Role__c FROM Contact WHERE Id = '{ids['contact']}'")[0]["Buying_Role__c"]
    rows = writes(org, ids["contact"], "Contact.Buying_Role__c")
    check("the role is written", role == "Economic Buyer", role)
    check("and stamped with its evidence, unconfirmed", rows and rows[0]["Status__c"] == "Written"
          and rows[0]["Evidence__c"] and rows[0]["Confirmed_By__c"] is None, rows)

    print("\n-- 4. sending the quote to that contact rests on the agent's own guess")
    talk(org, bundle, session, f"Make contact {ids['contact']} the quote recipient for opportunity {ids['oppA']}.")
    drafts = writes(org, ids["oppA"], "Opportunity.Quote_Recipient__c")
    check("the recipient is not set yet", recipient(org, ids["oppA"]) is None)
    check("a draft waits for a person", drafts and drafts[0]["Status__c"] == "Draft"
          and drafts[0]["Reasons__c"] == "lowest input tier is advisory", drafts)
    if not drafts:
        return

    print("\n-- 5. a person approves the draft, through the action a review flow calls")
    status, body = rest(org, "actions/custom/apex/FieldTrustReview", "POST", {"inputs": [{"agentWriteId": drafts[0]["Id"]}]})
    result = (body[0].get("outputValues") or {}) if status == 200 and body else {"status": status, "body": body}
    role_rows = writes(org, ids["contact"], "Contact.Buying_Role__c")
    check("approval applies the draft", result.get("decision") == "approved" and recipient(org, ids["oppA"]) == ids["contact"],
          result)
    check("and confirms the agent's guess", role_rows and role_rows[0]["Confirmed_By__c"] == admin_id, role_rows)

    print("\n-- 6. the confirmed role is now action-grade: the next send on the same account goes straight through")
    talk(org, bundle, session, f"Make contact {ids['contact']} the quote recipient for opportunity {ids['oppA2']}.")
    rows = writes(org, ids["oppA2"], "Opportunity.Quote_Recipient__c")
    check("the recipient is set without a draft", recipient(org, ids["oppA2"]) == ids["contact"] and rows
          and rows[0]["Status__c"] == "Written", rows)

    print("\n-- 7. the same contact for another company's renewal")
    talk(org, bundle, session, f"Make contact {ids['contact']} the quote recipient for opportunity {ids['oppB']}.")
    check("is refused: the contact is not on that account", recipient(org, ids["oppB"]) is None
          and not writes(org, ids["oppB"], "Opportunity.Quote_Recipient__c"))


def agent_permissions_hold(org, username, alias, user_id, ids):
    step("With the agent's permission set, a user can neither record provenance nor approve a draft")
    # the contract integration user holds a full license, so it can take the agent's permission set
    sf("org", "assign", "permset", "--target-org", org, "--name", "FieldTrust_Agent", "--on-behalf-of", username)
    existing = apex(org, f"""
        Agent_Write__c w = new Agent_Write__c(Record_Id__c = '{ids['contact']}', Field__c = 'Contact.Buying_Role__c',
            Value__c = 'Champion', Status__c = 'Held', Reasons__c = 'e2e: a row to try to edit');
        insert w;
        System.debug(LoggingLevel.ERROR, 'E2E ' + JSON.serialize(new Map<String, Id>{{ 'id' => w.Id }}));
    """)["id"]
    status, body = rest(alias, f"sobjects/Agent_Write__c/{existing}")
    check("it can read provenance, as the gate needs", status == 200, (status, body))
    status, body = rest(alias, "sobjects/Agent_Write__c", "POST", {
        "Record_Id__c": ids["oppB"], "Field__c": "Opportunity.Amount", "Value__c": "1", "Status__c": "Draft"})
    print(f"  creating: {status} {errors(body)}")
    check("it cannot record provenance, so it cannot forge a draft or a confirmation",
          status == 400 and errors(body) == ["CANNOT_INSERT_UPDATE_ACTIVATE_ENTITY"], (status, body))
    status, body = rest(alias, f"sobjects/Agent_Write__c/{existing}", "PATCH", {"Confirmed_By__c": user_id})
    check("it cannot edit provenance", errors(body) == ["CANNOT_INSERT_UPDATE_ACTIVATE_ENTITY"], (status, body))
    status, body = rest(alias, "actions/custom/apex/FieldTrustReview", "POST", {"inputs": [{"agentWriteId": existing}]})
    check("it cannot approve", errors(body) == ["INSUFFICIENT_ACCESS"], (status, body))

if __name__ == "__main__":
    main()
