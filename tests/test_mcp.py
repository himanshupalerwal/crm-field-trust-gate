"""Drive examples/mcp_server.py the way an agent would: a real MCP client over stdio."""
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
from mcp import Client  # noqa: E402
from mcp.client.stdio import StdioServerParameters  # noqa: E402

SERVER = StdioServerParameters(command=sys.executable, args=[str(ROOT / "examples" / "mcp_server.py")])


def session(steps):
    """Run `steps(client)` against a fresh server process and return what it returns."""
    async def go():
        async with Client(SERVER, raise_exceptions=True) as client:
            return await steps(client)
    return asyncio.run(go())


async def call(client, tool, **args):
    return (await client.call_tool(tool, args)).structured_content


def test_tools_on_offer_and_no_way_to_approve():
    async def steps(client):
        return {t.name for t in (await client.list_tools()).tools}
    assert session(steps) == {"update_renewal_quote", "send_quote", "set_contact_role"}


def test_the_gate_decides_every_write():
    async def steps(client):
        clean = await call(client, "update_renewal_quote", opportunity_id="opp-1", amount=126000)
        dirty = await call(client, "update_renewal_quote", opportunity_id="opp-2", amount=99750)
        guess = await call(client, "set_contact_role", contact_id="contact-1", role="economic_buyer",
                           evidence="email signature")
        again = await call(client, "set_contact_role", contact_id="contact-1", role="champion",
                           evidence="a meeting note")
        send = await call(client, "send_quote", opportunity_id="opp-1", contact_id="contact-1")
        drafts = await client.read_resource("drafts://pending")
        return clean, dirty, guess, again, send, json.loads(drafts.contents[0].text)

    clean, dirty, guess, again, send, drafts = session(steps)
    assert clean == {"decision": "proceed", "reasons": []}
    assert dirty == {"decision": "ask", "reasons": ["Account.ARR: authority is billing, not crm",
                                                     "Account.ARR: older than its SLA",
                                                     "Account.ARR: 2 records match, expected 1",
                                                     "Account.ARR: last written by sales_rep"]}
    assert guess == {"decision": "written", "reasons": []}
    assert again["decision"] == "ask"                 # the guess is not overwritten by another
    assert send == {"decision": "draft", "reasons": ["lowest input tier is advisory"]}
    assert drafts == [{"record": "opp-1", "field": "Quote.Recipient", "value": "contact-1",
                       "based_on": ["contact-1/Contact.Role"]}]


def test_unknown_records_are_asked_about_not_written():
    async def steps(client):
        return (await call(client, "update_renewal_quote", opportunity_id="opp-9", amount=1),
                await call(client, "send_quote", opportunity_id="opp-1", contact_id="contact-9"))
    missing_opp, missing_contact = session(steps)
    assert missing_opp == {"decision": "ask", "reasons": ["no opportunity opp-9"]}
    assert missing_contact["decision"] == "ask" and "0 records match" in " ".join(missing_contact["reasons"])
