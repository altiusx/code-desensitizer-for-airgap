# Internet-First, Airgap-Final
### A disciplined delivery paradigm for confidential environments — strategy, architecture, governance, and timeline

> Companion document to `internet-first-airgap-final.pptx`. The deck is the pitch; this is the operating manual behind it.

---

## 0. Executive framing

The threat is not that GenAI-adopter user teams can build faster — prototypes are always faster than products. The threat is that their *story* ("days, not months") resets stakeholder expectations, after which a months-long cycle is politically indefensible regardless of engineering merit. The answer is not to defend slowness and not to out-cowboy them; it is to match their velocity with a loop that remains auditable, testable, and secure.

The enabling bet: **~90% of the application logic is not classified — only terminology, data, and specific rule values are.** Therefore: build the 90% on the internet under a fictional context ("Looking at Trees Corp."), and bind the classified 10% inside the airgap as *data, not code*.

One sentence to keep repeating: **the internet repo is the factory; the Confi repo is the product.** The gate is crossed only by lean spec packages outbound and tagged releases inbound — never by live repositories in either direction.

---

## 1. The Decoupled Business Logic Pattern (the Sensitizer dilemma)

### 1.1 The critical redirect: do not build a code-transforming Sensitizer

The planned "Sensitizer" — a tool that reverse-transforms business logic after crossing — is the wrong shape. Any tool that must rewrite *logic* (not just names) on every crossing will:

- accumulate special cases until it is itself a codebase nobody trusts;
- silently corrupt behavior in ways `reverse`-style renaming never can (renames are syntactic; logic is semantic);
- require the transformation rules to encode the sensitive logic somewhere — which is exactly what must not exist outside.

`code_extractor.py` works because renaming is mechanical and reversible. Logic is neither. So: **make the sensitive logic data that the code loads, and the problem disappears.** The Sensitizer becomes a *logic-pack authoring, validation, and compilation tool that lives entirely inside the airgap* — it never touches internet-built code at all.

### 1.2 The pattern: Passive Shell + Logic Pack

The React MFE built outside is a **passive shell**: it renders whatever schemas, state machines, labels, and rule outcomes it is given. Outside, it is fed a fictional "trees" pack, making it fully runnable and testable. Inside, the **same unmodified bundle** is fed the real pack at runtime (config endpoint) or at Confi build time (baked asset).

A logic pack contains:

| Artifact | Contents | Notes |
|---|---|---|
| `labels.json` | Terminology catalog — every user-facing string is a token (`entity.name.plural`) | i18n infrastructure repurposed for classification. Replaces most of what a Sensitizer would have rewritten. |
| `schemas/*.json` | JSON Schema (or serializable Zod) validation per form/entity | The shell ships a generic validation engine; rule values never appear in JS source. |
| `flows/*.json` | UI state machines — states, events, guards referenced by abstract key (XState-style) | Flow differences between fictional and real domains live here, not in components. |
| `rules.json` | Decision tables / guard outcomes keyed `RULE_nnn` | Outside pack holds plausible fictional values so tests are meaningful; the same Playwright suites re-run inside against real values. |
| `contract.d.ts` + meta-schema | The pack's type contract — the ONE artifact both worlds share | CI on both sides validates its packs against the same pinned contract version. |

**Component discipline:** feature code calls `t('token')`, `validate(schema, data)`, `can('RULE_017')`, `flow.send(event)`. ESLint bans hardcoded user-facing strings and literal rule values in feature directories.

### 1.3 The escape hatch — plan for it, don't discover it

10–20% of logic will be too complex or too sensitive for declarative packs (intricate calculations, classified algorithms). Handle these as **stub modules**: outside, a module implements the interface with fictional behavior; inside, contractors/core devs hand-implement the real module behind the identical interface. Every stub is flagged in the feature tracker at spec time so integration effort is budgeted, not discovered.

### 1.4 Honest limits of the pattern

- Schema-driven engines constrain UI expressiveness; invest early in a strong internal library of schema-driven form/table/flow components or devs will route around it.
- Pack contract churn is the new coupling point — version it like an API (semver, changelog, CI validation on both sides).
- Watch for "pack bloat": if a pack starts containing what is effectively a programming language, that module belonged in the escape hatch.

---

