"""Alerte simple + suivi des echecs consecutifs."""

from __future__ import annotations

from email.message import EmailMessage
import json
import logging
import os
from pathlib import Path
import smtplib

import requests

from error_codes import (
    IO_ALERT_SEND_FAILED,
    IO_STATE_READ_FAILED,
    IO_STATE_WRITE_FAILED,
    log_with_code,
)


LOGGER = logging.getLogger(__name__)


def _load_state(state_file: Path) -> dict:
    if not state_file.exists():
        return {"consecutive_failures": 0}

    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        log_with_code(
            LOGGER,
            logging.WARNING,
            IO_STATE_READ_FAILED,
            "Lecture de l'etat impossible, reinitialisation: %s",
            state_file,
            exc_info=True,
        )
        return {"consecutive_failures": 0}


def _save_state(state_file: Path, state: dict) -> None:
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        log_with_code(
            LOGGER,
            logging.ERROR,
            IO_STATE_WRITE_FAILED,
            "Ecriture de l'etat impossible: %s",
            state_file,
            exc_info=True,
        )


def _send_slack_alert(webhook_url: str, message: str) -> bool:
    try:
        response = requests.post(webhook_url, json={"text": message}, timeout=10)
        response.raise_for_status()
        return True
    except requests.RequestException:
        log_with_code(
            LOGGER,
            logging.ERROR,
            IO_ALERT_SEND_FAILED,
            "Echec envoi alerte Slack",
            exc_info=True,
        )
        return False


def _send_email_alert(message: str) -> bool:
    to_addr = os.getenv("ALERT_EMAIL_TO")
    from_addr = os.getenv("ALERT_EMAIL_FROM")
    smtp_host = os.getenv("ALERT_SMTP_HOST", "localhost")
    smtp_port = int(os.getenv("ALERT_SMTP_PORT", "25"))

    if not to_addr or not from_addr:
        return False

    email = EmailMessage()
    email["Subject"] = os.getenv("ALERT_EMAIL_SUBJECT", "[Pipeline] Alerte scraping")
    email["From"] = from_addr
    email["To"] = to_addr
    email.set_content(message)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as smtp:
            smtp.send_message(email)
        return True
    except Exception:
        log_with_code(
            LOGGER,
            logging.ERROR,
            IO_ALERT_SEND_FAILED,
            "Echec envoi alerte email",
            exc_info=True,
        )
        return False


def maybe_send_consecutive_failure_alert(
    state_file: Path,
    reason: str,
    details: str,
    threshold: int,
) -> int:
    """Incremente les echecs consecutifs, puis alerte si seuil atteint."""
    state = _load_state(state_file)
    consecutive = int(state.get("consecutive_failures", 0)) + 1

    state["consecutive_failures"] = consecutive
    state["last_failure_reason"] = reason
    state["last_failure_details"] = details
    _save_state(state_file, state)

    if consecutive < threshold:
        return consecutive

    message = (
        f"Pipeline scraping en echec.\n"
        f"Consecutive failures: {consecutive}\n"
        f"Reason: {reason}\n"
        f"Details: {details}"
    )

    sent = False
    slack_webhook = os.getenv("ALERT_SLACK_WEBHOOK_URL")
    if slack_webhook:
        sent = _send_slack_alert(slack_webhook, message) or sent

    sent = _send_email_alert(message) or sent

    if not sent:
        LOGGER.warning(
            "Alerte non envoyee (aucun canal configure ou en echec). seuil=%s consecutive=%s",
            threshold,
            consecutive,
        )

    return consecutive


def reset_consecutive_failures(state_file: Path) -> None:
    state = _load_state(state_file)
    state["consecutive_failures"] = 0
    state.pop("last_failure_reason", None)
    state.pop("last_failure_details", None)
    _save_state(state_file, state)
