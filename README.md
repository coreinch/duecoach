# Duecoach

From what you meant to do to what you did.

Duecoach is an ADHD coach in your chat app, on Telegram, Viber and WhatsApp (Greek and English), built around follow-through. It coaches with a structured method — goals,
small weekly objectives, barrier analysis, a toolbox of what works, a weekly review — with a 54-card playbook of strategies in
the prompt of every reply (one model call per message). New users are led through a short conversation (a few intake questions,
a first goal, their timezone) and then talk freely. Once the timezone is known, the bot checks in after 30 minutes of silence
during the day and backs off when ignored (`/interval off` turns it off).

It is a coaching aid, not therapy or medical advice. Chats are stored in a local SQLite database and each message is sent to the
language-model provider you configure, so users must agree to a privacy notice (`/agree`) before anything is processed.
Messages older than `RETENTION_DAYS` (90) are deleted, people who have not written for `INACTIVE_DELETE_DAYS` (365) are deleted
entirely, `/deletedata` erases everything at once, and writing about suicide or self-harm gets a fixed safety message with
emergency numbers instead of a model reply (`CRISIS_HELP` can add a local helpline). Daily backups keep copies of the database for
7 days, so a deletion leaves the backups within a week.

## Run it with Docker

```bash
cp .env.example .env        # fill in TELEGRAM_BOT_TOKEN, LLM_API_KEY and ALLOWED_USERS at least
docker compose up -d --build
docker compose logs -f
```