## 2. Outbound Security Gates & Human Governance

### 2.1 Position the desensitizer correctly

`code_extractor.py` is a **productivity tool, not a security control**. Its own README lists bypasses: template literals with interpolation are never masked, JS regex literals confuse the scanner, CSS/assets are skipped, Java text blocks aren't masked, and it only removes what `mapping.json` names. Meaning also leaks through channels no renamer touches: comments describing rules, fixture *shapes* and cardinalities, uniquely identifying flow structures, commit messages, and file organization. The gates are the control; the tool reduces gate workload.

### 2.2 The four gates (every egress, no exceptions)

**Gate 0 — Automated scan (machine, blocking):**
- Extractor `--dry-run` diff reviewed before writing output.
- Secret/credential scanner (gitleaks-class) over the output tree.
- Denylist scan: corpus of real terms (generated from mapping.json inside — the corpus never leaves), internal hostnames, IP/URL patterns, org markers, employee names.
- String-literal audit: every literal in the output is either masked (`STR_n`) or explicitly allowlisted.
- Any hit blocks. No human override at this gate — fix and re-run.

**Gate 1 — Author checklist (signed):**
- Comments/doc tags stripped (tool default) and eyeballed for survivors in template literals, concatenations, and formats the scanner misses.
- All fixtures synthetic — *generated* (faker with fixed seeds), never copied-then-edited from real data.
- Error messages, test names/descriptions, enum values, and config samples desensitized.
- No real rule values anywhere in the package.
- Signature: "I have read every line of this package."

**Gate 2 — Second-person review:**
- Reviewer did **not** author the package; reads the desensitized output only, hunting residual meaning and identifying structure.
- Rotates among tech leads/supervisor to prevent rubber-stamping.

**Gate 3 — Approval + egress ledger:**
- Approval per the risk matrix below.
- Ledger entry: package hash, file list, mapping.json version fingerprint, author, reviewer, approver, timestamp. Package archived immutably.
- Transfer via the organization's sanctioned egress channel only.

### 2.3 Risk-tiered approval matrix

| Tier | Package type | Gate 2 reviewer | Final approver | Target SLA |
|---|---|---|---|---|
| **0 — Standing patterns** | Recurring pre-approved shapes (CRUD contract template, N-th similar fixture set) | Checklist only | Pre-authorized by a prior Tier-2 approval | Immediate |
| **1 — Structural** | OpenAPI shapes, routes, component trees, type signatures — no logic, no literals | One tech lead | Engineering supervisor | Same day |
| **2 — Sanitized source** | `code_extractor` output, service signatures, synthetic fixtures | Tech lead + one peer | Supervisor; cyber audits the ledger monthly | ≤ 2 working days |
| **3 — Logic-adjacent** | Abstract rule keys, state-machine shapes, anything near classified flows | Tech lead + supervisor | Named cyber/software-assurance fast-track contact | ≤ 5 working days |

**How this keeps velocity:** cyber assurance is *in the loop but not on the path* — they co-author the checklists and denylists, audit the ledger monthly, and are live-in-path only for Tier 3. Velocity compounds through Tier 0: the more spec-package shapes are standardized, the more egress becomes routine instead of negotiation.

### 2.4 mapping.json custody

Treat it like key material: encrypted at rest, access-logged, two named custodians, never committed to any repo inside or outside, version-fingerprinted (the tool already SHA-256s it) and pinned to each egress ledger entry and each release.

---

## 3. Internet Sandbox, Mocking & Migration

### 3.1 The "Looking at Trees Corp." universe

- **World bible:** document LTC's domain model, entities, personas, and vocabulary richly enough that developers never need to reach for a real term. Naming vacuum is how leaks happen.
- **One canonical glossary** maps LTC ↔ real terms; it lives inside the airgap, version-locked to mapping.json. Ad-hoc per-developer improvisation of parallel names will diverge and eventually collide or leak.
- **Structural isomorphism is a leak vector:** a perfect 1:1 mirror of org structure, entity cardinalities, and unusual flow shapes can identify the real domain to a knowledgeable observer. Deliberately vary non-essential structure.
- **Account hygiene:** private org-owned repos, 2FA, no personal accounts, and the working assumption that everything outside is adversary-readable.

