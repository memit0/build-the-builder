# Plan: a builder that works on any brief (extract → rule sheet → generate)

## Context

Freya "Build the Builder" hackathon. We submit a **builder**: `python3 builder/build.py {brief_path} {tool_api_path}` reads a bank brief and creates a Freya phone agent (workflow JSON) for debt-collection calls. It's scored on 60 hidden customers across **two unseen briefs**. Each customer runs 3 times and must pass all 3 checks (outcome / policy / honesty) every time. Score = passed/60. Limits: 30 min per build, no brief-specific content in code, a trace is required.

What we know so far:

- The kit's example agent (`examples/base-agent.json`) scored **0/10** on Brief A.
- The example agentic builder (`examples/agent_builder.py` + skill) scored **8/10 → 7/10** on Brief A in 30.4 min for $3.33. It produced `gen/brief_a.py`, a 776-line generator that works but has **Brief A hard-coded**: holidays, bands, phrases, outcomes. Useless for hidden briefs.
- Hidden briefs differ in numbers, identity questions, outcome table, calendar and format. One is "raw bank material": an email, a policy extract, an amending memo and a code list. The tool API keeps the same 7 tool names but fields and enums can change. `tools/run_cases.py` already knows identity fields `postcode`, `birth_year`, `company_number`, `founded_year`, which hints at the hidden variants.
- The practice bank serves only PUB-01..13, so we **cannot simulate customers for a new brief**. At final build time the only self-test is `POST /api/v2/chat/message`.

**Goal:** split building into (1) LLM extraction of the brief into a structured **rule sheet**, then (2) a deterministic **generator** that turns any rule sheet into a workflow, using the proven patterns from `gen/brief_a.py` with every Brief-A value replaced by a rule-sheet lookup.

## Architecture

```
builder/
  build.py        entry: build.py BRIEF TOOL_API [--slot NAME] [--test]  → saves + exports workflow; also holds Trace and the chat smoke test
  extract.py      brief.md (+ tool API) → rules.json   (Claude, JSON-schema output, 1–3 calls) + validate(rules, api) cross-checks
  api_spec.py     parse openapi.json deterministically: base URL, per-tool fields, enums
  generate.py     rules.json + api_spec → workflow dict (pure Python, no LLM), incl. prompt templates filled from rules
  snippets.py     parameterized Python code-step templates (dates, amounts, identity, hooks)
  lint.py         static checks on the generated workflow
dev/
  briefs/         synthetic briefs C (clean, other values) and D (raw mail+memo+code list) + their tool APIs
  check.py        run extract+generate+lint on every brief in briefs/ and dev/briefs/
```

Reuse, don't rewrite:

- `freya.py`: `save_workflow`, `export_workflow`, `get_call`, chat helpers.
- `gen/brief_a.py`: port its working pieces into `snippets.py`/`generate.py` and replace the constants. That covers `COMMON` (business-day maths), `SETUP_CODE`, `ACCOUNT_CODE`, the `*_PRE`/`*_POST` hooks (DOB normalisation, verify, callback window, promise checks, transfer, close), `api_tool`, `close_tool`, `transfer_tool`, `code_node`, `conv`, the bool-both-spellings helper and the "no double braces in snippets" safety walk.
- `tools/run_cases.py`: practice regression on PUB cases (`--reps 3`, `--style …`).

## Step 1: rule-sheet schema (`schema.py`)

Covers every parameter that changed or could change between briefs:

- **call_vars**: names and meaning (customer_id, name fields, title, now).
- **opening**: required disclosures, forbidden words before verification, confirmation phrases, max asks, unclear outcome.
- **third_party**: callback window (days offset, business-day rule, hours, inclusive), retry count, outcomes (callback / no_callback / wrong_number / deceased + exact sympathy sentence).
- **identity**: ordered list of questions (`field` enum from the tool API, how to ask, normalisation: date / digits / year / postcode / free), max wrong total, hint handling (e.g. `day_month_swapped`), decline rule and outcomes.
- **products**: per type → spoken name template, floor field name, extra fields.
- **calendar**: business weekdays, holiday list, "next/this weekday" semantics.
- **bands**: list of `{days_late range, deadline business days, persuasion attempts}`.
- **amount rules**: ask full first, floor offer count, below-floor handling, "rest stays overdue" style phrases.
- **persuasion ladder**: ordered attempts (date rule + amount rule).
- **intents**: a list of `{trigger, response sentence(s), tool action (transfer queue / note reason / none), outcome+reason, precedence}`, for paid, dispute, hardship, restructuring, human, scam, robot, unknown-question, refuses-to-talk.
- **confirmation and recording**: read-back phrase, retry-on-failure count, success sentence, failure outcome.
- **multi_product**: present all, ask-all-today, per-product order, note rules, combined read-back, closing precedence.
- **outcomes**: the full table `{outcome, reason, when}`.
- **verbatim_phrases**: every quoted sentence in the brief.
- **unmapped_rules**: free-text rules that fit nowhere else. These go into prompts word for word, so nothing is silently dropped.

