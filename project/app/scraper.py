"""Couche scraping: HTTP + extraction des donnees depuis le site."""

from dataclasses import dataclass
import re
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://books.toscrape.com/"
RATING_TO_INT = {
    "One": 1,
    "Two": 2,
    "Three": 3,
    "Four": 4,
    "Five": 5,
}


@dataclass
class ScrapedBook:
    title: str
    category: str
    price: float
    rating: int


def create_session() -> requests.Session:
    """Session unique reutilisee sur tout le pipeline."""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
    )
    return session


def get_soup(session: requests.Session, url: str) -> Optional[BeautifulSoup]:
    """Charge une page HTML. Retourne None en cas d'erreur."""
    try:
        response = session.get(url, timeout=20)
        response.raise_for_status()
        response.encoding = "utf-8"
        return BeautifulSoup(response.text, "html.parser")
    except requests.RequestException as error:
        print(f"[ERREUR] Impossible de charger {url} ({error})")
        return None


def parse_price(raw_price: str) -> float:
    return round(float(re.sub(r"[^0-9.]", "", raw_price)), 2)


def parse_rating_from_card(card: BeautifulSoup) -> int:
    rating_node = card.select_one(".star-rating")
    if rating_node is None:
        return 0

    classes = rating_node.get("class", [])
    rating_label = next((c for c in classes if c != "star-rating"), "")
    return RATING_TO_INT.get(rating_label, 0)


def parse_category_from_breadcrumb(soup: BeautifulSoup) -> str:
    """
    Sur les pages categories:
    Home > Books > [Categorie]
    """
    crumbs = [li.get_text(strip=True) for li in soup.select("ul.breadcrumb li")]
    if len(crumbs) >= 3:
        return crumbs[2]
    return "Unknown"


def get_category_links(session: requests.Session) -> list[str]:
    """Recupere les URLs de categories dans l'ordre du menu du site."""
    soup = get_soup(session, BASE_URL)
    if soup is None:
        return []

    category_urls = []
    for link in soup.select("div.side_categories ul.nav-list ul li a"):
        href = link.get("href", "").strip()
        if href:
            category_urls.append(urljoin(BASE_URL, href))

    return category_urls


def parse_book_card(card: BeautifulSoup, category: str) -> Optional[ScrapedBook]:
    """Parse un livre depuis la carte produit (sans ouvrir la fiche livre)."""
    try:
        title_link = card.select_one("h3 a")
        price_node = card.select_one(".price_color")

        if title_link is None or price_node is None:
            return None

        title = title_link.get("title", "").strip() or title_link.get_text(strip=True)
        price = parse_price(price_node.get_text(strip=True))
        rating = parse_rating_from_card(card)

        return ScrapedBook(
            title=title,
            category=category,
            price=price,
            rating=rating,
        )
    except Exception as error:
        print(f"[ERREUR] Donnee mal formee sur une carte produit ({error})")
        return None


def scrape_one_category(
    session: requests.Session, category_url: str
) -> tuple[list[ScrapedBook], int]:
    """Scrape une categorie complete (pagination incluse)."""
    rows: list[ScrapedBook] = []
    skipped_books = 0
    page_url = category_url

    while page_url:
        soup = get_soup(session, page_url)
        if soup is None:
            break

        category = parse_category_from_breadcrumb(soup)
        cards = soup.select("article.product_pod")

        for card in cards:
            row = parse_book_card(card, category)
            if row is None:
                skipped_books += 1
                continue
            rows.append(row)

        next_link = soup.select_one("li.next a")
        page_url = urljoin(page_url, next_link["href"]) if next_link else None

    return rows, skipped_books


def scrape_books_in_category_order() -> tuple[list[ScrapedBook], int]:
    """Scrape les livres dans l'ordre des categories du site."""
    all_rows: list[ScrapedBook] = []
    skipped_books = 0

    with create_session() as session:
        category_urls = get_category_links(session)
        total_categories = len(category_urls)
        print(f"Categories detectees: {total_categories}")

        for index, category_url in enumerate(category_urls, start=1):
            print(f"Categorie {index}/{total_categories}: {category_url}")
            rows, skipped = scrape_one_category(session, category_url)
            print(f"  Livres trouves: {len(rows)}")
            all_rows.extend(rows)
            skipped_books += skipped

    return all_rows, skipped_books
