"""Couche scraping resiliente: HTTP, extraction, fallback et monitoring."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from error_codes import (
    IO_FAILED_HTML_CAPTURE,
    NET_HTTP_404,
    NET_HTTP_410,
    NET_HTTP_429_RETRY,
    NET_HTTP_5XX_RETRY,
    NET_REQUEST_GIVEUP,
    NET_REQUEST_RETRY,
    NET_TIMEOUT_GIVEUP,
    NET_TIMEOUT_RETRY,
    PARSE_CATEGORY_PAGE_FAILED,
    PARSE_JSONLD_INVALID,
    PARSE_PRICE_INVALID,
    PARSE_RATING_INVALID,
    PARSE_SELECTOR_MISS,
    log_with_code,
)


BASE_URL = "https://books.toscrape.com/"
LOGGER = logging.getLogger(__name__)

HTTP_TIMEOUT_SECONDS = float(os.getenv("SCRAPER_HTTP_TIMEOUT_SECONDS", "20"))
HTTP_MAX_RETRIES = int(os.getenv("SCRAPER_HTTP_MAX_RETRIES", "3"))
BACKOFF_BASE_SECONDS = float(os.getenv("SCRAPER_BACKOFF_BASE_SECONDS", "1.0"))
ENABLE_BROWSER_FALLBACK = os.getenv("SCRAPER_ENABLE_BROWSER_FALLBACK", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
CAPTURE_FAILED_HTML = os.getenv("SCRAPER_CAPTURE_FAILED_HTML", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

RATING_TO_INT = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
}


@dataclass
class ScrapedBook:
    title: Optional[str]
    category: Optional[str]
    price: Optional[float]
    rating: Optional[float]


@dataclass
class ScrapeMetrics:
    total_categories: int = 0
    failed_categories: int = 0
    skipped_books: int = 0
    network_errors: int = 0
    parse_errors: int = 0
    total_cards_seen: int = 0
    total_books_scraped: int = 0
    categories_with_zero_books: int = 0
    jsonld_fallback_used: int = 0
    browser_fallback_attempts: int = 0
    browser_fallback_success: int = 0
    selector_attempts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    selector_hits: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    error_codes: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    failed_category_urls: list[str] = field(default_factory=list)


@dataclass
class ScrapeResult:
    books: list[ScrapedBook]
    metrics: ScrapeMetrics


def _count_error(metrics: ScrapeMetrics, error_code: str) -> None:
    metrics.error_codes[error_code] += 1


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


def _sanitize_url_for_path(url: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9._-]", "_", url)
    return sanitized[:120]


def _capture_failed_html(
    html: str,
    url: str,
    failed_pages_dir: Path,
    suffix: str,
    metrics: ScrapeMetrics,
) -> None:
    if not CAPTURE_FAILED_HTML:
        return

    try:
        failed_pages_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{int(time.time())}_{suffix}_{_sanitize_url_for_path(url)}.html"
        (failed_pages_dir / filename).write_text(html, encoding="utf-8")
    except OSError:
        _count_error(metrics, IO_FAILED_HTML_CAPTURE.code)
        log_with_code(
            LOGGER,
            logging.WARNING,
            IO_FAILED_HTML_CAPTURE,
            "Impossible de sauvegarder le HTML en echec pour %s",
            url,
            exc_info=True,
        )


def _parse_retry_after_seconds(value: Optional[str]) -> Optional[float]:
    if not value:
        return None

    stripped = value.strip()
    if not stripped:
        return None

    try:
        return max(float(stripped), 0.0)
    except ValueError:
        return None


def _backoff_delay(attempt_index: int, retry_after: Optional[float] = None) -> float:
    if retry_after is not None:
        return retry_after
    return BACKOFF_BASE_SECONDS * (2 ** attempt_index)


def _fetch_html_with_browser_fallback(
    url: str,
    metrics: ScrapeMetrics,
) -> Optional[str]:
    metrics.browser_fallback_attempts += 1

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=int(HTTP_TIMEOUT_SECONDS * 1000))
            html = page.content()
            browser.close()

        if html:
            metrics.browser_fallback_success += 1
            return html
    except Exception:
        log_with_code(
            LOGGER,
            logging.WARNING,
            PARSE_CATEGORY_PAGE_FAILED,
            "Fallback Playwright indisponible ou en echec pour %s",
            url,
            exc_info=True,
        )

    try:
        from selenium import webdriver

        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(HTTP_TIMEOUT_SECONDS)
        driver.get(url)
        html = driver.page_source
        driver.quit()

        if html:
            metrics.browser_fallback_success += 1
            return html
    except Exception:
        log_with_code(
            LOGGER,
            logging.WARNING,
            PARSE_CATEGORY_PAGE_FAILED,
            "Fallback Selenium indisponible ou en echec pour %s",
            url,
            exc_info=True,
        )

    return None


def _get_html_with_retries(
    session: requests.Session,
    url: str,
    metrics: ScrapeMetrics,
    failed_pages_dir: Path,
) -> Optional[str]:
    max_attempts = HTTP_MAX_RETRIES + 1

    for attempt in range(max_attempts):
        try:
            response = session.get(url, timeout=HTTP_TIMEOUT_SECONDS)
        except requests.Timeout as error:
            metrics.network_errors += 1
            if attempt < HTTP_MAX_RETRIES:
                delay = _backoff_delay(attempt)
                _count_error(metrics, NET_TIMEOUT_RETRY.code)
                log_with_code(
                    LOGGER,
                    logging.WARNING,
                    NET_TIMEOUT_RETRY,
                    "Timeout sur %s, tentative=%s/%s, retry dans %.2fs (%s)",
                    url,
                    attempt + 1,
                    max_attempts,
                    delay,
                    error,
                )
                time.sleep(delay)
                continue

            _count_error(metrics, NET_TIMEOUT_GIVEUP.code)
            log_with_code(
                LOGGER,
                logging.ERROR,
                NET_TIMEOUT_GIVEUP,
                "Timeout final sur %s apres %s tentatives (%s)",
                url,
                max_attempts,
                error,
            )
            return None
        except requests.RequestException as error:
            metrics.network_errors += 1
            if attempt < HTTP_MAX_RETRIES:
                delay = _backoff_delay(attempt)
                _count_error(metrics, NET_REQUEST_RETRY.code)
                log_with_code(
                    LOGGER,
                    logging.WARNING,
                    NET_REQUEST_RETRY,
                    "Erreur reseau sur %s, tentative=%s/%s, retry dans %.2fs (%s)",
                    url,
                    attempt + 1,
                    max_attempts,
                    delay,
                    error,
                )
                time.sleep(delay)
                continue

            _count_error(metrics, NET_REQUEST_GIVEUP.code)
            log_with_code(
                LOGGER,
                logging.ERROR,
                NET_REQUEST_GIVEUP,
                "Erreur reseau finale sur %s apres %s tentatives (%s)",
                url,
                max_attempts,
                error,
            )
            return None

        status_code = response.status_code

        if status_code == 404:
            metrics.network_errors += 1
            _count_error(metrics, NET_HTTP_404.code)
            log_with_code(
                LOGGER,
                logging.ERROR,
                NET_HTTP_404,
                "Abandon immediat sur %s (HTTP 404)",
                url,
            )
            _capture_failed_html(response.text, url, failed_pages_dir, "http404", metrics)
            return None

        if status_code == 410:
            metrics.network_errors += 1
            _count_error(metrics, NET_HTTP_410.code)
            log_with_code(
                LOGGER,
                logging.ERROR,
                NET_HTTP_410,
                "Abandon immediat sur %s (HTTP 410)",
                url,
            )
            _capture_failed_html(response.text, url, failed_pages_dir, "http410", metrics)
            return None

        if status_code == 429:
            metrics.network_errors += 1
            if attempt < HTTP_MAX_RETRIES:
                retry_after = _parse_retry_after_seconds(response.headers.get("Retry-After"))
                delay = _backoff_delay(attempt, retry_after=retry_after)
                _count_error(metrics, NET_HTTP_429_RETRY.code)
                log_with_code(
                    LOGGER,
                    logging.WARNING,
                    NET_HTTP_429_RETRY,
                    "HTTP 429 sur %s, tentative=%s/%s, retry dans %.2fs",
                    url,
                    attempt + 1,
                    max_attempts,
                    delay,
                )
                time.sleep(delay)
                continue

            _count_error(metrics, NET_REQUEST_GIVEUP.code)
            log_with_code(
                LOGGER,
                logging.ERROR,
                NET_REQUEST_GIVEUP,
                "HTTP 429 final sur %s apres %s tentatives",
                url,
                max_attempts,
            )
            _capture_failed_html(response.text, url, failed_pages_dir, "http429", metrics)
            return None

        if 500 <= status_code < 600:
            metrics.network_errors += 1
            if attempt < HTTP_MAX_RETRIES:
                delay = _backoff_delay(attempt)
                _count_error(metrics, NET_HTTP_5XX_RETRY.code)
                log_with_code(
                    LOGGER,
                    logging.WARNING,
                    NET_HTTP_5XX_RETRY,
                    "HTTP %s sur %s, tentative=%s/%s, retry dans %.2fs",
                    status_code,
                    url,
                    attempt + 1,
                    max_attempts,
                    delay,
                )
                time.sleep(delay)
                continue

            _count_error(metrics, NET_REQUEST_GIVEUP.code)
            log_with_code(
                LOGGER,
                logging.ERROR,
                NET_REQUEST_GIVEUP,
                "HTTP %s final sur %s apres %s tentatives",
                status_code,
                url,
                max_attempts,
            )
            _capture_failed_html(response.text, url, failed_pages_dir, f"http{status_code}", metrics)
            return None

        try:
            response.raise_for_status()
        except requests.HTTPError as error:
            metrics.network_errors += 1
            _count_error(metrics, NET_REQUEST_GIVEUP.code)
            log_with_code(
                LOGGER,
                logging.ERROR,
                NET_REQUEST_GIVEUP,
                "HTTP non gere sur %s (%s)",
                url,
                error,
            )
            _capture_failed_html(response.text, url, failed_pages_dir, "http_error", metrics)
            return None

        response.encoding = "utf-8"
        return response.text

    return None


def get_soup(
    session: requests.Session,
    url: str,
    metrics: ScrapeMetrics,
    failed_pages_dir: Path,
) -> Optional[BeautifulSoup]:
    html = _get_html_with_retries(session, url, metrics, failed_pages_dir)

    if html is None and ENABLE_BROWSER_FALLBACK:
        html = _fetch_html_with_browser_fallback(url, metrics)

    if html is None:
        return None

    return BeautifulSoup(html, "html.parser")


def parse_price(raw_price: Optional[str], metrics: ScrapeMetrics) -> Optional[float]:
    if not raw_price:
        metrics.parse_errors += 1
        _count_error(metrics, PARSE_PRICE_INVALID.code)
        log_with_code(
            LOGGER,
            logging.WARNING,
            PARSE_PRICE_INVALID,
            "Prix manquant",
        )
        return None

    cleaned = raw_price.replace("\xa0", " ").strip()
    number_match = re.search(r"[-+]?\d[\d.,\s]*", cleaned)
    if not number_match:
        metrics.parse_errors += 1
        _count_error(metrics, PARSE_PRICE_INVALID.code)
        log_with_code(
            LOGGER,
            logging.WARNING,
            PARSE_PRICE_INVALID,
            "Format prix invalide: %s",
            raw_price,
        )
        return None

    numeric = number_match.group(0).replace(" ", "")

    if "," in numeric and "." in numeric:
        if numeric.rfind(",") > numeric.rfind("."):
            numeric = numeric.replace(".", "").replace(",", ".")
        else:
            numeric = numeric.replace(",", "")
    elif "," in numeric and "." not in numeric:
        numeric = numeric.replace(",", ".")

    try:
        return round(float(numeric), 2)
    except ValueError:
        metrics.parse_errors += 1
        _count_error(metrics, PARSE_PRICE_INVALID.code)
        log_with_code(
            LOGGER,
            logging.WARNING,
            PARSE_PRICE_INVALID,
            "Conversion prix impossible: %s",
            raw_price,
        )
        return None


def _extract_text_from_selectors(
    node: BeautifulSoup,
    selectors: list[str],
    field: str,
    metrics: ScrapeMetrics,
) -> Optional[str]:
    metrics.selector_attempts[field] += 1

    for selector in selectors:
        element = node.select_one(selector)
        if element is None:
            continue

        value = element.get_text(" ", strip=True)
        if value:
            metrics.selector_hits[field] += 1
            return value

    metrics.parse_errors += 1
    _count_error(metrics, PARSE_SELECTOR_MISS.code)
    log_with_code(
        LOGGER,
        logging.WARNING,
        PARSE_SELECTOR_MISS,
        "Aucun selecteur fonctionnel pour le champ '%s'",
        field,
    )
    return None


def _extract_title(card: BeautifulSoup, metrics: ScrapeMetrics) -> Optional[str]:
    metrics.selector_attempts["title"] += 1

    title_link = card.select_one("h3 a")
    if title_link is not None:
        title_attr = (title_link.get("title") or "").strip()
        if title_attr:
            metrics.selector_hits["title"] += 1
            return title_attr

        title_text = title_link.get_text(" ", strip=True)
        if title_text:
            metrics.selector_hits["title"] += 1
            return title_text

    for selector in [".product_main h1", "[itemprop='name']", "h1", "h2"]:
        element = card.select_one(selector)
        if element is None:
            continue
        value = element.get_text(" ", strip=True)
        if value:
            metrics.selector_hits["title"] += 1
            return value

    metrics.parse_errors += 1
    _count_error(metrics, PARSE_SELECTOR_MISS.code)
    log_with_code(
        LOGGER,
        logging.WARNING,
        PARSE_SELECTOR_MISS,
        "Titre introuvable dans la carte",
    )
    return None


def _parse_rating_from_text(raw_rating: str) -> Optional[float]:
    lowered = raw_rating.strip().lower()

    for label, rating in RATING_TO_INT.items():
        if label in lowered:
            return float(rating)

    number_match = re.search(r"([0-5](?:[.,]\d+)?)", lowered)
    if number_match:
        value = number_match.group(1).replace(",", ".")
        try:
            parsed = float(value)
            if 0 <= parsed <= 5:
                return parsed
        except ValueError:
            return None

    return None


def parse_rating_from_card(card: BeautifulSoup, metrics: ScrapeMetrics) -> Optional[float]:
    metrics.selector_attempts["rating"] += 1

    rating_node = card.select_one(".star-rating")
    if rating_node is not None:
        classes = [str(c).lower() for c in rating_node.get("class", [])]
        for css_class in classes:
            if css_class in RATING_TO_INT:
                metrics.selector_hits["rating"] += 1
                return float(RATING_TO_INT[css_class])

        for attr in ["aria-label", "title", "data-rating", "data-score"]:
            value = rating_node.get(attr)
            if value:
                parsed = _parse_rating_from_text(str(value))
                if parsed is not None:
                    metrics.selector_hits["rating"] += 1
                    return parsed

        node_text = rating_node.get_text(" ", strip=True)
        if node_text:
            parsed = _parse_rating_from_text(node_text)
            if parsed is not None:
                metrics.selector_hits["rating"] += 1
                return parsed

    rating_text = _extract_text_from_selectors(
        card,
        ["[itemprop='ratingValue']", "[data-rating]", ".rating", ".review-rating"],
        "rating_fallback",
        metrics,
    )
    if rating_text:
        parsed = _parse_rating_from_text(rating_text)
        if parsed is not None:
            metrics.selector_hits["rating"] += 1
            return parsed

    metrics.parse_errors += 1
    _count_error(metrics, PARSE_RATING_INVALID.code)
    log_with_code(
        LOGGER,
        logging.WARNING,
        PARSE_RATING_INVALID,
        "Note introuvable ou invalide pour une carte (mode degrade: rating=None)",
    )
    return None


def parse_category_from_breadcrumb(soup: BeautifulSoup, metrics: ScrapeMetrics) -> Optional[str]:
    metrics.selector_attempts["category"] += 1

    crumb_selectors = [
        "ul.breadcrumb li:nth-of-type(3) a",
        "ul.breadcrumb li:nth-of-type(3)",
        "nav.breadcrumb li:nth-of-type(3)",
        "[itemprop='itemListElement']:nth-of-type(3)",
    ]

    for selector in crumb_selectors:
        node = soup.select_one(selector)
        if node is None:
            continue

        value = node.get_text(" ", strip=True)
        if value:
            metrics.selector_hits["category"] += 1
            return value

    metrics.parse_errors += 1
    _count_error(metrics, PARSE_SELECTOR_MISS.code)
    log_with_code(
        LOGGER,
        logging.WARNING,
        PARSE_SELECTOR_MISS,
        "Categorie introuvable, fallback sur 'Unknown'",
    )
    return "Unknown"


def _parse_jsonld_objects(soup: BeautifulSoup, metrics: ScrapeMetrics) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []

    for script in soup.select("script[type='application/ld+json']"):
        raw = script.get_text(strip=True)
        if not raw:
            continue

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            metrics.parse_errors += 1
            _count_error(metrics, PARSE_JSONLD_INVALID.code)
            log_with_code(
                LOGGER,
                logging.WARNING,
                PARSE_JSONLD_INVALID,
                "JSON-LD invalide ignore",
            )
            continue

        if isinstance(parsed, dict):
            objects.append(parsed)
        elif isinstance(parsed, list):
            objects.extend([item for item in parsed if isinstance(item, dict)])

    return objects


def _extract_book_from_jsonld(
    jsonld_objects: list[dict[str, Any]],
    category: Optional[str],
    metrics: ScrapeMetrics,
) -> Optional[ScrapedBook]:
    for obj in jsonld_objects:
        candidate = obj
        if "mainEntity" in obj and isinstance(obj["mainEntity"], dict):
            candidate = obj["mainEntity"]

        name = candidate.get("name")
        offers = candidate.get("offers") if isinstance(candidate.get("offers"), dict) else {}
        aggregate_rating = (
            candidate.get("aggregateRating")
            if isinstance(candidate.get("aggregateRating"), dict)
            else {}
        )

        raw_price = offers.get("price") if isinstance(offers, dict) else None
        raw_rating = aggregate_rating.get("ratingValue") if isinstance(aggregate_rating, dict) else None

        title = str(name).strip() if isinstance(name, str) else None
        price = parse_price(str(raw_price), metrics) if raw_price is not None else None
        rating = _parse_rating_from_text(str(raw_rating)) if raw_rating is not None else None

        if title or price is not None or rating is not None:
            metrics.jsonld_fallback_used += 1
            return ScrapedBook(
                title=title,
                category=category,
                price=price,
                rating=rating,
            )

    return None


def get_category_links(
    session: requests.Session,
    metrics: ScrapeMetrics,
    failed_pages_dir: Path,
) -> list[str]:
    """Recupere les URLs de categories dans l'ordre du menu du site."""
    soup = get_soup(session, BASE_URL, metrics, failed_pages_dir)
    if soup is None:
        return []

    category_urls = []
    for link in soup.select("div.side_categories ul.nav-list ul li a"):
        href = link.get("href", "").strip()
        if href:
            category_urls.append(urljoin(BASE_URL, href))

    if not category_urls:
        log_with_code(
            LOGGER,
            logging.ERROR,
            PARSE_CATEGORY_PAGE_FAILED,
            "Aucune categorie detectee sur la page d'accueil",
        )

    return category_urls


