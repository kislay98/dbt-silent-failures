"""Check a case, assemble its prompt, or run a model on it.

    uv run --project jaffle_shop check_case.py [cases/<name> ...]      check (default: every case)
    uv run --project jaffle_shop check_case.py cases/<name> --prompt   print the assembled prompt
    uv run --project jaffle_shop check_case.py cases/<name> --run sonnet --run haiku --times 3
        run each model that many times on one assembled prompt, save runs/<model>-<n>.md

A case passes when the clean project builds with every test passing, inject.py
changes at least one model's output, `dbt build` still passes after the
injection, and the key number in ground_truth.md matches on both sides:

    **Key number:** `select round(sum(amount), 2) from orders` is 1672.0 clean and 1852.0 broken.

prompt.md holds the symptom text, whether seeds are shown, whether the build
log is the full build or a models-and-tests build (`Log: models only` runs
`dbt build --exclude resource_type:seed` after the injection, like a nightly
build where ingestion loads the sources), and the queries whose results the
model gets; the project files, the broken build log and the query results are
assembled at run time. Each saved run records the sha256 of the prompt it answered;
a model call that gives no answer within TIMEOUT seconds is reported as a failed
run and the batch goes on. dbt runs with a full parse, because the partial-parse cache
once kept a test name from a case that had edited a schema file. jaffle_shop/ is copied aside before
the injection and put back after it, so uncommitted project changes survive.
After the injection the seeds are reloaded in full (a source table with a new
column must land) and the models run incrementally, like a nightly build.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import duckdb

ROOT = Path(__file__).parent
SHOP = ROOT / "jaffle_shop"
KEEP = {".venv", "target", "logs", ".user.yml"}  # build artifacts, not part of the project
TIMEOUT = 300  # seconds per model call; a hung call is reported as a failed run and the batch goes on
KEY = re.compile(r"\*\*Key number:\*\* `(.+?)` is (\S+?) clean and (\S+?) broken")


class CaseError(Exception):
    pass


def sh(*cmd, cwd=ROOT, **kw):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, **kw)


def dbt(*args):
    """(ok, 'PASS=.. ERROR=..' summary, full stdout)"""
    r = sh("uv", "run", "dbt", *args, "--no-use-colors", "--no-partial-parse", cwd=SHOP)  # a full parse: the cache kept a renamed test after a case edited a schema file
    done = [l.split("Done. ")[-1] for l in r.stdout.splitlines() if "Done. " in l]
    return r.returncode == 0, done[-1] if done else r.stdout[-800:], r.stdout


def connect():
    return duckdb.connect(str(SHOP / "jaffle_shop.duckdb"), read_only=True)


def snapshot():
    con = connect()
    names = [n for (n,) in con.sql(
        "select table_name from information_schema.tables where table_schema = 'main'").fetchall()]
    return {n: Counter(con.sql(f'select * from "{n}" order by all').fetchall()) for n in names}


def clean_build():
    ok, summary, _ = dbt("build", "--full-refresh")
    if not ok:
        raise CaseError(f"clean build failed: {summary}")
    return summary


@contextmanager
def injected(case):
    """inject.py applied, seeds reloaded, models built; yields (inject output, build summary, build log)."""
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(SHOP, f"{tmp}/shop", ignore=shutil.ignore_patterns(*KEEP, "*.duckdb"))
        try:
            inj = sh(sys.executable, str(case / "inject.py"))
            if inj.returncode:
                raise CaseError(f"inject.py failed: {inj.stderr.strip()[-800:]}")
            ok, summary, _ = dbt("seed", "--full-refresh")
            if not ok:
                raise CaseError(f"seeds do not load after injection: {summary}")
            ok, summary, log = dbt("build")
            if not ok:
                raise CaseError(f"not silent, dbt build failed after injection: {summary}")
            yield inj.stdout.strip(), summary, log
        finally:
            for p in SHOP.iterdir():
                if p.name not in KEEP and p.suffix != ".duckdb":
                    shutil.rmtree(p) if p.is_dir() else p.unlink()
            shutil.copytree(f"{tmp}/shop", SHOP, dirs_exist_ok=True)


def key_number(case):
    gt = case / "ground_truth.md"
    m = KEY.search(gt.read_text()) if gt.exists() else None
    return (m.group(1), float(m.group(2)), float(m.group(3))) if m else None


def check(case):
    """Return (reason or None, summary lines)."""
    key = key_number(case)
    try:
        clean_summary = clean_build()
        clean = snapshot()
        clean_key = connect().sql(key[0]).fetchone()[0] if key else None
        with injected(case) as (inject_out, broken_summary, _):
            broken = snapshot()
            broken_key = connect().sql(key[0]).fetchone()[0] if key else None
            manifest = json.loads((SHOP / "target/manifest.json").read_text())
    except CaseError as e:
        return str(e), []
    seeds = {n["name"] for n in manifest["nodes"].values() if n["resource_type"] == "seed"}

    lines = [f"clean:    {clean_summary}", f"injected: {broken_summary}", f"inject.py: {inject_out}"]
    changed_models = []
    for name in sorted(clean.keys() | broken.keys()):
        added = broken.get(name, Counter()) - clean.get(name, Counter())
        removed = clean.get(name, Counter()) - broken.get(name, Counter())
        if not added and not removed:
            continue
        if name not in seeds:
            changed_models.append(name)
        lines.append(f"{name}: +{sum(added.values())} -{sum(removed.values())} rows")
        lines += [f"    + {r}" for r in list(added)[:3]] + [f"    - {r}" for r in list(removed)[:3]]
    if not changed_models:
        return "no model output changed after injection", lines
    if key:
        sql, want_clean, want_broken = key
        lines.append(f"key number: {sql} -> clean {clean_key}, broken {broken_key}")
        for label, got, want in (("clean", clean_key, want_clean), ("broken", broken_key, want_broken)):
            if got is None or abs(float(got) - want) > 0.005:
                return f"key number {label}: ground truth says {want}, got {got}", lines
    else:
        lines.append("key number: none in ground_truth.md")
    return None, lines


TYPE_NAMES = {"BIGINT": "int64", "INTEGER": "int32", "HUGEINT": "int128", "DOUBLE": "double", "VARCHAR": "varchar",
              "DATE": "date", "TIMESTAMP": "timestamp", "BOOLEAN": "boolean"}


def render(rel):
    """DuckDB-style box table with every row (str(rel) stops at 20 rows)."""
    cols = [d[0] for d in rel.description]
    types = [TYPE_NAMES.get(str(t), str(t).lower()) for t in rel.types]
    numeric = [str(t) in ("BIGINT", "INTEGER", "HUGEINT", "DOUBLE") or str(t).startswith("DECIMAL") for t in rel.types]
    rows = [["NULL" if v is None else str(v) for v in r] for r in rel.fetchall()]
    width = [max(len(x) for x in [c, t] + [r[i] for r in rows]) for i, (c, t) in enumerate(zip(cols, types))]
    cell = lambda x, i: x.rjust(width[i]) if numeric[i] else x.ljust(width[i])
    line = lambda cells: "│ " + " │ ".join(cells) + " │"
    out = ["┌" + "┬".join("─" * (w + 2) for w in width) + "┐",
           line([c.center(w) for c, w in zip(cols, width)]), line([t.center(w) for t, w in zip(types, width)]),
           "├" + "┼".join("─" * (w + 2) for w in width) + "┤"]
    out += [line([cell(x, i) for i, x in enumerate(r)]) for r in rows]
    out.append("└" + "┴".join("─" * (w + 2) for w in width) + "┘")
    if len(rows) > 10:
        out.append(f"  {len(rows)} rows")
    return "\n".join(out) + "\n"


def build_prompt(case):
    text = (case / "prompt.md").read_text()
    header, _, rest = text.partition("\n## Included\n")
    included, _, queries_section = rest.partition("\n## Queries\n")
    seeds_shown = re.search(r"Seeds:\s*shown", included) is not None
    models_only = re.search(r"Log:\s*models only", included) is not None
    queries = re.findall(r"```sql\n(.*?)\n```", queries_section, re.S)
    clean_build()
    with injected(case) as (_, _, log):  # project files are read in the injected state: a case may edit them
        if models_only:
            ok, summary, log = dbt("build", "--exclude", "resource_type:seed")
            if not ok:
                raise CaseError(f"models-only build failed after injection: {summary}")
        files = [SHOP / "dbt_project.yml"] + sorted(p for p in SHOP.glob("models/**/*") if p.suffix in (".sql", ".yml"))
        if seeds_shown:
            files += sorted(SHOP.glob("seeds/*.csv"))
        body = "".join(f"\n===== jaffle_shop/{p.relative_to(SHOP)} =====\n{p.read_text().rstrip()}\n" for p in files)
        con = connect()
        sample = "\n".join(f"```sql\n{q}\n```\n```\n{render(con.sql(q))}\n```\n" for q in queries)
    log = "\n".join(l.rstrip() for l in log.splitlines())
    return header.rstrip("\n") + "\n" + body + f"\n===== dbt build log =====\n{log}\n\n===== output sample =====\n{sample}"


def project_version():
    rev = sh("git", "rev-parse", "--short", "HEAD").stdout.strip()
    return rev + ("+dirty" if sh("git", "status", "--porcelain", "--", "jaffle_shop").stdout.strip() else "")


def run_models(case, models, times):
    """Assemble the prompt once, run every model `times` times concurrently, save each under runs/."""
    prompt = build_prompt(case)
    version = project_version()
    digest = hashlib.sha256(prompt.encode()).hexdigest()[:12]

    def one(model):
        cmd = ["claude", "-p", "--setting-sources", "", "--model", model, "--tools", "", "--output-format", "json"]
        t0 = time.time()
        try:
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=tempfile.gettempdir(), timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            return model, None, f"claude failed: no answer after {TIMEOUT} s"
        try:
            d = json.loads(r.stdout)
        except json.JSONDecodeError:
            return model, None, f"claude failed: {r.stderr.strip()[-800:] or r.stdout[-800:]}"
        if d.get("is_error"):
            return model, None, f"claude failed: {d.get('result')}"
        shown = " ".join(f'"{a}"' if a == "" else a for a in cmd)
        head = (f"{next(iter(d.get('modelUsage', {})), model)} | {shown} | {time.time() - t0:.0f} s | "
                f"USD {d.get('total_cost_usd', 0):.3f} | {date.today()} | project {version} | prompt {digest}")
        return model, head, d["result"].rstrip()

    jobs = [m for m in models for _ in range(times)]
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        results = list(pool.map(one, jobs))
    runs = case / "runs"
    runs.mkdir(exist_ok=True)
    failed = 0
    for model, head, body in results:
        if head is None:
            print(f"{case.name} {model}: {body}")
            failed += 1
            continue
        out = runs / f"{model}-{1 + len(list(runs.glob(f'{model}-*.md')))}.md"
        out.write_text(f"{head}\n\n{body}\n")
        print(f"saved {out.relative_to(ROOT)}  ({head.split(' | ')[2]}, {head.split(' | ')[3]})")
    return failed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="*")
    ap.add_argument("--prompt", action="store_true", help="print the assembled prompt instead of checking")
    ap.add_argument("--run", metavar="MODEL", action="append", help="run MODEL on the assembled prompt, save under runs/ (repeatable)")
    ap.add_argument("--times", type=int, default=1, help="runs per model (default 1)")
    a = ap.parse_args()
    cases = [ROOT / c for c in a.cases] or sorted(p for p in (ROOT / "cases").iterdir() if (p / "inject.py").exists())
    if a.prompt or a.run:
        failed = 0
        for case in cases:
            if a.prompt:
                print(build_prompt(case))
            else:
                failed += run_models(case, a.run, a.times)
        sys.exit(1 if failed else 0)
    failed = 0
    for case in cases:
        reason, lines = check(case)
        failed += reason is not None
        print(f"{case.name}: {'FAIL, ' + reason if reason else 'PASS'}")
        print(*[f"  {l}" for l in lines], sep="\n")
    sys.exit(1 if failed else 0)
