/**
 * The actions end to end inside Apex. Salesforce writes no field history in Apex tests, so a fake
 * history source stands in for it; the contract, writer labels and a second (reviewing) user are
 * set up here too, so your org's own setup never breaks these tests. Records, the custom
 * permission and Agent_Write__c provenance are real. The tests run as the admin who deploys, so
 * object and field permissions are checked by FieldTrustPermissionsTest and by the end-to-end run.
 */
@IsTest
private class FieldTrustTest {
    class FakeHistory implements FieldTrustReads.HistorySource {
        Map<String, FieldTrustReads.HistoryRow> rows = new Map<String, FieldTrustReads.HistoryRow>();

        public FieldTrustReads.HistoryRow latest(Id recordId, String objectName, String fieldName) {
            return rows.get(recordId + '.' + fieldName);
        }

        void wrote(Id recordId, String field, Id userId, Integer hoursAgo) {
            rows.put(recordId + '.' + field, new FieldTrustReads.HistoryRow(userId, Datetime.now().addHours(-hoursAgo)));
        }
    }

    static Id fakeUser(Integer n) {
        return Id.valueOf('005' + String.valueOf(n).leftPad(12, '0'));
    }

    static final Id BILLING = fakeUser(1);
    static final Id CONTRACTS = fakeUser(2);
    static final Id AGENT = fakeUser(4);
    static final Id STRANGER = fakeUser(5);
    /** The rep who reviews drafts. A real user, since confirmations store who confirmed. */
    static Id rep;
    static FakeHistory history;

    static Field_Trust_Writer__mdt writer(String label, String systemName) {
        return new Field_Trust_Writer__mdt(Match_On__c = 'Username', Match_Value__c = label + '@example.com',
            Writer_Label__c = label, System__c = systemName);
    }

    static Field_Trust_Contract__mdt rule(String field, String authority, Decimal sla, String writers, String tier,
                                          String syncField, String matchKey) {
        return new Field_Trust_Contract__mdt(Field__c = field, Authority__c = authority, Freshness_SLA_Hours__c = sla,
            Allowed_Writers__c = writers, Tier__c = tier, Sync_Field__c = syncField, Match_Key_Field__c = matchKey);
    }

    /**
     * Give the running user exactly these of the two permission sets, whatever it held before (an
     * admin who runs the tests may be a reviewer). The test rolls it all back.
     */
    static void holdOnly(List<String> names) {
        System.runAs(new User(Id = UserInfo.getUserId())) {
            delete [SELECT Id FROM PermissionSetAssignment WHERE AssigneeId = :UserInfo.getUserId()
                    AND PermissionSet.Name IN ('FieldTrust_Agent', 'FieldTrust_Reviewer')];
            List<PermissionSetAssignment> grants = new List<PermissionSetAssignment>();
            for (PermissionSet ps : [SELECT Id FROM PermissionSet WHERE Name IN :names]) {
                grants.add(new PermissionSetAssignment(AssigneeId = UserInfo.getUserId(), PermissionSetId = ps.Id));
            }
            insert grants;
        }
    }

    /**
     * The running user plays the agent and holds FieldTrust_Agent, plus FieldTrust_Reviewer when
     * `withReviewer` is true. Run the test body as the returned user: permission sets assigned
     * inside a test apply only inside System.runAs.
     */
    static User wire(Boolean withReviewer) {
        holdOnly(withReviewer ? new List<String>{ 'FieldTrust_Agent', 'FieldTrust_Reviewer' }
            : new List<String>{ 'FieldTrust_Agent' });
        rep = [SELECT Id FROM User WHERE Id != :UserInfo.getUserId() AND IsActive = true AND UserType = 'Standard'
               ORDER BY CreatedDate LIMIT 1].Id;
        history = new FakeHistory();
        FieldTrustReads.history = history;
        FieldTrustContract.rowsForTest = new List<Field_Trust_Contract__mdt>{
            rule('Account.ARR__c', 'billing', 36, 'billing_sync', 'action', 'ARR_Synced_At__c', 'Website'),
            rule('Opportunity.Renewal_Date__c', 'contracts', 36, 'contract_sync', 'action', 'Renewal_Synced_At__c', null),
            rule('Contact.Buying_Role__c', 'crm', 4320, 'sales_rep, agent:renewal', 'action', null, null) };
        FieldTrustReads.writerRowsForTest = new List<Field_Trust_Writer__mdt>{
            writer('billing_sync', 'billing'), writer('contract_sync', 'contracts'),
            writer('sales_rep', 'crm'), writer('agent:renewal', 'crm') };
        FieldTrustReads.labelCache.put(BILLING, 'billing_sync');
        FieldTrustReads.labelCache.put(CONTRACTS, 'contract_sync');
        FieldTrustReads.labelCache.put(rep, 'sales_rep');
        FieldTrustReads.labelCache.put(AGENT, 'agent:renewal');
        FieldTrustReads.labelCache.put(STRANGER, 'unmapped');
        FieldTrustReads.labelCache.put(UserInfo.getUserId(), 'agent:renewal');
        return new User(Id = UserInfo.getUserId());
    }

