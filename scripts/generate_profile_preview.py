"""Generate the appended headings and real GitHub profile stats.

Run from any directory: python scripts/generate_profile_preview.py
Uses GITHUB_TOKEN in Actions, or public GitHub data when no token is available.
Pass --cached to redraw the last successful snapshot without network requests.
"""

import argparse
from datetime import date
from html import escape
import json
import os
from pathlib import Path

from profile_stats import fetch, summarize

OUTPUT = Path(__file__).resolve().parents[1] / "assets" / "profile-preview"
WIDTH = 620
HEADING_WIDTH = 900
CONTRIBUTIONS_TEMPLATE = Path(__file__).resolve().parent / 'templates' / 'contributions.svg'
PALETTES = {
    "light": {"ink": "#424a53", "muted": "#6e7781", "rule": "#d0d7de", "surface": "#ffffff"},
    "dark": {"ink": "#e6edf3", "muted": "#9198a1", "rule": "#30363d", "surface": "#0d1117"},
}


def label(x, y, value, size=11, tone="muted", anchor="start", bold=False):
    weight = ' font-weight="600"' if bold else ""
    return (
        f'<text x="{x}" y="{y}" class="{tone}" font-size="{size}"'
        f' text-anchor="{anchor}"{weight}>{escape(str(value))}</text>'
    )


def line(x1, y1, x2, y2):
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" class="rule"/>'


def heading(title):
    return 30, label(0, 20, title, 16, "ink", bold=True) + line(
        len(title) * 10 + 20, 15, HEADING_WIDTH, 15
    )


def contributions(stats, palette):
    """Populate the user's supplied animated SVG with this profile's numbers."""
    weekly = stats['weekly'] or [0]
    peak = max(weekly, default=0) or 1
    step = WIDTH / max(len(weekly) - 1, 1)
    points = [(i * step, 138 - count / peak * 48) for i, count in enumerate(weekly)]
    path = 'M' + 'L'.join(f'{x:.1f} {y:.1f}' for x, y in points)
    colors = (
        f".d-f{{fill:{palette['muted']}}}.d-s{{stroke:{palette['muted']}}}"
        f".e-f{{fill:{palette['ink']}}}.m-f{{fill:{palette['muted']}}}"
        f".u-s{{stroke:{palette['rule']}}}.r{{stroke:{palette['surface']}}}"
        f".w{{fill:{palette['muted']};opacity:.13}}"
        '@media(prefers-reduced-motion:reduce){'
        'animate,set{display:none}g[opacity="0"],circle[opacity="0"]{opacity:1}'
        'g[clip-path]{clip-path:none}rect[opacity="0"]{opacity:0}}'
    )
    replacements = {
        '__TOTAL__': f"{stats['total']:,}", '__ACTIVE__': str(stats['active']),
        '__BEST_WEEK__': str(max(weekly, default=0)), '__LINE__': path,
        '__AREA__': f'M0.0 138.0L' + 'L'.join(f'{x:.1f} {y:.1f}' for x, y in points) + f'L{points[-1][0]:.1f} 138.0Z',
        '__END_Y__': f'{points[-1][1]:.1f}', '__PALETTE__': colors,
        '__LOGIN__': escape(stats['login']), '__AS_OF__': stats['days'][-1]['date'],
    }
    template = CONTRIBUTIONS_TEMPLATE.read_text(encoding='utf-8')
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    return template


def streak(stats):
    parts = [line(310, 18, 310, 90)]
    for x, key, title in [(0, "current", "current streak"), (344, "longest", "longest streak")]:
        run = stats[key]
        span = f"{run['start']} – {run['end']}" if run['length'] else "no active streak"
        parts += [label(x, 49, run['length'], 34, "ink", bold=True), label(x, 71, title),
                  label(x, 90, span, 10)]
    parts.append(label(0, 112, "consecutive active days · within the last 365 days", 9))
    return 126, "\n".join(parts)


