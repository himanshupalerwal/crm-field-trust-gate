/**
 * The field trust gate in Apex: the same checks, in the same order, with the same reasons as
 * gate.py. evaluate() decides whether a write may proceed from the fields it is based on.
 * Strings are compared with equals(), because Apex's == ignores case and Python's does not.
 */
public with sharing class FieldTrustGate {
    public static final Map<String, Integer> TIER_RANK = new Map<String, Integer>{
        'context' => 0, 'advisory' => 1, 'action' => 2
    };

    public class FieldRead {
        public String name;            // Object.Field, for example Account.ARR__c
        public String sourceSystem;    // where this value actually came from
        public Datetime lastSynced;
        public Integer matches;        // records that matched the entity key
        public String lastWriter;      // for example billing_sync, sales_rep, agent:renewal

        public FieldRead(String name, String sourceSystem, Datetime lastSynced, Integer matches, String lastWriter) {
            this.name = name;
            this.sourceSystem = sourceSystem;
            this.lastSynced = lastSynced;
            this.matches = matches;
            this.lastWriter = lastWriter;
        }
    }

    public class Rule {
        public String authority;
        public Decimal freshnessSlaHours;
        public Set<String> allowedWriters;
        public String tier;

        public Rule(String authority, Decimal freshnessSlaHours, Set<String> allowedWriters, String tier) {
            this.authority = authority;
            this.freshnessSlaHours = freshnessSlaHours;
            this.allowedWriters = allowedWriters;
            this.tier = tier;
        }
    }

    public class Decision {
        public String decision;        // proceed, draft or ask
        public List<String> reasons;

        public Decision(String decision, List<String> reasons) {
            this.decision = decision;
            this.reasons = reasons;
        }
    }

    public static Decision evaluate(List<FieldRead> reads, Map<String, Rule> contract, Datetime now) {
        if (reads == null || reads.isEmpty()) {
            return new Decision('draft', new List<String>{ 'no declared inputs' });
        }
        if (now == null) {
            now = Datetime.now();
        }
        List<String> failures = new List<String>();
        List<String> tiers = new List<String>();
        for (FieldRead r : reads) {
            Rule rule = contract.get(r.name);
            if (rule == null) {
                failures.add(r.name + ': no contract');
                continue;
            }
            if (!rule.authority.equals(r.sourceSystem)) {
                failures.add(r.name + ': authority is ' + rule.authority + ', not ' + r.sourceSystem);
            }
            Long slaMillis = (rule.freshnessSlaHours * 3600000).longValue();
            if (r.lastSynced == null || now.getTime() - r.lastSynced.getTime() > slaMillis) {
                failures.add(r.name + ': older than its SLA');
            }
            if (r.matches != 1) {
                failures.add(r.name + ': ' + r.matches + ' records match, expected 1');
            }
            if (!rule.allowedWriters.contains(r.lastWriter)) {
                failures.add(r.name + ': last written by ' + r.lastWriter);
            }
            if ('context'.equals(rule.tier)) {
                failures.add(r.name + ': context only, cannot drive a write');
            }
            String tier = rule.tier;
            if (r.lastWriter != null && r.lastWriter.startsWith('agent:')
                    && TIER_RANK.get(tier) > TIER_RANK.get('advisory')) {
                tier = 'advisory';         // an unconfirmed agent value
            }
            tiers.add(tier);
        }
        if (!failures.isEmpty()) {
            return new Decision('ask', failures);
        }
        String lowest = tiers[0];
        for (String t : tiers) {
            if (TIER_RANK.get(t) < TIER_RANK.get(lowest)) {
                lowest = t;
            }
        }
        if ('action'.equals(lowest)) {
            return new Decision('proceed', new List<String>());
        }
        return new Decision('draft', new List<String>{ 'lowest input tier is ' + lowest });
    }
}
