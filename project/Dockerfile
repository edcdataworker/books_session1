FROM python:3.9-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/local/bin/python3 /usr/bin/python3 \
    && mkdir -p /data

COPY app/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY app/ /app
COPY crontab.sh /etc/cron.d/data-pipeline-cron

RUN chmod 0644 /etc/cron.d/data-pipeline-cron \
    && crontab /etc/cron.d/data-pipeline-cron

CMD ["cron", "-f"]
