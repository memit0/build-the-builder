# Conversation stress suites

These suites replay every supported practice case with a different customer
speaking style. They test whether the agent can preserve the same bank outcome
when customers split numbers across turns, correct themselves, digress, ask
questions back, or say dates and amounts in words.

Generate or refresh them from the canonical practice cases with:

```sh
python3 tools/generate_stress_cases.py
```

Run a suite against a built agent:

```sh
python3 tools/run_cases.py <agent_id> --cases stress-tests/cases-pieces.json
```

Repeat for `corrects`, `talkative`, `asks_back`, and `in_words`. Each suite has
the same 13 supported case IDs and expected ledger outcomes as
`public-test-set.json`; only the simulated customer's conversational style
changes. This makes the suites compatible with the practice session endpoint.
They do not add new bank records or replace the unseen final cases.
