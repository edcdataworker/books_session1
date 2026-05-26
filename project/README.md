# Books Scraper - session 2

Pipeline de scraping de `https://books.toscrape.com/`, execute dans Docker et
planifie par `cron` chaque minute.

## Architecture

```text
books.toscrape.com
        |
        v
app/scraper.py      # extraction, retries, metriques et fallbacks
        |
        v
app/transform.py    # validation, quarantaine et CSV atomique
        |
        v
data/books.csv      # sortie persistee sur l'hote
```

`app/main.py` orchestre le run, applique un verrou contre les executions
concurrentes et decline le run lorsque les seuils de qualite sont depasses.
`app/alerting.py` suit les echecs consecutifs et supporte les alertes Slack ou
email.

## Structure

```text
project/
|-- Dockerfile
|-- docker-compose.yml
|-- crontab.sh
|-- app/
|   |-- main.py
|   |-- scraper.py
|   |-- transform.py
|   |-- alerting.py
|   |-- error_codes.py
|   |-- logging_config.py
|   |-- runtime_lock.py
|   `-- requirements.txt
`-- data/
    `-- books.csv
```

## Lancer avec Docker Compose

Depuis `project/` :

```bash
docker compose up -d --build
docker compose exec -T python-app /usr/bin/python3 /app/main.py
docker compose exec -T python-app sh -lc "tail -n 40 /data/pipeline.log"
docker compose down
```

Le volume `./data:/data` conserve `books.csv` et les logs apres l'arret du
conteneur.

## Configuration utile

```text
LOG_LEVEL=INFO
ALERT_CONSECUTIVE_FAILURES=3
CATEGORY_FAILURE_RATE_THRESHOLD=0.30
PARSE_ERROR_RATE_THRESHOLD=0.20
SCRAPER_HTTP_MAX_RETRIES=3
SCRAPER_HTTP_TIMEOUT_SECONDS=20
ALERT_SLACK_WEBHOOK_URL=<url optionnelle>
```
