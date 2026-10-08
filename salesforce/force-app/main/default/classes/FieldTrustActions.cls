/** Helpers the action classes share. */
public with sharing class FieldTrustActions {
    /** The id if the text is one of this object's ids, otherwise null. */
    public static Id idOf(String value, Schema.SObjectType expected) {
        try {
            Id candidate = Id.valueOf(value == null ? '' : value.trim());
            return candidate.getSObjectType() == expected ? candidate : null;
        } catch (Exception e) {
            return null;
        }
    }

    /** The outcome in the shape the action classes return; they all share these three outputs. */
    public static Map<String, String> fields(FieldTrustService.Outcome o) {
        return new Map<String, String>{
            'decision' => o.decision, 'reasons' => String.join(o.reasons, '\n'), 'message' => o.message() };
    }
}
