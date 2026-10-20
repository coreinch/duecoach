# Duecoach

From what you meant to do to what you did.

Duecoach is an ADHD coach in your chat app, on Telegram (Greek and English), built around follow-through. It coaches with a structured method — goals,
small weekly objectives, barrier analysis, a toolbox of what works, a weekly review — with a 54-card playbook of strategies in
the prompt of every reply (one model call per message). New users are asked for their timezone first and then talk freely: the coach learns about them, sets goals and weekly steps
and follows up on them in conversation, saving them with tools. Once the timezone is known, the bot checks in after 30 minutes of silence
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
  the model in use; a model outage alone does not fail it.
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
| `TELEGRAM_BOT_TOKEN`, `LLM_API_KEY` | the bot's credentials (`LLM_API_KEY` is your MiniMax key) |
| `ALLOWED_USERS` | who may use the bot, e.g. `telegram:123`. **Required** unless the `OPEN_ACCESS` variable is `true`: the deploy refuses to run with it empty |
| `OPEN_ACCESS` (repository *variable*, not secret) | set to `true` to let everyone use the bot in production; it overrides `ALLOWED_USERS`. Everyone then shares your model quota and their chats are stored |
| `LLM_MODEL` | optional, defaults to `MiniMax-M3.1-Flash`. The bot uses this one model; it must support tool calling. Another OpenAI-compatible provider works via the `LLM_BASE_URL` repository variable (default `https://api.minimax.io/v1`) and its key in `LLM_API_KEY` |

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

`/help` lists them. Highlights: `/stuck`, `/plan`, `/overwhelm`, `/goals`, `/toolbox`, `/progress`, `/remind`, `/timezone`,
`/interval on|off|<minutes>`, `/morning`, `/evening`, `/language`, `/wrong` (marks the coach's last message as wrong, for the maintainer), `/privacy`, `/deletedata`.

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

Replay evals: `python -m evals [--runs 3] [case]` replays conversations that went wrong in production (`evals/cases.py`) against the **real**
model and fails if the coach repeats the mistake. It needs `LLM_API_KEY` in `.env` and costs model calls, so it is run by hand before a deploy
that changes prompts or the verifier, not in CI (locally, or from GitHub: Actions > Evals > Run workflow, which also scores the verifier with `python -m evals.verifier`). Set `LLM_VERIFIER_MODEL` (and, for another provider, `LLM_VERIFIER_BASE_URL` and `LLM_VERIFIER_API_KEY`) to judge and verify with a different model than the coach's.
Each turn also leaves a statistics row without any text (`turn_stats`: what the verifier flagged, which tools ran, how it ended; summarised
hourly in the log), and `/wrong` records which message a user reported (`flags`). Unlike those, `flagged_drafts` keeps the text of a draft
the verifier flagged (never sent), the user's message and the verifier's reasons for `FLAGGED_DRAFT_DAYS` (7; 0 keeps nothing), so the flags
can be read and labelled to measure how often the verifier is right (`python -m evals.verifier`); it is deleted with the user's data.

Layout: `duecoach/core.py` (the order a message is handled in: rate limit, consent, crisis, commands, coaching),
`duecoach/commands.py` (slash commands), `duecoach/chat.py` (the coach's reply and the
crisis answer), `duecoach/crisis.py` (the one thing the bot recognises itself: crisis wording), `duecoach/llm.py` (model calls,
tool loop), `duecoach/tools.py` and `duecoach/playbook.py` (what the coach can do and the strategy cards),
`duecoach/prompts.py` and `duecoach/strings.py` (model prompts, fixed texts in both languages), `duecoach/bot.py` (reminders, check-ins,
housekeeping, startup), `duecoach/channels/` (Telegram), `duecoach/db.py` (SQLite, numbered migrations). The Docker base image is pinned by digest; Dependabot proposes updates.

## Licence

MIT, see [LICENSE](LICENSE).