`validate()` checks that every outcome, reason, queue and identity field in rules.json exists in the tool API's enums, that bands cover 1..max days, and that holidays parse. On failure it sends the errors back to the extractor (max 2 repair rounds).

## Step 2: extraction (`extract.py`)

- Claude (`claude-opus-5-5`) via `claude_agent_sdk` (works with the existing login) or the `anthropic` SDK if `ANTHROPIC_API_KEY` is set. Structured JSON output against the schema.
- Prompt: read the whole brief **and** tool API. Raw material may contain an amending memo, so later or dated instructions override earlier ones; resolve that and record what was overridden. Map code lists to API enums.
- Two passes: extract, then a separate "audit" call that diffs the brief against rules.json and lists missed or contradicted rules. Merge the result.

## Step 3: generator (`generate.py`)

- Graph shape stays like the passing `gen/brief_a.py`: setup code → opening → (third-party branch) → identity → get_account code → negotiate (per product loop when there are several products) → confirm/record → close nodes. Intents become global nodes or tools per rules.
- All rule values are injected into snippets as a JSON constant (`RULES = json.loads(...)`). Code reads values from it; nothing is hard-coded.
- Tool bodies are built from `api_spec` (fields, enums, base URL), never typed by hand.
- Prefer deterministic routing on tool results and code-set string flags over `matches_intent`.
- Sandbox-outage resilience (the main failure cause in the example run): wrap every snippet in try/except with a safe fallback value, compute date facts once in setup, and keep hooks small.

## Step 4: lint (`lint.py`)

Every node is reachable and every path ends in `end_call`. Every passthrough node has an `always` transition. Every outcome/reason pair in rules is reachable via some close path. No `null` values. No `{{` inside code. Every tool field and enum matches the API. Every verbatim phrase appears in some prompt or line.

## Step 5: build.py flow and budget

extract (≤5 min) → validate/repair → generate → lint → `save_workflow(slot)` (updates in place) → smoke via chat (2–3 scripted openings) → `export_workflow` → trace. Target **under 10 min** (build time is a tie-break). `--test` optionally runs `run_cases.py` for brief A/B practice only.

## Step 6: generalisation set (`dev/briefs/`)

Write Brief C (clean spec: other holidays, 3 bands, postcode + birth_year identity, different outcome names and queues) and Brief D (the same rules as raw material: mail + policy extract + amending memo + code list), each with a modified tool API. `dev/check.py` must pass extract + validate + lint on A, B, C and D. Practice simulations stay on A and B.

## Verification

1. `python3 dev/check.py`: extract/validate/lint pass on all 4 briefs; rules.json for A matches the known Brief A values (spot-check against `gen/brief_a.py` constants).
2. `python3 builder/build.py briefs/brief-a.md tools/openapi.json` → `run_cases.py <agent> --brief A --reps 3`. Target ≥ the example's 8/10, then add `--style pieces|corrects|in_words`.
3. The same for Brief B (pass both A+B, or a combined brief) with `--brief B`.
4. Build C and D end-to-end: check lint passes, the workflow saves, and chat smoke shows the correct opening and identity question.
5. Check the trace contains start/end, every model call with purpose, workflow/agent IDs, and smoke results. Check the build time is under 30 min.

## Notes / risks

- Brief B "builds on Brief A". Hidden briefs are single self-contained files, but `build.py` should accept several brief paths (concatenated) for practice.
- The code sandbox (snekbox) had outages. Keep critical logic working when a hook fails: a fallback flag plus a prompt-level backstop.
- Commit `rules.json` and the generated workflow per run as artifacts. Keep `env.sh` out (it's already in `.gitignore`).
