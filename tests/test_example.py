from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))
import renewal_agent  # noqa: E402


def test_full_loop_guess_draft_confirm_proceed():
    steps, crm = renewal_agent.demo()
    assert [s[1] for s in steps] == ["proceed", "ask", "written", "draft", "approved", "proceed", "ask"]
    p = crm.values[("contact-1", "Contact.Role")].provenance
    assert p["writer"] == "agent:renewal" and p["confirmed_by"] == "sales_rep"


def test_outputs_match_the_article_examples():
    steps, _ = renewal_agent.demo()
    assert steps[0][1:] == ("proceed", [])
    assert steps[1][1:] == ("ask", ["Account.ARR: authority is billing, not crm",
                                    "Account.ARR: older than its SLA",
                                    "Account.ARR: 2 records match, expected 1",
                                    "Account.ARR: last written by sales_rep"])
    assert steps[3][1:] == ("draft", ["lowest input tier is advisory"])


def test_approval_rechecks_inputs_and_is_not_an_agent_tool():
    crm = renewal_agent.CRM()
    reviews = renewal_agent.Reviews(crm)
    tools = renewal_agent.ToolLayer(crm, reviews.submit)
    assert not any("approve" in name or "review" in name for name in vars(tools))
    tools.fill_blank("contact-1", "Contact.Role", "economic_buyer", evidence="email signature")
    assert tools.send_quote("quote-1", "contact-1")[0] == "draft"
    crm.matches["contact-1"] = 2                   # a duplicate contact appears before review
    decision, reasons = reviews.approve(reviews.drafts[0], reviewer="sales_rep")
    assert decision == "ask" and "2 records match" in reasons[0]
    assert ("quote-1", "Quote.Recipient") not in crm.values and len(reviews.drafts) == 1
    crm.matches["contact-1"] = 1
    draft = reviews.drafts[0]
    assert reviews.approve(draft, reviewer="sales_rep")[0] == "approved"
    assert reviews.approve(draft, reviewer="sales_rep")[0] == "ask"     # not twice


def test_an_old_draft_never_overwrites_a_later_change():
    crm = renewal_agent.CRM()
    reviews = renewal_agent.Reviews(crm)
    tools = renewal_agent.ToolLayer(crm, reviews.submit)
    tools.fill_blank("contact-1", "Contact.Role", "economic_buyer", evidence="email signature")
    assert tools.send_quote("quote-1", "contact-1")[0] == "draft"
    crm.put("quote-1", "Quote.Recipient", "contact-2", "sales_rep")     # a rep picks someone else meanwhile
    decision, reasons = reviews.approve(reviews.drafts[0], reviewer="sales_rep")
    assert (decision, reasons) == ("ask", ["Quote.Recipient changed after this draft was made"])
    assert crm.values[("quote-1", "Quote.Recipient")].value == "contact-2" and not reviews.drafts


def test_only_proceed_and_approved_drafts_reach_the_crm():
    _, crm = renewal_agent.demo()
    assert crm.values[("quote-1", "Quote.Amount")].value == 126_000
    assert ("quote-2", "Quote.Amount") not in crm.values      # ask: nothing written
    assert ("quote-4", "Quote.Amount") not in crm.values      # gate error: fails closed
    assert crm.values[("quote-1", "Quote.Recipient")].provenance["confirmed_by"] == "sales_rep"
