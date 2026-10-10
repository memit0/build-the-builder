# Policy compiler and validation

The pipeline remains brief → Claude rule sheet → deterministic Python generator → Freya workflow. This update adds an executable policy layer; it does not replace the runtime language model.

## What changed

- Extraction first enumerates an independent inventory of atomic source requirements. Each gets an ID, exact source quote, criticality and active/superseded status. A final independent audit checks values at concrete rule-sheet paths. Missing IDs, audit errors, changed criticality, fabricated source quotes and critical rules left in `unmapped_rules` fail the build.
- `execution.minimum_offer` stores a limit, per-product/per-call scope, explicit numbered-attempt exceptions and source quote. A limit of -1 means unspecified, not a guessed bank policy. Brief A's permitted second floor offer must remain an exception.
- `execution.intent_actions` uses reusable flags: stop negotiation, allow a customer-initiated proposal and prohibit a promise. These support dispute/paid/hardship variants without generating arbitrary Python or copying bank values into the compiler.
- `execution.refused_note_reason` maps exhausted persuasion to the bank's actual note enum, which can differ from its closing reason.
- Runtime state stores issued/refused attempts, floor-offer counts, staged values, the combined read-back snapshot, exact confirmed values, recorded products and recording failures. Any change invalidates the unrecorded confirmation set. Notes use actual state counters; a supplied `attempts_made` can no longer bypass checks.
- Identity question order and wrong-answer totals are guarded. Only the guarded `id_state` routes to account access. Account loading has a verification pre-hook too.
- Amounts, product IDs, allowed dates and recording budgets are checked again before recording. NaN, infinity, missing account data and unknown IDs are blocked. Date calculations use the call timestamp exclusively: missing time no longer falls back to an example or the server clock.
- Rule-schema validation, semantic validation, workflow lint and platform dry-run failures stop before save/publication. Failed runs leave a final trace event. Failed smoke/simulation checks return failure; a smoke failure can only be detected after publication, and its export remains available for diagnosis.
- `<slot>.coverage.json` links requirements to emitted code/transition evidence and labels mixed code/prompt rules weak. Legacy sheets carry explicit audit/offer-limit warnings. Re-extract them for a final submission.

## Local checks

```sh
python -m unittest discover -s tests -v
python dev/check.py
python builder/build.py briefs/brief-a.md briefs/brief-b.md tools/openapi.json \
  --rules dev/out/ab.rules.json --no-save --slot offline-ab
```

The tests execute generated snippets. They cover offer exceptions and scopes, fabricated attempt counts, changed and combined confirmations, holiday/deadline boundaries, nonfinite values, identity ordering, disputes, recording retries, extraction completeness and save-blocking validation failures. They are offline and do not prove conversation-level compliance.

## Live validation still required

The `policy_step` tool uses Freya's documented `code_exec` tool form and `<tool>_args.<field>` variables. A code tool writes `policy_instruction`, routes through `policy_dispatch`, and enters `policy_result` so its prompt can resolve updated variables. Unlike `api_call`, `code_exec` does not support pre/post hooks. Confirm this argument binding and re-entry behavior in a real Freya simulation before submitting a final agent.

Pre-hooks block invalid bank requests by clearing the URL; Freya documents that a missing URL fails locally. We do not use fabricated bank endpoints anymore. An outage in the platform's hook executor itself cannot be handled by Python inside that failed executor; keep the prompt backstops and test on the platform.

The runtime model still classifies customer intent, refusal and confirmation. It is also responsible for speaking only the computed proposal and actually reading back the values before requesting confirmation. State guards prevent forbidden state changes when these events go through the protocol, but cannot prevent an arbitrary model utterance or prove that a claimed customer confirmation happened. The coverage report marks these mixed rules as weak rather than certifying them as deterministic.

With credentials available, rebuild from the source briefs (without `--rules`) to obtain the new policy and inventory fields:

```sh
python builder/build.py briefs/brief-a.md briefs/brief-b.md tools/openapi.json \
  --slot policy-ab --test B --reps 3
python tools/run_cases.py <agent_id> --brief A --reps 3
python tools/run_cases.py <agent_id> --cases stress-tests/cases-corrects.json --reps 3
python tools/run_cases.py <agent_id> --cases stress-tests/cases-asks_back.json --reps 3
```

Run the other existing styles as time permits, sequentially in the workspace. The runner now exits nonzero when any case fails any repetition. No live extraction, publication or simulation was validated in the implementation environment.
