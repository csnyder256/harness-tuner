"""Rendering: summary.md and report.html.

Both are built by hand from strings. There is no template engine and no
plotting library, because the engine has to run on a machine where nothing can
be installed, and because a bar drawn with a div is not worse than a bar drawn
with a charting dependency.

Unavailable metrics are rendered as prominently as measured ones. A report that
quietly omitted what it could not see would read as a complete picture of the
harness, which is the specific way an evaluation tool misleads.
"""

from __future__ import annotations

import html
from typing import Any

from ..protocol.metrics import LOWER_IS_BETTER, is_measured, partial_coverage

STAR_FULL = "*"
STAR_EMPTY = "."


def _fmt(entry: dict[str, Any]) -> str:
    if not is_measured(entry):
        return "unavailable"
    value = entry["value"]
    unit = entry.get("unit", "")
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        text = f"{value:.6g}"
    else:
        text = f"{value:,}"
    if unit in ("ratio", "count", "bool", ""):
        return text
    return f"{text} {unit}"


def _stars(dimension: dict[str, Any]) -> str:
    if dimension.get("stars") is None:
        return "insufficient evidence"
    filled = dimension["stars"]
    return STAR_FULL * filled + STAR_EMPTY * (5 - filled) + f"  ({dimension['score']:.3f})"


