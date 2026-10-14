"""Thirty runs for a second scorer, drawn at random from results.csv with a fixed seed. Each file in reports/blind/
holds the case's ground truth and the run's response with the header line removed, reports/blind/sheet.csv is the blank
scoring sheet, and reports/blind_key.csv (kept out of the folder, not for the scorer) maps the numbers back to the runs
and carries the first scorer's verdicts. `--samples standard` draws from the standard-sample runs instead, into
reports/blind_standard/ with the key in reports/blind_standard_key.csv.
Run from the repo root: python3 reports/blind_sample.py [--samples standard]
"""
import csv
import random
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
SAMPLES = sys.argv[sys.argv.index("--samples") + 1] if "--samples" in sys.argv else "curated"
rows = [r for r in csv.DictReader((ROOT / "results.csv").open()) if r["samples"] == SAMPLES]
picked = random.Random(2026).sample(rows, 30)
name = "blind" if SAMPLES == "curated" else f"blind_{SAMPLES}"
out = ROOT / "reports" / name
out.mkdir(exist_ok=True)
key = [["id", "case", "type", "samples", "model", "run", "root_cause", "fix", "defects_found"]]
for i, r in enumerate(picked, 1):
    case = ROOT / "cases" / r["case"]
    run = case / "runs" / ("standard" if r["samples"] == "standard" else "") / f"{r['model']}-{r['run']}.md"
    response = run.read_text().split("\n", 1)[1].strip()  # the header line names the model, the cost and the date
    truth = (case / "ground_truth.md").read_text().strip()
    (out / f"{i:02}.md").write_text(f"# Blind run {i:02}\n\n## Ground truth\n\n{truth}\n\n## Response\n\n{response}\n")
    key.append([f"{i:02}", r["case"], r["type"], r["samples"], r["model"], r["run"], r["root_cause"], r["fix"], r["defects_found"]])
with (out / "sheet.csv").open("w", newline="") as f:
    w = csv.writer(f, lineterminator="\n")
    w.writerow(["id", "root_cause", "fix", "defects_found", "notes"])
    w.writerows([[f"{i:02}", "", "", "", ""] for i in range(1, 31)])
with (ROOT / "reports" / f"{name}_key.csv").open("w", newline="") as f:
    csv.writer(f, lineterminator="\n").writerows(key)
print(f"reports/{name}: 30 of {len(rows)} {SAMPLES} runs, key in reports/{name}_key.csv")
