# The field trust gate on Salesforce and Agentforce

The same pattern as the Python core, native to Salesforce: the contract in Custom Metadata, the gate in Apex, and three Agentforce actions whose writes go through it. The code, not the model, decides what each write is based on, and the gate's inputs come from what the org already keeps: field history, sync timestamp fields, duplicate counts and a provenance object.

## What is here

| Part | What it does |
| --- | --- |
| `Field_Trust_Contract__mdt` | The contract: one row per field, with authority, freshness SLA, allowed writers, tier, and optionally a sync timestamp field and a match key field. The deployed rows mirror [`field_contract.yaml`](../field_contract.yaml). |
| `Field_Trust_Writer__mdt` | Maps users to writer labels (`billing_sync`, `sales_rep`, `agent:renewal`) by username, permission set or profile, and each label to the system it speaks for. The deployed rows map the `FieldTrust_Agent` permission set to `agent:renewal` and `FieldTrust_Reviewer` to `sales_rep`. |
| `FieldTrustGate` | The gate, with the same checks and reasons as `gate.py`. A test runs 404 cases generated from `gate.py` and requires identical answers. |
| `FieldTrustContract` | Loads the contract and stops on mistakes, as `contract.py` does, and on fields your org does not have. |
| `FieldTrustReads` | Builds each input from the org: the last writer from field history, freshness from the sync field, duplicates from a normalized match key, and confirmation from `Agent_Write__c`. |
| `FieldTrustUpdateRenewalQuote`, `FieldTrustSetQuoteRecipient`, `FieldTrustSetContactRole` | The agent's actions (Invocable Apex). Each refuses writes that make no sense (a closed renewal, an amount of zero, a contact from another company) and fails closed. |
| `FieldTrustReview` | Approval of a draft, for a person: it refuses a draft whose field changed since it was proposed, rebuilds the draft's inputs, re-runs the gate, confirms the agent-written values it rests on and applies the write. |
| `Agent_Write__c` | Provenance: every write, draft and hold the actions make, with its inputs, reasons, evidence, the value a draft would replace, and who confirmed it. Held rows give you the hold rate by failed check; a write refused before the gate (a closed renewal, a contact from another company) leaves no row. |
| `FieldTrust_Agent`, `FieldTrust_Reviewer`, `FieldTrust_Approve_Drafts` | Permission sets and a custom permission. The agent runs the actions and reads provenance, but cannot create or edit it, confirm a value or approve. Reviewers approve through the custom permission and cannot edit provenance either. |

Every query and write runs in user mode, so the running user's object and field permissions and sharing apply, with three deliberate exceptions. Who a user is (their permission sets, directly or through a permission set group) and duplicate counts are read across the whole org, because duplicates the agent cannot see still count. And provenance is recorded by the actions themselves, in system mode, so no user can write it directly.

`Agent_Write__c` is Public Read Only, so reviewers can find drafts. It copies values from the records it describes (quote amounts, the evidence for a role), so give access to it only to the agent and reviewers, or make it Private and share drafts with reviewers if those values must follow your record sharing.

## The agent's identity

Run the agent as a dedicated agent user, with `FieldTrust_Agent` and nothing that maps to a person's label. The actions refuse to run for any user whose writer label does not start with `agent:`. An agent that ran as the signed-in person would record its guesses as that person's, and the gate could never tell a guess from a fact. That rules out employee agents, which run as the signed-in user; use a service agent or another agent with a user of its own.

What that user can do decides what the agent can do:

- **Records.** The gate decides whether the data a write rests on can be trusted, not whether the person talking to the agent may make the change. The agent user's own access decides which records it can change, so share with it only the records its users may change, and control who can talk to it.
- **Other write paths.** `FieldTrust_Agent` lets the agent user edit the fields the actions write, because the actions write in user mode. Give that user no other way to edit them, such as other agent actions, flows or API integrations, or those writes skip the gate.
- **Approval.** Keep the two permission sets apart: never give the agent user `FieldTrust_Reviewer`. Approval also refuses any approver whose writer label is an agent's, so a draft is always approved by a person.

## Install

```bash
cd salesforce
sf project deploy start --source-dir force-app --target-org my-sandbox
sf org assign permset --name FieldTrust_Agent --on-behalf-of <agent user> --target-org my-sandbox
sf org assign permset --name FieldTrust_Reviewer --on-behalf-of <reviewer> --target-org my-sandbox
sf apex run test --test-level RunLocalTests --code-coverage --wait 30 --target-org my-sandbox
```

