# Blind scoring, 30 runs

Each numbered file holds one case's ground truth (what was injected and what the right fix restores) and one model's
response to that case. Score the response on `sheet.csv` without the first scorer's verdicts. The files were drawn at
random from `results.csv` by `reports/blind_sample.py` (seed 2026; this folder holds the standard-sample runs).

- `root_cause`: yes if the response names the change that broke the data as its conclusion. Naming only the mechanism
  (for example "the incremental window misses old rows" without the late backfill that fell outside it), hedging
  between the right cause and a wrong one, or handing the decision back ("may be a duplicate or a legitimate split
  payment, check with finance") is no. A cause named as the conclusion with a data check the model could not run is yes.
- `fix`: yes if the response's fix, applied to the broken data, makes every affected value match the clean data. A fix
  that corrects some values, or one that would also change rows that were right, is no.
- `defects_found`: how many of the injected defects the response names as a cause: 0, 1 or 2 on a case with two
  defects, 0 or 1 otherwise. `root_cause` is yes only when every defect is named.
- `notes`: optional, a few words.

Each response was written by a model with no tools, from the project files, the build log and a few query results.
The response alone is scored, not what the model could have found with more.
