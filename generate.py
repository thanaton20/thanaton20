"""Draw the profile header: a treemap of the code in the projects listed in projects.toml.

Each block is a project, each cell inside it a language, and areas follow the
byte counts GitHub reports. Only the standard library is used.

    python generate.py            # writes assets/map-dark.svg and assets/map-light.svg
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tomllib
import urllib.request
from dataclasses import dataclass
from xml.sax.saxutils import escape

ROOT = pathlib.Path(__file__).resolve().parent
API = "https://api.github.com"

WIDTH, HEIGHT = 880, 420
HEADER = 44
FONT = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"

THEMES = {
    "dark": dict(
        bg="#0d1117", frame="#30363d", block="#161b22", text="#e6edf3", dim="#8b949e",
        soften=0.22, on_light="#0d1117", on_dark="#ffffff",
    ),
    "light": dict(
        bg="#ffffff", frame="#d0d7de", block="#f6f8fa", text="#1f2328", dim="#656d76",
        soften=0.12, on_light="#1f2328", on_dark="#ffffff",
    ),
}

# GitHub's own language colours, so the map reads like the language bar on a repository page.
LANGUAGE_COLORS = {
    "Python": "#3572a5", "Rust": "#dea584", "TypeScript": "#3178c6", "Svelte": "#ff3e00",
    "JavaScript": "#f1e05a", "CSS": "#663399", "HTML": "#e34c26", "Java": "#b07219",
    "C#": "#178600", "C": "#555555", "C++": "#f34b7d", "Go": "#00add8", "Shell": "#89e051",
    "Kotlin": "#a97bff", "Swift": "#f05138", "Makefile": "#427819", "Jupyter Notebook": "#da5b0b",
}
FALLBACK_COLOR = "#6e7681"
# Used when a cell is too narrow for the full name.
SHORT_NAMES = {
    "JavaScript": "JS", "TypeScript": "TS", "Python": "Py", "Jupyter Notebook": "Jupyter",
    "Makefile": "Make",
}


@dataclass(frozen=True)
class Project:
    repo: str
    label: str
    note: str = ""


@dataclass(frozen=True)
class Config:
    title: str
    projects: list[Project]


@dataclass(frozen=True)
class ProjectData:
    project: Project
    langs: dict[str, int]
    pushed_at: str


def load_config(path: pathlib.Path) -> Config:
    with open(path, "rb") as handle:
        raw = tomllib.load(handle)
    projects = [
        Project(item["repo"], item["label"], item.get("note", ""))
        for item in raw.get("project", [])
    ]
    return Config(raw["title"], projects)


def squarify(weights, x, y, w, h):
    """Squarified treemap (Bruls, Huizing, van Wijk). Rectangles come back in input order."""
    total = sum(weights)
    if not weights or total <= 0:
        return []
    order = sorted(range(len(weights)), key=lambda i: -weights[i])
    areas = [weights[i] * w * h / total for i in order]
    rects = [None] * len(weights)

    def worst(row, side):
        s = sum(row)
        return max(max(row) * side * side / (s * s), s * s / (min(row) * side * side))

    pos = 0
    while pos < len(areas):
        side = min(w, h)
        row = [areas[pos]]
        end = pos + 1
        while end < len(areas) and worst(row + [areas[end]], side) <= worst(row, side):
            row.append(areas[end])
            end += 1
        last = end == len(areas)
        s = sum(row)
        if w >= h:
            # The last row takes whatever is left, so rounding never leaves a sliver.
            col = w if last else s / h
            cy = y
            for k, area in enumerate(row):
                ch = area / col
                rects[order[pos + k]] = (x, cy, col, ch)
                cy += ch
            x, w = x + col, w - col
        else:
            band = h if last else s / w
            cx = x
            for k, area in enumerate(row):
                cw = area / band
                rects[order[pos + k]] = (cx, y, cw, band)
                cx += cw
            y, h = y + band, h - band
        pos = end
    return rects


def fold_small(langs: dict[str, int], min_share: float = 0.03) -> list[tuple[str, int]]:
    """Languages by size; those under min_share of the project are merged into "Other"."""
    total = sum(langs.values())
    items = sorted(((n, b) for n, b in langs.items() if b > 0), key=lambda item: (-item[1], item[0]))
    kept = [(n, b) for n, b in items if b >= total * min_share]
    rest = sum(b for _, b in items) - sum(b for _, b in kept)
    if rest:
        kept.append(("Other", rest))
    return kept


def human_size(n: int) -> str:
    if n < 1000:
        return f"{n} B"
    if round(n / 1000) < 1000:
        return f"{n / 1000:.0f} KB"
    return f"{n / 1_000_000:.1f} MB"


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _mix(color: str, other: str, share: float) -> str:
    return "#" + "".join(
        f"{round(a + (b - a) * share):02x}" for a, b in zip(_rgb(color), _rgb(other))
    )


def _luminance(color: str) -> float:
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ink(fill: str, dark: str, light: str) -> str:
    """Whichever of the two text colours has the higher contrast against the fill."""
    def contrast(a, b):
        hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)

    return dark if contrast(fill, dark) >= contrast(fill, light) else light


def _n(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _fit(text: str, room: float, char: float) -> str:
    """The text, shortened with an ellipsis, or "" when not even three characters fit."""
    if len(text) * char <= room:
        return text
    count = int(room / char)
    return text[:count - 1].rstrip() + "…" if count >= 3 else ""


def render(title: str, projects: list[ProjectData], theme: str) -> str:
    if theme not in THEMES:
        raise ValueError(f"unknown theme: {theme}")
    t = THEMES[theme]
    drawn = [p for p in projects if sum(p.langs.values()) > 0]
    if not drawn:
        raise ValueError("no project has any code to draw")

    total = sum(sum(p.langs.values()) for p in drawn)
    updated = max(p.pushed_at for p in drawn)[:10]
    count = f"{len(drawn)} project" + ("" if len(drawn) == 1 else "s")
    summary = f"{count} · {human_size(total)} · updated {updated}"
    described = ", ".join(p.project.label for p in drawn)

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'width="{WIDTH}" height="{HEIGHT}" role="img" font-family="{FONT}" '
        f'aria-label="{escape(f"Code map of {title}: {described}", {chr(34): "&quot;"})}">',
        "<style>",
        ".c{animation:in .7s cubic-bezier(.2,.7,.2,1) both;transform-box:fill-box;transform-origin:center}",
        "@keyframes in{from{opacity:0;transform:scale(.86)}to{opacity:1;transform:none}}",
        "@media (prefers-reduced-motion:reduce){.c{animation:none}}",
        "</style>",
        "<defs>",
        '<linearGradient id="cushion" x1="0" y1="0" x2="1" y2="1">',
        '<stop offset="0" stop-color="#fff" stop-opacity=".2"/>',
        '<stop offset=".5" stop-color="#fff" stop-opacity="0"/>',
        '<stop offset="1" stop-color="#000" stop-opacity=".2"/>',
        "</linearGradient>",
        "</defs>",
        f'<rect x=".5" y=".5" width="{WIDTH - 1}" height="{HEIGHT - 1}" rx="10" '
        f'fill="{t["bg"]}" stroke="{t["frame"]}"/>',
        f'<text x="20" y="28" font-size="14" fill="{t["dim"]}">'
        f'<tspan font-weight="700" fill="{t["text"]}">{escape(title)}</tspan> / code map</text>',
        f'<text x="{WIDTH - 20}" y="28" font-size="12" text-anchor="end" fill="{t["dim"]}">'
        f"{escape(summary)}</text>",
    ]

    blocks = squarify(
        [sum(p.langs.values()) for p in drawn], 12, HEADER, WIDTH - 24, HEIGHT - HEADER - 12
    )
    step = 0
    for data, (bx, by, bw, bh) in zip(drawn, blocks):
        bx, by, bw, bh = bx + 4, by + 4, bw - 8, bh - 8
        captioned = bh >= 70 and bw >= 60
        out.append(f'<g class="c" style="animation-delay:{step * 60}ms">')
        step += 1
        out.append(
            f'<rect x="{_n(bx)}" y="{_n(by)}" width="{_n(bw)}" height="{_n(bh)}" rx="8" '
            f'fill="{t["block"]}" stroke="{t["frame"]}"/>'
        )
        # A block too short for a caption line keeps its name beside the cells instead.
        beside = "" if captioned or bh < 24 else _fit(data.project.label, bw * 0.6 - 20, 8.4)
        if captioned:
            out.extend(_caption(data, t, bx, by, bw))
        elif beside:
            out.append(
                f'<text x="{_n(bx + 10)}" y="{_n(by + bh / 2 + 5)}" font-size="14" font-weight="700" '
                f'fill="{t["text"]}">{escape(beside)}</text>'
            )
        out.append("</g>")

        top = 30 if captioned else 6
        left = 20 + len(beside) * 8.4 if beside else 6
        langs = fold_small(data.langs)
        size = sum(b for _, b in langs)
        cells = squarify([b for _, b in langs], bx + left, by + top, bw - left - 6, bh - top - 6)
        for (name, nbytes), (cx, cy, cw, ch) in zip(langs, cells):
            cx, cy, cw, ch = cx + 1.5, cy + 1.5, cw - 3, ch - 3
            if cw <= 0 or ch <= 0:
                continue
            fill = _mix(LANGUAGE_COLORS.get(name, FALLBACK_COLOR), t["bg"], t["soften"])
            colour = ink(fill, t["on_light"], t["on_dark"])
            box = f'x="{_n(cx)}" y="{_n(cy)}" width="{_n(cw)}" height="{_n(ch)}" rx="4"'
            out.append(f'<g class="c" style="animation-delay:{step * 60}ms">')
            step += 1
            out.append(f'<rect {box} fill="{fill}"/>')
            out.append(f'<rect {box} fill="url(#cushion)"/>')
            shown = next(
                (s for s in (name, SHORT_NAMES.get(name)) if s and cw >= len(s) * 7.8 + 20), ""
            )
            if shown and ch >= 30:
                out.append(
                    f'<text x="{_n(cx + 10)}" y="{_n(cy + 21)}" font-size="13" font-weight="700" '
                    f'fill="{colour}">{escape(shown)}</text>'
                )
                details = (
                    f"{human_size(nbytes)} · {round(100 * nbytes / size)}%",
                    f"{round(100 * nbytes / size)}%",
                )
                detail = next((d for d in details if cw >= len(d) * 6.6 + 20), "")
                if detail and ch >= 48:
                    out.append(
                        f'<text x="{_n(cx + 10)}" y="{_n(cy + 37)}" font-size="11" '
                        f'fill="{colour}" fill-opacity=".8">{escape(detail)}</text>'
                    )
            elif 16 <= ch < 30:
                # A flat cell has room for one small line.
                small = next(
                    (s for s in (name, SHORT_NAMES.get(name)) if s and cw >= len(s) * 6.6 + 12), ""
                )
                if small:
                    out.append(
                        f'<text x="{_n(cx + 6)}" y="{_n(cy + ch / 2 + 4)}" font-size="11" '
                        f'font-weight="700" fill="{colour}">{escape(small)}</text>'
                    )
            out.append("</g>")

    out.append("</svg>")
    return "\n".join(out) + "\n"


def _caption(data: ProjectData, t: dict, bx: float, by: float, bw: float) -> list[str]:
    """The line above a project's cells: label, then the note and the size if there is room."""
    room = bw - 20
    label = _fit(data.project.label, room, 8.4)
    if not label:
        return []
    lines = [
        f'<text x="{_n(bx + 10)}" y="{_n(by + 20)}" font-size="14" font-weight="700" '
        f'fill="{t["text"]}">{escape(label)}</text>'
    ]
    if label != data.project.label:
        return lines
    used = len(label) * 8.4
    size = human_size(sum(data.langs.values()))
    if used + 12 + len(size) * 7.2 > room:
        return lines
    lines.append(
        f'<text x="{_n(bx + bw - 10)}" y="{_n(by + 20)}" font-size="12" text-anchor="end" '
        f'fill="{t["dim"]}">{escape(size)}</text>'
    )
    note = data.project.note
    if note and used + 10 + len(note) * 7.2 + 12 + len(size) * 7.2 <= room:
        lines.append(
            f'<text x="{_n(bx + 20 + used)}" y="{_n(by + 20)}" font-size="12" '
            f'fill="{t["dim"]}">{escape(note)}</text>'
        )
    return lines


def fetch(repo: str, token: str | None, opener=urllib.request.urlopen) -> tuple[dict[str, int], str]:
    """Language byte counts and the last push time of a repository."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "profile-code-map"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def get(path):
        request = urllib.request.Request(f"{API}/repos/{repo}{path}", headers=headers)
        with opener(request, timeout=20) as reply:
            return json.loads(reply.read())

    return get("/languages"), get("")["pushed_at"]


def main(argv=None, fetcher=fetch) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=pathlib.Path, default=ROOT / "projects.toml")
    parser.add_argument("--out", type=pathlib.Path, default=ROOT / "assets")
    args = parser.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    try:
        config = load_config(args.config)
        projects = [ProjectData(p, *fetcher(p.repo, token)) for p in config.projects]
        # Render everything before writing anything, so a failure never leaves half a result.
        images = {theme: render(config.title, projects, theme) for theme in THEMES}
    except Exception as error:
        print(f"generate: {error}", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    for theme, svg in images.items():
        path = args.out / f"map-{theme}.svg"
        if not path.exists() or path.read_text(encoding="utf-8") != svg:
            path.write_text(svg, encoding="utf-8")
            print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