    static User wire() {
        return wire(true);
    }

    static Account account(String website, Integer syncedHoursAgo, Id arrWriter) {
        Account a = new Account(Name = 'Customer', Website = website, ARR__c = 120000,
            ARR_Synced_At__c = Datetime.now().addHours(-syncedHoursAgo));
        insert a;
        history.wrote(a.Id, 'ARR__c', arrWriter, 3);
        return a;
    }

    static Opportunity renewal(Account a) {
        Opportunity o = new Opportunity(Name = 'Renewal', AccountId = a.Id, StageName = 'Prospecting',
            CloseDate = Date.today().addDays(60), Amount = 100000, Renewal_Date__c = Date.today().addDays(90),
            Renewal_Synced_At__c = Datetime.now().addHours(-1));
        insert o;
        history.wrote(o.Id, 'Renewal_Date__c', CONTRACTS, 200);   // unchanged for weeks, synced an hour ago
        return o;
    }

    static Contact contact(Account a, String role) {
        Contact c = new Contact(LastName = 'Buyer', AccountId = a.Id, Buying_Role__c = role);
        insert c;
        return c;
    }

    static FieldTrustUpdateRenewalQuote.Result updateQuote(String oppId, Decimal amount) {
        FieldTrustUpdateRenewalQuote.Request r = new FieldTrustUpdateRenewalQuote.Request();
        r.opportunityId = oppId;
        r.amount = amount;
        return FieldTrustUpdateRenewalQuote.run(new List<FieldTrustUpdateRenewalQuote.Request>{ r })[0];
    }

    static FieldTrustSetQuoteRecipient.Result setRecipient(String oppId, String contactId) {
        FieldTrustSetQuoteRecipient.Request r = new FieldTrustSetQuoteRecipient.Request();
        r.opportunityId = oppId;
        r.contactId = contactId;
        return FieldTrustSetQuoteRecipient.run(new List<FieldTrustSetQuoteRecipient.Request>{ r })[0];
    }

    static FieldTrustSetContactRole.Result setRole(String contactId, String role) {
        FieldTrustSetContactRole.Request r = new FieldTrustSetContactRole.Request();
        r.contactId = contactId;
        r.role = role;
        r.evidence = 'email signature';
        return FieldTrustSetContactRole.run(new List<FieldTrustSetContactRole.Request>{ r })[0];
    }

    static FieldTrustReview.Result approve(Id agentWriteId) {
        FieldTrustReview.Request r = new FieldTrustReview.Request();
        r.agentWriteId = agentWriteId;
        return FieldTrustReview.run(new List<FieldTrustReview.Request>{ r })[0];
    }

    static Id draftId() {
        return [SELECT Id FROM Agent_Write__c WHERE Status__c = 'Draft' ORDER BY CreatedDate DESC, Id DESC LIMIT 1].Id;
    }

    @IsTest
    static void cleanInputsProceedAndTheWriteIsStamped() {
        System.runAs(wire()) {
            Opportunity o = renewal(account('https://www.customer1.example/', 2, BILLING));
            FieldTrustUpdateRenewalQuote.Result r = updateQuote(o.Id, 126000);
            System.assertEquals('proceed', r.decision, r.reasons);
            System.assertEquals('Done.', r.message);
            System.assertEquals(126000, [SELECT Amount FROM Opportunity WHERE Id = :o.Id].Amount);
            Agent_Write__c w = [SELECT Status__c, Field__c, Value__c, Agent__c, Inputs__c FROM Agent_Write__c];
            System.assertEquals('Written', w.Status__c);
            System.assertEquals('Opportunity.Amount', w.Field__c);
            System.assertEquals('126000', w.Value__c);
            System.assertEquals('agent:renewal', w.Agent__c);
            System.assert(w.Inputs__c.contains('Account.ARR__c') && w.Inputs__c.contains('Opportunity.Renewal_Date__c'), w.Inputs__c);
        }
    }

