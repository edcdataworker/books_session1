"""Couche transformation: validation, quarantaine et export CSV atomique."""

from __future__ import annotations

import csv
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Optional

from error_codes import (
    DATA_INVALID_PRICE,
    DATA_INVALID_RATING,
    DATA_INVALID_TITLE,
    IO_ATOMIC_WRITE_FAILED,
    IO_QUARANTINE_WRITE_FAILED,
    log_with_code,
)
from scraper import ScrapedBook


CSV_COLUMNS = [
    "DateHeureScraping",
    "NomLivre",
    "CategorieLivre",
    "PrixLivre",
    "NoteLivre",
]
LOGGER = logging.getLogger(__name__)


def format_rating(rating: float) -> str:
    """Exemple: 4 -> '4,0/5'."""
    return f"{rating:.1f}".replace(".", ",") + "/5"


def _validate_book(book: ScrapedBook) -> list[tuple[str, str]]:
    errors: list[tuple[str, str]] = []

    title = (book.title or "").strip() if isinstance(book.title, str) else ""
    if not title:
        errors.append((DATA_INVALID_TITLE.code, "titre_vide"))

    if not isinstance(book.price, (int, float)) or float(book.price) <= 0:
        errors.append((DATA_INVALID_PRICE.code, "prix_invalide"))

    if not isinstance(book.rating, (int, float)) or not (0 <= float(book.rating) <= 5):
        errors.append((DATA_INVALID_RATING.code, "note_invalide"))

    return errors


def _book_to_dict(book: ScrapedBook) -> dict:
    return {
        "title": book.title,
        "category": book.category,
        "price": book.price,
        "rating": book.rating,
    }


def _append_rejected_rows_jsonl(rejected_rows: list[dict], rejected_file: Path) -> None:
    if not rejected_rows:
        return

    try:
        rejected_file.parent.mkdir(parents=True, exist_ok=True)
        with rejected_file.open("a", encoding="utf-8") as file:
            for row in rejected_rows:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        log_with_code(
            LOGGER,
            logging.ERROR,
            IO_QUARANTINE_WRITE_FAILED,
            "Echec d'ecriture de la quarantaine JSONL: %s",
            rejected_file,
            exc_info=True,
        )


def build_output_rows(
    books: list[ScrapedBook],
    scraped_at: str,
    rejected_file: Path,
) -> tuple[list[dict], int]:
    rows: list[dict] = []
    rejected_rows: list[dict] = []

    for book in books:
        validation_errors = _validate_book(book)
        if validation_errors:
            for code, label in validation_errors:
                if code == DATA_INVALID_TITLE.code:
                    log_with_code(
                        LOGGER,
                        logging.WARNING,
                        DATA_INVALID_TITLE,
                        "Livre invalide (%s): %s",
                        label,
                        _book_to_dict(book),
                    )
                elif code == DATA_INVALID_PRICE.code:
                    log_with_code(
                        LOGGER,
                        logging.WARNING,
                        DATA_INVALID_PRICE,
                        "Livre invalide (%s): %s",
                        label,
                        _book_to_dict(book),
                    )
                elif code == DATA_INVALID_RATING.code:
                    log_with_code(
                        LOGGER,
                        logging.WARNING,
                        DATA_INVALID_RATING,
                        "Livre invalide (%s): %s",
                        label,
                        _book_to_dict(book),
                    )

            rejected_rows.append(
                {
                    "DateHeureScraping": scraped_at,
                    "errors": [{"code": code, "detail": label} for code, label in validation_errors],
                    "payload": _book_to_dict(book),
                }
            )
            continue

        rows.append(
            {
                "DateHeureScraping": scraped_at,
                "NomLivre": (book.title or "").strip(),
                "CategorieLivre": (book.category or "Unknown").strip(),
                "PrixLivre": round(float(book.price), 2),
                "NoteLivre": format_rating(float(book.rating)),
            }
        )

    _append_rejected_rows_jsonl(rejected_rows, rejected_file)
    if rejected_rows:
        LOGGER.warning("Lignes invalide(s) envoyees en quarantaine: %s", len(rejected_rows))

    return rows, len(rejected_rows)


def write_books_csv(rows: list[dict], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)

    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            newline="",
            encoding="utf-8-sig",
            delete=False,
            dir=output_file.parent,
            prefix=f".{output_file.name}.",
            suffix=".tmp",
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
            writer = csv.DictWriter(tmp_file, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

        os.replace(tmp_path, output_file)
    except OSError:
        log_with_code(
            LOGGER,
            logging.ERROR,
            IO_ATOMIC_WRITE_FAILED,
            "Echec d'ecriture atomique CSV: %s",
            output_file,
            exc_info=True,
        )
        raise
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)

    LOGGER.info("CSV ecrit (atomique): %s (%s lignes)", output_file, len(rows))
