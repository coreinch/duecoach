FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/data/coach.db \
    WEB_HOST=0.0.0.0

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY duecoach ./duecoach

# run as an unprivileged user; /data is the only writable place (SQLite database and instance lock)
RUN useradd --system --uid 10001 --no-create-home duecoach && mkdir /data && chown duecoach /data
USER duecoach
VOLUME /data
EXPOSE 8080

# the bot serves /healthz on WEB_PORT; it turns 503 when a background loop (reminders, check-ins) stops completing passes
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.getenv('WEB_PORT', '8080'), timeout=4)"]

# umask 077: the database (private chats) and its journal files are readable only by the bot's user
CMD ["sh", "-c", "umask 077 && exec python -m duecoach.bot"]
