/**
 * Agent action: set an open renewal opportunity's quote amount, through the field trust gate.
 * The code, not the model, decides what the quote is based on: the account's ARR and the
 * opportunity's renewal date. One record per call is the intended use; each request in a batch
 * succeeds or fails on its own.
 */
public with sharing class FieldTrustUpdateRenewalQuote {
    public class Request {
        @InvocableVariable(required=true label='Opportunity Id' description='Id of the open renewal opportunity')
        public String opportunityId;
        @InvocableVariable(required=true label='Amount' description='The new renewal quote amount, above zero')
        public Decimal amount;
    }

    public class Result {
        @InvocableVariable(label='Decision' description='proceed (written), draft (a person reviews it first) or ask')
        public String decision;
        @InvocableVariable(label='Reasons' description='What blocked the write, or why a person must review it')
        public String reasons;
        @InvocableVariable(label='Message' description='A sentence to relay to the user')
        public String message;
    }

    @InvocableMethod(label='Update renewal quote (gated)' description='Sets an open renewal opportunity\'s quote amount. The field trust gate checks the account\'s ARR and the renewal date before anything is written.')
    public static List<Result> run(List<Request> requests) {
        List<Result> results = new List<Result>();
        for (Request r : requests) {
            Savepoint sp = Database.setSavepoint();
            try {
                results.add(toResult(updateQuote(r.opportunityId, r.amount)));
            } catch (Exception e) {
                Database.rollback(sp);
                results.add(toResult(FieldTrustService.ask('write failed: ' + e.getTypeName())));
            }
        }
        return results;
    }

    public static FieldTrustService.Outcome updateQuote(String opportunityId, Decimal amount) {
        Id oppId = FieldTrustActions.idOf(opportunityId, Opportunity.SObjectType);
        return oppId == null ? FieldTrustService.ask('no renewal opportunity ' + opportunityId)
            : FieldTrustService.gatedWrite(oppId, 'Amount', amount);
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
