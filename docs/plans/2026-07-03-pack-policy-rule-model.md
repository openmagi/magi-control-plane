# pack -> policy -> rule: a first-class Policy layer

Status: DESIGN. Stacked on the session-evidence primitive (PR #65). Captures the
three-tier model so the conversational compiler authors a **policy** (not loose
rules), and the dashboard/packs manage at policy granularity.

## 1. The three tiers

- **rule** — the developer-side minimal unit. This is today's IR policy
  (`EvidencePolicy`, `EvidenceAuditPolicy`, `RunCommandPolicy`, ...). It is what
  the compiler turns into a Claude Code managed-settings hook, and what
  precedence/resolve operate on. UNCHANGED.
- **policy** — NEW. The user-side semantic minimal unit. One authored intent.
  A policy OWNS one or more rules. "Require a verified source before trading"
  is one policy that owns two rules (an audit + a precondition). A simple rule a
  user authors directly is a policy that owns exactly one rule.
- **pack** — a named collection. Now references **policies**, not rules.

## 2. Key simplification: policy is a management overlay, not a new compile unit

The compiler and resolve pipeline stay **rule-based**. A policy does not compile
to anything itself; it EXPANDS to rules (via `compound.py`, already built) and
those rules compile as today. This keeps precedence/resolve/matrix untouched.

What "first-class policy" adds:
1. A **policy store**: `{id, description, draft, rule_ids, source, enabled}`.
   `draft` is the compound (or simple) authored form, retained so the policy is
   re-editable; `rule_ids` are the ids of the rules it currently owns.
2. **Owner linkage** on each rule: an `owner_policy` id (empty for legacy
   free-standing rules). Lets the store/dashboard group rules under their policy
   and cascade operations.
3. **Cascade**: enable/disable/delete a policy applies to all its rules in one
   transaction. Re-saving a policy diffs `rule_ids` (removes rules it no longer
   owns, upserts the rest).
4. **Packs reference policy ids**: at compile time a pack expands each policy id
   to its `rule_ids`, then resolves rules as today. Back-compat: a pack row that
   still holds a rule id is treated as a one-rule policy reference.

## 3. Data model

```
PolicyRecord:               # NEW store: policies.json
  id: str                   # user-facing, e.g. "verified-trade"
  description: str
  kind: "simple" | "compound"
  draft: dict               # the authored form (compound draft or single rule)
  rule_ids: list[str]       # rules this policy currently owns
  source: str
  enabled: bool

Rule (existing IR policy) gains:
  owner_policy: str = ""     # "" = legacy free-standing rule
```

Rule store stays as-is (PolicyOverride list); we add the `owner_policy` field to
the IR dataclasses (additive, default "") and thread it through save.

## 4. Save flow (the conversational compiler / builder target)

`POST /policies` (policy-level, replaces the direct rule PUT for authored
policies):
1. Take a policy draft (`kind`, `draft`, `description`, `enabled`, `pack_ids`).
2. `simple`  -> the single rule dict is the one member.
   `compound` -> `expand_compound_draft(draft)` yields the member rule dicts.
3. Stamp each member rule with `owner_policy = policy.id`.
4. Validate every member (IR `__post_init__`); all-or-none.
5. In one lock: write the PolicyRecord + upsert member rules + delete rules whose
   ids were owned before but not now.
6. Pack membership operates on the policy id.

The existing rule-level `PUT /policies/{id}` stays for advanced/raw authoring and
legacy callers; such a rule is a policy with `owner_policy=""` (its own policy).

## 5. Dashboard

- `/rules` becomes policy-grouped: list PolicyRecords; each expands to its rules
  (read-only rule view). Enable/disable/delete act on the policy.
- The evidence-gate builder + conversational compiler POST a policy, not rules.
- A free-standing legacy rule with no owner renders as a one-rule policy.

## 6. Migration / back-compat

- IR `owner_policy` default "" -> every existing rule loads unchanged.
- No policy store yet -> synthesize a one-rule policy per owner-less rule for the
  grouped view (lazy, read-time), so nothing breaks before a write.
- Packs: accept both policy ids and (legacy) rule ids; expand policy ids to rules
  at compile, pass rule ids through.

## 7. Build order (stacked, each independently testable)

1. **compound.py** expansion. DONE.
2. **POST /policies/compound** (expand + atomic save of member rules). DONE
   (interim; folds into the policy save in step 4).
3. IR `owner_policy` field (additive) + serialize/deserialize + thread through
   rule save.
4. **PolicyStore** + `POST /policies` (policy-level save: record + member rules +
   cascade) + `GET /policies` (grouped) + `DELETE`/enable cascade.
5. Pack references policies (expand at compile; back-compat rule ids).
6. Dashboard policy-grouped view + builder/compiler POST a policy.
7. Conversational compiler authors a policy (compound-aware draft, single draft
   through the turn loop, expand at save).
8. Context-aware conversation (inject existing evidence kinds / policies).
9. Review loop (LLM checks the authored policy's rules implement the intent).

## 8. Non-goals

- No change to the compiler, matrix, precedence, or resolve set (rule-based).
- No signed/cloud evidence (separate trust follow-up).