- The database lives in the `data` volume (named `<project>_data`, i.e. `duecoach_data` on the server) (`/data/coach.db`). Back that volume up.
- `GET /healthz` on port 8080 reports 503 when the reminder or check-in loop stops (the container's healthcheck uses it) and shows
  which models are cooling down after failures; a model outage alone does not fail it.
- Viber and WhatsApp need a public HTTPS address in front of port 8080 (reverse proxy or tunnel) and `PUBLIC_URL` in `.env`.
  Telegram works without one.
- Only one instance can use a database (a lock file enforces it). Don't run the bot twice with the same Telegram token.
- The container runs as an unprivileged user with a read-only root filesystem; `docker-compose.yml` has the details.

### Moving an existing local database into Docker

```bash
docker compose stop
docker compose run --rm --no-deps -v "$PWD:/import:ro" --entrypoint sh duecoach -c 'cp /import/coach.db /data/coach.db'
docker compose up -d
```

(Stop any locally running `python -m duecoach.bot` first. The database is upgraded automatically on start.)

## CI/CD: GitHub Actions + Ansible to the VPS

`.github/workflows/ci-cd.yml` runs on every push and pull request to `main`:

1. **test**: `ruff check`, `ruff format --check`, `mypy`, `pip-audit` (known vulnerabilities in the pinned dependencies), `pytest`.
2. **build-and-push**: builds the Docker image (always, so a broken Dockerfile fails CI) and, on `main` only, pushes it to
   `ghcr.io/coreinch/duecoach` tagged `main-<short sha>`.
3. **deploy** (`main` only): installs Ansible, generates the inventory from the `DEPLOY_HOST` secret, and runs
   `ansible/playbooks/deploy.yml`, which renders the compose file and `.env` on the VPS, logs in to GHCR, pulls the new image,
   restarts the stack, waits for the container to report healthy (a crashing bot fails the deploy), schedules a daily database
   backup, and removes this app's old image tags (keeping 3 for rollback).

Runs on the same branch are queued, never cancelled, so the newest commit is always the one deployed last.

### Repository secrets

| Secret | Purpose |
| --- | --- |
| `DEPLOY_HOST` | VPS address (never committed; the inventory is generated in CI) |
| `DEPLOY_SSH_KEY` | private key CI uses to SSH in as root |
| `GHCR_PULL_PAT` | token with `read:packages`, so the VPS can pull the private image |
| `TELEGRAM_BOT_TOKEN`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | the bot's credentials (`LLM_API_KEY` instead, or as well, for a non-Cloudflare provider) |
| `ALLOWED_USERS` | who may use the bot, e.g. `telegram:123`. **Required** unless the `OPEN_ACCESS` variable is `true`: the deploy refuses to run with it empty |
| `OPEN_ACCESS` (repository *variable*, not secret) | set to `true` to let everyone use the bot in production; it overrides `ALLOWED_USERS`. Everyone then shares your model quota and their chats are stored |
| `LLM_MODEL` | optional, defaults to `MiniMax-M3` when `MINIMAX_API_KEY` is set, else `@cf/meta/llama-4-scout-17b-16e-instruct` (Cloudflare Workers AI, free tier; needs the `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` secrets). Models not starting with `@cf/` go to the OpenAI-compatible API at the `LLM_BASE_URL` repository variable (default: Gemini's endpoint) with `LLM_API_KEY`, which is then optional |
| `LLM_FALLBACK_MODELS` | optional, comma-separated models to try in order when `LLM_MODEL` fails (they must support tool calling); `LLM_BUDGET`, `LLM_MODEL_COOLDOWN` tune the fallback |
| `LLM_REFRESH_HOURS`, `LLM_POOL_SIZE` | optional: every `LLM_REFRESH_HOURS` (default 0 = off; written for the Kilo gateway) the free `:free` models on the gateway are probed with a Greek reply and a reminder tool call, and the `LLM_POOL_SIZE` fastest that pass are used first (auto-routers like `kilo-auto/free` are never picked). `LLM_MODEL` and `LLM_FALLBACK_MODELS` are the seed and the last resort. Preview it with `python -m duecoach.modelpicker`; `/healthz` shows the last result |
| `VIBER_AUTH_TOKEN`, `WHATSAPP_*`, `PUBLIC_URL` | optional, only for those channels |

Set them with `gh secret set NAME -R coreinch/duecoach`.

### First deploy checklist

1. Stop any other copy of the bot that uses the same Telegram token (a local `python -m duecoach.bot`, or another container):
   two pollers on one token fight each other.
2. Merge to `main`. The first deploy starts with an empty database. To carry over an existing one, copy it into the volume
   before the first start (`docker compose cp coach.db duecoach:/data/coach.db` on the VPS, then restart), otherwise users
   simply start fresh.
3. Roll back by re-running the playbook with an older tag: `ansible-playbook ... -e image_tag=main-<older sha>`.

Backups land in the volume's `backups/` folder (7 daily copies). They survive a bad deploy but not the loss of the VPS, so copy
them off the box if the data matters.

## Commands

`/help` lists them. Highlights: `/stuck`, `/plan`, `/overwhelm`, `/goals`, `/goal`, `/step`, `/toolbox`, `/progress`, `/remind`, `/timezone`,
`/interval on|off|<minutes>`, `/morning`, `/evening`, `/language`, `/privacy`, `/deletedata`.

## Telegram profile

The bot's name, descriptions and command menu (English and Greek) live in `duecoach/telegram_profile.py`. Apply them with
`python -m duecoach.telegram_profile` (needs `TELEGRAM_BOT_TOKEN`; run it again after editing the texts). Telegram limits how often the name can
change, so this is not done at start-up. The avatar is set in BotFather with `/setuserpic`.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest          # unit and integration tests, no network needed
.venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/mypy
.venv/bin/python -m duecoach.bot       # run locally (needs .env)
```

Layout: `duecoach/core.py` (the order a message is handled in: rate limit, consent, crisis, commands, flows, coaching),
`duecoach/commands.py` (slash commands), `duecoach/chat.py` (the coach's reply plus at most one question of the bot's own, and the
crisis answer), `duecoach/flows/` (the bot-led conversations, one module each: `intake`, `goals`, `followup`, `timezone`, with
`moments` deciding when to ask, `onboarding` the hand-over and `state` where a flow is stored), `duecoach/llm.py` (model calls,
fallback models, tool loop), `duecoach/tools.py` and `duecoach/playbook.py` (what the coach can do and the strategy cards),
`duecoach/prompts.py` and `duecoach/strings.py` (model prompts, fixed texts in both languages), `duecoach/bot.py` (reminders, check-ins,
housekeeping, startup), `duecoach/channels/` (Telegram, Viber, WhatsApp), `duecoach/db.py` (SQLite, numbered migrations). The Docker base image is pinned by digest; Dependabot proposes updates.

## Licence

MIT, see [LICENSE](LICENSE).
