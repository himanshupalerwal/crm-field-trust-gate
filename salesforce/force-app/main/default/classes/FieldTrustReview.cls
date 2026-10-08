/**
 * The human side: approve a draft an agent proposed. Call it from a review screen or flow run by
 * a person who holds the FieldTrust_Approve_Drafts custom permission (the FieldTrust_Reviewer
 * permission set grants it; the agent's does not).
 *
 * Approving trusts nothing on the draft row but which write it proposes. It checks that an agent
 * proposed the draft and a person approves it, that the write is one the actions make and still
 * makes sense, and that nobody changed the field since the draft (an old draft never overwrites a
 * later change; it is closed instead). Then it rebuilds the inputs in code, re-runs the gate,
 * confirms the agent-written inputs that hold their current value, and applies the write. Nobody
 * can edit provenance directly; the confirmation is recorded here.
 */
public with sharing class FieldTrustReview {
    public static final String PERMISSION = 'FieldTrust_Approve_Drafts';

    /** Tests stand in a second person here; Apex tests cannot rely on a free license for one. */
    @TestVisible
    private static Id approverForTest;

    public class Request {
        @InvocableVariable(required=true label='Agent Write Id' description='Id of the Agent_Write__c draft to approve')
        public Id agentWriteId;
    }

    public class Result {
        @InvocableVariable(label='Decision' description='approved, or ask with the reasons')
        public String decision;
        @InvocableVariable(label='Reasons' description='Why it was not approved')
        public String reasons;
    }

    /** Records confirmations, which no person can edit by hand. */
    private without sharing class Ledger {
        void save(List<Agent_Write__c> rows) {
            Database.update(rows, AccessLevel.SYSTEM_MODE);
        }
    }

    @InvocableMethod(label='Approve an agent draft' description='For a person reviewing an agent draft, never for the agent itself.')
    public static List<Result> run(List<Request> requests) {
        List<Result> results = new List<Result>();
        for (Request r : requests) {
            Savepoint sp = Database.setSavepoint();
            FieldTrustService.Outcome o;
            try {
                o = approve(r.agentWriteId);
            } catch (Exception e) {
                Database.rollback(sp);
                o = FieldTrustService.ask('approval failed: ' + e.getTypeName());
            }
            Result out = new Result();
            out.decision = o.decision;
            out.reasons = String.join(o.reasons, '\n');
            results.add(out);
        }
        return results;
    }

    public static FieldTrustService.Outcome approve(Id agentWriteId) {
        if (!FeatureManagement.checkPermission(PERMISSION)) {
            return FieldTrustService.ask('approving drafts needs the Field Trust: Approve Drafts permission');
        }
        List<Agent_Write__c> drafts = [
            SELECT Id, Record_Id__c, Field__c, Value__c, Previous_Value__c, Status__c, Reasons__c, CreatedById
            FROM Agent_Write__c WHERE Id = :agentWriteId WITH USER_MODE LIMIT 1 FOR UPDATE
        ];
        if (drafts.isEmpty() || !'Draft'.equals(drafts[0].Status__c)) {
            return FieldTrustService.ask('this draft is no longer waiting for review');
        }
        Agent_Write__c draft = drafts[0];
        Id approver = approverForTest != null ? approverForTest : UserInfo.getUserId();
        if (FieldTrustReads.writerLabel(approver).startsWith('agent:')) {
            return FieldTrustService.ask('drafts are approved by a person, not an agent');
        }
        if (!FieldTrustReads.writerLabel(draft.CreatedById).startsWith('agent:')) {
            return FieldTrustService.ask('only a draft an agent proposed can be approved');
        }
        Id target;
        try {
            target = Id.valueOf(draft.Record_Id__c);
        } catch (Exception e) {
            return FieldTrustService.ask('the draft names no valid record');
        }
        if (draft.Field__c == null || !FieldTrustService.objectName(target).equals(draft.Field__c.substringBefore('.'))) {
            return FieldTrustService.ask('the draft\'s field does not belong to its record');
        }
        String field = draft.Field__c.substringAfter('.');
        Object value = FieldTrustService.typed(target, field, draft.Value__c);
        String invalid = FieldTrustService.invalid(target, field, value);
        if (invalid != null) {
            return FieldTrustService.ask(invalid);
        }
        Object before = FieldTrustService.typed(target, field, draft.Previous_Value__c);
        if (!FieldTrustService.sameValue(before, FieldTrustService.currentValue(target, field))) {
            String changed = draft.Field__c + ' changed after this draft was proposed';
            new Ledger().save(new List<Agent_Write__c>{ new Agent_Write__c(Id = draft.Id, Status__c = 'Held',
                Reasons__c = (String.isBlank(draft.Reasons__c) ? '' : draft.Reasons__c + '\n') + changed) });
            return new FieldTrustService.Outcome('ask', new List<String>{ changed }, draft.Id);
        }
        List<FieldTrustService.Input> inputs = FieldTrustService.inputsFor(target, field, value);
        FieldTrustGate.Decision recheck = FieldTrustService.check(inputs);
        if ('ask'.equals(recheck.decision)) {           // an input changed since the draft
            return new FieldTrustService.Outcome('ask', recheck.reasons, draft.Id);
        }
        Datetime now = Datetime.now();
        List<Agent_Write__c> ledger = new List<Agent_Write__c>();
        for (FieldTrustService.Input i : inputs) {
            String name = FieldTrustService.objectName(i.recordId) + '.' + i.field;
            Object current = FieldTrustService.currentValue(i.recordId, i.field);
            for (Agent_Write__c w : [
                    SELECT Id, Value__c FROM Agent_Write__c
                    WHERE Record_Id__c = :i.recordId AND Field__c = :name AND Status__c IN ('Written', 'Approved')
                    AND Confirmed_By__c = null WITH USER_MODE ORDER BY CreatedDate DESC, Id DESC LIMIT 1]) {
                if (FieldTrustService.sameValue(FieldTrustService.typed(i.recordId, i.field, w.Value__c), current)) {
                    ledger.add(new Agent_Write__c(Id = w.Id, Confirmed_By__c = approver, Confirmed_At__c = now));
                }
            }
        }
        FieldTrustService.apply(target, field, value);
        ledger.add(new Agent_Write__c(Id = draft.Id, Status__c = 'Approved', Confirmed_By__c = approver, Confirmed_At__c = now));
        new Ledger().save(ledger);
        return new FieldTrustService.Outcome('approved', new List<String>(), draft.Id);
    }
}
