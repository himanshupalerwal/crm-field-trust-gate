/**
 * Builds the gate's FieldRead for one field of one record from what the org already keeps:
 * field history (who last wrote the field), the contract's sync timestamp field, duplicate
 * counts on a match key, and Agent_Write__c provenance (whether a person confirmed this exact
 * value). The model supplies none of it.
 *
 * Freshness is the sync timestamp when the contract names one. Without one it is the time of
 * the last change in field history, which does not move when a value is saved unchanged, so
 * give every field an agent acts on a sync timestamp (or have a person confirm it).
 *
 * Field history records a field's changes, and only a "created" row when a record is created,
 * so a value set at creation and never changed reads as written by whoever created the record.
 * History is kept for 18 months (24 through the API) unless you use Field Audit Trail; older
 * values read as having no known writer, and the gate asks.
 */
public with sharing class FieldTrustReads {
    public class HistoryRow {
        public Id userId;
        public Datetime createdDate;

        public HistoryRow(Id userId, Datetime createdDate) {
            this.userId = userId;
            this.createdDate = createdDate;
        }
    }

    /** A read the gate cannot rely on; the gate turns it into ask. */
    public class ReadException extends Exception {}

    public interface HistorySource {
        HistoryRow latest(Id recordId, String objectName, String fieldName);
    }

    /**
     * Field history from the org: the field's latest change, or the record's creation when the
     * field never changed. Salesforce writes no history inside Apex tests, so tests swap it.
     */
    public class OrgHistory implements HistorySource {
        public HistoryRow latest(Id recordId, String objectName, String fieldName) {
            List<String> table = historyTable(objectName);
            String soql = 'SELECT CreatedById, CreatedDate FROM ' + table[0] + ' WHERE ' + table[1]
                + ' = :recordId AND (Field = :fieldName OR Field = \'created\') ORDER BY CreatedDate DESC, Id DESC LIMIT 1';
            List<SObject> rows = Database.queryWithBinds(soql,
                new Map<String, Object>{ 'recordId' => recordId, 'fieldName' => fieldName }, AccessLevel.USER_MODE);
            return rows.isEmpty() ? null
                : new HistoryRow((Id) rows[0].get('CreatedById'), (Datetime) rows[0].get('CreatedDate'));
        }
    }

    /**
     * Lookups that must not depend on what the running user may see: who a user is (setup data the
     * agent never receives) and how many records share a match key (duplicates the agent cannot
     * see still count). System mode alone drops object and field permissions but keeps the class's
     * sharing, so this inner class is declared without sharing.
     */
    private without sharing class Unshared {
        List<SObject> query(String soql, Map<String, Object> binds) {
            return Database.queryWithBinds(soql, binds, AccessLevel.SYSTEM_MODE);
        }
    }

    @TestVisible
    private static HistorySource history = new OrgHistory();
    @TestVisible
    private static List<Field_Trust_Writer__mdt> writerRowsForTest;
    @TestVisible
    private static Map<Id, String> labelCache = new Map<Id, String>();

    private static final Unshared UNSHARED = new Unshared();
    private static final Integer MAX_CANDIDATES = 2000;
    private static final Map<String, List<String>> STANDARD_HISTORY = new Map<String, List<String>>{
        'Account' => new List<String>{ 'AccountHistory', 'AccountId' },
        'Opportunity' => new List<String>{ 'OpportunityFieldHistory', 'OpportunityId' },
        'Contact' => new List<String>{ 'ContactHistory', 'ContactId' },
        'Lead' => new List<String>{ 'LeadHistory', 'LeadId' },
        'Case' => new List<String>{ 'CaseHistory', 'CaseId' }
    };
    private static final Map<String, Integer> MATCH_PRIORITY = new Map<String, Integer>{
        'Username' => 0, 'Permission Set' => 1, 'Profile' => 2
    };

    /** The history table and its record column for an object. */
    public static List<String> historyTable(String objectName) {
        if (STANDARD_HISTORY.containsKey(objectName)) {
            return STANDARD_HISTORY.get(objectName);
        }
        if (objectName.endsWith('__c')) {
            return new List<String>{ objectName.removeEnd('__c') + '__History', 'ParentId' };
        }
        throw new FieldTrustContract.ContractException('no field history table known for ' + objectName);
    }

    /** Writer rows in a fixed order, so the first match at a priority always wins the same way. */
    public static List<Field_Trust_Writer__mdt> writerRows() {
        if (writerRowsForTest != null) {
            return writerRowsForTest;
        }
        Map<String, Field_Trust_Writer__mdt> byName = Field_Trust_Writer__mdt.getAll();
        List<String> names = new List<String>(byName.keySet());
        names.sort();
        List<Field_Trust_Writer__mdt> rows = new List<Field_Trust_Writer__mdt>();
        for (String n : names) {
            rows.add(byName.get(n));
        }
        return rows;
    }

    /** Permission sets a user holds directly or through a permission set group, lower case. */
    private static Set<String> permissionSetsOf(Id userId) {
        Set<String> names = new Set<String>();
        Set<Id> groups = new Set<Id>();
        for (SObject a : UNSHARED.query(
                'SELECT PermissionSet.Name, PermissionSetGroupId FROM PermissionSetAssignment WHERE AssigneeId = :userId',
                new Map<String, Object>{ 'userId' => userId })) {
            PermissionSetAssignment psa = (PermissionSetAssignment) a;
            names.add(psa.PermissionSet.Name.toLowerCase());
            if (psa.PermissionSetGroupId != null) {
                groups.add(psa.PermissionSetGroupId);
            }
        }
        if (!groups.isEmpty()) {
            for (SObject c : UNSHARED.query(
                    'SELECT PermissionSet.Name FROM PermissionSetGroupComponent WHERE PermissionSetGroupId IN :groups',
                    new Map<String, Object>{ 'groups' => groups })) {
                names.add(((PermissionSetGroupComponent) c).PermissionSet.Name.toLowerCase());
            }
        }
        return names;
    }

    /**
     * The writer label for a user: a username row wins, then a permission set, then a profile.
     * Between two rows of the same kind an agent label wins, so an agent never reads as a person.
     */
    public static String writerLabel(Id userId) {
        if (userId == null) {
            return 'unknown';
        }
        if (labelCache.containsKey(userId)) {
            return labelCache.get(userId);
        }
        List<SObject> users = UNSHARED.query('SELECT Username, Profile.Name FROM User WHERE Id = :userId',
            new Map<String, Object>{ 'userId' => userId });
        User u = users.isEmpty() ? null : (User) users[0];
        Set<String> permissionSets = permissionSetsOf(userId);
        String label = 'unmapped';
        Integer best = 99;
        for (Field_Trust_Writer__mdt w : writerRows()) {
            Boolean hit = false;
            if ('Username'.equals(w.Match_On__c)) {
                hit = u != null && u.Username.equalsIgnoreCase(w.Match_Value__c);
            } else if ('Permission Set'.equals(w.Match_On__c)) {
                hit = w.Match_Value__c != null && permissionSets.contains(w.Match_Value__c.toLowerCase());
            } else if ('Profile'.equals(w.Match_On__c)) {
                hit = u != null && u.Profile != null && w.Match_Value__c.equalsIgnoreCase(u.Profile.Name);
            }
            Integer priority = MATCH_PRIORITY.get(w.Match_On__c);
            Boolean agentWinsTie = priority == best && w.Writer_Label__c != null
                && w.Writer_Label__c.startsWith('agent:') && !label.startsWith('agent:');
            if (hit && priority != null && (priority < best || agentWinsTie)) {
                best = priority;
                label = w.Writer_Label__c;
            }
        }
        labelCache.put(userId, label);
        return label;
    }

    /** The system a writer label speaks for. Agent labels you leave out map to "agent". */
    public static String systemOf(String label) {
        for (Field_Trust_Writer__mdt w : writerRows()) {
            if (label != null && label.equals(w.Writer_Label__c)) {
                return w.System__c;
            }
        }
        return label != null && label.startsWith('agent:') ? 'agent' : 'unknown';
    }

    /** Same as normalize_key in crm_audit.py: lower case, no scheme, no www., no path. */
    public static String normalizeKey(String value) {
        String v = value == null ? '' : value.trim().toLowerCase();
        v = v.replaceFirst('^https?://', '').replaceFirst('^www\\.', '');
        return v.split('/', 2)[0].trim();
    }

    /**
     * How many records share this record's match key, after normalizing it, across the whole org.
     * The query looks for the key as the whole host (with or without a scheme, www. or a path), so
     * a short key never pulls in every value that merely contains it; if it still finds more rows
     * than it can count, the read fails and the gate asks.
     */
    public static Integer countMatches(String objectName, String keyField, String value) {
        String key = normalizeKey(value);
        if (String.isBlank(key)) {
            return 1;              // nothing to match on: the identity check cannot judge it
        }
        String k = key.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_');
        Map<String, Object> binds = new Map<String, Object>();
        List<String> likes = new List<String>();
        for (String prefix : new List<String>{ '', 'www.', '%://', '%://www.' }) {
            for (String pattern : new List<String>{ prefix + k, prefix + k + '/%' }) {
                String bind = 'p' + binds.size();
                binds.put(bind, pattern);
                likes.add(keyField + ' LIKE :' + bind);
            }
        }
        List<SObject> candidates = UNSHARED.query('SELECT ' + keyField + ' FROM ' + objectName + ' WHERE '
            + String.join(likes, ' OR ') + ' LIMIT ' + (MAX_CANDIDATES + 1), binds);
        if (candidates.size() > MAX_CANDIDATES) {
            throw new ReadException('more records share the match key than the gate can count');
        }
        Integer n = 0;
        for (SObject c : candidates) {
            if (key.equals(normalizeKey((String) c.get(keyField)))) {
                n++;
            }
        }
        return n;
    }

    /**
     * The person's confirmation of this field's current value, if there is one: the newest
     * provenance row for the field, stamped no earlier than the latest change in field history,
     * holding the value the field holds now, and confirmed. Anything else is unconfirmed.
     */
    public static Agent_Write__c confirmation(Id recordId, String name, HistoryRow latest, Object currentValue) {
        List<Agent_Write__c> rows = [
            SELECT Value__c, Confirmed_By__c, Confirmed_At__c, CreatedDate FROM Agent_Write__c
            WHERE Record_Id__c = :recordId AND Field__c = :name AND Status__c IN ('Written', 'Approved')
            WITH USER_MODE ORDER BY CreatedDate DESC, Id DESC LIMIT 1
        ];
        if (rows.isEmpty() || rows[0].Confirmed_By__c == null) {
            return null;
        }
        Agent_Write__c row = rows[0];
        if (latest != null && row.CreatedDate < latest.createdDate.addSeconds(-5)) {
            return null;           // the field changed after this row: it confirms an older value
        }
        String field = name.substringAfter('.');
        return FieldTrustService.sameValue(FieldTrustService.typed(recordId, field, row.Value__c), currentValue) ? row : null;
    }

    /** The FieldRead the gate sees for one field of one record. */
    public static FieldTrustGate.FieldRead read(Id recordId, String fieldApiName) {
        String objectName = recordId.getSObjectType().getDescribe().getName();
        String name = objectName + '.' + fieldApiName;
        Field_Trust_Contract__mdt rule = FieldTrustContract.rowsByField().get(name);
        String syncField = rule == null ? null : rule.Sync_Field__c;
        String keyField = rule == null ? null : rule.Match_Key_Field__c;
        Set<String> columns = new Set<String>{ 'Id', 'LastModifiedDate', fieldApiName };
        if (String.isNotBlank(syncField)) {
            columns.add(syncField);
        }
        if (String.isNotBlank(keyField)) {
            columns.add(keyField);
        }
        List<SObject> records = Database.queryWithBinds(
            'SELECT ' + String.join(new List<String>(columns), ', ') + ' FROM ' + objectName + ' WHERE Id = :recordId',
            new Map<String, Object>{ 'recordId' => recordId }, AccessLevel.USER_MODE);
        if (records.isEmpty()) {
            return new FieldTrustGate.FieldRead(name, 'unknown', null, 0, 'unknown');
        }
        SObject record = records[0];
        HistoryRow latest = history.latest(recordId, objectName, fieldApiName);
        String writer = latest == null ? 'unknown' : writerLabel(latest.userId);
        Datetime synced = String.isNotBlank(syncField) ? (Datetime) record.get(syncField) : null;
        if (synced == null) {
            synced = latest != null ? latest.createdDate : (Datetime) record.get('LastModifiedDate');
        }
        if (writer.startsWith('agent:')) {
            Agent_Write__c confirmed = confirmation(recordId, name, latest, record.get(fieldApiName));
            if (confirmed != null) {
                writer = writerLabel(confirmed.Confirmed_By__c);   // a confirmed value reads as the confirmer's
                if (confirmed.Confirmed_At__c != null && (synced == null || confirmed.Confirmed_At__c > synced)) {
                    synced = confirmed.Confirmed_At__c;           // and a person's confirmation is fresh
                }
            }
        }
        Integer matches = String.isBlank(keyField) ? 1 : countMatches(objectName, keyField, (String) record.get(keyField));
        return new FieldTrustGate.FieldRead(name, systemOf(writer), synced, matches, writer);
    }
}
