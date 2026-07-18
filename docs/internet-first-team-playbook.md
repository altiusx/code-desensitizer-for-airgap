# Internet-First, Airgap-Final — Team Working Edition
### A practical playbook for the core team

> Companion to `internet-first-team-edition.pptx`. This is the plain-language version, aimed at the people who'll actually do the work — not the leadership/cyber-facing pitch. That fuller version (`internet-first-strategy.md` + `internet-first-airgap-final.pptx`) still exists and covers gates, approval tiers, department politics, and audit-style risk analysis in depth; keep both around and mix them for the final presentation as needed.

---

## Why we're doing this

Three honest reasons, no scare stats:

1. **Friction eats our months.** Environment setup, access requests, and pipeline waiting take up more of a delivery cycle than the actual feature does. That's the part we can fix.
2. **Let's out-finish, not just out-start.** Other teams are already prototyping fast with AI on the public internet. A fast demo is easy — we want our fast path to lead all the way to a real, running feature.
3. **We rebuild the same plumbing every time.** Auth, API wiring, form validation, test setup — written fresh on every MFE. A lot of that doesn't need to be rewritten again.

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

## The part that makes this easy later: staying aligned with Confi

This is the single highest-leverage habit in the whole paradigm. Do these six things consistently and "bringing it inside" becomes closer to a deploy than a rewrite:

1. **Same lint / test / SAST rules** — copy the actual config files over, don't reinvent them.
2. **Same API contract format (OpenAPI)** as the source of truth on both sides.
3. **Same folder & module structure and naming conventions** — just wearing our cover names.
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

## The one tricky part, made simple: real rules and data

The app we build outside works off placeholder rules and made-up sample data, so it's fully testable entirely on its own. Once it moves inside, we point it at the real rules and real data instead — similar to swapping an `.env` file, not rewriting the app.

- **Outside:** fake but realistic values. Looks and behaves the way the real thing will — enough to build and test the whole feature with confidence.
- **Inside:** same components, same tests. Only the values underneath change — reviewed and versioned like everything else we ship.

Some rules will be complex or sensitive enough that we just build them directly inside — that's completely fine. We'll flag those upfront, per feature, so nobody's surprised later.

---

## Building without waiting on anyone: testing without a real backend

1. **OpenAPI spec** — the contract everyone builds against: types, endpoints, sample data.
2. **Generated types + client** — nobody hand-writes fetch calls; they're generated from the spec.
3. **Mock backend (MSW)** — the app runs for real, with realistic fake data, zero live backend.
4. **Playwright tests** — real user flows, including error cases, fast and repeatable.

This is the part that actually saves us months: most bugs get caught outside, where fixing them is a two-minute edit instead of a trip through the internal pipeline.

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

## Getting there: a few honest stages

**Stage 1 — Try it:** pick one real feature, set up matching pipelines & configs outside, run the full loop once start to finish, talk honestly about what was clunky.

**Stage 2 — Get comfortable:** a few more features through the loop, fix the friction found in Stage 1, contractors settle into integration & testing roles, the routine starts feeling routine.

**Stage 3 — Make it the norm:** most new work starts outside by default, Angular inventory & porting well underway, sign-off is quick because the habits are solid, we're no longer thinking about this as "new."

No fixed dates on purpose — each stage ends when the criteria above are actually true, not when a calendar says so.

---

## Being straight about the people side

- **For developers:** more time on the interesting problems, less on environment friction and rebuilding plumbing. The tools handle more of the repetitive part so we can focus on what actually needs a person.
- **For contractors:** some roles shift toward integration, environment testing, and configuration management — genuinely valuable, hands-on work, not busywork. Where useful, some may also move toward more BA-style spec work.
- **For everyone:** nothing about headcount is decided or urgent right now. As things become clearer, we'll say so — plainly and early, not as a surprise later.

---

## What we actually do next

A few small steps, not a big bang:

1. Pick one small feature to pilot the whole loop on.
2. Copy our lint / SAST / test configs over so outside matches inside from day one.
3. Run the extractor → mock → Playwright loop on it, end to end.
4. Get together as a team afterward: what worked, what didn't, what we adjust.

Let's prove this on one feature, learn from it, and go from there — together.