### 3.2 Repo parity with Confi GitLab

- Same pipeline stages (lint → unit → SAST → build → tag) with the *same config files* — export the actual ESLint/SAST configs inward via Tier 1 so rules are identical, not similar.
- **Semantic version tags are the only inbound unit.** No branch syncing, no cherry-picked commits across the gap.
- **Dependency-delta job in CI:** diff `package-lock.json` / `pom.xml` against the Confi-approved manifest; emit a machine-readable request list for the internal registry team. A delta is a conscious flagged decision, never an integration-day surprise.
- Pin exact versions; Confi rebuilds from source through its own pipeline — internet build artifacts are never trusted directly.

### 3.3 Contract-first mock & test framework

1. **OpenAPI spec** is authored/extracted inside, desensitized, egressed — the single most valuable spec-package artifact (your tech lead is right).
2. **Generate, don't hand-write:** `openapi-typescript`/`orval` generate TS types and the thin API client.
3. **MSW handlers** generated from the same spec + synthetic fixture factories (seeded faker). MSW runs in the browser for dev and in Node for CI.
4. **Playwright** drives full journeys against the MSW-backed app: zero backend, deterministic data, and failure-mode coverage (500s, timeouts, empty/slow states) that is cheap outside and nearly impossible inside.
5. **Contract tests** validate mocks against the spec so mock drift fails CI; the spec version is pinned in both repos and a mismatch fails both pipelines loudly.

**Definition of "done outside":** app fully runnable with zero backend; Playwright green on every journey; contract tests green; the same suites re-run inside against the real backend as the integration check.

### 3.4 Environment decoupling (ports & adapters)

- **Feature core** (identical in both worlds): components, hooks, state machines, validation engine, logic-pack consumer. Imports only the API-client interface and tokens — never fetch/axios, never `window.location`, never env vars.
- **Thin API client** (identical interface, swappable transport): generated from OpenAPI; all I/O passes through it (base URL, auth header injection, error normalization). MSW intercepts it outside; the real gateway serves it inside.
- **Environment binding** (differs per world, injected at deploy): SSPA-style runtime config (import-map / `env.js`): endpoints, auth adapter, certs, logic-pack source. Auth is an interface with two adapters — `FakeAuthProvider` outside, real SSO inside.
- **Enforced by lint, not hope:** an ESLint boundary rule (`import/no-restricted-paths`) forbids feature code from importing transport/env modules, validated by CI in both worlds. That is what makes "integration = configuration" true.

### 3.5 Migration factory (Angular → React)

Per feature: **Inventory → Contract → Build → Verify & ship.**

1. **Inventory:** catalog every Angular feature (routes, screens, API calls, rule touchpoints, complexity, usage). One tracker row per feature — this is the program backlog and the parity scoreboard.
2. **Contract:** write the feature's spec inside (OpenAPI slice, rule keys, fixtures, flow notes); desensitize, gate, egress. Timebox it — a spec taking more than ~a week is being over-written.
3. **Build:** implement outside as a passive shell against MSW, end-to-end (UI + tests + docs), never all-UI-first. Escape-hatch stubs flagged for inside work.
4. **Verify & ship:** tag release; inside, reverse-map, bind real config + logic pack, implement stubs, re-run the same Playwright suite against the real backend. When a gap is found, **fix the spec first**, then the code — otherwise the spec pipeline rots.

**Pilot selection:** medium complexity, low classification-density, real users. Its job is to calibrate the pipeline (spec effort, gate SLAs, integration tax, pack coverage). Instrument everything; the pilot's metrics are the Phase 3 business case.

---

## 4. Strategy Critique (blunt) & Timeline

### 4.1 Where the plan is exposed