    /** ARR a rep typed, three days old, on a duplicated account: the gate asks, with four reasons. */
    @IsTest
    static void aRepTypedStaleDuplicatedArrIsAskedAboutNotWritten() {
        System.runAs(wire()) {
            Account a = account('https://www.customer2.example/', 72, rep);
            insert new Account(Name = 'Customer (dup)', Website = 'http://customer2.example');
            Opportunity o = renewal(a);
            FieldTrustUpdateRenewalQuote.Result r = updateQuote(o.Id, 99750);
            System.assertEquals('ask', r.decision);
            System.assertEquals('Account.ARR__c: authority is billing, not crm\n'
                + 'Account.ARR__c: older than its SLA\n'
                + 'Account.ARR__c: 2 records match, expected 1\n'
                + 'Account.ARR__c: last written by sales_rep', r.reasons);
            System.assert(r.message.startsWith('Not changed.'), r.message);
            System.assertEquals(100000, [SELECT Amount FROM Opportunity WHERE Id = :o.Id].Amount);
            Agent_Write__c w = [SELECT Status__c, Reasons__c FROM Agent_Write__c];
            System.assertEquals('Held', w.Status__c);
            System.assertEquals(r.reasons, w.Reasons__c);
        }
    }

    /** A renewal date with no fresh sync stamp is stale, however recently the record was saved. */
    @IsTest
    static void aRenewalDateWithoutAFreshSyncIsStale() {
        System.runAs(wire()) {
            Opportunity o = renewal(account('https://www.customer15.example/', 2, BILLING));
            update new Opportunity(Id = o.Id, Renewal_Synced_At__c = Datetime.now().addHours(-48));
            FieldTrustUpdateRenewalQuote.Result r = updateQuote(o.Id, 126000);
            System.assertEquals('ask', r.decision);
            System.assertEquals('Opportunity.Renewal_Date__c: older than its SLA', r.reasons);
        }
    }

    /** The feedback loop: a guess, a draft, a person's approval, and then the guess is trusted. */
    @IsTest
    static void aGuessIsADraftUntilAPersonConfirmsIt() {
        System.runAs(wire()) {
            Account a = account('https://www.customer3.example/', 2, BILLING);
            Opportunity o = renewal(a);
            Contact c = contact(a, null);

            FieldTrustSetContactRole.Result filled = setRole(c.Id, 'Economic Buyer');
            System.assertEquals('written', filled.decision, filled.reasons);
            System.assertEquals('Economic Buyer', [SELECT Buying_Role__c FROM Contact WHERE Id = :c.Id].Buying_Role__c);
            System.assertEquals('ask', setRole(c.Id, 'Champion').decision, 'a guess is never overwritten by another');
            history.wrote(c.Id, 'Buying_Role__c', AGENT, 1);   // what field history records for the agent's write

            FieldTrustSetQuoteRecipient.Result sent = setRecipient(o.Id, c.Id);
            System.assertEquals('draft', sent.decision);
            System.assertEquals('lowest input tier is advisory', sent.reasons);
            System.assert(sent.message.startsWith('Not applied yet'), sent.message);
            System.assertEquals(null, [SELECT Quote_Recipient__c FROM Opportunity WHERE Id = :o.Id].Quote_Recipient__c);

            FieldTrustReview.approverForTest = rep;               // a rep reviews it
            System.assertEquals('approved', approve(draftId()).decision);
            System.assertEquals(c.Id, [SELECT Quote_Recipient__c FROM Opportunity WHERE Id = :o.Id].Quote_Recipient__c);
            Agent_Write__c guess = [SELECT Confirmed_By__c FROM Agent_Write__c WHERE Field__c = 'Contact.Buying_Role__c'];
            System.assertEquals(rep, guess.Confirmed_By__c);

            System.assertEquals('proceed', setRecipient(renewal(a).Id, c.Id).decision, 'a confirmed role is action-grade again');
        }
    }

