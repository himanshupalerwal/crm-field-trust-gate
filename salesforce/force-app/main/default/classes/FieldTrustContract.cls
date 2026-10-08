/**
 * Loads the field trust contract from Field_Trust_Contract__mdt and checks it before the gate
 * relies on it, as contract.py does: a misspelled tier or an empty writer list stops the action
 * instead of quietly changing what the gate decides. It also checks the org's schema: the field
 * exists, its sync field is a date and time, and its match key is text.
 */
public with sharing class FieldTrustContract {
    public static final Set<String> TIERS = new Set<String>{ 'context', 'advisory', 'action' };
    private static final Pattern FIELD_NAME = Pattern.compile('[A-Za-z]\\w*\\.[A-Za-z]\\w*');
    private static Map<String, Schema.SObjectType> objects;

    @TestVisible
    private static List<Field_Trust_Contract__mdt> rowsForTest;

    public class ContractException extends Exception {}

    public static List<Field_Trust_Contract__mdt> rows() {
        return rowsForTest != null ? rowsForTest : Field_Trust_Contract__mdt.getAll().values();
    }

    public static Map<String, Field_Trust_Contract__mdt> rowsByField() {
        Map<String, Field_Trust_Contract__mdt> out = new Map<String, Field_Trust_Contract__mdt>();
        for (Field_Trust_Contract__mdt row : rows()) {
            out.put(row.Field__c, row);
        }
        return out;
    }

    /** The contract as the gate uses it; throws ContractException listing every problem. */
    public static Map<String, FieldTrustGate.Rule> load() {
        List<Field_Trust_Contract__mdt> all = rows();
        List<String> problems = validate(all);
        if (!problems.isEmpty()) {
            throw new ContractException('Field trust contract: ' + String.join(problems, '; '));
        }
        Map<String, FieldTrustGate.Rule> contract = new Map<String, FieldTrustGate.Rule>();
        for (Field_Trust_Contract__mdt row : all) {
            contract.put(row.Field__c, new FieldTrustGate.Rule(
                row.Authority__c, row.Freshness_SLA_Hours__c, writers(row.Allowed_Writers__c), row.Tier__c));
        }
        return contract;
    }

    public static List<String> validate(List<Field_Trust_Contract__mdt> all) {
        List<String> problems = new List<String>();
        if (all == null || all.isEmpty()) {
            return new List<String>{ 'the contract needs at least one Field_Trust_Contract__mdt row' };
        }
        Set<String> seen = new Set<String>();
        for (Field_Trust_Contract__mdt row : all) {
            String name = String.isBlank(row.Field__c) ? '(no field)' : row.Field__c;
            if (String.isBlank(row.Field__c) || !FIELD_NAME.matcher(row.Field__c).matches()) {
                problems.add(name + ': field names look like Object.Field');
            } else if (!seen.add(row.Field__c)) {
                problems.add(name + ': more than one contract row');
            } else {
                problems.addAll(schemaProblems(row));
            }
            if (String.isBlank(row.Authority__c)) {
                problems.add(name + ': authority must name a system');
            }
            if (row.Freshness_SLA_Hours__c == null || row.Freshness_SLA_Hours__c <= 0) {
                problems.add(name + ': freshness SLA must be a positive number of hours');
            }
            if (writers(row.Allowed_Writers__c).isEmpty()) {
                problems.add(name + ': allowed writers must list at least one writer label');
            }
            if (row.Tier__c == null || !TIERS.contains(row.Tier__c)) {
                problems.add(name + ': tier must be one of context, advisory, action, not ' + row.Tier__c);
            }
        }
        return problems;
    }

    /** What the org's schema says is wrong with a row whose field name is well formed. */
    private static List<String> schemaProblems(Field_Trust_Contract__mdt row) {
        if (objects == null) {
            objects = Schema.getGlobalDescribe();
        }
        String name = row.Field__c;
        Schema.SObjectType type = objects.get(name.substringBefore('.'));
        if (type == null) {
            return new List<String>{ name + ': no such object' };
        }
        Map<String, Schema.SObjectField> fields = type.getDescribe().fields.getMap();
        List<String> problems = new List<String>();
        if (!fields.containsKey(name.substringAfter('.'))) {
            problems.add(name + ': no such field');
        }
        if (String.isNotBlank(row.Sync_Field__c) && (!fields.containsKey(row.Sync_Field__c)
                || fields.get(row.Sync_Field__c).getDescribe().getType() != Schema.DisplayType.DATETIME)) {
            problems.add(name + ': the sync field must be a date and time field on the same object, not ' + row.Sync_Field__c);
        }
        if (String.isNotBlank(row.Match_Key_Field__c) && (!fields.containsKey(row.Match_Key_Field__c)
                || fields.get(row.Match_Key_Field__c).getDescribe().getSoapType() != Schema.SoapType.STRING)) {
            problems.add(name + ': the match key must be a text field on the same object, not ' + row.Match_Key_Field__c);
        }
        return problems;
    }

    /** Comma-separated writer labels, trimmed; case is kept, as the gate compares exactly. */
    public static Set<String> writers(String csv) {
        Set<String> out = new Set<String>();
        if (csv == null) {
            return out;
        }
        for (String w : csv.split(',')) {
            if (String.isNotBlank(w)) {
                out.add(w.trim());
            }
        }
        return out;
    }
}
