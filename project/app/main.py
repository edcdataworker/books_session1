"""Point d'entree du pipeline avec monitoring et gestion d'erreurs avancee."""

from __future__ import annotations

from datetime import datetime
import logging
import os
from pathlib import Path
import time

from alerting import maybe_send_consecutive_failure_alert, reset_consecutive_failures
from error_codes import (
    DATA_HIGH_CATEGORY_FAILURE_RATE,
    IO_LOCK_ACQUIRE_FAILED,
    PARSE_HIGH_ERROR_RATE,
    PARSE_ZERO_BOOKS,
    PARSE_ZERO_CATEGORIES,
    log_with_code,
)
from logging_config import setup_logging
from runtime_lock import LockAcquisitionError, RuntimeLock
from scraper import ScrapeMetrics, scrape_books_in_category_order
from transform import build_output_rows, write_books_csv


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
OUTPUT_CSV = DATA_DIR / "books.csv"
REJECTED_ROWS_FILE = DATA_DIR / "rejected_rows.jsonl"
FAILED_PAGES_DIR = DATA_DIR / "failed_pages"
LOCK_FILE = DATA_DIR / "pipeline.lock"
STATE_FILE = DATA_DIR / "pipeline_state.json"

ALERT_CONSECUTIVE_FAILURES = int(os.getenv("ALERT_CONSECUTIVE_FAILURES", "3"))
CATEGORY_FAILURE_RATE_THRESHOLD = float(os.getenv("CATEGORY_FAILURE_RATE_THRESHOLD", "0.30"))
PARSE_ERROR_RATE_THRESHOLD = float(os.getenv("PARSE_ERROR_RATE_THRESHOLD", "0.20"))


def _log_selector_hit_rates(metrics: ScrapeMetrics, logger: logging.Logger) -> None:
    keys = sorted(set(metrics.selector_attempts) | set(metrics.selector_hits))
    for key in keys:
        attempts = int(metrics.selector_attempts.get(key, 0))
        hits = int(metrics.selector_hits.get(key, 0))
        rate = (hits / attempts * 100.0) if attempts > 0 else 0.0
        logger.info(
            "Selector hit-rate | field=%s | hits=%s | attempts=%s | rate=%.2f%%",
            key,
            hits,
            attempts,
            rate,
        )


def _evaluate_run_health(metrics: ScrapeMetrics, total_valid_rows: int, logger: logging.Logger) -> list[str]:
    failures: list[str] = []

    if metrics.total_categories == 0:
        log_with_code(
            logger,
            logging.ERROR,
            PARSE_ZERO_CATEGORIES,
            "Aucune categorie detectee (garde-fou de rupture)",
        )
        failures.append("zero_categories")

    if total_valid_rows == 0:
        log_with_code(
            logger,
            logging.ERROR,
            PARSE_ZERO_BOOKS,
            "Aucun livre valide produit (garde-fou de rupture)",
        )
        failures.append("zero_books")

    if metrics.total_categories > 0:
        category_failure_rate = metrics.failed_categories / metrics.total_categories
        if category_failure_rate > CATEGORY_FAILURE_RATE_THRESHOLD:
            log_with_code(
                logger,
                logging.ERROR,
                DATA_HIGH_CATEGORY_FAILURE_RATE,
                "Taux d'echec categories trop eleve: %.2f%% (seuil=%.2f%%)",
                category_failure_rate * 100,
                CATEGORY_FAILURE_RATE_THRESHOLD * 100,
            )
            failures.append("category_failure_rate")

    parse_error_rate_denominator = max(metrics.total_cards_seen, 1)
    parse_error_rate = metrics.parse_errors / parse_error_rate_denominator
    if parse_error_rate > PARSE_ERROR_RATE_THRESHOLD:
        log_with_code(
            logger,
            logging.ERROR,
            PARSE_HIGH_ERROR_RATE,
            "Taux d'erreur parsing trop eleve: %.2f%% (seuil=%.2f%%)",
            parse_error_rate * 100,
            PARSE_ERROR_RATE_THRESHOLD * 100,
        )
        failures.append("parse_error_rate")

    return failures


def _run_pipeline(logger: logging.Logger) -> None:
    started_ts = time.time()
    started_at = datetime.now()
    scraped_at = started_at.strftime("%Y-%m-%d %H:%M:%S")
    logger.info("Debut du pipeline de scraping")

    scrape_result = scrape_books_in_category_order(failed_pages_dir=FAILED_PAGES_DIR)
    metrics = scrape_result.metrics

    rows, rejected_rows = build_output_rows(
        scrape_result.books,
        scraped_at,
        rejected_file=REJECTED_ROWS_FILE,
    )
    write_books_csv(rows, OUTPUT_CSV)

    ended_at = datetime.now()
    ended_ts = time.time()
    duration = ended_at - started_at
    duration_seconds = ended_ts - started_ts

    total_livres_scrappes = metrics.total_books_scraped
    total_livres = len(rows)

    logger.info("Total livres: %s", total_livres)
    logger.info("Total livres scrappes: %s", total_livres_scrappes)
    logger.info("Livres ignores (parsing): %s", metrics.skipped_books)
    logger.info("Lignes rejetees (validation): %s", rejected_rows)
    logger.info("Categories en echec: %s/%s", metrics.failed_categories, metrics.total_categories)
    logger.info("Erreurs reseau: %s", metrics.network_errors)
    logger.info("Erreurs parsing: %s", metrics.parse_errors)
    logger.info("CSV genere: %s", OUTPUT_CSV)
    logger.info("Quarantaine JSONL: %s", REJECTED_ROWS_FILE)
    logger.info("Debut: %s", started_at.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("Fin:   %s", ended_at.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("Duree: %s", duration)
    logger.info("DureeSecondes(time.time): %.3f", duration_seconds)

    if metrics.failed_category_urls:
        logger.warning("Categories echouees: %s", metrics.failed_category_urls)

    if metrics.error_codes:
        logger.info("Compteur erreurs par code: %s", dict(metrics.error_codes))

    _log_selector_hit_rates(metrics, logger)

    failures = _evaluate_run_health(metrics, total_valid_rows=total_livres, logger=logger)
    if failures:
        failure_details = ",".join(failures)
        raise RuntimeError(f"Run considere en echec ({failure_details})")


def main() -> int:
    setup_logging(default_log_dir=DATA_DIR)
    logger = logging.getLogger(__name__)

    try:
        with RuntimeLock(LOCK_FILE):
            try:
                _run_pipeline(logger)
            except Exception as error:
                reason = type(error).__name__
                details = str(error)
                consecutive = maybe_send_consecutive_failure_alert(
                    state_file=STATE_FILE,
                    reason=reason,
                    details=details,
                    threshold=ALERT_CONSECUTIVE_FAILURES,
                )
                logger.error(
                    "Echec pipeline (consecutive_failures=%s, threshold=%s): %s",
                    consecutive,
                    ALERT_CONSECUTIVE_FAILURES,
                    error,
                )
                raise
            else:
                reset_consecutive_failures(STATE_FILE)

    except LockAcquisitionError as error:
        log_with_code(
            logger,
            logging.ERROR,
            IO_LOCK_ACQUIRE_FAILED,
            "Execution refusee: un autre pipeline est deja en cours (%s)",
            error,
        )
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
