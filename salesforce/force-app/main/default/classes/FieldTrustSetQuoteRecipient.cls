/**
 * Agent action: choose the contact who receives an open renewal opportunity's quote, through the
 * field trust gate. The contact must be on the opportunity's account. The write is based on the
 * contact's buying role, so a role the agent filled in itself makes it a draft until a person
 * confirms the role.
 */
public with sharing class FieldTrustSetQuoteRecipient {
    public class Request {
        @InvocableVariable(required=true label='Opportunity Id' description='Id of the open renewal opportunity')
        public String opportunityId;
        @InvocableVariable(required=true label='Contact Id' description='Id of a contact on the opportunity\'s account')
        public String contactId;
    }

    public class Result {
        @InvocableVariable(label='Decision' description='proceed (set), draft (a person reviews it first) or ask')
        public String decision;
        @InvocableVariable(label='Reasons' description='What blocked it, or why a person must review it')
        public String reasons;
        @InvocableVariable(label='Message' description='A sentence to relay to the user')
        public String message;
    }

    @InvocableMethod(label='Set quote recipient (gated)' description='Sets the contact who receives a renewal quote, based on the contact\'s buying role. The contact must be on the opportunity\'s account, and the field trust gate checks the role first.')
    public static List<Result> run(List<Request> requests) {
        List<Result> results = new List<Result>();
        for (Request r : requests) {
            Savepoint sp = Database.setSavepoint();
            try {
                results.add(toResult(setRecipient(r.opportunityId, r.contactId)));
            } catch (Exception e) {
                Database.rollback(sp);
                results.add(toResult(FieldTrustService.ask('write failed: ' + e.getTypeName())));
            }
        }
        return results;
    }

    public static FieldTrustService.Outcome setRecipient(String opportunityId, String contactId) {
        Id oppId = FieldTrustActions.idOf(opportunityId, Opportunity.SObjectType);
        Id conId = FieldTrustActions.idOf(contactId, Contact.SObjectType);
        if (oppId == null) {
            return FieldTrustService.ask('no renewal opportunity ' + opportunityId);
        }
        if (conId == null) {
            return FieldTrustService.ask('no contact ' + contactId);
        }
        return FieldTrustService.gatedWrite(oppId, 'Quote_Recipient__c', conId);
    }

    private static Result toResult(FieldTrustService.Outcome o) {
        Map<String, String> f = FieldTrustActions.fields(o);
        Result r = new Result();
        r.decision = f.get('decision');
        r.reasons = f.get('reasons');
        r.message = f.get('message');
        return r;
    }
}