1. **It started as shadow IT — legitimize before anything else.** Building outside "secretly," however well-intentioned, is the plan's largest single vulnerability. In a confidential environment, an unsanctioned egress workflow discovered before it is sanctioned doesn't just kill the initiative — it destroys the credibility of the people proposing it, permanently. The remedy is cheap: brief your supervisor and the cyber/assurance lead, present the gate design, and get written authorization for a bounded pilot. That is Phase 0 and it blocks everything else. You are currently one curious security review away from losing the argument you're trying to win.
2. **The desensitizer is being asked to be something it is not.** It's a good renamer with honest documented limits. It is not DLP. Structure, comments, fixtures, and flow shapes leak meaning no renamer touches. Position it as workload reduction inside a gated human process.
3. **Split-brain drift will kill the factory quietly.** Hotfixes made inside never flow back out; six months later the internet repo no longer matches the product and "internet-first" collapses back to inside-first. Mitigations: pin mapping/contract/pack versions together per release; make "inside-only patch" an explicit tracked exception with a scheduled re-sync ritual; watch the integration-tax metric — it is the drift alarm.
4. **The people plan is the weakest chapter.** (a) Contractors are being redeployed into roles whose explicit endgame is their own elimination — expect quiet attrition of exactly the best ones, and plan knowledge capture (runbooks + core-team rotation through every contractor duty) from day one, not at Phase 3. (b) The plan is React-heavy but the estate is Java/Spring too — who builds backend features in the new model, and does the same paradigm apply? Name it. (c) Be honest with contractors early; they will infer the trajectory anyway, and discovering it by inference is worse.
5. **The first loop will be slower than today.** You are building the factory while running it. Say this on day one and set expectations at MFE #3, or month 3 gets read as failure by the same stakeholders you're trying to convince.
6. **One leakage incident likely ends the program.** The gates exist because the downside is asymmetric: the paradigm saves months, but a single confirmed egress incident costs the paradigm plus trust. This asymmetry is the justification for every checklist above — internalize it when tempted to skip a gate under deadline pressure.

### 4.2 Phased timeline (exits are criteria, not dates)

**Phase 0 — Legitimize & pilot (months 0–2).** Written authorization (scope, egress channel, named approvers); gates 0–3 stood up with cyber co-authoring the denylist; world bible v1; internet org + repos with CI parity; pilot feature specced and egressed.

**Phase 1 — Internet-first core team (months 2–8).** Core team builds the pilot plus 2–3 MFEs outside; logic-pack engine v1; contractors own integration, mappings, environment testing, config management (some move toward BA/spec roles); dependency-delta job feeding Confi registry requests. *Exit:* two MFEs shipped through the full loop with integration tax ≤ 15% of feature effort.

**Phase 2 — Outbound spec pipeline at scale (months 6–14, overlapping).** Spec-package tooling hardened; Tier-0 standing approvals in force; Angular inventory complete and the migration factory running per-feature; Sensitizer realized as the inside-only logic-pack compiler + validators. *Exit:* egress SLA met ≥ 90%; zero security incidents; parity scoreboard trending to completion.

**Phase 3 — Core-team absorption (trigger-based).** The trigger (contractor reduction mandate) is outside your control, so readiness is built during Phases 1–2: every contractor duty has a runbook and a core-team owner who has actually executed it (quarterly rotation); residual manual work automated (integration scripts, mapping validators, config lint). *Exit:* one full release proven with core team only.

### 4.3 The scoreboard (publish monthly, to stakeholders)

| KPI | Meaning | Target |
|---|---|---|
| Two clocks | Time-to-**demo** (spec → clickable mock) and time-to-**production** (spec → running in Confi), published side-by-side | Demo clock matches the vibe-coders; production clock trending down |
| Integration tax | % of feature effort spent inside after the tag lands | ≤ 15%; rising = drift alarm |
| Egress SLA & first-pass yield | Gate turnaround vs. matrix; % passing Gate 0 clean | ≥ 90% SLA; yield rising |
| Pack coverage | % of rules as pack data vs. escape-hatch code | Rising = Phase 3 readiness |
| Security incidents | Confirmed leakage past Gate 3 | Zero. Non-negotiable. |
| Bus-factor spread | Contractor duties with runbook + rotated core-team owner | 100% before Phase 3 triggers |

### 4.4 The ask

1. Authorize a bounded Phase-0 pilot **in writing**: one feature, defined egress channel, defined gates.
2. Name the approvers: engineering supervisor (Tiers 1–2), cyber/SW-assurance fast-track contact (Tier 3).
3. Fund the gate tooling: scanner integration, egress ledger, logic-pack contract + validators.
4. Endorse the workforce message: contractors get a real transition story, told early and honestly.
5. Charter a department working group: one gate standard, shared tooling, and a joint incident-response playbook — before N teams invent N processes (see Section 7).

