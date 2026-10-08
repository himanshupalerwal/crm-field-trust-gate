"""The field trust gate behind an MCP server: a renewal agent's CRM write tools, gated.

Any MCP client can launch this server and call its tools. The rules of the pattern carry over:
  - the tool, not the model, declares the fields a write depends on, so the model cannot leave
    an input out to get past the gate;
  - approval is not a tool: the agent can read its pending drafts but never approve them;
  - an error inside the gate becomes "ask", never a write.

The CRM is the in-memory one from renewal_agent.py, with the same records: opp-1 sits on a
clean account, opp-2 on an account whose ARR a rep typed over, and contact-1 has no role yet.

Run:  python examples/mcp_server.py     (stdio: give this command to your MCP client)
"""
import inspect
import json
import sys
from pathlib import Path
from typing import TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp.server import MCPServer  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

import renewal_agent as ra  # noqa: E402

ACCOUNT_OF = {"opp-1": "acct-1", "opp-2": "acct-2"}   # the CRM's opportunity -> account link

crm = ra.seed()
reviews = ra.Reviews(crm)
tools = ra.ToolLayer(crm, reviews.submit, run_id="mcp-session")
server = MCPServer("crm-field-trust-gate")


class Decision(TypedDict):
    decision: str           # proceed, draft, ask, or written for set_contact_role
    reasons: list[str]      # what to ask the user, or why a person must review it


def gated_tool(**hints):
    """Register a tool with its docstring, cleaned up, as the description the model reads."""
    def register(fn):
        return server.tool(description=inspect.cleandoc(fn.__doc__),
                           annotations=ToolAnnotations(read_only_hint=False, **hints))(fn)
    return register


def result(decision_and_reasons):
    decision, reasons = decision_and_reasons
    return {"decision": decision, "reasons": reasons}


@gated_tool(destructive_hint=True, idempotent_hint=False, open_world_hint=False)
def update_renewal_quote(opportunity_id: str, amount: float) -> Decision:
    """Set the renewal quote amount for an opportunity.

    The quote is based on the account's ARR and the opportunity's renewal date, and the gate
    checks both before anything is written. proceed means written; draft means a person will
    review it; ask comes with reasons to put to the user.
    """
    account = ACCOUNT_OF.get(opportunity_id)
    if account is None:
        return {"decision": "ask", "reasons": [f"no opportunity {opportunity_id}"]}
    return result(tools.update_quote(opportunity_id, amount, based_on=[
        (account, "Account.ARR"), (opportunity_id, "Opportunity.Renewal_Date")]))


@gated_tool(destructive_hint=False, idempotent_hint=False, open_world_hint=True)
def send_quote(opportunity_id: str, contact_id: str) -> Decision:
    """Send an opportunity's renewal quote to a contact.

    Based on the contact's role. A role the agent filled in itself becomes a draft until a
    person confirms it.
    """
    if opportunity_id not in ACCOUNT_OF:
        return {"decision": "ask", "reasons": [f"no opportunity {opportunity_id}"]}
    return result(tools.send_quote(opportunity_id, contact_id))


@gated_tool(destructive_hint=False, idempotent_hint=True, open_world_hint=False)
def set_contact_role(contact_id: str, role: str, evidence: str) -> Decision:
    """Fill in a contact's role when it is blank, from outside evidence such as an email signature.

    The value is stamped with its evidence and reads as advisory until a person confirms it.
    """
    return result(tools.fill_blank(contact_id, "Contact.Role", role, evidence))


@server.resource("drafts://pending")
def pending_drafts() -> str:
    """Writes waiting for a person. Read-only: approval happens outside the agent."""
    return json.dumps([{"record": d["record"], "field": d["field"], "value": d["value"],
                        "based_on": [f"{r}/{f}" for r, f in d["based_on"]]}
                       for d in reviews.drafts])


if __name__ == "__main__":
    server.run()
