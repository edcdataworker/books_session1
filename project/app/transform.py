"""Couche transformation: formatage et export CSV."""

import csv
from pathlib import Path

from scraper import ScrapedBook


CSV_COLUMNS = [
    "DateHeureScraping",
    "NomLivre",
    "CategorieLivre",
    "PrixLivre",
    "NoteLivre",
]


def format_rating(rating: int) -> str:
    """Exemple: 4 -> '4,0/5'."""
    return f"{rating:.1f}".replace(".", ",") + "/5"


def build_output_rows(books: list[ScrapedBook], scraped_at: str) -> list[dict]:
    rows = []
    for book in books:
        rows.append(
            {
                "DateHeureScraping": scraped_at,
                "NomLivre": book.title,
                "CategorieLivre": book.category,
                "PrixLivre": book.price,
                "NoteLivre": format_rating(book.rating),
            }
        )
    return rows


def write_books_csv(rows: list[dict], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with output_file.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
