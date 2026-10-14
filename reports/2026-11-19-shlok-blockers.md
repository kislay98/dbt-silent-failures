BLOCKERS | shlok | 2026-11-19
- None blocking. Kislay's four answers (14 Nov) are in: Opus three times on every case, one more lookup join, no spares,
  the rest mixed. Four notes:
- **Second extension in.** `raw_payment_methods` (payment method, processor, fee rate) is joined inside `orders` for a
  `fees` column. Every existing table is unchanged on the clean build; the test count goes 40 to 44. The Opus runs on
  the 21 existing cases were made before the extension landed, so all three models answered the same project; the seven
  new cases run on the extended one, and their run headers say so (`f769fdf+wk6ext`).
- **Two hung Opus calls.** Two `claude -p` calls on Opus sat for over ten minutes with no answer while a direct call
  answers in four seconds, so not a rate limit. The harness now gives each call 300 s and records a hung call as a failed
  run, and the batch goes on; the two were re-run on a fresh prompt build, which their headers record. No fallback to one
  run per case was needed; the Opus runs cost about USD 16.
- **A stale parse in the scratch copy.** The new cases were built and first run from a scratch copy of the project while
  the Opus batch used the real one. dbt's partial-parse cache in that copy kept a test name (`...__delivered`) from the
  case that widens `accepted_values`, so the build log in every later prompt from that copy named a value that leaks one
  mixed case's hidden defect. Eighteen Sonnet and Haiku runs were thrown away and re-run after `check_case.py` switched
  to a full parse (`--no-partial-parse`). The real project never had the stale name: its manifest and the week 5 prompt
  renders were checked.
- **One prompt amended after its first runs.** `rename_and_utc`'s first prompt did not say that daily counts had matched
  the admin before the load; with the project docs calling `order_date` "Date (UTC)", all six first runs read the whole
  history as UTC and converted every row, which is a defensible reading of that prompt. One sentence was added, the six
  runs set aside, and the case run again; the score file says so.
- **Counting mixed cases.** With the remaining cases mixed, I count a mixed case toward each of its two types for the
  four-per-type target (`results.csv` keeps `mixed` as the type for the chart). Counted that way every type is at four
  or more after this week (28 cases), and four more mixed cases in week 7 reach the 32. Say if you want mixed counted
  only as mixed: that leaves duplicate rows, schema drift, late data and category change at two and needs eleven more.
