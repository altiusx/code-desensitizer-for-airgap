# Developer walkthrough: extract → verify → audit → approve → transfer

This walks through the full flow a developer runs to take a feature out of a
confidential environment, using a small Angular `payroll` feature as the
example. Every block below is real tool output (ANSI colour stripped).

The two things that changed recently:

- **Extraction is fail-closed.** All protections (comment/doc-tag/logger
  stripping *and* string masking) are ON by default. You opt out per flag, and
  any disabled protection is announced loudly.
- **Nothing is cleared for transfer automatically.** Every extraction is
  verified against the mapping and audited for residual content, and the output
  stays *unapproved* until a human reviews the report and records approval of
  its exact content hash.

---

## 0. The input

```
src/app/payroll/
├── payroll-list.component.ts     # title, an internal support email
├── payroll.service.ts            # a prod API host behind the classified VLAN
├── payroll-list.component.html   # a title="Employee SSN and salary records"
└── payroll-list.component.scss   // brand color for Project BLUEJAY
```

## 1. Trace and extract (defaults = maximal scrubbing)

```bash
python code_extractor.py trace \
  --entry src/app/payroll/payroll-list.component.ts --src src --out ./extracted \
  --map-package app/payroll=app/feature1 --map-var Payroll=Widget
```

```
Writing sanitized files...
  ✓ app/feature1/widget-list.component.ts
  ✓ app/feature1/widget.service.ts
  ✓ app/feature1/widget-list.component.html
  ✓ app/feature1/widget-list.component.scss

  4 file(s) written to: extracted
  Prompt template → extracted/CLAUDE_PROMPT.txt
  Mappings saved → extracted/mapping.json
  Reversal instructions → extracted/REVERSE_INSTRUCTIONS.txt
  ✓ Verified: output is a fixed point of the mapping        ← runtime gate

═══ Transfer audit ═══
  Blocking: 0   Review: 0   Residual: 11   Unmapped identifiers: 9
  Report: extracted/TRANSFER_AUDIT.txt
  ⚠ NOT APPROVED FOR TRANSFER — review the report, then run:
    python code_extractor.py approve --dir extracted --hash <hash>
```

Because masking is on by default, the internal email and the prod URL in the
`.ts` files became reversible `STR_n` placeholders:

```ts
// extracted/app/feature1/widget.service.ts
@Injectable({ providedIn: 'root' })
export class WidgetService {
  private readonly apiHost = 'STR_2';         // was https://payroll.prod.acme-defense.mil
  load() { return fetchRows(this.apiHost); }
}
```

The runtime gate (`✓ Verified: output is a fixed point of the mapping`) is a
hard check: re-applying the mapping to every emitted file changes nothing. If a
sensitive name ever escaped the renamer, extraction aborts with exit code 2 and
writes nothing you could transfer.

## 2. Read the audit — it lists what the tool could NOT transform

`extracted/TRANSFER_AUDIT.txt`:

```
RESIDUAL CONTENT — untransformed text that will transfer: 11
  [html-attribute] app/feature1/widget-list.component.html:3: Employee SSN and salary records
  [html-text]      app/feature1/widget-list.component.html:2: {{ title }}
  [surviving-string] app/feature1/widget-list.component.ts:5: app-widget-list
  ...

Unmapped identifier vocabulary (top 9 of 9 — every name below transfers as-is):
  WidgetService ×3   apiHost ×2   load ×2   rows ×2
  fetchRows ×1   supportContact ×1   title ×1  ...
```

Two developer takeaways:

- **Masking does not touch HTML/CSS.** In the `.ts` file the email was masked,
  but `title="Employee SSN and salary records"` in the template passed straight
  through. The audit surfaces it with `file:line` so you catch it *before*
  transfer — fix it by adding a `--map-var`/`--map-package` entry or editing the
  source, then re-extract.
- **The identifier inventory is your mapping's completeness check.** Every name
  there is a term your mapping never renamed and that will leave as-is. Scan it
  for anything domain-sensitive (`supportContact`, `apiHost`, ...).

## 3. Disabling a protection is loud, and the audit catches the fallout

If you turn a protection off, the residue moves into the review report instead
of leaving silently:

```bash
python code_extractor.py trace ... --no-mask-strings
```

```
  ⚠ PROTECTIONS DISABLED: string masking
    Content those protections would remove will remain in the output
    and will be visible in the transfer audit.
...
REVIEW — sensitive-shaped content, confirm each one: 2
  [secret-pattern:email] app/feature1/widget-list.component.ts:11: payroll-ops@acme-defense.example
  [secret-pattern:url]   app/feature1/widget.service.ts:5: https://payroll.prod.acme-defense.mil
```

The audit's `REVIEW` bucket recognises secret shapes regardless of your mapping:
private-key blocks, AWS keys, JWTs, IPv4 addresses, emails, URLs, UNC paths,
GUIDs, credential-shaped assignments, and high-entropy tokens.

## 4. Approve the reviewed output

Approval is bound to the exact bytes you reviewed. A wrong or stale hash is
refused:

```bash
$ python code_extractor.py approve --dir extracted --hash 000000000000
Hash mismatch: the reviewed report does not describe the current output.
  current hash: ed5cfdb03d05   given: 000000000000

$ python code_extractor.py approve --dir extracted --hash ed5cfdb03d05
✓ Approved for transfer (hash ed5cfdb03d05).
  Marker → extracted/.transfer_approval.json
  Remember: mapping.json, REVERSE_INSTRUCTIONS.txt stay inside the environment.
```

Re-check any time. Edit one file after approval and it drops back to
"needs review":

```bash
$ python code_extractor.py audit --dir extracted        # exit 0 = approved & unchanged
$ echo "// late edit" >> extracted/app/feature1/widget.service.ts
$ python code_extractor.py audit --dir extracted        # exit 1 = changed, re-review
```

## 5. Classification markings are a hard stop

There is no flag to force past a blocking finding — remove the content and
re-extract:

```bash
$ printf '\n// Classification: SECRET//NOFORN\n' >> extracted/app/feature1/widget.service.ts
$ python code_extractor.py audit --dir extracted
  Blocking: 1 ...
  ✗ BLOCKING findings — this output cannot be approved for transfer.
    [classification-marking] app/feature1/widget.service.ts:9 // Classification: SECRET//NOFORN
```

`audit` exits 2, and `approve` also refuses (exit 2) even with the correct fresh
hash.

## Exit codes (the scriptable contract)

| Command   | 0                              | 1                          | 2                                   |
|-----------|--------------------------------|----------------------------|-------------------------------------|
| `trace`   | extracted + verified + audited | —                          | verification failed (nothing usable)|
| `audit`   | approved & unchanged           | awaiting/stale approval    | blocking findings present           |
| `approve` | approval recorded              | hash mismatch / refused    | blocking findings present           |

See [`transfer-gate.sh`](./transfer-gate.sh) for a copy-paste gate that refuses
to package an output directory unless `audit` exits 0.
