"""Check that a case is a silent failure: the clean project builds with every
test passing, inject.py changes at least one model's output, and `dbt build`
still passes after the injection.

Run from the repo root:
    uv run --project jaffle_shop check_case.py [cases/<name> ...]
No argument checks every folder under cases/ that has an inject.py. Exit 1 if
any case fails. jaffle_shop/ is copied aside before the injection and put back
after it, so uncommitted project changes survive the run. After the injection
the seeds are reloaded in full (a source table with a new column must land) and
the models run incrementally, like a nightly build after a source change.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import duckdb

ROOT = Path(__file__).parent
SHOP = ROOT / "jaffle_shop"
KEEP = {".venv", "target", "logs", ".user.yml"}  # build artifacts, not part of the project


def sh(*cmd, cwd=ROOT):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def dbt(*args):
    r = sh("uv", "run", "dbt", *args, "--no-use-colors", cwd=SHOP)
    done = [l.split("Done. ")[-1] for l in r.stdout.splitlines() if "Done. " in l]
    return r.returncode == 0, done[-1] if done else r.stdout[-800:]


def snapshot():
    con = duckdb.connect(str(SHOP / "jaffle_shop.duckdb"), read_only=True)
    names = [n for (n,) in con.sql(
        "select table_name from information_schema.tables where table_schema = 'main'").fetchall()]
    return {n: Counter(con.sql(f'select * from "{n}" order by all').fetchall()) for n in names}


def inject_and_build(case):
    """Run inject.py then dbt build with jaffle_shop/ restored afterwards."""
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(SHOP, f"{tmp}/shop", ignore=shutil.ignore_patterns(*KEEP, "*.duckdb"))
        try:
            inj = sh(sys.executable, str(case / "inject.py"))
            if inj.returncode:
                return f"inject.py failed: {inj.stderr.strip()[-800:]}", None, None
            ok, summary = dbt("seed", "--full-refresh")
            if not ok:
                return f"seeds do not load after injection: {summary}", None, None
            ok, summary = dbt("build")
            if not ok:
                return f"not silent, dbt build failed after injection: {summary}", None, None
            return None, inj.stdout.strip(), summary
        finally:
            for p in SHOP.iterdir():
                if p.name not in KEEP and p.suffix != ".duckdb":
                    shutil.rmtree(p) if p.is_dir() else p.unlink()
            shutil.copytree(f"{tmp}/shop", SHOP, dirs_exist_ok=True)


def check(case):
    """Return (reason or None, summary lines)."""
    ok, clean_summary = dbt("build", "--full-refresh")
    if not ok:
        return f"clean build failed: {clean_summary}", []
    clean = snapshot()

    reason, injected, broken_summary = inject_and_build(case)
    if reason:
        return reason, []
    broken = snapshot()
    manifest = json.loads((SHOP / "target/manifest.json").read_text())
    seeds = {n["name"] for n in manifest["nodes"].values() if n["resource_type"] == "seed"}

    lines = [f"clean:    {clean_summary}", f"injected: {broken_summary}", f"inject.py: {injected}"]
    changed_models = []
    for name in sorted(clean.keys() | broken.keys()):
        added, removed = broken.get(name, Counter()) - clean.get(name, Counter()), \
                         clean.get(name, Counter()) - broken.get(name, Counter())
        if not added and not removed:
            continue
        if name not in seeds:
            changed_models.append(name)
        lines.append(f"{name}: +{sum(added.values())} -{sum(removed.values())} rows")
        lines += [f"    + {r}" for r in list(added)[:3]] + [f"    - {r}" for r in list(removed)[:3]]
    if not changed_models:
        return "no model output changed after injection", lines
    return None, lines


if __name__ == "__main__":
    cases = [ROOT / a for a in sys.argv[1:]] or sorted(
        p for p in (ROOT / "cases").iterdir() if (p / "inject.py").exists())
    failed = 0
    for case in cases:
        reason, lines = check(case)
        failed += reason is not None
        print(f"{case.name}: {'FAIL, ' + reason if reason else 'PASS'}")
        print(*[f"  {l}" for l in lines], sep="\n")
    sys.exit(1 if failed else 0)