    /** A confirmation holds for the value a person saw, not for whatever the field holds later. */
    @IsTest
    static void aConfirmationDoesNotCarryOverToALaterValue() {
        System.runAs(wire()) {
            Account a = account('https://www.customer9.example/', 2, BILLING);
            Contact c = contact(a, null);
            setRole(c.Id, 'Economic Buyer');
            history.wrote(c.Id, 'Buying_Role__c', AGENT, 1);
            System.assertEquals('draft', setRecipient(renewal(a).Id, c.Id).decision);
            FieldTrustReview.approverForTest = rep;
            System.assertEquals('approved', approve(draftId()).decision);

            update new Contact(Id = c.Id, Buying_Role__c = 'Champion');   // the agent's user changes it, unstamped
            history.wrote(c.Id, 'Buying_Role__c', AGENT, 0);
            System.assertEquals('draft', setRecipient(renewal(a).Id, c.Id).decision,
                'the old confirmation must not cover the new value');
        }
    }

    /** Nor for the same value written again later: the field's history is newer than the confirmation. */
    @IsTest
    static void aConfirmationDoesNotCoverALaterWriteOfTheSameValue() {
        System.runAs(wire()) {
            Account a = account('https://www.customer16.example/', 2, BILLING);
            Contact c = contact(a, null);
            setRole(c.Id, 'Economic Buyer');
            history.wrote(c.Id, 'Buying_Role__c', AGENT, 1);
            System.assertEquals('draft', setRecipient(renewal(a).Id, c.Id).decision);
            FieldTrustReview.approverForTest = rep;
            System.assertEquals('approved', approve(draftId()).decision);
            System.assertEquals('proceed', setRecipient(renewal(a).Id, c.Id).decision);

            history.wrote(c.Id, 'Buying_Role__c', AGENT, -1);   // changed and set back after the confirmation
            System.assertEquals('draft', setRecipient(renewal(a).Id, c.Id).decision);
        }
    }

    /** An old draft never overwrites a later change: approval closes it instead. */
    @IsTest
    static void aStaleDraftIsClosedNotApplied() {
        System.runAs(wire()) {
            Account a = account('https://www.customer17.example/', 2, BILLING);
            Opportunity o = renewal(a);
            Contact guessed = contact(a, 'Champion');
            Contact chosen = contact(a, 'Economic Buyer');
            history.wrote(guessed.Id, 'Buying_Role__c', AGENT, 1);
            System.assertEquals('draft', setRecipient(o.Id, guessed.Id).decision);
            Id draft = draftId();
            System.assertEquals(null, [SELECT Previous_Value__c FROM Agent_Write__c WHERE Id = :draft].Previous_Value__c);

            update new Opportunity(Id = o.Id, Quote_Recipient__c = chosen.Id);   // a rep picks someone meanwhile
            FieldTrustReview.approverForTest = rep;
            FieldTrustReview.Result r = approve(draft);
            System.assertEquals('ask', r.decision);
            System.assertEquals('Opportunity.Quote_Recipient__c changed after this draft was proposed', r.reasons);
            System.assertEquals(chosen.Id, [SELECT Quote_Recipient__c FROM Opportunity WHERE Id = :o.Id].Quote_Recipient__c);
            Agent_Write__c closed = [SELECT Status__c, Reasons__c FROM Agent_Write__c WHERE Id = :draft];
            System.assertEquals('Held', closed.Status__c);
            System.assert(closed.Reasons__c.endsWith('changed after this draft was proposed'), closed.Reasons__c);
            System.assertEquals('this draft is no longer waiting for review', approve(draft).reasons);
        }
    }

    @IsTest
    static void anAgentCannotApprove() {
        System.runAs(wire()) {
            Account a = account('https://www.customer18.example/', 2, BILLING);
            Contact c = contact(a, 'Champion');
            history.wrote(c.Id, 'Buying_Role__c', AGENT, 1);
            System.assertEquals('draft', setRecipient(renewal(a).Id, c.Id).decision);
            FieldTrustReview.approverForTest = AGENT;   // a second agent user given the permission
            System.assertEquals('drafts are approved by a person, not an agent', approve(draftId()).reasons);
        }
    }

