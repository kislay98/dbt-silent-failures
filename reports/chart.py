"""Root cause found and complete fix by failure type and model, from results.csv, as reports/success_by_type.svg.

Four panels on one scale: the share of runs that named the change that broke the data and the share whose fix made
every affected value right (strict), each on the standard output sample (week 8 on, the same queries for every case)
and on the curated per-case queries of weeks 1 to 7. Run from the repo root: python3 reports/chart.py
"""
import csv
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parents[1]
TYPES = ["duplicate rows", "join fan-out", "schema drift", "null flood", "timezone shift", "late data",
         "unit change", "category change", "mixed"]
MODELS = [("sonnet", "Sonnet", "#2a78d6"), ("haiku", "Haiku", "#eb6834"), ("opus", "Opus", "#1baf7a")]  # validated categorical slots 1 to 3
QUESTIONS = [("root_cause", "Root cause found", "the run names the change that broke the data"),
             ("fix", "Complete fix", "the fix makes every affected value right, strict")]
SAMPLES = [("standard", "standard sample, the same queries on every case"),
           ("curated", "curated sample, queries written per case, weeks 1 to 7")]

runs = defaultdict(list)  # (samples, type, model) -> rows
for r in csv.DictReader((ROOT / "results.csv").open()):
    if r.get("headline", "yes") == "yes":  # a case superseded by a v2 keeps its rows, out of the headline
        runs[(r["samples"], r["type"], r["model"])].append(r)
types = [t for t in TYPES if any(k[1] == t for k in runs)]
panels = [(s, sl, q, t, sub) for q, t, sub in QUESTIONS for s, sl in SAMPLES if any(k[0] == s for k in runs)]
n = sum(len(v) for v in runs.values())

W, L, R, TOP, PB, PLOT = 880, 56, 16, 48, 270, 190
H = TOP + PB * len(panels) + 30
plot_w = W - L - R
group = plot_w / max(len(types), 1)
bar, gap = 22, 2
svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="-apple-system, Segoe UI, Helvetica, Arial, sans-serif" font-size="12">',
       '<style>svg{background:#fcfcfb}.ink{fill:#0b0b0b}.muted{fill:#52514e}.grid{stroke:#e5e4df}.axis{stroke:#b5b4ad}'
       '@media (prefers-color-scheme: dark){svg{background:#1a1a19}.ink{fill:#f0efec}.muted{fill:#c3c2b7}.grid{stroke:#34332f}.axis{stroke:#6b6a63}}</style>',
       f'<text x="{L}" y="20" class="ink" font-size="14" font-weight="600">Silent failures by failure type and model</text>',
       f'<text x="{L}" y="36" class="muted">Share of runs, three per model per case, scored yes or no on two questions, on two output samples</text>']
for pi, (smp, slabel, col, title, sub) in enumerate(panels):
    T = TOP + pi * PB + 28
    svg.append(f'<text x="{L}" y="{T - 12}"><tspan class="ink" font-weight="600">{title}, {slabel.split(",")[0]}</tspan>'
               f'<tspan class="muted">: {sub}; {slabel.partition(", ")[2]}</tspan></text>')
    for pct in (0, 50, 100):
        y = T + PLOT - PLOT * pct / 100
        svg.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="{"axis" if pct == 0 else "grid"}" stroke-width="1"/>')
        svg.append(f'<text x="{L - 6}" y="{y + 4:.1f}" class="muted" text-anchor="end">{pct}%</text>')
    for gi, t in enumerate(types):
        x0 = L + group * gi + group / 2 - (bar * len(MODELS) + gap * (len(MODELS) - 1)) / 2
        for mi, (m, label, color) in enumerate(MODELS):
            rs = runs.get((smp, t, m), [])
            if not rs:
                continue
            hits = sum(r[col] == "yes" for r in rs)
            rate = hits / len(rs)
            x = x0 + mi * (bar + gap)
            h = PLOT * rate
            y = T + PLOT - h
            r = min(4, h / 2)
            path = (f'M{x:.1f},{T + PLOT:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} H{x + bar - r:.1f} '
                    f'Q{x + bar:.1f},{y:.1f} {x + bar:.1f},{y + r:.1f} V{T + PLOT:.1f} Z')
            svg.append(f'<path d="{path}" fill="{color}"><title>{label}, {t}, {title.lower()}, {smp} sample: {hits} of {len(rs)} runs</title></path>')
            if rate < 1:  # label the exceptions only
                svg.append(f'<text x="{x + bar / 2:.1f}" y="{y - 5:.1f}" class="ink" text-anchor="middle">{hits}/{len(rs)}</text>')
        svg.append(f'<text x="{L + group * gi + group / 2:.1f}" y="{T + PLOT + 18}" class="ink" text-anchor="middle">{t}</text>')
lx = L
for m, label, color in MODELS:
    svg.append(f'<rect x="{lx}" y="{H - 22}" width="12" height="12" rx="2" fill="{color}"/>')
    svg.append(f'<text x="{lx + 17}" y="{H - 12}" class="ink">{label}</text>')
    lx += 17 + 8 * len(label) + 20
svg.append(f'<text x="{W - R}" y="{H - 12}" class="muted" text-anchor="end">{n} runs, results.csv</text></svg>')
(ROOT / "reports/success_by_type.svg").write_text("\n".join(svg) + "\n")
print(f"wrote reports/success_by_type.svg from {n} runs, {len(types)} types, {len(panels)} panels")
