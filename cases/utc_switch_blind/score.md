# Score: utc_switch_blind

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden, project
7d5b525 plus the week 3 working tree. Sonnet is `claude-sonnet-5-5`, Haiku is `claude-haiku-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict, every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | no | no | Mechanism and 5h30 right (India gives IST), the switch not: treated the feed as UTC throughout, offered the switch as an alternative with a query to decide, then wrote the blanket conversion (+330 minutes on every row). |
| sonnet-2 | no | no | Same mechanism, no switch; explained the mid-March start as coincidence ("earlier orders happened to fall before 18:30 UTC"), which is false, ten do. Blanket conversion. |
| sonnet-3 | yes | no | "The feed changed from IST to UTC around mid-March." `case` on a cut-over var defaulting to 15 March. |
| haiku-1 | no | no | Mechanism right, the switch not: steady-state UTC against IST, hedged on why it started mid-March; blanket conversion. |
| haiku-2 | yes | no | Placed the switch around 15 March, then applied the blanket conversion anyway, with a check that would have exposed the January orders. |
| haiku-3 | yes | no | Argued from the base rate (a quarter of orders after 18:30 UTC, none shifted before March) that early rows are local; two-step fix with the 15 March boundary. |

**Verification.** On this injection the blanket conversion moves ten January and February orders a day forward
(checked on `utc_switch`, same data). A boundary at 15 March, or at order 73, gets every `order_date` right and leaves
16 timestamps (1 to 14 March) 5h30 early, because none of those orders moved across midnight and nothing in the
prompt can place the switch earlier. The admin-count reconciliation every run proposed fails with 9 days on the broken
data and passes on clean data.

**What it says.** Without the example order the root-cause rate halves (3 of 6; three more named the zone mismatch but not the switch) and no fix is complete (0 of 6). The country gives the
offset; the only evidence for a switch is "the two always agreed" plus the absence of pre-March mismatches, and three
runs explained that away.

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | no | IST wall-clock until mid-March, UTC after, with no example order to go on; every listed disagreement reproduced by +5:30; a cut-over id var "between the last order on 14 March and the first on the 15th", so every date comes right and the 1 to 14 March times stay 5h30 early; tells the reader to check the older rows are IST before applying. |
| opus-2 | yes | no | Same switch at about 15 March, keyed on a cut-over id; same partial result; order 89 at 15 minutes past the boundary cited as the proof. |
| opus-3 | yes | no | Same; places the first UTC row at order 73 from the counts and converts from there; same partial result. |

Opus: 3 of 3 found, 0 of 3 fixed.
