"""Point d'entree du pipeline."""

from datetime import datetime
from pathlib import Path

from scraper import scrape_books_in_category_order
from transform import build_output_rows, write_books_csv


ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_CSV = ROOT_DIR / "data" / "books.csv"


def main() -> None:
    started_at = datetime.now()
    scraped_at = started_at.strftime("%Y-%m-%d %H:%M:%S")

    books, skipped_books = scrape_books_in_category_order()
    rows = build_output_rows(books, scraped_at)
    write_books_csv(rows, OUTPUT_CSV)

    ended_at = datetime.now()
    duration = ended_at - started_at

    print(f"\n[INFO] Livres ignores: {skipped_books}")
    print(f"Total livres: {len(rows)}")
    print(f"CSV genere: {OUTPUT_CSV}")
    print(f"Debut: {started_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Fin:   {ended_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Duree: {duration}")


if __name__ == "__main__":
    main()
