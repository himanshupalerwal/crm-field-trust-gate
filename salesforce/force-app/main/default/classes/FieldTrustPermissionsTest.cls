/** The separation the pattern relies on, checked in the deployed permission sets. */
@IsTest
private class FieldTrustPermissionsTest {
    static Integer classAccess(String permissionSet, String className) {
        Id classId = [SELECT Id FROM ApexClass WHERE Name = :className LIMIT 1].Id;
        return [SELECT COUNT() FROM SetupEntityAccess WHERE Parent.Name = :permissionSet AND SetupEntityId = :classId];
    }

    static Integer customPermission(String permissionSet) {
        Id permissionId = [SELECT Id FROM CustomPermission WHERE DeveloperName = :FieldTrustReview.PERMISSION LIMIT 1].Id;
        return [SELECT COUNT() FROM SetupEntityAccess WHERE Parent.Name = :permissionSet AND SetupEntityId = :permissionId];
    }

    static ObjectPermissions provenance(String permissionSet) {
        return [SELECT PermissionsCreate, PermissionsRead, PermissionsEdit, PermissionsDelete FROM ObjectPermissions
                WHERE Parent.Name = :permissionSet AND SobjectType = 'Agent_Write__c' LIMIT 1];
    }

    @IsTest
    static void theAgentCanRunTheActionsButCannotApproveOrConfirm() {
        for (String action : new List<String>{
                'FieldTrustUpdateRenewalQuote', 'FieldTrustSetQuoteRecipient', 'FieldTrustSetContactRole' }) {
            System.assertEquals(1, classAccess('FieldTrust_Agent', action), action);
            System.assertEquals(0, classAccess('FieldTrust_Reviewer', action), action);
        }
        System.assertEquals(0, classAccess('FieldTrust_Agent', 'FieldTrustReview'), 'the agent must not approve');
        System.assertEquals(0, customPermission('FieldTrust_Agent'), 'the agent must not approve');
        ObjectPermissions agent = provenance('FieldTrust_Agent');
        System.assert(agent.PermissionsRead, 'the agent reads provenance to tell its guesses from confirmed values');
        System.assert(!agent.PermissionsCreate && !agent.PermissionsEdit && !agent.PermissionsDelete,
            'only the actions record provenance; the agent user must not create, edit or delete it');
        List<FieldPermissions> fields = [SELECT Field, PermissionsRead, PermissionsEdit FROM FieldPermissions
                                         WHERE Parent.Name = 'FieldTrust_Agent' AND SobjectType = 'Agent_Write__c'];
        System.assert(fields.size() >= 13, 'every provenance field must be readable by the agent');
        for (FieldPermissions f : fields) {
            System.assert(f.PermissionsRead && !f.PermissionsEdit, f.Field + ' must be read-only for the agent');
        }
    }

    /** People approve through the action, which records the confirmation; they cannot edit provenance. */
    @IsTest
    static void reviewersApproveButCannotEditProvenance() {
        System.assertEquals(1, classAccess('FieldTrust_Reviewer', 'FieldTrustReview'));
        System.assertEquals(1, customPermission('FieldTrust_Reviewer'));
        ObjectPermissions reviewer = provenance('FieldTrust_Reviewer');
        System.assert(reviewer.PermissionsRead, 'reviewers read drafts');
        System.assert(!reviewer.PermissionsCreate && !reviewer.PermissionsEdit && !reviewer.PermissionsDelete,
            'reviewers must not create, edit or delete provenance');
        for (FieldPermissions f : [SELECT Field, PermissionsEdit FROM FieldPermissions
                                   WHERE Parent.Name = 'FieldTrust_Reviewer' AND SobjectType = 'Agent_Write__c']) {
            System.assert(!f.PermissionsEdit, f.Field + ' must be read-only for reviewers');
        }
    }
}