def parse_book_card(
    card: BeautifulSoup,
    category: Optional[str],
    metrics: ScrapeMetrics,
    page_soup: Optional[BeautifulSoup] = None,
) -> Optional[ScrapedBook]:
    """Parse un livre depuis la carte produit, avec fallback JSON-LD."""
    try:
        title = _extract_title(card, metrics)

        raw_price = _extract_text_from_selectors(
            card,
            [
                ".price_color",
                "[itemprop='price']",
                ".product_price .price_color",
                ".price",
            ],
            "price",
            metrics,
        )
        price = parse_price(raw_price, metrics)

        rating = parse_rating_from_card(card, metrics)

        if title is None or price is None:
            if page_soup is not None:
                jsonld_objects = _parse_jsonld_objects(page_soup, metrics)
                fallback = _extract_book_from_jsonld(jsonld_objects, category, metrics)
                if fallback is not None:
                    if title is None:
                        title = fallback.title
                    if price is None:
                        price = fallback.price
                    if rating is None:
                        rating = fallback.rating

        # Mode degrade: rating peut rester None, la validation aval decidera si la ligne est rejetee.
        return ScrapedBook(
            title=title,
            category=category,
            price=price,
            rating=rating,
        )
    except Exception:
        metrics.parse_errors += 1
        _count_error(metrics, PARSE_CATEGORY_PAGE_FAILED.code)
        log_with_code(
            LOGGER,
            logging.ERROR,
            PARSE_CATEGORY_PAGE_FAILED,
            "Erreur inattendue lors du parsing d'une carte produit",
            exc_info=True,
        )
        return None


