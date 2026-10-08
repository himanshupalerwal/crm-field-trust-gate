/**
 * What the agent's actions and the review share.
 *
 * - The code, not the model or a stored record, decides what each write is based on (inputsFor)
 *   and whether it makes sense at all (invalid).
 * - The gate runs on those inputs and fails closed: an error inside it becomes ask, never a write.
 * - On proceed the write and its provenance row commit together, or neither does.
 * - The actions run only as a dedicated agent user. An agent that runs as the signed-in person
 *   would record its guesses as that person's, and the guess-draft-confirm loop would vanish.
 * - Only these actions record provenance. The agent user can read Agent_Write__c but not create
 *   or edit it, so it cannot forge a draft or a confirmation through any other path.
 *
 * Every other query and change runs in user mode, so the running user's permissions apply.
 */
public with sharing class FieldTrustService {
    public static final Set<String> TARGETS = new Set<String>{ 'Opportunity.Amount', 'Opportunity.Quote_Recipient__c' };

    public class Input {
        public Id recordId;
        public String field;

        public Input(Id recordId, String field) {
            this.recordId = recordId;
            this.field = field;
        }

        public String key() {
            return recordId + '/' + objectName(recordId) + '.' + field;
        }
    }

    public class Outcome {
        public String decision;
        public List<String> reasons;
        public Id agentWriteId;

        public Outcome(String decision, List<String> reasons, Id agentWriteId) {
            this.decision = decision;
            this.reasons = reasons;
            this.agentWriteId = agentWriteId;
        }

        /** A sentence the agent can relay as it stands. */
        public String message() {
            if ('proceed'.equals(decision) || 'written'.equals(decision) || 'approved'.equals(decision)) {
                return 'Done.';
            }
            if ('draft'.equals(decision)) {
                return 'Not applied yet: a person will review it first (' + String.join(reasons, '; ') + ').';
            }
            return 'Not changed. Tell the user what blocked it and ask how to proceed: ' + String.join(reasons, '; ');
        }
    }

    /** Records provenance on the agent's behalf; the agent user itself cannot create Agent_Write__c. */
    private without sharing class Provenance {
        void record(Agent_Write__c row) {
            Database.insert(row, AccessLevel.SYSTEM_MODE);
        }
    }

    private static final Provenance PROVENANCE = new Provenance();

    public static String objectName(Id recordId) {
        return recordId.getSObjectType().getDescribe().getName();
    }

    public static Outcome ask(String reason) {
        return new Outcome('ask', new List<String>{ reason }, null);
    }

    /** Null when the running user is mapped as an agent, otherwise the reason it is refused. */
    public static String notAnAgent() {
        String me = FieldTrustReads.writerLabel(UserInfo.getUserId());
        return me.startsWith('agent:') ? null
            : 'these actions run only as a dedicated agent user, and the running user reads as ' + me;
    }

    /** What a write of `value` to `field` on `target` is based on. Only the actions' writes are known. */
    public static List<Input> inputsFor(Id target, String field, Object value) {
        String name = objectName(target) + '.' + field;
        if ('Opportunity.Amount'.equals(name)) {
            Opportunity o = [SELECT AccountId FROM Opportunity WHERE Id = :target WITH USER_MODE LIMIT 1];
            return new List<Input>{ new Input(o.AccountId, 'ARR__c'), new Input(target, 'Renewal_Date__c') };
        }
        if ('Opportunity.Quote_Recipient__c'.equals(name)) {
            return new List<Input>{ new Input(Id.valueOf(String.valueOf(value)), 'Buying_Role__c') };
        }
        throw new FieldTrustContract.ContractException(name + ' is not a write these actions make');
    }

    /** Null when the write makes sense, otherwise why not: an open renewal, a positive amount, the right company. */
    public static String invalid(Id target, String field, Object value) {
        String name = objectName(target) + '.' + field;
        if (!TARGETS.contains(name)) {
            return name + ' is not a write these actions make';
        }
        List<Opportunity> opps = [SELECT AccountId, IsClosed FROM Opportunity WHERE Id = :target WITH USER_MODE LIMIT 1];
        if (opps.isEmpty() || opps[0].AccountId == null) {
            return 'no renewal opportunity ' + target;
        }
        if (opps[0].IsClosed) {
            return 'opportunity ' + target + ' is closed';
        }
        if ('Opportunity.Amount'.equals(name)) {
            return value instanceof Decimal && (Decimal) value > 0 ? null : 'the amount must be a positive number';
        }
        Id contactId = FieldTrustActions.idOf(String.valueOf(value), Contact.SObjectType);
        List<Contact> contacts = contactId == null ? new List<Contact>()
            : [SELECT AccountId FROM Contact WHERE Id = :contactId WITH USER_MODE LIMIT 1];
        if (contacts.isEmpty()) {
            return 'no contact ' + value;
        }
        return contacts[0].AccountId == opps[0].AccountId ? null
            : 'contact ' + contactId + ' is not on this opportunity\'s account';
    }

    /** The gate's decision for these inputs. An error inside it becomes ask, never a write. */
    public static FieldTrustGate.Decision check(List<Input> inputs) {
        try {
            Map<String, FieldTrustGate.Rule> contract = FieldTrustContract.load();
            List<FieldTrustGate.FieldRead> reads = new List<FieldTrustGate.FieldRead>();
            for (Input i : inputs) {
                reads.add(FieldTrustReads.read(i.recordId, i.field));
            }
            return FieldTrustGate.evaluate(reads, contract, Datetime.now());
        } catch (Exception e) {
            System.debug(LoggingLevel.ERROR, 'field trust gate error: ' + e.getMessage());
            return new FieldTrustGate.Decision('ask', new List<String>{ 'gate error: ' + e.getTypeName() });
        }
    }

    /** Write `value` to `field` on `target`, based on what inputsFor says, only if the gate lets it through. */
    public static Outcome gatedWrite(Id target, String field, Object value) {
        String refused = notAnAgent();
        if (refused == null) {
            refused = invalid(target, field, value);
        }
        if (refused == null && stored(value) != null && stored(value).length() > 255) {
            refused = 'the value is too long to record';
        }
        if (refused != null) {
            return ask(refused);
        }
        List<Input> inputs = inputsFor(target, field, value);
        FieldTrustGate.Decision d = check(inputs);
        Agent_Write__c row = stamp(target, field, value, inputs, d.reasons);
        if (!'proceed'.equals(d.decision)) {
            row.Status__c = 'draft'.equals(d.decision) ? 'Draft' : 'Held';
            if ('Draft'.equals(row.Status__c)) {
                row.Previous_Value__c = stored(currentValue(target, field));   // what approval would replace
            }
            PROVENANCE.record(row);
            return new Outcome(d.decision, d.reasons, row.Id);
        }
        Savepoint sp = Database.setSavepoint();
        try {
            apply(target, field, value);
            row.Status__c = 'Written';
            PROVENANCE.record(row);
            return new Outcome('proceed', new List<String>(), row.Id);
        } catch (Exception e) {
            Database.rollback(sp);   // the write and its provenance commit together, or neither does
            System.debug(LoggingLevel.ERROR, 'field trust write failed: ' + e.getMessage());
            Agent_Write__c held = stamp(target, field, value, inputs, new List<String>{ 'write failed: ' + e.getTypeName() });
            held.Status__c = 'Held';
            PROVENANCE.record(held);
            return new Outcome('ask', new List<String>{ 'write failed: ' + e.getTypeName() }, held.Id);
        }
    }

    /**
     * Fill a blank field from outside evidence, such as an email signature. This is the one write
     * that skips the gate, because its evidence is not a CRM field: the contract must list the
     * running agent's writer label for the field, and the stamp keeps the value advisory until a
     * person confirms it.
     */
    public static Outcome fillBlank(Id target, String field, String value, String evidence) {
        String refused = notAnAgent();
        if (refused != null) {
            return ask(refused);
        }
        String name = objectName(target) + '.' + field;
        Savepoint sp = Database.setSavepoint();
        try {
            Field_Trust_Contract__mdt rule = FieldTrustContract.rowsByField().get(name);
            String writer = FieldTrustReads.writerLabel(UserInfo.getUserId());
            Boolean allowed = rule != null && FieldTrustContract.writers(rule.Allowed_Writers__c).contains(writer);
            if (!allowed || currentValue(target, field) != null || String.isBlank(value) || value.length() > 255) {
                return ask(name + ': not a blank field this agent may write');
            }
            apply(target, field, value);
            Agent_Write__c row = stamp(target, field, value, new List<Input>(), new List<String>());
            row.Status__c = 'Written';
            row.Evidence__c = evidence == null ? null : evidence.left(255);
            PROVENANCE.record(row);
            return new Outcome('written', new List<String>(), row.Id);
        } catch (Exception e) {
            Database.rollback(sp);
            System.debug(LoggingLevel.ERROR, 'field trust fill failed: ' + e.getMessage());
            return ask('write failed: ' + e.getTypeName());
        }
    }

    public static Agent_Write__c stamp(Id target, String field, Object value, List<Input> inputs, List<String> reasons) {
        List<String> keys = new List<String>();
        for (Input i : inputs) {
            keys.add(i.key());
        }
        return new Agent_Write__c(
            Record_Id__c = target,
            Field__c = objectName(target) + '.' + field,
            Value__c = stored(value),
            Agent__c = FieldTrustReads.writerLabel(UserInfo.getUserId()),
            Run_Id__c = Request.getCurrent().getRequestId(),
            Inputs__c = JSON.serialize(keys),
            Reasons__c = String.join(reasons, '\n'),
            Written_At__c = Datetime.now());
    }

    /** A value as text that typed() turns back into the same value. */
    public static String stored(Object value) {
        if (value == null) {
            return null;
        }
        if (value instanceof Datetime) {
            return ((Datetime) value).formatGmt('yyyy-MM-dd HH:mm:ss');
        }
        if (value instanceof Decimal) {
            return ((Decimal) value).toPlainString();
        }
        return String.valueOf(value);
    }

    /** A field's value now, read in user mode; null when the record is gone or out of reach. */
    public static Object currentValue(Id target, String field) {
        List<SObject> rows = Database.queryWithBinds(
            'SELECT ' + field + ' FROM ' + objectName(target) + ' WHERE Id = :target',
            new Map<String, Object>{ 'target' => target }, AccessLevel.USER_MODE);
        return rows.isEmpty() ? null : rows[0].get(field);
    }

    /** One field on one record, in user mode. */
    public static void apply(Id target, String field, Object value) {
        SObject record = target.getSObjectType().newSObject(target);
        record.put(field, value);
        Database.update(record, AccessLevel.USER_MODE);
    }

    /** A stored value back in the field's own type. */
    public static Object typed(Id target, String field, String value) {
        if (value == null) {
            return null;
        }
        Schema.DisplayType t = target.getSObjectType().getDescribe().fields.getMap().get(field).getDescribe().getType();
        switch on t {
            when CURRENCY, DOUBLE, PERCENT {
                return Decimal.valueOf(value);
            }
            when INTEGER {
                return Integer.valueOf(value);
            }
            when LONG {
                return Long.valueOf(value);
            }
            when DATE {
                return Date.valueOf(value);
            }
            when DATETIME {
                return Datetime.valueOfGmt(value);
            }
            when BOOLEAN {
                return Boolean.valueOf(value);
            }
            when else {
                return value;
            }
        }
    }

    /** Whether two field values are the same: numbers by value, everything else exactly. */
    public static Boolean sameValue(Object a, Object b) {
        if (a == null || b == null) {
            return a == null && b == null;
        }
        if (a instanceof Decimal && b instanceof Decimal) {
            return ((Decimal) a - (Decimal) b) == 0;
        }
        return String.valueOf(a).equals(String.valueOf(b));
    }
}
