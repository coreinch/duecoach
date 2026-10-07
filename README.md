# ADHD coach bot

A chat coach for people with ADHD on Telegram, Viber and WhatsApp (Greek and English). It coaches with a structured method — goals,
small weekly objectives, barrier analysis, a toolbox of what works, a weekly review — and reads a playbook of strategy cards
before every reply. Check-ins are opt-in and back off when ignored.

It is a coaching aid, not therapy or medical advice. Chats are stored in a local SQLite database and each message is sent to the
language-model provider you configure, so users must agree to a privacy notice (`/agree`) before anything is processed.

## Run it with Docker

```bash
cp .env.example .env        # fill in TELEGRAM_BOT_TOKEN, LLM_API_KEY and ALLOWED_USERS at least
docker compose up -d --build
docker compose logs -f
```

- The database lives in the `coach-data` volume (`/data/coach.db`). Back that volume up.
- `GET /healthz` on port 8080 reports 503 when the reminder or check-in loop stops; the container's healthcheck uses it.
- Viber and WhatsApp need a public HTTPS address in front of port 8080 (reverse proxy or tunnel) and `PUBLIC_URL` in `.env`.
  Telegram works without one.
- Only one instance can use a database (a lock file enforces it). Don't run the bot twice with the same Telegram token.
- The container runs as an unprivileged user with a read-only root filesystem; `docker-compose.yml` has the details.

### Moving an existing local database into Docker

```bash
docker compose stop
docker compose run --rm --no-deps -v "$PWD:/import:ro" --entrypoint sh coach -c 'cp /import/coach.db /data/coach.db'
docker compose up -d
```

(Stop any locally running `python -m coach.bot` first. The database is upgraded automatically on start.)

## Commands

`/help` lists them. Highlights: `/stuck`, `/plan`, `/overwhelm`, `/goals`, `/toolbox`, `/progress`, `/remind`, `/timezone`,
`/interval on|off|<minutes>`, `/morning`, `/evening`, `/language`, `/privacy`, `/deletedata`.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest          # unit and integration tests, no network needed
.venv/bin/ruff check . && .venv/bin/ruff format .
.venv/bin/python -m coach.bot       # run locally (needs .env)
```

Layout: `coach/core.py` (commands, consent, rate limit), `coach/llm.py` (model loop and tools), `coach/tools.py` and
`coach/playbook.py` (what the coach can do and the strategy cards), `coach/bot.py` (reminders, check-ins, startup),
`coach/channels/` (Telegram, Viber, WhatsApp), `coach/db.py` (SQLite, numbered migrations).