    @IsTest
    static void approvalRechecksTheInputsAndHappensOnce() {
        System.runAs(wire()) {
            Account a = account('https://www.customer5.example/', 2, BILLING);
            Opportunity o = renewal(a);
            Contact c = contact(a, 'Champion');
            history.wrote(c.Id, 'Buying_Role__c', AGENT, 1);
            System.assertEquals('draft', setRecipient(o.Id, c.Id).decision);
            Id draft = draftId();

            System.assertEquals('drafts are approved by a person, not an agent', approve(draft).reasons);

            FieldTrustReview.approverForTest = rep;
            history.wrote(c.Id, 'Buying_Role__c', STRANGER, 0);   // someone unknown changed the role since
            FieldTrustReview.Result refused = approve(draft);
            System.assertEquals('ask', refused.decision);
            System.assert(refused.reasons.contains('last written by unmapped'), refused.reasons);
            System.assertEquals('Draft', [SELECT Status__c FROM Agent_Write__c WHERE Id = :draft].Status__c);

            history.wrote(c.Id, 'Buying_Role__c', rep, 0);
            System.assertEquals('approved', approve(draft).decision);
            System.assertEquals('this draft is no longer waiting for review', approve(draft).reasons);
        }
    }

    @IsTest
    static void approvalNeedsTheCustomPermission() {
        System.runAs(wire(false)) {
            Account a = account('https://www.customer10.example/', 2, BILLING);
            Contact c = contact(a, 'Champion');
            history.wrote(c.Id, 'Buying_Role__c', AGENT, 1);
            System.assertEquals('draft', setRecipient(renewal(a).Id, c.Id).decision);
            FieldTrustReview.approverForTest = rep;
            System.assertEquals('approving drafts needs the Field Trust: Approve Drafts permission', approve(draftId()).reasons);
        }
    }

    /** Approval trusts only which write a draft proposes, so a hand-made draft cannot widen it. */
    @IsTest
    static void aForgedDraftIsNotApproved() {
        System.runAs(wire()) {
            Account a = account('https://www.customer11.example/', 2, BILLING);
            Opportunity o = renewal(a);
            Agent_Write__c forged = new Agent_Write__c(Record_Id__c = o.Id, Field__c = 'Opportunity.Name',
                Value__c = 'Renamed', Status__c = 'Draft', Inputs__c = '[]');
            Agent_Write__c mismatched = new Agent_Write__c(Record_Id__c = a.Id, Field__c = 'Opportunity.Amount',
                Value__c = '1', Status__c = 'Draft');
            Agent_Write__c nowhere = new Agent_Write__c(Record_Id__c = 'nope', Field__c = 'Opportunity.Amount',
                Value__c = '1', Status__c = 'Draft');
            insert new List<Agent_Write__c>{ forged, mismatched, nowhere };
            FieldTrustReview.approverForTest = rep;
            System.assertEquals('Opportunity.Name is not a write these actions make', approve(forged.Id).reasons);
            System.assertEquals('the draft\'s field does not belong to its record', approve(mismatched.Id).reasons);
            System.assertEquals('the draft names no valid record', approve(nowhere.Id).reasons);
            System.assertEquals('Renewal', [SELECT Name FROM Opportunity WHERE Id = :o.Id].Name);
        }
    }

    @IsTest
    static void writesThatMakeNoSenseAreRefused() {
        System.runAs(wire()) {
            Account a = account('https://www.customer12.example/', 2, BILLING);
            Opportunity o = renewal(a);
            System.assertEquals('the amount must be a positive number', updateQuote(o.Id, 0).reasons);
            Contact elsewhere = contact(account('https://www.customer13.example/', 2, BILLING), 'Champion');
            System.assert(setRecipient(o.Id, elsewhere.Id).reasons.endsWith('is not on this opportunity\'s account'));
            update new Opportunity(Id = o.Id, StageName = 'Closed Lost');
            System.assert(updateQuote(o.Id, 5).reasons.endsWith('is closed'));
            System.assertEquals(0, [SELECT COUNT() FROM Agent_Write__c], 'refused writes leave no row');
        }
    }

    @IsTest
    static void onlyADedicatedAgentUserMayRunTheActions() {
        System.runAs(wire()) {
            Account a = account('https://www.customer14.example/', 2, BILLING);
            Opportunity o = renewal(a);
            FieldTrustReads.labelCache.put(UserInfo.getUserId(), 'sales_rep');   // an agent running as a person
            System.assert(updateQuote(o.Id, 1).reasons.startsWith('these actions run only as a dedicated agent user'));
            System.assert(setRole(contact(a, null).Id, 'Champion').reasons.startsWith('these actions run only'));
        }
    }

