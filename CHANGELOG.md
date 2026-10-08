# Changelog

All notable changes to crm-field-trust-gate are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). These count as its public interface: the field contract's format, the `FieldRead` fields, what `gate()` and `stamp()` return, the audit config's format and the keys of `metrics.json`.

## [1.0.0] - 2026-10-08

First public release.

### Added

- **Field trust contract** (`field_contract.yaml`): authority, freshness SLA, allowed writers and a tier for each field an agent touches, and `contract.py`, which reports mistakes such as a misspelled tier before anything relies on the contract.
- **Gate and provenance** (`gate.py`): `gate()` returns `proceed`, `draft` or `ask` with reasons for a write, from the fields it is based on; `stamp()` attaches the agent, run, inputs and confirmation to an agent write.
- **End-to-end example** (`examples/renewal_agent.py`): write tools that declare their inputs, the gate in the tool layer, a review queue the agent cannot call, and confirmation that restores a field's tier.
- **MCP server** (`examples/mcp_server.py`): the same gated tools for any MCP-capable agent. The tools declare their own inputs, approval is not a tool, and each tool carries annotations for the client.
- **Salesforce and Agentforce** (`salesforce/`): the contract in Custom Metadata, the gate in Apex with the same answers as `gate.py` on 404 generated cases, and three Agentforce actions. The code decides what each write rests on, and the gate reads it from field history, sync timestamps, duplicate counts and provenance. Approval is for people: it needs a custom permission the agent's permission set does not grant, refuses approvers mapped as agents, and never overwrites a later change. Includes Apex tests and an end-to-end test with a live Agentforce agent under private sharing.
- **Read-only audit** (`audit/`): field profiles, the gate in shadow mode, reversal rates and mismatch against a source of truth, from CSV exports. Includes a Salesforce export script, a fake sample org and an example report.
- **Simulations** (`simulate.py`): what the gate catches and costs, and what provenance changes when an agent's guesses feed back, with committed results and figures.
- **CI** on Python 3.10 and 3.12 and at the oldest allowed versions, with byte-for-byte reproduction of `results/` and the example report.

[1.0.0]: https://github.com/himanshupalerwal/crm-field-trust-gate/releases/tag/v1.0.0
