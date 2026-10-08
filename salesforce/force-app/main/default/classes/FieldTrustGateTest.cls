@IsTest
private class FieldTrustGateTest {
    /** Every case in FieldTrustGateCases was answered by gate.py; Apex must answer the same. */
    @IsTest
    static void answersEveryCaseExactlyAsGatePyDoes() {
        StaticResource resource = [SELECT Body FROM StaticResource WHERE Name = 'FieldTrustGateCases' LIMIT 1];
        Map<String, Object> data = (Map<String, Object>) JSON.deserializeUntyped(resource.Body.toString());
        Map<String, Object> fields = (Map<String, Object>) ((Map<String, Object>) data.get('contract')).get('fields');
        Map<String, FieldTrustGate.Rule> contract = new Map<String, FieldTrustGate.Rule>();
        for (String name : fields.keySet()) {
            Map<String, Object> f = (Map<String, Object>) fields.get(name);
            Set<String> writers = new Set<String>();
            for (Object w : (List<Object>) f.get('allowed_writers')) {
                writers.add((String) w);
            }
            contract.put(name, new FieldTrustGate.Rule((String) f.get('authority'),
                Decimal.valueOf(String.valueOf(f.get('freshness_sla_hours'))), writers, (String) f.get('tier')));
        }
        Datetime now = Datetime.newInstanceGmt(2026, 1, 1, 0, 0, 0);
        Integer checked = 0;
        for (Object c : (List<Object>) data.get('cases')) {
            Map<String, Object> expected = (Map<String, Object>) c;
            List<FieldTrustGate.FieldRead> reads = new List<FieldTrustGate.FieldRead>();
            for (Object r : (List<Object>) expected.get('reads')) {
                Map<String, Object> m = (Map<String, Object>) r;
                Integer hoursAgo = (Integer) m.get('hours_ago');
                reads.add(new FieldTrustGate.FieldRead((String) m.get('name'), (String) m.get('source_system'),
                    now.addHours(-hoursAgo), (Integer) m.get('matches'), (String) m.get('last_writer')));
            }
            FieldTrustGate.Decision d = FieldTrustGate.evaluate(reads, contract, now);
            List<String> reasons = new List<String>();
            for (Object s : (List<Object>) expected.get('reasons')) {
                reasons.add((String) s);
            }
            // equals(), not ==: the comparison must be case-sensitive, as Python's is
            System.assert(((String) expected.get('decision')).equals(d.decision),
                'case ' + checked + ' decision: expected ' + expected.get('decision') + ', got ' + d.decision);
            System.assert(String.join(reasons, '|').equals(String.join(d.reasons, '|')),
                'case ' + checked + ' reasons: expected ' + reasons + ', got ' + d.reasons);
            checked++;
        }
        System.assert(checked >= 400, 'expected the full case file, read ' + checked);
    }

    @IsTest
    static void aMissingSyncTimeCountsAsStale() {
        Map<String, FieldTrustGate.Rule> contract = new Map<String, FieldTrustGate.Rule>{
            'Account.ARR__c' => new FieldTrustGate.Rule('billing', 36, new Set<String>{ 'billing_sync' }, 'action') };
        FieldTrustGate.Decision d = FieldTrustGate.evaluate(new List<FieldTrustGate.FieldRead>{
            new FieldTrustGate.FieldRead('Account.ARR__c', 'billing', null, 1, 'billing_sync') }, contract, null);
        System.assertEquals('ask', d.decision);
        System.assertEquals(new List<String>{ 'Account.ARR__c: older than its SLA' }, d.reasons);
    }
}