Then:

1. **Field history.** The deploy adds the example fields (`Account.ARR__c`, `Opportunity.Renewal_Date__c`, `Contact.Buying_Role__c` and others) with history tracking on. For your own fields, turn tracking on and edit the `Field_Trust_Contract__mdt` rows.
   - A value set when a record was created, and never changed, reads as written by whoever created the record.
   - If you turn on tracking for a field on an object that already tracks others, earlier changes to that field are not in history, and its value reads as the record creator's. Turn tracking on before an agent relies on a field.
   - Salesforce keeps field history for 18 months (24 through the API) unless you use Field Audit Trail. Older values have no known writer, and the gate asks.
2. **Freshness.** Give every field an agent acts on a sync timestamp field that its integration sets on every sync, and name it in the contract row (`Sync_Field__c`). Without one, freshness falls back to the last change in field history, which does not move when a sync writes the same value again. A person's confirmation also counts as fresh.
3. **Duplicates.** Name a match key (`Match_Key_Field__c`, such as `Website`) for fields where a duplicate record would make the value ambiguous. Keys are compared as hosts: no scheme, no `www.`, no path, any case. A record with a blank key counts as one match, since there is nothing to compare. If more than 2,000 records share a key, the read fails and the gate asks.
4. **Writers.** Map your writers in `Field_Trust_Writer__mdt`: one row per integration user (by username), and per group of people (by permission set or profile). A username row wins over a permission set row, and a permission set row over a profile row; between two rows of the same kind, an agent label wins. Users you do not map read as `unmapped`, which no contract allows.
5. **The agent.** Add the actions to your agent in Agent Builder, or in Agent Script with `target: "apex://FieldTrustUpdateRenewalQuote"` and the like. [`e2e/templates/renewal-agent.agent`](e2e/templates/renewal-agent.agent) shows the actions and the instructions that make the agent relay drafts and holds honestly; the end-to-end test publishes it, but it is a test agent, not one to deploy as is.
6. **Review.** Give reviewers a screen flow or a quick action on `Agent_Write__c` that calls the "Approve an agent draft" action.

The actions are built for one record per call, which is how an agent calls them. In a batch, an error in one request does not undo the others, but each request runs its own queries, so a large batch can reach Salesforce's limit of 100 queries per transaction, and that fails the whole batch.

## Tests

- **Apex** (28 tests, 94% coverage): the parity cases, the contract and schema checks, every action outcome, the full guess, draft, approval and proceed loop, confirmations that do not carry over to a later value, approval re-checks, stale and forged drafts, agents as approvers, duplicate matching, writes that make no sense, failing closed, and the permission sets. Salesforce writes no field history inside Apex tests, so a fake history source stands in, and the tests set up their own contract, writer labels and reviewing user. They run as the admin who deploys, so object and field permissions are checked by the permission tests and end to end.
- **Python** ([`tests/test_salesforce.py`](../tests/test_salesforce.py), in CI): the parity cases match `gate.py`, the Custom Metadata contract mirrors `field_contract.yaml`, and the Apex gate's reason strings are the ones `gate.py` uses.
- **End to end** ([`scripts/e2e.py`](scripts/e2e.py)): a real org with private sharing, real field history from separate users writing through the REST API, and a published Agentforce agent with live actions. It runs the Apex tests there, then talks to the agent:
  1. A quote on a renewal the contract sync created is written.
  2. A quote built on ARR a rep typed over is held with the gate's four reasons, one of them a duplicate account the agent cannot see.
  3. The agent fills a blank buying role from an email signature.
  4. A send that rests on that guess becomes a draft.
  5. The org admin approves it through the action a review flow calls, which confirms the guess.
  6. The next send goes straight through.
  7. A contact from another company is refused.

  Last, it checks that a user with the agent's permission set can read provenance but cannot record or edit it, and cannot approve. Every step is checked against the records.

```bash
python salesforce/scripts/e2e.py --dev-hub <alias>     # new 1-day scratch org with Agentforce, deleted after
```

It needs a Dev Hub that can create orgs with the `Einstein1AIPlatform` feature, and uses fake data only. The test agent and its files live in a separate project, [`e2e/`](e2e), which also sets Account, Opportunity and Case sharing to Private, so nothing it generates touches `force-app`.

When `gate.py` changes, regenerate the parity cases with `python salesforce/scripts/make_gate_cases.py`.
