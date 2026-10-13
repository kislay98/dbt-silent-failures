WEEKLY REPORT | shlok | Week 1 | 2026-10-17

## Done
| Task ID | Task | Planned days | Actual days | Checks passed first try (Y/N) | Link (PR, post, chart) |
|---|---|---|---|---|---|
| T1-shlok-01 | dbt + DuckDB setup, clean `jaffle_shop` build, log in repo (`logs/dbt_build_clean_shlok.log`) | 1 | 0.5 | Y | branch `shlok/week1` |
| T1-shlok-02 | Verify dev's `duplicate_payments` case: ran `inject.py`, all five `ground_truth.md` numbers match | 0.5 | 0.5 | Y | branch `shlok/week1` |
| T1-shlok-03 | Inject `gift_card_dollars`: 12 gift card payments in dollars, 28/28 tests pass, `sum(orders.amount)` 1672 to 1469.05 | 1 | 1 | Y | `cases/gift_card_dollars/inject.py` |
| T1-shlok-04 | Ground truth for `gift_card_dollars` | 0.5 | 0.5 | Y | `cases/gift_card_dollars/ground_truth.md` |
| T1-shlok-05 | Model run: Claude Sonnet, saved prompt and full response | 1 | 0.5 | Y | `cases/gift_card_dollars/prompt.md`, `response.md` |
| T1-shlok-06 | Score the model: root cause found, fix applied and verified, its test catches the bug | 0.5 | 0.5 | Y | `cases/gift_card_dollars/score.md` |
| T1-shlok-07 | Friday weekly report | 0.5 | 0.5 | Y | `reports/2026-10-17-shlok-weekly.md` |

## Not done
| Task ID | Task | Status | Why | Blocked since |
|---|---|---|---|---|
| none | none | none | none | none |

## Next week
| Task ID | Task | Definition of done | Planned days |
|---|---|---|---|
| T1-shlok-08 | Harder variant of `gift_card_dollars`: convert only half the gift card rows and hide the seed from the prompt | Case folder with the five files, model scored | 2 |
| T1-shlok-09 | Case: a join that silently drops rows (orders with no payment vanish from `orders`) | Case folder with the five files, model scored | 2 |
| T1-shlok-10 | Run a second model (Claude Haiku) on both my cases and dev's | `response_haiku.md` and score for each case | 1 |

## Reviews I gave
| PR link | Author | Result (approved / changes requested) |
|---|---|---|
| none | none | none |

## Numbers
- T1 cases built (total): 1
- T1 cases verified by the other intern (total): 1
- T1 models run (total): 1
- T2 / T3 shipped this week: none

## Demo on Friday
Run `cases/gift_card_dollars/inject.py`, then `dbt build` showing 28/28 pass with `sum(orders.amount)` at 1469.05 instead of 1672, then Sonnet's response naming the 12 rows.

## Shareable win
A unit mismatch that shrinks gift card revenue 100x passes all 28 dbt tests; Claude Sonnet found the exact 12 rows and the bad divide from the project files alone.
