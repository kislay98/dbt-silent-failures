BLOCKERS | shlok | 2026-11-12
- None blocking. Two decisions needed for the four-per-type target, and two notes:
- **Join fan-out stops at two cases without a second lookup join.** The regions feed is the only join in `jaffle_shop`
  that can fan out without tripping a `unique` test, and both variants are built (`region_fanout`, re-sent rows;
  `regions_fanout_history`, real region changes). Two more need another lookup joined before an aggregate, for example
  a `raw_payment_fees` table keyed by payment method joined in `orders`, or a products table. That is a second
  extension of the base project after the week 2 one Kislay allowed once. Need: extend once more (one table, one join,
  clean totals unchanged), or cap fan-out at two and over-fill other types to reach 32.
- **Soft deletes and stale sources (the two spares) cannot pass the check as defined.** A `deleted_at` column the models
  ignore changes no model output, so the check's "at least one model output changed" rule fails it by design; the
  wrongness is against a new truth, not against the clean build. The same holds for a feed that stops updating. They
  need a second check mode where `ground_truth.md` states the expected output and the check verifies the actual output
  did *not* move. Need: a yes to add that mode (half a day) or leave the spares out of the pilot.
- **The models-only log is now a real build** (`dbt build --exclude resource_type:seed`, 34 nodes numbered from 1), and
  each run header records a sha256 of its prompt. Two Sonnet runs still inferred that the seed tests did not run, from
  the "29 data tests" header against the 27 that ran; that is what a real excluded build prints, so it stays.
- **Two swaps against the week 4 plan**, both for cause: `null_regions_new_customers` became `null_payment_amounts`
  (missing lookup rows are the `join_drops_rows` family, not a null flood), and `dst_end` became `utc_reexport` (a
  one-hour DST shift moves one order in this data; the re-export moves 14 and is the first timezone case a model can
  fix). `payments_fanout` is the first decision above. Six cases built either way, check passes on all six, 36 runs
  scored. Opus is still waiting on approval.
