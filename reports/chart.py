"""Success rate by failure type and model, from results.csv, as reports/success_by_type.svg.

A run succeeds when both root_cause and fix are "yes". Run from the repo root: python3 reports/chart.py
"""
import csv
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parents[1]
TYPES = ["duplicate rows", "join fan-out", "schema drift", "null flood", "timezone shift", "late data",
         "unit change", "category change", "mixed"]
MODELS = [("sonnet", "Sonnet", "#2a78d6"), ("haiku", "Haiku", "#eb6834")]  # validated categorical slots 1 and 2

runs = defaultdict(list)  # (type, model) -> [success bool]
for r in csv.DictReader((ROOT / "results.csv").open()):
    runs[(r["type"], r["model"])].append(r["root_cause"] == "yes" and r["fix"] == "yes")
types = [t for t in TYPES if any((t, m) in runs for m, *_ in MODELS)]

W, H, L, R, T, B = 880, 360, 56, 16, 44, 64
plot_w, plot_h = W - L - R, H - T - B
group = plot_w / max(len(types), 1)
bar, gap = 22, 2
svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="-apple-system, Segoe UI, Helvetica, Arial, sans-serif" font-size="12">',
       '<style>svg{background:#fcfcfb}.ink{fill:#0b0b0b}.muted{fill:#52514e}.grid{stroke:#e5e4df}.axis{stroke:#b5b4ad}'
       '@media (prefers-color-scheme: dark){svg{background:#1a1a19}.ink{fill:#f0efec}.muted{fill:#c3c2b7}.grid{stroke:#34332f}.axis{stroke:#6b6a63}}</style>',
       f'<text x="{L}" y="20" class="ink" font-size="14" font-weight="600">Silent failures found and fixed, by failure type and model</text>',
       f'<text x="{L}" y="36" class="muted">Share of runs where the model named the root cause and its fix made the output correct</text>']
for pct in (0, 50, 100):
    y = T + plot_h - plot_h * pct / 100
    svg.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="{"axis" if pct == 0 else "grid"}" stroke-width="1"/>')
    svg.append(f'<text x="{L - 6}" y="{y + 4:.1f}" class="muted" text-anchor="end">{pct}%</text>')
for gi, t in enumerate(types):
    x0 = L + group * gi + group / 2 - (bar * len(MODELS) + gap * (len(MODELS) - 1)) / 2
    for mi, (m, label, color) in enumerate(MODELS):
        rs = runs.get((t, m), [])
        if not rs:
            continue
        rate = sum(rs) / len(rs)
        x = x0 + mi * (bar + gap)
        h = plot_h * rate
        y = T + plot_h - h
        r = min(4, h / 2)
        path = (f'M{x:.1f},{T + plot_h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} H{x + bar - r:.1f} '
                f'Q{x + bar:.1f},{y:.1f} {x + bar:.1f},{y + r:.1f} V{T + plot_h:.1f} Z')
        svg.append(f'<path d="{path}" fill="{color}"><title>{label}, {t}: {sum(rs)} of {len(rs)} runs</title></path>')
        if rate < 1:  # label the exceptions only
            svg.append(f'<text x="{x + bar / 2:.1f}" y="{y - 5:.1f}" class="ink" text-anchor="middle">{sum(rs)}/{len(rs)}</text>')
    svg.append(f'<text x="{L + group * gi + group / 2:.1f}" y="{T + plot_h + 18}" class="ink" text-anchor="middle">{t}</text>')
lx = L
for m, label, color in MODELS:
    svg.append(f'<rect x="{lx}" y="{H - 22}" width="12" height="12" rx="2" fill="{color}"/>')
    svg.append(f'<text x="{lx + 17}" y="{H - 12}" class="ink">{label}</text>')
    lx += 17 + 8 * len(label) + 20
n = sum(len(v) for v in runs.values())
svg.append(f'<text x="{W - R}" y="{H - 12}" class="muted" text-anchor="end">{n} runs, results.csv</text></svg>')
(ROOT / "reports/success_by_type.svg").write_text("\n".join(svg) + "\n")
print(f"wrote reports/success_by_type.svg from {n} runs, {len(types)} types")