---

## 5. Second look — gaps beyond the founding vision

These are gaps neither the original blueprint nor Section 4's critique covers. Each comes with a concrete proposal.

### 5.1 The bridge cuts both ways: inbound is nearly ungoverned

All governance energy so far went into egress, but this workflow also creates a *standing channel into* a classified network — the direction a real adversary cares about. Threat scenario: the internet GitHub org is compromised (phished contractor account, malicious dependency, poisoned AI-generated code) and a subtle backdoor rides a tagged release through `reverse`, which nobody reads line-by-line because "it's our own code." AI-generated code adds two further inbound problems: **license contamination** (models occasionally reproduce GPL or copyrighted code verbatim — a genuine issue in a government/classified codebase) and sheer volume of code no team member has actually read.

**Proposal — mirror the gates inbound:**
- Signed tags are the only inbound unit (already policy) — add verification inside.
- SCA + license scan as a *release gate* in the internet CI, not an advisory job.
- Mandatory inside diff-review of each release against the previous tag — human, not tool.
- Confi rebuilds everything from source through its own pipeline as an inbound **gate**, not a habit.
- An inbound ledger symmetrical to the egress ledger.

> Outbound gates protect the mission; inbound gates protect the network. Both directions, or neither is credible.

### 5.2 The clipboard is the biggest real leak channel, and no gate covers it

The four gates govern *packages*; the daily workflow is developers having hundreds of ad-hoc conversations with internet AI assistants. The realistic leak is not a bad extraction — it is a developer at 6pm pasting an error message, stack trace, or a rule "from memory" into a chatbot. Human memory crosses the airgap every evening and no scanner runs on it.

**Proposal — an AI-usage policy as part of Phase 0:**
- The only airgap-derived content permitted in any prompt is content that has passed Gate 3. No paraphrasing from memory.
- Enterprise / zero-data-retention agreements with AI providers, on org accounts only — never personal ones.
- The LTC world bible baked into repo-level assistant context (CLAUDE.md or equivalent) so the AI itself speaks the fiction and never invites real vocabulary into a conversation.

### 5.3 Angular does not stop existing while we migrate

The legacy app is a production system users depend on for the next 1–3 years: bugfixes and regulatory changes must ship *inside, in the old paradigm*, while the best people are outside in the new one. Unmanaged, legacy sustainment quietly re-absorbs the core team and starves the factory.

**Proposal — a formal strangler policy, decided per feature at inventory time:**
- **Frozen:** bug-fix only.
- **Sustained:** contractors own it — a genuine, non-euphemistic contractor role during Phases 1–2.
- **In-migration:** the Angular version is feature-frozen the day the React contract is egressed, so the port never chases a moving target.

Since the platform is already single-spa, run mixed mode deliberately: swap route-by-route behind the SSPA shell rather than waiting for whole-MFE parity.

### 5.4 mapping.json does not scale from tool artifact to program artifact

Today the mapping is per-extraction and artisanal — whoever runs `trace` invents the aliases. At team scale this produces collisions (two features both mapped to `Item`), contradictions (the same real term mapped two ways, which breaks `reverse` on shared code), and an unowned glossary drifting from the world bible.

**Proposal — a single mapping registry inside the airgap:**
- One governed, versioned superset mapping (the v2 schema merges cleanly).
- A named owner who approves new entries.
- Collision checks run as a blocking pre-commit inside (`validate_mappings` warnings become errors).
- Every egress ledger entry pinned to a registry version.

Roughly a day's work in the existing tool; it prevents the one failure class that is unrecoverable later — inconsistent reversals silently corrupting integrated code.

### 5.5 The paradigm itself has a bus factor of two

Everything currently lives in two heads. If either founder leaves — or moves fully into management — the workflow degrades into folklore, and folklore is what fails a security audit. The irony: the plan rigorously de-risks *contractor* knowledge while the paradigm's own knowledge is the least-transferred asset in the program.

**Proposal — institutionalization as a Phase 1 exit criterion:**
- Gates, tiers, and inbound/outbound checklists written into the org's accredited security operating procedures, so the process survives its authors.
- Runbooks for extraction, egress, integration, and reversal.
- At least one person outside the founding pair runs a full loop solo before Phase 1 closes.

