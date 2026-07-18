# Internet-First, Airgap-Final — Team Working Edition
### A practical playbook for the core team

> Companion to `internet-first-team-edition.pptx`. This is the plain-language version, aimed at the people who'll actually do the work — not the leadership/cyber-facing pitch. That fuller version (`internet-first-strategy.md` + `internet-first-airgap-final.pptx`) still exists and covers gates, approval tiers, department politics, and audit-style risk analysis in depth; keep both around and mix them for the final presentation as needed.

---

## Why we're doing this

Three honest reasons, no scare stats:

1. **Friction eats our months.** Environment setup, access requests, and pipeline waiting take up more of a delivery cycle than the actual feature does. That's the part we can fix.
2. **Let's out-finish, not just out-start.** Other teams are already prototyping fast with AI on the public internet. A fast demo is easy — we want our fast path to lead all the way to a real, running feature.
3. **We want proof before it counts.** Building and testing outside means the feature has already been exercised end-to-end by the time it reaches Confi. Integration becomes validation, not discovery.

The point isn't to move faster than is safe. It's to stop spending our speed on friction that has nothing to do with the actual problem — and to make the boring parts (setup, plumbing, waiting) so consistent nobody has to think about them anymore.

---

## How it works, in one picture

**Write the spec inside → Build & test outside → Tag a release, bring it in → Wire it up inside.**

- **Inside:** routes, API contracts, and sample data get written up as the blueprint for a feature.
- **Outside:** the feature gets built and tested fast, with AI pairing, against realistic mocks — no backend needed.
- **Crossing:** one small, reviewed package — never a full repository.
- **Inside again:** the same code gets pointed at the real rules and data. Mostly configuration, not a rewrite.

Everything actually sensitive — real names, real data, real rule values, `mapping.json` — stays inside the whole time. What crosses is small, plain, and looked at by a teammate first.

---

## Who we are, outside: meet Looking at Trees Corporation

On the internet, we operate as **Looking at Trees Corporation** — a company focused on marketing and specialized trees. LTC is built around five core service lines (think *Brain Partnerships*, *Japanese Ops*, and a few others), each with its own departments and sub-teams — shaped, fittingly, like a tree.

Every screen, flow, and dataset we build outside speaks LTC's language. The real names and structure only get plugged back in once the code is inside.

**One deliberate choice:** LTC's shape is ours to design — it doesn't need to mirror our real org chart one-for-one. Keeping some healthy distance there is a feature, not an accident: the closer a fictional structure mirrors the real one (same number of divisions, same reporting shape), the easier it becomes for someone to connect the dots. A little creative distance costs nothing and buys real cover.

---

## The part that makes this easy later: staying aligned with Confi

This is the single highest-leverage habit in the whole paradigm. Do these six things consistently and "bringing it inside" becomes closer to a deploy than a rewrite:

1. **Same lint & static-analysis rules (ESLint, SAST)** — copy the actual configs over, don't reinvent them.
2. **Same API contract format (OpenAPI)** as the shared source of truth on both sides.
3. **Same single-spa shell contracts & design-token libraries** Confi's `common-root-config` already provides — plug in, don't rebuild. We're not recreating the shell from scratch outside; we build functional shells against the same tokens and contracts, so what's proven outside plugs straight into the real root config inside.
4. **Same versioning & tagging approach** — Confi always pulls one known-good, tagged release.
5. **Dependencies checked against what Confi already allows**, so surprises show up early, not at integration.
6. **Config (URLs, auth, environment stuff) lives in one clearly separate place** from the feature code.

If integration ever feels hard, the answer is almost always "which of these six drifted" — not "we need a bigger process."

---

## Keeping it simple: what goes out, what stays in

**Goes outside:**
- Routes & structure, renamed
- API contracts + sample data
- Source code, run through our extractor tool first

**Stays inside:**
- `mapping.json` — our one key file
- Real names, real data
- The actual rule values
- URLs, certs, auth secrets

One habit does most of the work: run it through the extractor, have a teammate glance over the output, then send it. That's the whole routine for most features.

---

## The rare occasion we bring code out: how code occasionally leaves Confi

Most of the time, work flows the other direction — spec inside, build outside, tag it, bring it in. But on the odd occasion, code genuinely needs to go the other way, out of Confi. Here's the flow:

1. **Decide what's needed** — almost always a lean spec: routes, contracts, structure. Never a full active repository.
2. **Run the desensitizer** — our `code_extractor.py` tool strips real names, masks data & strings, and swaps in Looking at Trees terms, automatically, in one pass.
3. **A teammate checks it** — a second pair of eyes reads the sanitized output before anything leaves, catching what the tool might miss.
4. **Send it, log it** — out through our approved channel; what left and when gets noted, simply.

`mapping.json` is the one file that turns "Looking at Trees" back into the real thing — it stays inside, always. Everything else is designed to be boring to send.

---

## Elevating "Looking at Trees" to real C3: the code doesn't care, the data does

Whether it's Looking at Trees or the real thing, the components, forms, validation logic, and flows are exactly the same code either way.