def languages(stats):
    parts = []
    for start, title, key in [(0, "BY BYTES", "bytes"), (330, "BY REPOS", "repos")]:
        parts.append(label(start, 16, title, 10))
        ranked = sorted((row for row in stats['languages'] if row[key]), key=lambda row: (-row[key], row['name']))[:5]
        total = sum(row[key] for row in stats['languages'])
        peak = max((row[key] for row in ranked), default=1)
        if not ranked:
            parts.append(label(start, 44, "no language data", 11))
        for index, row in enumerate(ranked):
            y = 44 + index * 25
            name = row['name'] if len(row['name']) <= 14 else row['name'][:13] + '…'
            shown = f"{row[key] / total:.0%}" if key == 'bytes' else str(row[key])
            parts += [
                label(start, y, name, 11, "ink"),
                f'<rect x="{start + 106}" y="{y - 8}" width="{140 * row[key] / peak:.2f}" height="6" '
                'rx="2" class="track"/>',
                label(start + 288, y, shown, anchor="end"),
            ]
    parts.append(label(0, 175, "public owned repos · forks excluded · by repos = primary language", 9))
    return 190, "\n".join(parts)


def year(stats):
    parts = [label(0, 16, "THE YEAR", 10),
             label(0, 37, f"{stats['active']} of 365 days had a contribution", 11),
             label(620, 37, "less · : + # @ more", 9, anchor="end")]
    for row in range(7):
        y = 62 + row * 13
        if row in (1, 3, 5):
            parts.append(label(0, y, {1: "mon", 3: "wed", 5: "fri"}[row], 9))
        for column, week in enumerate(stats['weeks']):
            day = next((d for d in week['days'] if (date.fromisoformat(d['date']).weekday() + 1) % 7 == row), None)
            if day is None:
                continue
            count = day['count']
            symbol = '·' if count == 0 else ':' if count <= 2 else '+' if count <= 5 else '#' if count <= 9 else '@'
            parts.append(label(34 + column * 10.8, y, symbol, 10, "muted" if count == 0 else "ink"))
    parts += [label(34, 163, "older", 9), label(606, 163, "newer", 9, anchor="end"),
              label(0, 188, f"{stats['days'][0]['date']} – {stats['days'][-1]['date']}", 9)]
    return 200, "\n".join(parts)


def svg(name, height, content, palette, width=WIDTH):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
  <title id="title">{escape(name)} — GitHub profile</title>
  <desc id="desc">GitHub profile graphic. Statistics are generated from GitHub data and refreshed daily.</desc>
  <style>
    text {{ font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace; }}
    .ink {{ fill: {palette['ink']}; }}
    .muted {{ fill: {palette['muted']}; }}
    .rule {{ stroke: {palette['rule']}; stroke-width: 1; }}
    .track {{ fill: {palette['rule']}; }}
    .plot {{ stroke: {palette['muted']}; stroke-dasharray: 4000; }}
    @media (prefers-reduced-motion: reduce) {{
      .plot {{ stroke-dasharray: none; }}
    }}
  </style>
  {content}
</svg>
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cached', action='store_true', help='Use the last fetched snapshot without network access')
    parser.add_argument('--login', default=os.environ.get('GH_LOGIN', '51-Shenn'))
    args = parser.parse_args()
    snapshot = OUTPUT / 'profile-stats.json'
    data = json.loads(snapshot.read_text(encoding='utf-8')) if args.cached else fetch(args.login)
    stats = summarize(data)
    headings = {
        'about': 'About Me',
        'stack': 'Tech Stack',
        'projects': 'Featured Projects',
        'stats': 'GitHub Stats',
    }
    drawings = {f'heading-{name}': heading(title) for name, title in headings.items()}
    drawings.update(streak=streak(stats), languages=languages(stats), year=year(stats))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for theme, palette in PALETTES.items():
        path = OUTPUT / f'contributions-{theme}.svg'
        content = contributions(stats, palette)
        if not path.exists() or path.read_text(encoding='utf-8') != content:
            path.write_text(content, encoding='utf-8')
        for name, (height, content) in drawings.items():
            path = OUTPUT / f"{name}-{theme}.svg"
            width = HEADING_WIDTH if name.startswith('heading-') else WIDTH
            content = svg(name, height, content, palette, width=width)
            if not path.exists() or path.read_text(encoding='utf-8') != content:
                path.write_text(content, encoding="utf-8")
    snapshot.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    print(f"{stats['total']} contributions, {stats['active']} active days, current streak {stats['current']['length']}, longest streak {stats['longest']['length']}")


if __name__ == "__main__":
    main()