### 5.6 Nothing verifies the two worlds actually behave the same

Contract tests catch API drift, but the riskiest divergence is behavioral: the fictional logic pack exercises different branches than the real one, so "Playwright green outside" can be true while real rules hit code paths no outside test ever touched. Coverage of the fiction is not coverage of the reality.

**Proposal — a pack-equivalence discipline:**
- The fictional pack must be structurally isomorphic where it matters: same rule count, same branch shapes, same boundary classes (min/max/empty/overflow) — different values.
- Generate both packs' skeletons from the same inside-only source of truth so they cannot diverge structurally.
- Measure branch coverage inside on the first few integrations; if real-pack runs light up untested branches, fix the *pack*, not the tests.

### 5.7 There is no incident-response plan for the thing that ends the program

The KPI table says one confirmed leakage incident likely ends the program — yet nothing defines what happens in the first 24 hours after a *suspected* leak. Improvising incident response mid-incident is how a recoverable event becomes a fatal one. Note also the hard constraint: a leaked `mapping.json` cannot be "rotated" like a credential — the real names it reveals stay revealed. IR here is about blast-radius containment and demonstrating control, not undo.

**Proposal — a pre-agreed IR playbook, rehearsed once per phase:**
- **Detect & declare:** anyone can raise a suspected-leak flag, no blame for false alarms; a named incident owner (supervisor) takes command.
- **Assess:** the egress ledger is the blast-radius instrument — exactly which packages, hashes, and mapping versions could be affected. This is the strongest operational argument for the ledger existing at all.
- **Contain:** quarantine the affected internet repos (private → archived), suspend egress program-wide until root cause is known, retire affected aliases so future packages don't reinforce the correlation.
- **Report:** through the organization's proper security channel, proactively. A self-reported near-miss builds credibility; a discovered cover-up ends careers as well as programs.

---

## 6. Second look — opportunities not yet claimed

### 6.1 Win at the production gate (the competitive strategy against fearless-only teams)

> Revised after strategic review: the internal GenAI user teams are structured to be *fearless-only* — they carry no security obligations — and they are direct competitors, so collaboration ("absorb them") is off the table.

This is an **asymmetric race**: they pay none of the security tax, so competing on build-speed means racing on a rigged scoreboard. Their fearlessness, however, is a liability with a delivery date — a vibe-coded prototype has no answer for day 2: real backend integration, classified data handling, accreditation, sustainment, security review. The strategy is therefore to move the finish line to where their model structurally fails:

- **Answer the binding question first.** Can they actually ship to production without meeting the security bar we're held to? If *no*, the bar is our moat — make it visible. If *yes*, the problem is organizational inconsistency, and the escalation is legitimate and career-safe: *one production bar, stated once, applied uniformly* — either it binds them too, or we must be released from a standard our competitors are exempt from. Do not build an 18-month strategy before knowing which world applies.
- **Publish the platform standard.** Their output, to ever ship, must run inside the SSPA shell, pipelines, and backends *this team operates*. Publish a neutral, technical onboarding standard for any MFE entering the production platform: contract-first API spec, test coverage, SAST-clean, dependency policy — the same bar our own MFEs meet, applied uniformly, defensible to any referee. Two outcomes, both wins: they meet the bar (they have adopted our paradigm on our terms) or they stall at the gate (the promise-vs-production gap becomes evidence demonstrated by their own attempt).
- **Reframe the public metric** from time-to-demo to time-to-production (the "two clocks" KPI), and beat them to the only finish line that counts: get one real feature through the full loop before their first prototype hits the wall.
- **Never badmouth the demos.** Let the production gate do the arguing.

### 6.2 Match them in demo-space — and fix the requirements bottleneck doing it

The one lane where competing head-to-head *is* winnable: demos. The MSW-backed passive shell carries none of the security tax — mocks touch nothing classified — so at the demo layer this team is exactly as fast as the vibe-coders. Put clickable, realistic-data prototypes in front of stakeholders and user representatives in week one: *fearless demos, disciplined delivery.* This simultaneously fixes the named root cause the plan otherwise skips — heavy requirements gathering — because users react to behavior instead of signing off documents. Cheapest change with the largest lead-time effect.