    @IsTest
    static void aBrokenContractFailsClosed() {
        System.runAs(wire()) {
            Opportunity o = renewal(account('https://www.customer6.example/', 2, BILLING));
            FieldTrustContract.rowsForTest = new List<Field_Trust_Contract__mdt>{
                rule('Account.ARR__c', 'billing', 36, 'billing_sync', 'Action', null, null) };
            FieldTrustUpdateRenewalQuote.Result r = updateQuote(o.Id, 1);
            System.assertEquals('ask', r.decision);
            System.assertEquals('gate error: FieldTrustContract.ContractException', r.reasons);
            System.assertEquals(100000, [SELECT Amount FROM Opportunity WHERE Id = :o.Id].Amount);
        }
    }

    @IsTest
    static void unknownRecordsAreAskedAboutNotWritten() {
        System.runAs(wire()) {
            System.assertEquals('no renewal opportunity nope', updateQuote('nope', 1).reasons);
            Account a = account('https://www.customer7.example/', 2, BILLING);
            Opportunity o = renewal(a);
            System.assertEquals('no contact nope', setRecipient(o.Id, 'nope').reasons);
            System.assertEquals('no renewal opportunity nope', setRecipient('nope', 'nope').reasons);
            System.assertEquals('ask', setRole(o.Id, 'Champion').decision, 'an opportunity id is not a contact id');
            Account gone = account('https://www.customer8.example/', 2, BILLING);
            delete gone;
            FieldTrustGate.FieldRead read = FieldTrustReads.read(gone.Id, 'ARR__c');
            System.assertEquals(0, read.matches);
            System.assertEquals('unknown', read.lastWriter);
        }
    }

    @IsTest
    static void readHelpers() {
        System.assertEquals(new List<String>{ 'AccountHistory', 'AccountId' }, FieldTrustReads.historyTable('Account'));
        System.assertEquals(new List<String>{ 'Invoice__History', 'ParentId' }, FieldTrustReads.historyTable('Invoice__c'));
        try {
            FieldTrustReads.historyTable('Asset');
            System.assert(false, 'an object without a known history table must fail');
        } catch (FieldTrustContract.ContractException e) {
            System.assert(e.getMessage().contains('Asset'));
        }
        System.assertEquals('customer1.example', FieldTrustReads.normalizeKey('https://www.Customer1.example/about'));
        System.assertEquals('customer1.example', FieldTrustReads.normalizeKey('http://customer1.example'));
        System.assertEquals(1, FieldTrustReads.countMatches('Account', 'Website', ''));
        insert new List<Account>{
            new Account(Name = 'Same', Website = 'HTTPS://WWW.Customer.example/about'),
            new Account(Name = 'Same', Website = 'customer.example'),
            new Account(Name = 'Other', Website = 'https://bigcustomer.example/'),
            new Account(Name = 'Other', Website = 'customer.example.org'),
            new Account(Name = 'Other', Website = 'https://www.customer.examples/') };
        System.assertEquals(2, FieldTrustReads.countMatches('Account', 'Website', 'http://customer.example'));
        System.assertEquals(1, FieldTrustReads.countMatches('Account', 'Website', 'customer.example.org'));
        System.assertEquals('agent', FieldTrustReads.systemOf('agent:other'));
        System.assertEquals('unknown', FieldTrustReads.systemOf('somebody'));
        System.assertEquals('unknown', FieldTrustReads.writerLabel(null));
        Account a = new Account(Name = 'History');
        insert a;
        System.assertEquals(null, new FieldTrustReads.OrgHistory().latest(a.Id, 'Account', 'ARR__c'),
            'no history is written inside Apex tests');
    }

