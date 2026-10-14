BLOCKERS | shlok | 2026-11-05
- None blocking. Three things to know, one of them a number that differs from what was agreed:
- **Timezone root cause scores 9 of 12, not 12 of 12.** Under the yes/no rubric the three blind-prompt runs that named
  the zone mismatch but explained the switch away ("earlier orders happened to fall before 18:30 UTC") or hedged between
  "always UTC" and "switched" score no. The root-cause panel shows 4 of 6 for Sonnet and 5 of 6 for Haiku on the type;
  complete fix is 0 of 12 as agreed. No run was re-read for this: `results.csv` already had the two columns, so `partly`
  became `no` and the three score files say why. If "names the mechanism" should count as found, those three rows flip.
- **`region_fanout_quiet` is dropped.** With the seed lines gone from the build log (`Log: models only`, now a line in
  `prompt.md` that `check_case.py` honours) all six new runs still found and fixed it: every run read the `updated_at`
  column off the raw table list and the one non-distinct `sum` in the join. The week 3 runs were 6 of 6 too, so the
  variant measured nothing and went, per the plan's definition of done; the week 3 version is in git history. The flag
  stays and is on for every week 4 case. One Haiku run noticed the stripped lines from the node numbering ("the log
  starts at 5 of 40"); a models-only log from a real `dbt build --exclude resource_type:seed` would have no gap, proposed
  for week 5 rather than changed under the 30 runs already saved against the current prompts.
- **Five cases built and run.** `duplicate_orders`, `coupon_cents`, `payment_method_renamed`, `late_payments` and
  `status_new_value`: check passes on all five, 29 of 30 runs found and fixed. The miss is Haiku on `coupon_cents`, which
  read the shrunken coupon rows as missing rows and proposed `relationships` tests. 15 cases after this week, two per type
  for six types and one each for join fan-out, null flood and mixed; 32 by week 8 means about six a week in weeks 5 to 7.
