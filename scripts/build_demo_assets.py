"""Generate the binary demo assets (PDFs, SQLite database) from the committed sources.

Run standalone (`uv run python scripts/build_demo_assets.py`) or import ``build`` from the
demo launcher. Outputs go to ``demo/build/`` which is git-ignored.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

DEMO = Path(__file__).resolve().parent.parent / "demo"
BUILD = DEMO / "build"


def _markdown_to_pdf(markdown_path: Path, pdf_path: Path) -> None:
    """Render Markdown to a multi-page PDF: each ``##`` section starts on a new page so the
    demo produces distinct page-level citations."""
    import pymupdf

    font = pymupdf.Font("helv")
    document = pymupdf.open()
    page = document.new_page()
    writer = pymupdf.TextWriter(page.rect)
    y = 60.0
    started_section = False

    def new_page() -> None:
        nonlocal page, writer, y
        writer.write_text(page)
        page = document.new_page()
        writer = pymupdf.TextWriter(page.rect)
        y = 60.0

    for raw in markdown_path.read_text().splitlines():
        text = raw.rstrip()
        size = 11.0
        if text.startswith("# "):
            text, size = text[2:], 18.0
        elif text.startswith("## "):
            text, size = text[3:], 14.0
            if started_section:
                new_page()
            started_section = True
        if y > page.rect.height - 60:
            new_page()
        for chunk in _wrap(text, font, size, page.rect.width - 120) if text else []:
            writer.append((60, y), chunk, font=font, fontsize=size)
            y += size * 1.6
        y += size * 0.6
    writer.write_text(page)
    document.save(pdf_path)
    document.close()


def _wrap(text: str, font: Any, size: float, max_width: float) -> list[str]:
    words, line, out = text.split(), "", []
    for word in words:
        candidate = f"{line} {word}".strip()
        if font.text_length(candidate, fontsize=size) <= max_width:
            line = candidate
        else:
            if line:
                out.append(line)
            line = word
    if line:
        out.append(line)
    return out or [""]


def _build_database(path: Path) -> None:
    path.unlink(missing_ok=True)
    connection = sqlite3.connect(path)
    cursor = connection.cursor()
    cursor.executescript(
        """
        CREATE TABLE regions (name TEXT PRIMARY KEY, revenue_usd INTEGER, churn_rate REAL);
        CREATE TABLE departments (name TEXT PRIMARY KEY, avg_cost_usd INTEGER);
        CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, price_usd INTEGER);
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, created_on TEXT);
        CREATE TABLE tickets (
            id INTEGER PRIMARY KEY, status TEXT, opened_days_ago INTEGER, queue TEXT
        );
        CREATE TABLE orders (id INTEGER PRIMARY KEY, order_month TEXT);
        CREATE TABLE quarterly_sales (quarter TEXT PRIMARY KEY, amount_usd INTEGER);
        """
    )
    cursor.executemany(
        "INSERT INTO regions VALUES (?,?,?)",
        [("West", 250, 0.04), ("East", 180, 0.11), ("North", 210, 0.06), ("South", 165, 0.08)],
    )
    cursor.executemany(
        "INSERT INTO departments VALUES (?,?)",
        [("Finance", 120), ("Engineering", 95), ("Operations", 110), ("Security", 105)],
    )
    cursor.executemany(
        "INSERT INTO products VALUES (?,?,?)",
        [(1, "Scout", 420), (2, "Hauler", 780), (3, "Sentry", 610), (4, "Titan", 999)],
    )
    cursor.executemany(
        "INSERT INTO customers VALUES (?,?,?)",
        [
            (7, "Cedar Freight", "2026-05-02"),
            (8, "Aurora Mining", "2026-06-20"),
            (9, "Delta Ports", "2026-08-15"),
            (10, "Vantage Retail", "2026-09-03"),
        ],
    )
    cursor.executemany(
        "INSERT INTO tickets VALUES (?,?,?,?)",
        [
            (29, "closed", 12, "onboarding"),
            (30, "open", 8, "platform"),
            (31, "open", 45, "billing"),
            (32, "open", 3, "platform"),
        ],
    )
    cursor.executemany(
        "INSERT INTO orders VALUES (?,?)",
        [(i, "2026-09" if i <= 84 else "2026-08") for i in range(1, 121)],
    )
    cursor.executemany(
        "INSERT INTO quarterly_sales VALUES (?,?)",
        [("Q1", 100), ("Q2", 120), ("Q3", 130), ("Q4", 175)],
    )
    connection.commit()
    connection.close()


def build() -> dict[str, Path]:
    BUILD.mkdir(parents=True, exist_ok=True)
    pdfs = {}
    for name in ("hr-and-finance-policies", "security-program"):
        pdf = BUILD / f"{name}.pdf"
        _markdown_to_pdf(DEMO / "sources" / f"{name}.md", pdf)
        pdfs[name] = pdf
    database = BUILD / "meridian.db"
    _build_database(database)
    return {**pdfs, "database": database}


if __name__ == "__main__":
    outputs = build()
    for label, path in outputs.items():
        print(f"{label:>22}  {path}  ({path.stat().st_size} bytes)")
