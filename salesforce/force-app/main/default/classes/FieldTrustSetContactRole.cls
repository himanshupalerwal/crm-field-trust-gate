/**
 * Agent action: fill in a contact's buying role when it is blank, from outside evidence such as
 * an email signature. This is the one write that skips the gate, because its evidence is not a
 * CRM field; the value is stamped with its evidence and reads as advisory until a person
 * confirms it.
 */
public with sharing class FieldTrustSetContactRole {
    public class Request {
        @InvocableVariable(required=true label='Contact Id' description='Id of the contact')
        public String contactId;
        @InvocableVariable(required=true label='Role' description='The buying role, for example Economic Buyer')
        public String role;
        @InvocableVariable(required=true label='Evidence' description='Where the role comes from, for example an email signature')
        public String evidence;
    }

    public class Result {
        @InvocableVariable(label='Decision' description='written, or ask with the reason')
        public String decision;
        @InvocableVariable(label='Reasons' description='Why it was not written')
        public String reasons;
        @InvocableVariable(label='Message' description='A sentence to relay to the user')
        public String message;
    }

    @InvocableMethod(label='Set contact buying role (gated)' description='Fills in a contact\'s buying role when it is blank. The value stays advisory until a person confirms it.')
    public static List<Result> run(List<Request> requests) {
        List<Result> results = new List<Result>();
        for (Request r : requests) {
            Savepoint sp = Database.setSavepoint();
            try {
                Id conId = FieldTrustActions.idOf(r.contactId, Contact.SObjectType);
                results.add(toResult(conId == null
                    ? FieldTrustService.ask('no contact ' + r.contactId)
                    : FieldTrustService.fillBlank(conId, 'Buying_Role__c', r.role, r.evidence)));
            } catch (Exception e) {
                Database.rollback(sp);
                results.add(toResult(FieldTrustService.ask('write failed: ' + e.getTypeName())));
            }
        }
        return results;
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
