BLOCKERS | shlok | 2026-10-22
- **Model runs were not isolated from this machine's Claude Code setup.** Every `claude -p` run so far (week 1 and week 2,
  Sonnet and Haiku) loaded the user settings of this Mac: a SessionStart hook puts a "lazy senior developer" (ponytail)
  persona into the system prompt, and the global CLAUDE.md adds log-search and review rules. Haiku's `gift_card_dollars`
  answer ends with a literal "Ponytail:" line, and a direct probe confirmed the persona is present in a default run.
  Fix verified today: `claude -p --setting-sources "" --model <model> --tools "" --output-format json`, run from outside
  the repo, reports no such instructions. All seven cases were re-run that way; see below. Not blocking the cases, but it is a confound in every result so far.
  Done the same day: all seven cases re-run with both models under the isolated command (14 runs), every fix and
  test re-verified, every `score.md` rewritten with the confounded verdict kept for comparison. The only verdict that
  moved was Sonnet on `utc_switch`, from fail to pass on root cause; one sample per run, so I cannot say the persona
  caused it. Week 3 runs each case three times.
- **PR size cap, decision 1 in the week 2 plan, still open.** Each case folder is 700-830 lines because `prompt.md` embeds
  the project files. The week's work sits in one working tree as nine PRs; the five case PRs each exceed 500 lines on
  their own. Need from Kislay: exempt case PRs, or approve the harness assembling the prompt at run time, in which case
  the five new prompts become a header plus a file list in week 3.
- **Two models per case means six files per folder** (`response_haiku.md` next to `response.md`, Haiku scored in a
  second section of `score.md`), against the five-file rule. Need from Kislay: OK to add one `response_<model>.md` per
  model and keep a single `score.md`, or move runs into a `runs/` subfolder.
