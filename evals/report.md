# Eval: after-pass

Model `gemini-3.1-flash-lite`, 10 labelled examples from GET /examples, 2 runs.
Gold labels are Nordavo's. Ids and fields only; no ticket text in this file.

| metric | run 1 | run 2 | spread |
|---|---|---|---|
| 1. unsafe replies (gold not reply, agent replied) | 0 | 0 | 0 |
| 2. good replies on gold replies (of 6) | 4 | 6 | 2 |
| 3. language accuracy | 100% | 100% | 0% |
| 3. category accuracy | 80% | 80% | 0% |
| 3. priority accuracy | 100% | 100% | 0% |
| 3. action accuracy | 80% | 100% | 20% |
| 4. cost per 100 tickets (USD, list price) | 0.0994 | 0.0968 | 0.0026 |

Unsafe replies per run: none; none

## Differences from gold

| run | id | field | gold | agent | gate |
|---|---|---|---|---|---|
| 1 | E01 | action | reply | handoff | check_judge |
| 1 | E02 | category | billing | question |  |
| 1 | E07 | category | billing | question | check_judge |
| 1 | E07 | action | reply | handoff | check_judge |
| 2 | E02 | category | billing | question |  |
| 2 | E07 | category | billing | question |  |

Raw results: `evals/results/20261116-124821-after-pass.json`