def scrape_one_category(
    session: requests.Session,
    category_url: str,
    metrics: ScrapeMetrics,
    failed_pages_dir: Path,
) -> tuple[list[ScrapedBook], bool]:
    """Scrape une categorie complete (pagination incluse).

    Retourne:
    - rows de la categorie
    - bool: la categorie a subi un echec majeur (page inaccessible ou parse critique)
    """
    rows: list[ScrapedBook] = []
    page_url = category_url
    category_failed = False

    while page_url:
        soup = get_soup(session, page_url, metrics, failed_pages_dir)
        if soup is None:
            category_failed = True
            log_with_code(
                LOGGER,
                logging.ERROR,
                PARSE_CATEGORY_PAGE_FAILED,
                "Categorie partiellement/totalement ignoree (page inaccessible): %s",
                page_url,
            )
            break

        category = parse_category_from_breadcrumb(soup, metrics) or "Unknown"
        cards = soup.select("article.product_pod")
        metrics.total_cards_seen += len(cards)

        if not cards:
            if ENABLE_BROWSER_FALLBACK:
                html = _fetch_html_with_browser_fallback(page_url, metrics)
                if html:
                    soup = BeautifulSoup(html, "html.parser")
                    cards = soup.select("article.product_pod")
                    metrics.total_cards_seen += len(cards)

        if not cards:
            category_failed = True
            metrics.categories_with_zero_books += 1
            _capture_failed_html(str(soup), page_url, failed_pages_dir, "zero_cards", metrics)
            log_with_code(
                LOGGER,
                logging.WARNING,
                PARSE_CATEGORY_PAGE_FAILED,
                "Aucune carte produit trouvee sur %s",
                page_url,
            )

        for card in cards:
            row = parse_book_card(card, category, metrics, page_soup=soup)
            if row is None:
                metrics.skipped_books += 1
                continue
            rows.append(row)

        next_link = soup.select_one("li.next a")
        page_url = urljoin(page_url, next_link["href"]) if next_link else None

    if not rows:
        category_failed = True

    return rows, category_failed


def scrape_books_in_category_order(
    failed_pages_dir: Path,
) -> ScrapeResult:
    """Scrape les livres dans l'ordre des categories du site.

    Toujours en mode degrade: la boucle continue meme si une categorie echoue.
    """
    all_rows: list[ScrapedBook] = []
    metrics = ScrapeMetrics()

    with create_session() as session:
        category_urls = get_category_links(session, metrics, failed_pages_dir)
        metrics.total_categories = len(category_urls)
        LOGGER.info("Categories detectees: %s", metrics.total_categories)

        for index, category_url in enumerate(category_urls, start=1):
            LOGGER.info("Categorie %s/%s: %s", index, metrics.total_categories, category_url)
            rows, category_failed = scrape_one_category(
                session,
                category_url,
                metrics,
                failed_pages_dir,
            )
            LOGGER.info("Livres trouves pour la categorie: %s", len(rows))

            if category_failed:
                metrics.failed_categories += 1
                metrics.failed_category_urls.append(category_url)

            all_rows.extend(rows)

    metrics.total_books_scraped = len(all_rows)
    return ScrapeResult(books=all_rows, metrics=metrics)
