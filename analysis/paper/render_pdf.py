"""Render paper.md to paper.pdf (reportlab/platypus, paper-style layout).

Handles exactly the markdown constructs the draft uses: #/##/### headings,
paragraphs with **bold** / *italic* / `code`, pipe tables, images, and hrules.
"""

import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Image,
                                Table, TableStyle, HRFlowable)
from PIL import Image as PILImage

HERE = Path(__file__).resolve().parent
SRC = HERE / "paper.md"
OUT = HERE / "paper.pdf"

styles = getSampleStyleSheet()
BODY = ParagraphStyle("Body", parent=styles["Normal"], fontName="Times-Roman",
                      fontSize=10, leading=13.5, alignment=TA_JUSTIFY,
                      spaceAfter=6)
TITLE = ParagraphStyle("PTitle", parent=styles["Title"], fontName="Times-Bold",
                       fontSize=16, leading=20, spaceAfter=10)
H1 = ParagraphStyle("PH1", parent=styles["Heading1"], fontName="Times-Bold",
                    fontSize=12.5, leading=15, spaceBefore=12, spaceAfter=4)
H2 = ParagraphStyle("PH2", parent=styles["Heading2"], fontName="Times-Bold",
                    fontSize=11, leading=13, spaceBefore=8, spaceAfter=3)
META = ParagraphStyle("PMeta", parent=BODY, alignment=TA_CENTER,
                      fontSize=9.5, textColor=colors.HexColor("#333333"))
CELL = ParagraphStyle("PCell", parent=BODY, fontSize=8.5, leading=10.5,
                      alignment=0, spaceAfter=0)
CAPTION = ParagraphStyle("PCap", parent=BODY, alignment=TA_CENTER,
                         fontSize=8.5, textColor=colors.HexColor("#444444"))


def inline(md):
    """Markdown inline -> reportlab XML markup."""
    s = (md.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"`(.+?)`", r'<font face="Courier" size="8.5">\1</font>', s)
    s = s.replace("^2", "<super>2</super>")
    return s


def table_flowable(rows, avail_width):
    data = [[Paragraph(f"<b>{inline(c)}</b>" if i == 0 else inline(c), CELL)
             for c in row] for i, row in enumerate(rows)]
    ncols = max(len(r) for r in rows)
    t = Table(data, colWidths=[avail_width / ncols] * ncols, repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    return t


def build():
    doc = SimpleDocTemplate(str(OUT), pagesize=letter,
                            leftMargin=0.95 * inch, rightMargin=0.95 * inch,
                            topMargin=0.9 * inch, bottomMargin=0.9 * inch,
                            title="OpenMuscle: FMG with VR ground truth",
                            author="TURFPTAx / OpenMuscle")
    avail = doc.width
    story = []
    lines = SRC.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("# "):
            story.append(Paragraph(inline(line[2:]), TITLE))
        elif line.startswith("## "):
            story.append(Paragraph(inline(line[3:]), H1))
        elif line.startswith("### "):
            story.append(Paragraph(inline(line[4:]), H2))
        elif line.startswith("**Authors:") or line.startswith("**Artifacts:"):
            story.append(Paragraph(inline(line), META))
        elif line.startswith("!["):
            m = re.match(r"!\[(.*?)\]\((.*?)\)", line)
            if m:
                img_path = HERE / m.group(2)
                w, h = PILImage.open(img_path).size
                draw_w = min(avail, 5.6 * inch)
                story.append(Spacer(1, 6))
                story.append(Image(str(img_path), width=draw_w,
                                   height=draw_w * h / w))
                if m.group(1):
                    story.append(Paragraph(inline(m.group(1)), CAPTION))
                story.append(Spacer(1, 6))
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            i -= 1
            story.append(Spacer(1, 4))
            story.append(table_flowable(rows, avail))
            story.append(Spacer(1, 6))
        elif line.strip() == "---":
            story.append(HRFlowable(width="100%", thickness=0.6,
                                    color=colors.HexColor("#888888"),
                                    spaceBefore=10, spaceAfter=6))
        elif line.strip():
            # Merge hard-wrapped paragraph lines until a blank/structural line.
            para = [line]
            while (i + 1 < len(lines) and lines[i + 1].strip()
                   and not re.match(r"^(#|\||!\[|---$|\*\*Auth|\*\*Arti)", lines[i + 1])):
                i += 1
                para.append(lines[i])
            story.append(Paragraph(inline(" ".join(para)), BODY))
        i += 1
    doc.build(story)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    build()