What's actually sensitive is almost always the **data** underneath — real customer records, real figures, real identifiers — plus, occasionally, one or two genuinely sensitive rule values.

So outside, the app runs on placeholder data (and placeholder values for the rare sensitive rule). Inside, we point it at the real data instead — closer to swapping an `.env` file than rewriting the app.

- **Outside:** fake but realistic records and figures. Same app, same behavior — nothing real underneath.
- **Inside:** same components, same tests. Only the values underneath change — reviewed and versioned like everything else we ship.

Some rules will be sensitive or complex enough that we just build them directly inside — that's completely fine. We'll flag those upfront, per feature, so nobody's surprised later.

---

## Building without waiting on anyone: testing without a real backend

1. **OpenAPI spec** — the contract everyone builds against: types, endpoints, sample data.
2. **Generated types + client** — nobody hand-writes fetch calls; they're generated from the spec.
3. **Mock backend (MSW)** — the app runs for real, with realistic fake data, zero live backend.
4. **Playwright tests** — real user flows, including error cases, fast and repeatable.

This is the part that actually saves us months: most bugs get caught outside, where fixing them is a two-minute edit instead of a trip through the internal pipeline.

---

## Before any code crosses: a short checklist, every time

1. **Unit tests green** — Jest for React/TypeScript, JUnit for any Java/Spring code involved.
2. **Static analysis & lint clean** — the same rules Confi uses, not a lighter internet-only version.
3. **Contract tests pass** — the mocks match the OpenAPI spec exactly.
4. **Playwright suite green** — full user flows, not just isolated components.

If backend code was touched, its tests travel with it — same bar as the frontend, no exceptions.

---

## An open question, decided case by case: do we rebuild backends outside too?

- **Default — MFE only:** for most features, build the shell outside against a mocked backend. The real Java/Spring service stays right where it is, built and maintained inside as normal.
- **Case by case — bring the backend along:** when a backend genuinely needs a rewrite — not just a re-skin — build a lean version of it outside too, under the same contract-first approach, tested there, then brought in like everything else.

The question to ask, per feature: does Confi already have a working backend for this? If yes, mock it. If the backend itself is due for a rewrite anyway, build it outside too.

---

## Who needs to look at what

Two tiers, kept deliberately simple to start:

- **Everyday stuff** (structure, contracts, sanitized code, sample fixtures): a teammate reviews it, off it goes — same day, routine.
- **Anything touching real rules** (unusual shapes, anything near sensitive logic): loop in a tech lead or the supervisor first — a day or two, not a blocker, just a second look.

We'll tighten this up as we go — better to start simple and add structure only where we actually need it.

---

## Porting features off Angular, one at a time

**Inventory → Spec → Build → Verify — repeat.**

1. **Inventory:** list out what each Angular feature actually does — routes, screens, calls, rules. One row per feature.
2. **Spec:** write the contract inside — API shape, rule keys, sample data. Keep it tight; a spec shouldn't take a week.
3. **Build:** build it outside end-to-end — UI, tests, docs together — against the mocks. Not all-screens-first.
4. **Verify:** bring it in, plug in the real backend, re-run the same tests. Found a gap? Fix the spec, then the code.

First pick a medium feature, not the hardest one. Its job is to help us learn the loop — how long a spec takes, how the mocks feel, how integration actually goes — before we scale it up.

---

## The three phases, named honestly: where this goes, and why

**Phase 1 — Core team goes outside:**
- Pick a pilot feature, run the full loop end to end
- Set up matching pipelines & configs outside
- Contractors take on environment testing, config management, and bringing internet work into Confi — the ingress side
- Fix the friction we find, together

**Phase 2 — Both directions get routine:**
- More features through the loop; Angular inventory & porting underway
- The occasional egress flow (Confi → internet) becomes the clear, repeatable process described above
- Sign-off is quick because the habits are solid
- We're no longer thinking about this as "new"

**Phase 3 — Contractor headcount comes down:**
- Core team absorbs the full loop, both directions, on its own
- Trigger-based, not date-based — we genuinely don't know when
- Phases 1 & 2 exist partly to make this transition possible without surprises
- We're naming it now, not waiting until it's decided to say anything

We'd rather be straightforward about where this is headed than let people guess.

---

## Being straight about the people side

- **For developers:** more time on the interesting problems, less on environment friction and waiting on access or pipelines. The tools handle more of the repetitive part so we can focus on what actually needs a person.
- **For contractors:** some roles shift toward integration, environment testing, and configuration management — the ingress side of the loop, and genuinely valuable, hands-on work, not busywork. Where useful, some may also move toward more BA-style spec work.
- **For everyone:** Phase 3 means a real reduction in contractor headcount, eventually — we don't know when, but we're naming it now rather than waiting. Phases 1 and 2 exist partly to make that transition possible without anyone being blindsided.

---

## What we actually do next

A few small steps, not a big bang:

1. Pick one small feature to pilot the whole loop on.
2. Copy our lint / SAST / test configs over so outside matches inside from day one.
3. Run the extractor → mock → Playwright loop on it, end to end.
4. Get together as a team afterward: what worked, what didn't, what we adjust.

Let's prove this on one feature, learn from it, and go from there — together.