### 6.3 Extend the paradigm to Spring before someone decides it is frontend-only

The extractor already speaks Java, and "who builds backend features in the new model?" is currently unanswered. Passive-shell has a clean server analogue: controllers/services built outside against the same OpenAPI contract, rule values externalized to Spring configuration (its native strength), Testcontainers replacing MSW as the outside harness. Running one backend service through the loop in Phase 1 answers the workforce question with evidence instead of a promise.

### 6.4 Make the desensitizer an internal product — political armor

This is surely not the only team in the organization facing "on-prem models are weak, code cannot leave." A sanctioned internal platform ("the secure AI-enablement toolkit") earns budget, allies, and — most valuably — makes the security organization a *co-owner* of the workflow rather than its auditor. Co-owned controls get improved; audited workarounds get shut down. It reframes the team from rule-benders into the team that built the organization's answer. Section 7 upgrades this from speculation to fact: the customers already exist.

---

## 7. The department dimension — federate or fragment

> Context: other core teams in the department are pursuing a similar paradigm shift. That changes the risk calculus and the opportunity landscape in four specific ways.

### 7.1 Shared fate on incidents (risk — close jointly)

With N teams running parallel egress workflows, **one team's leak ends the paradigm for all of them**. Leadership reacting to an incident will not distinguish whose gate failed — "the internet development thing caused a spill" is the headline either way. The weakest gate in the department now defines this program's risk, and that gate is outside this team's control.

**Proposal:** a department-level federation, minimal but binding:
- A **common gate standard** (gates 0–3, approval tiers, egress/inbound ledgers) that every participating team meets — per-team implementations, one bar.
- **Shared scanner and denylist infrastructure**: the Gate-0 tooling (secret scanners, denylist engine, ledger) built once, operated as internal infrastructure; each team supplies its own term corpus from its own mapping registry.
- A **joint incident-response playbook** (§5.7) at department level: shared detection/notification channel, one IR command structure, mutual suspension protocol (a confirmed incident anywhere pauses egress everywhere until scoped). Rehearsed jointly.

### 7.2 Fiction hygiene across teams (risk — close jointly)

Multiple fictional universes emitted by the same population of people and orgs is a **triangulation gift** to an observer: shared contributors, shared CI templates, similar repo structures, and correlated commit patterns let someone link the universes together and back out the shared real domain behind them. Rules:

- One fiction per team, never shared, never cross-referenced. Separate internet orgs.
- Share world-*building* lessons (what makes a fiction robust, naming-vacuum pitfalls) **inside the airgap**; never share world *content* outside it.
- Commit-identity hygiene as a department norm: org accounts, no personal handles linking a developer across multiple fictional universes.

### 7.3 Write the standard first (opportunity — move now)

Whoever formalizes their gate process first will effectively write the department SOP; every other team gets audited against it later. This team has the head start — the desensitizer exists, the gate design exists, the ledger design exists. Co-author the standard with cyber **now**, and two things follow:

- The §6.4 "internal product" play gains real customers immediately: sibling teams adopt this tooling (extractor, mapping registry, Gate-0 stack, logic-pack contract) rather than building their own, and this team becomes the paradigm's *platform team* instead of one of N users of someone else's rules.
- The standard is written by the team with the most operational experience, not by whichever team has the loudest incident first.

### 7.4 Cross-team leverage (opportunity — structural)

Sibling teams solve several of this program's hardest staffing problems:

- **Borrowed Gate-2 reviewers:** a reviewer from a sibling team is *genuinely* fresh eyes — they don't share the feature context, so residual meaning and identifying structure jump out at them in ways they cannot for a teammate. Formalize a cross-team review exchange.
- **Inner-source tooling:** passive-shell engine, schema-driven component library, MSW scaffolds, dependency-delta CI job, mapping-registry tool — built once, co-owned across teams, amortizing platform cost the department is otherwise about to pay N times.
- **KPI benchmarking:** with N teams running the same paradigm, cross-team comparison *will* happen. Co-defining the metrics (the §4.3 scoreboard) beats being measured by someone else's later.
- **Approver scaling:** N teams' Tier-3 requests will saturate a single named cyber fast-track contact. The department standard must define an approver *rota* with cross-trained deputies — a role, not a hero.
