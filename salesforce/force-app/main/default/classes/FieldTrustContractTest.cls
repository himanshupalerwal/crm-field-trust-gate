@IsTest
private class FieldTrustContractTest {
    static Field_Trust_Contract__mdt row(String field, String authority, Decimal sla, String writers, String tier) {
        return new Field_Trust_Contract__mdt(Field__c = field, Authority__c = authority,
            Freshness_SLA_Hours__c = sla, Allowed_Writers__c = writers, Tier__c = tier);
    }

    /** Whatever contract your org deploys must load: rows well formed, and fields that exist. */
    @IsTest
    static void theDeployedContractIsValid() {
        List<Field_Trust_Contract__mdt> rows = Field_Trust_Contract__mdt.getAll().values();
        System.assertEquals(new List<String>(), FieldTrustContract.validate(rows));
        System.assertEquals(rows.size(), FieldTrustContract.load().size());
    }

    @IsTest
    static void fieldsMustExistWithTheRightTypes() {
        Field_Trust_Contract__mdt typo = row('Account.ARR_c', 'billing', 36, 'billing_sync', 'action');
        Field_Trust_Contract__mdt nowhere = row('Invoice__c.Total__c', 'billing', 36, 'billing_sync', 'action');
        Field_Trust_Contract__mdt types = row('Account.ARR__c', 'billing', 36, 'billing_sync', 'action');
        types.Sync_Field__c = 'Website';
        types.Match_Key_Field__c = 'ARR__c';
        Field_Trust_Contract__mdt good = row('Opportunity.Renewal_Date__c', 'contracts', 36, 'contract_sync', 'action');
        good.Sync_Field__c = 'renewal_synced_at__c';
        List<String> problems = FieldTrustContract.validate(new List<Field_Trust_Contract__mdt>{ typo, nowhere, types, good });
        System.assertEquals(new List<String>{
            'Account.ARR_c: no such field',
            'Invoice__c.Total__c: no such object',
            'Account.ARR__c: the sync field must be a date and time field on the same object, not Website',
            'Account.ARR__c: the match key must be a text field on the same object, not ARR__c' }, problems);
    }

    @IsTest
    static void eachMistakeIsReported() {
        List<Field_Trust_Contract__mdt> rows = new List<Field_Trust_Contract__mdt>{
            row('ARR', 'billing', 36, 'billing_sync', 'action'),
            row('Account.Health_Score__c', '', 0, ' , ', 'Action'),
            row('Account.Description', 'crm', 8760, 'sales_rep', 'context'),
            row('Account.Description', 'crm', 8760, 'sales_rep', 'context') };
        List<String> problems = FieldTrustContract.validate(rows);
        System.assert(problems.contains('ARR: field names look like Object.Field'), problems);
        System.assert(problems.contains('Account.Health_Score__c: authority must name a system'), problems);
        System.assert(problems.contains('Account.Health_Score__c: freshness SLA must be a positive number of hours'), problems);
        System.assert(problems.contains('Account.Health_Score__c: allowed writers must list at least one writer label'), problems);
        System.assert(problems.contains('Account.Health_Score__c: tier must be one of context, advisory, action, not Action'), problems);
        System.assert(problems.contains('Account.Description: more than one contract row'), problems);
        System.assertEquals(new List<String>{ 'the contract needs at least one Field_Trust_Contract__mdt row' },
            FieldTrustContract.validate(new List<Field_Trust_Contract__mdt>()));
    }

    @IsTest
    static void loadStopsOnAnInvalidContract() {
        FieldTrustContract.rowsForTest = new List<Field_Trust_Contract__mdt>{
            row('Account.ARR__c', 'billing', 36, 'billing_sync', 'Action') };
        try {
            FieldTrustContract.load();
            System.assert(false, 'an invalid contract must not load');
        } catch (FieldTrustContract.ContractException e) {
            System.assert(e.getMessage().contains('tier must be one of'), e.getMessage());
        }
    }

    @IsTest
    static void writersAreTrimmedAndCaseIsKept() {
        System.assertEquals(new Set<String>{ 'sales_rep', 'Agent:Renewal' }, FieldTrustContract.writers(' sales_rep ,Agent:Renewal,,'));
        System.assertEquals(new Set<String>(), FieldTrustContract.writers(null));
    }
}