def summary_markdown(run: dict[str, Any], agg: dict[str, dict], fp: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# harness-tuner run {run['run_id']}")
    lines.append("")
    stamp = run["config"]
    lines.append(f"Harness: `{stamp['harness']['id'] or 'unnamed'}`  "
                 f"(adapter level {stamp['harness']['adapter_level']})")
    lines.append(f"Subject under test: {stamp['mode']['subject']}  "
                 f"| execution: {stamp['mode']['execution']}")
    lines.append(f"Harness model: `{stamp['models']['harness'] or 'not recorded'}`")
    reach = stamp["containment"]["reach"] or "not recorded"
    lines.append(f"Containment: {stamp['containment']['mechanism'] or 'none declared'}  "
                 f"| reach: {reach}")
    lines.append(f"Tasks: {run['task_count']}  | protocol: HTP-{run['htp_version']}")
    lines.append("")

    gaps = [(name, e) for name, e in agg.items() if not is_measured(e)]
    partial = partial_coverage(agg)

    lines.append("## What this run could not measure")
    lines.append("")
    if not gaps and not partial:
        lines.append(
            f"Nothing. Every registered metric was computable in all {run['task_count']} task(s)."
        )
    else:
        lines.append(
            f"{len(gaps)} of {len(agg)} metrics could not be computed at all, and "
            f"{len(partial)} more were computed from only some of the {run['task_count']} "
            "task(s). Both come first, because a report that buried them would read as a "
            "complete picture of the harness."
        )
    if gaps:
        lines.append("")
        lines.append("### Not computable")
        lines.append("")
        lines.append("| Metric | Why not |")
        lines.append("|---|---|")
        for name, entry in gaps:
            lines.append(f"| `{name}` | {entry['reason']} |")
    if partial:
        lines.append("")
        lines.append("### Computed from part of the task set")
        lines.append("")
        lines.append(
            "Read these as a statement about the tasks that reported them, not about the run. "
            "A cost totalled over three of ten tasks is not the run's cost."
        )
        lines.append("")
        lines.append("| Metric | Value | Coverage | How combined |")
        lines.append("|---|---|---|---|")
        for name, entry in partial:
            cov = entry["coverage"]
            lines.append(
                f"| `{name}` | {_fmt(entry)} | {cov['measured']} of {cov['total']} tasks "
                f"| {cov['combiner']} |"
            )
    lines.append("")

    lines.append("## Measured")
    lines.append("")
    lines.append("| Metric | Value | Coverage | How combined | Better when |")
    lines.append("|---|---|---|---|---|")
    for name, entry in agg.items():
        if not is_measured(entry):
            continue
        direction = "lower" if name in LOWER_IS_BETTER else "higher"
        cov = entry.get("coverage") or {}
        coverage = f"{cov.get('measured', '?')} of {cov.get('total', '?')}"
        lines.append(
            f"| `{name}` | {_fmt(entry)} | {coverage} | {cov.get('combiner', '?')} | {direction} |"
        )
    lines.append("")

    lines.append("## Fingerprint")
    lines.append("")
    if fp["status"] != "measured":
        lines.append(fp["reason"])
    else:
        lines.append("| Dimension | Score | Basis |")
        lines.append("|---|---|---|")
        for name, dim in fp["dimensions"].items():
            basis = dim.get("reason") or "all components measured"
            lines.append(f"| {name.replace('_', ' ')} | `{_stars(dim)}` | {basis} |")
        lines.append("")
        lines.append(
            "Scores are a stated function of the measured values above, using the scales "
            "recorded in `fingerprint.json`. The scales are declared opinions about what "
            "good looks like, not measurements."
        )
    lines.append("")
    return "\n".join(lines)


_CSS = """
:root { color-scheme: light dark; --fg:#16181d; --bg:#ffffff; --muted:#5b6270;
        --line:#e3e6ec; --warn:#8a5300; --warnbg:#fff6e5; --ok:#1c6b3c; --card:#f7f8fa; }
@media (prefers-color-scheme: dark) {
  :root { --fg:#e6e8ee; --bg:#14161a; --muted:#98a0b0; --line:#2a2e37;
          --warn:#e8b25e; --warnbg:#2c2416; --ok:#6fd39b; --card:#1b1e24; }
}
* { box-sizing: border-box; }
body { margin:0; padding:2rem 1.25rem 4rem; background:var(--bg); color:var(--fg);
       font:15px/1.6 ui-sans-serif,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
main { max-width: 60rem; margin: 0 auto; }
h1 { font-size:1.5rem; margin:0 0 .25rem; letter-spacing:-.01em; }
h2 { font-size:1.05rem; margin:2.5rem 0 .75rem; padding-bottom:.4rem;
     border-bottom:1px solid var(--line); }
.meta { color:var(--muted); font-size:.9rem; margin:0 0 .15rem; }
.meta code { color:var(--fg); }
code { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:.88em; }
.wrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:.9rem; }
th,td { text-align:left; padding:.45rem .6rem; border-bottom:1px solid var(--line);
        vertical-align:top; }
th { font-weight:600; color:var(--muted); font-size:.8rem; text-transform:uppercase;
     letter-spacing:.04em; }
.gap { background:var(--warnbg); }
.gap td { color:var(--warn); }
.note { color:var(--muted); font-size:.88rem; margin:.75rem 0 0; }
.bar { display:inline-block; height:.5rem; border-radius:3px; background:var(--ok);
       vertical-align:middle; }
.track { display:inline-block; width:8rem; height:.5rem; border-radius:3px;
         background:var(--line); vertical-align:middle; margin-right:.5rem; }
.none { color:var(--muted); font-style:italic; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px;
        padding:1rem 1.15rem; margin:1rem 0; }
"""


def _bar(score: float) -> str:
    pct = max(0.0, min(1.0, score)) * 100
    return f'<span class="track"><span class="bar" style="width:{pct:.1f}%"></span></span>'


def report_html(run: dict[str, Any], agg: dict[str, dict], fp: dict[str, Any]) -> str:
    e = html.escape
    stamp = run["config"]
    out: list[str] = []
    out.append("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">")
    out.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    out.append(f"<title>harness-tuner run {e(run['run_id'])}</title>")
    out.append(f"<style>{_CSS}</style></head><body><main>")
    out.append(f"<h1>harness-tuner run <code>{e(run['run_id'])}</code></h1>")
    out.append(
        f'<p class="meta">Harness <code>{e(stamp["harness"]["id"] or "unnamed")}</code>, '
        f'adapter level {e(stamp["harness"]["adapter_level"])}, '
        f'{run["task_count"]} task(s), protocol HTP-{e(run["htp_version"])}.</p>'
    )
    out.append(
        f'<p class="meta">Model under test <code>{e(stamp["models"]["harness"] or "not recorded")}</code>. '
        f'Containment: {e(stamp["containment"]["mechanism"] or "none declared")}. '
        f'Reach: {e(stamp["containment"]["reach"] or "not recorded")}.</p>'
    )

    gaps = [(n, x) for n, x in agg.items() if not is_measured(x)]
    partial = partial_coverage(agg)
    out.append("<h2>What this run could not measure</h2>")
    if not gaps and not partial:
        out.append(
            f'<p class="note">Nothing. Every registered metric was computable in all '
            f'{run["task_count"]} task(s).</p>'
        )
    else:
        out.append(
            f'<p class="note">{len(gaps)} of {len(agg)} metrics could not be computed at all, '
            f"and {len(partial)} more were computed from only some of the "
            f'{run["task_count"]} task(s). Both come first because a report that buried them '
            "would read as a complete picture of the harness.</p>"
        )
    if gaps:
        out.append('<div class="wrap"><table><tr><th>Not computable</th><th>Why not</th></tr>')
        for name, entry in gaps:
            out.append(f'<tr class="gap"><td><code>{e(name)}</code></td><td>{e(entry["reason"])}</td></tr>')
        out.append("</table></div>")
    if partial:
        out.append(
            '<p class="note">Read the following as a statement about the tasks that reported '
            "them, not about the run. A cost totalled over three of ten tasks is not the "
            "run's cost.</p>"
        )
        out.append(
            '<div class="wrap"><table><tr><th>Partial</th><th>Value</th><th>Coverage</th>'
            "<th>How combined</th></tr>"
        )
        for name, entry in partial:
            cov = entry["coverage"]
            out.append(
                f'<tr class="gap"><td><code>{e(name)}</code></td><td>{e(_fmt(entry))}</td>'
                f'<td>{cov["measured"]} of {cov["total"]}</td><td>{e(cov["combiner"])}</td></tr>'
            )
        out.append("</table></div>")

    out.append("<h2>Measured</h2>")
    out.append(
        '<div class="wrap"><table><tr><th>Metric</th><th>Value</th><th>Coverage</th>'
        "<th>How combined</th><th>Better when</th></tr>"
    )
    for name, entry in agg.items():
        if not is_measured(entry):
            continue
        direction = "lower" if name in LOWER_IS_BETTER else "higher"
        cov = entry.get("coverage") or {}
        out.append(
            f"<tr><td><code>{e(name)}</code></td><td>{e(_fmt(entry))}</td>"
            f'<td>{cov.get("measured", "?")} of {cov.get("total", "?")}</td>'
            f'<td>{e(str(cov.get("combiner", "?")))}</td><td>{direction}</td></tr>'
        )
    out.append("</table></div>")

    out.append("<h2>Fingerprint</h2>")
    if fp["status"] != "measured":
        out.append(f'<div class="card">{e(fp["reason"])}</div>')
    else:
        out.append('<div class="wrap"><table><tr><th>Dimension</th><th>Score</th><th>Basis</th></tr>')
        for name, dim in fp["dimensions"].items():
            label = e(name.replace("_", " "))
            if dim.get("score") is None:
                cell = '<span class="none">insufficient evidence</span>'
            else:
                cell = f'{_bar(dim["score"])}<code>{dim["score"]:.3f}</code>'
            basis = e(dim.get("reason") or "all components measured")
            out.append(f"<tr><td>{label}</td><td>{cell}</td><td>{basis}</td></tr>")
        out.append("</table></div>")
        out.append(
            '<p class="note">Every score is a stated function of the measured values above, '
            "using the scales recorded in <code>fingerprint.json</code>. Those scales are "
            "declared opinions about what good looks like, not measurements, and they are "
            "published so a disagreement can be about a specific number.</p>"
        )

    out.append(
        '<p class="note">Produced by harness-tuner, which never edits your harness. '
        "Findings and proposed diffs, when present, live in <code>metrics.json</code> and "
        "the run manifest.</p>"
    )
    out.append("</main></body></html>")
    return "".join(out)