    @IsTest
    static void writerLabelsComeFromUsernameThenPermissionSetThenProfile() {
        User me = [SELECT Username, Profile.Name FROM User WHERE Id = :UserInfo.getUserId()];
        holdOnly(new List<String>{ 'FieldTrust_Agent' });
        Field_Trust_Writer__mdt byProfile = new Field_Trust_Writer__mdt(Match_On__c = 'Profile',
            Match_Value__c = me.Profile.Name.toUpperCase(), Writer_Label__c = 'sales_rep', System__c = 'crm');
        Field_Trust_Writer__mdt bySet = new Field_Trust_Writer__mdt(Match_On__c = 'Permission Set',
            Match_Value__c = 'fieldtrust_agent', Writer_Label__c = 'agent:renewal', System__c = 'crm');
        Field_Trust_Writer__mdt byName = new Field_Trust_Writer__mdt(Match_On__c = 'Username',
            Match_Value__c = me.Username.toUpperCase(), Writer_Label__c = 'billing_sync', System__c = 'billing');

        FieldTrustReads.writerRowsForTest = new List<Field_Trust_Writer__mdt>{ byProfile };
        System.assertEquals('sales_rep', FieldTrustReads.writerLabel(me.Id));
        FieldTrustReads.labelCache.clear();
        FieldTrustReads.writerRowsForTest = new List<Field_Trust_Writer__mdt>{ byProfile, bySet };
        System.assertEquals('agent:renewal', FieldTrustReads.writerLabel(me.Id));
        FieldTrustReads.labelCache.clear();
        FieldTrustReads.writerRowsForTest = new List<Field_Trust_Writer__mdt>{ byProfile, bySet, byName };
        System.assertEquals('billing_sync', FieldTrustReads.writerLabel(me.Id));
        System.assertEquals('billing', FieldTrustReads.systemOf('billing_sync'));
        FieldTrustReads.labelCache.clear();
        FieldTrustReads.writerRowsForTest = new List<Field_Trust_Writer__mdt>();
        System.assertEquals('unmapped', FieldTrustReads.writerLabel(me.Id));

        holdOnly(new List<String>{ 'FieldTrust_Agent', 'FieldTrust_Reviewer' });   // a misconfigured agent user
        Field_Trust_Writer__mdt asPerson = new Field_Trust_Writer__mdt(Match_On__c = 'Permission Set',
            Match_Value__c = 'FieldTrust_Reviewer', Writer_Label__c = 'sales_rep', System__c = 'crm');
        FieldTrustReads.labelCache.clear();
        FieldTrustReads.writerRowsForTest = new List<Field_Trust_Writer__mdt>{ asPerson, bySet };
        System.assertEquals('agent:renewal', FieldTrustReads.writerLabel(me.Id), 'an agent never reads as a person');
    }

    @IsTest
    static void storedValuesComeBackInTheirOwnType() {
        Account a = new Account(Name = 'Types');
        insert a;
        Datetime syncedAt = Datetime.newInstanceGmt(2027, 1, 15, 8, 30, 0);
        System.assertEquals(syncedAt, FieldTrustService.typed(a.Id, 'ARR_Synced_At__c', FieldTrustService.stored(syncedAt)));
        Decimal arr = Decimal.valueOf('126000.50');
        System.assert(FieldTrustService.sameValue(arr, FieldTrustService.typed(a.Id, 'ARR__c', FieldTrustService.stored(arr))));
        System.assertEquals(42, FieldTrustService.typed(a.Id, 'NumberOfEmployees', '42'));
        System.assertEquals('Types', FieldTrustService.typed(a.Id, 'Name', 'Types'));
        System.assertEquals(null, FieldTrustService.typed(a.Id, 'Name', null));
        System.assertEquals(null, FieldTrustService.stored(null));
        Opportunity o = new Opportunity(Name = 'Types', StageName = 'Prospecting', CloseDate = Date.today());
        insert o;
        System.assertEquals(Date.newInstance(2027, 1, 15), FieldTrustService.typed(o.Id, 'CloseDate', '2027-01-15'));
        System.assertEquals(true, FieldTrustService.typed(o.Id, 'IsPrivate', 'true'));
        System.assert(FieldTrustService.sameValue(Decimal.valueOf('126000'), Decimal.valueOf('126000.00')));
        System.assert(!FieldTrustService.sameValue('Champion', 'champion'));
        System.assert(FieldTrustService.sameValue(null, null) && !FieldTrustService.sameValue(null, 'x'));
    }

    @IsTest
    static void messagesForEachDecision() {
        System.assertEquals('Done.', new FieldTrustService.Outcome('approved', new List<String>(), null).message());
        System.assert(new FieldTrustService.Outcome('draft', new List<String>{ 'x' }, null).message().contains('(x)'));
        System.assert(new FieldTrustService.Outcome('ask', new List<String>{ 'y' }, null).message().endsWith('y'));
    }
}
