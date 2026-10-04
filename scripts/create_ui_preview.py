"""Create a deterministic preview image for environments without an X display.

The actual application uses Tkinter; this preview documents the intended
three-column layout and is included as a QA artifact, not as a substitute for
the executable UI.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "paper2lab_ui_preview.png"


def font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def text(draw: ImageDraw.ImageDraw, xy, value: str, size: int = 16, fill="#d7e1ee", bold: bool = False):
    draw.text(xy, value, font=font(size, bold), fill=fill)


def panel(draw, box, title, fill="#111d2d"):
    draw.rounded_rectangle(box, radius=12, fill=fill, outline="#29415c", width=2)
    x, y, _, _ = box
    text(draw, (x + 18, y + 14), title, 18, "#f5f8fb", True)


def main() -> None:
    width, height = 1600, 1000
    image = Image.new("RGB", (width, height), "#08111e")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 72), fill="#10243a")
    text(draw, (30, 18), "Paper2Lab", 30, "#ffffff", True)
    text(draw, (238, 25), "Paper Reproduction Experiment Factory  ·  Paper → Experiment Workbench", 18, "#9fc2e8")
    draw.rounded_rectangle((1250, 18, 1560, 54), radius=15, fill="#164e63")
    text(draw, (1275, 27), "●  Local engine · Offline", 16, "#b7f7df", True)

    margin, gap, top = 24, 18, 92
    left_w, right_w = 300, 350
    center_w = width - margin * 2 - gap * 2 - left_w - right_w
    left = (margin, top, margin + left_w, 730)
    center = (margin + left_w + gap, top, margin + left_w + gap + center_w, 730)
    right = (margin + left_w + gap + center_w + gap, top, width - margin, 730)
    panel(draw, left, "Paper Library")
    panel(draw, center, "Reader / Experiment")
    panel(draw, right, "Claims / Notes / Metadata")

    # Left library list
    draw.rounded_rectangle((left[0] + 16, left[1] + 54, left[2] - 16, left[1] + 94), radius=7, fill="#0b1726", outline="#34516d")
    text(draw, (left[0] + 30, left[1] + 65), "Search papers…", 14, "#6f8ba7")
    papers = [
        ("EchoRAG: A Lightweight…", "SAMPLE · 2026", True),
        ("Memory Agents: A Survey", "imported · 2025", False),
        ("RAG Evaluation Protocol", "imported · 2024", False),
    ]
    y = left[1] + 112
    for title, sub, selected in papers:
        fill = "#183957" if selected else "#0d1928"
        draw.rounded_rectangle((left[0] + 14, y, left[2] - 14, y + 68), radius=8, fill=fill, outline="#2a5577" if selected else "#1a2b40")
        text(draw, (left[0] + 28, y + 12), title, 14, "#ffffff" if selected else "#cad8e6", selected)
        text(draw, (left[0] + 28, y + 38), sub, 12, "#82b8d8")
        y += 78
    buttons = [
        "Import paper", "Parse sections", "Extract claims", "Create blueprint",
        "Generate skeleton", "Run Mock", "Run skeleton", "Pause / Resume",
        "Cancel / Retry", "Compare / Report",
    ]
    y = 425
    for label in buttons:
        draw.rounded_rectangle((left[0] + 20, y, left[2] - 20, y + 24), radius=6, fill="#142b43", outline="#2c4c68")
        text(draw, (left[0] + 32, y + 4), label, 12, "#b9d5eb")
        y += 28

    # Center reader tabs and content
    tabs = ["Reader", "Structure", "Experiment", "Formula / Algorithm"]
    x = center[0] + 18
    for i, tab in enumerate(tabs):
        tw = 110 if i < 3 else 170
        fill = "#1b4e72" if i == 0 else "#0c1928"
        draw.rounded_rectangle((x, center[1] + 52, x + tw, center[1] + 88), radius=6, fill=fill, outline="#345f82")
        text(draw, (x + 12, center[1] + 62), tab, 13, "#ffffff" if i == 0 else "#9bb3c9")
        x += tw + 8
    content = (center[0] + 18, center[1] + 104, center[2] - 18, center[3] - 18)
    draw.rounded_rectangle(content, radius=8, fill="#0b1726", outline="#263f58")
    text(draw, (content[0] + 22, content[1] + 20), "EchoRAG: A Lightweight Retrieval Memory for Offline Assistants", 19, "#f2f7fc", True)
    text(draw, (content[0] + 22, content[1] + 58), "Abstract", 16, "#6fc4ed", True)
    abstract = [
        "We present EchoRAG, a small retrieval-and-memory module for", 
        "task-oriented assistants. The method retrieves the top-k notes", 
        "for a query and reports exact-match accuracy on MemoBench-S.",
    ]
    for i, line in enumerate(abstract):
        text(draw, (content[0] + 22, content[1] + 88 + i * 25), line, 14, "#c0d0de")
    text(draw, (content[0] + 22, content[1] + 190), "Workflow", 16, "#6fc4ed", True)
    flow = ["Sections", "Claims", "Blueprint", "Mock run", "Compare", "Report"]
    fx = content[0] + 22
    for i, step in enumerate(flow):
        draw.rounded_rectangle((fx, content[1] + 228, fx + 104, content[1] + 268), radius=10, fill="#15344c", outline="#2c698c")
        text(draw, (fx + 12, content[1] + 240), step, 12, "#d7f0ff", True)
        if i < len(flow) - 1:
            text(draw, (fx + 109, content[1] + 239), "→", 18, "#6fc4ed", True)
        fx += 125
    text(draw, (content[0] + 22, content[1] + 330), "Mock provider: deterministic · network_used = false", 13, "#8cd6b7")

    # Right claims and graph
    tabs2 = ["Claims", "Notes", "Metadata", "Graph"]
    x = right[0] + 14
    for i, tab in enumerate(tabs2):
        tw = 75 if i < 3 else 72
        fill = "#1b4e72" if i == 0 else "#0c1928"
        draw.rounded_rectangle((x, right[1] + 52, x + tw, right[1] + 88), radius=6, fill=fill, outline="#345f82")
        text(draw, (x + 9, right[1] + 62), tab, 12, "#ffffff" if i == 0 else "#9bb3c9")
        x += tw + 6
    cy = right[1] + 112
    claims = [
        ("Main Claim", "EchoRAG improves exact-match accuracy over NoMemory."),
        ("Supporting", "A memory budget of 4 improves recall over 2."),
        ("Engineering", "Tie-breaks use recency with a fixed seed."),
    ]
    for kind, claim in claims:
        draw.rounded_rectangle((right[0] + 16, cy, right[2] - 16, cy + 88), radius=8, fill="#0d1c2d", outline="#28435e")
        text(draw, (right[0] + 28, cy + 12), kind, 12, "#77c7ed", True)
        text(draw, (right[0] + 28, cy + 36), claim[:38], 12, "#d2deea")
        text(draw, (right[0] + 28, cy + 57), claim[38:76], 12, "#d2deea")
        cy += 102

    # Bottom runs/logs
    bottom = (margin, 752, width - margin, height - 24)
    panel(draw, bottom, "Runs / Logs", fill="#0f1b2a")
    headers = ["run_id", "blueprint", "status", "runtime", "result"]
    widths = [215, 220, 145, 110, 500]
    x = bottom[0] + 18
    for head, w in zip(headers, widths):
        text(draw, (x, bottom[1] + 55), head.upper(), 12, "#6f9abb", True)
        x += w
    draw.line((bottom[0] + 18, bottom[1] + 78, bottom[2] - 18, bottom[1] + 78), fill="#28445e", width=1)
    vals = ["run_20261004_174630", "bp_922cf37ea403", "SUCCEEDED", "0.004s", "accuracy=0.809 · recall=0.8955 · Mock"]
    x = bottom[0] + 18
    for val, w in zip(vals, widths):
        text(draw, (x, bottom[1] + 92), val, 13, "#d0e7f5" if val != "SUCCEEDED" else "#9de2bb")
        x += w
    text(draw, (bottom[0] + 18, bottom[1] + 132), "[offline] Imported sample paper · 13 sections · 21 claims · report saved as Markdown + HTML", 13, "#8ba9c0")
    text(draw, (width - 500, height - 48), "UI preview · actual runtime uses Tkinter", 12, "#56738e")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
