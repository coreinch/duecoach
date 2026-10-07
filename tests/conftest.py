import os
import tempfile

# coach.config reads the environment when it is first imported, so set it up before anything imports coach
os.environ.update(
    LLM_API_KEY="test-key",
    DB_PATH=os.path.join(tempfile.mkdtemp(), "unused.db"),
    ALLOWED_USERS="",
    ALLOWED_USER_ID="",
    ALLOWED_USER_IDS="",
    TIMEZONE="Europe/Athens",
)

import pytest  # noqa: E402

from coach import core, db, llm, ratelimit  # noqa: E402
from coach.flows import goal_ideas  # noqa: E402


@pytest.fixture(autouse=True)
def database(tmp_path):
    """A fresh, empty database for every test."""
    db.init(str(tmp_path / "coach.db"))
    core._locks.clear()
    ratelimit.reset()
    goal_ideas._prefetched.clear()  # background goal-idea jobs belong to the event loop of the test that started them
    yield
    db.close()


@pytest.fixture(autouse=True)
def no_real_model(monkeypatch):
    """A test that forgets to replace the model fails loudly instead of calling the real gateway."""

    async def refuse(*args, **kwargs):
        raise RuntimeError("the real model must not be called from tests")

    monkeypatch.setattr(llm, "_complete", refuse)


@pytest.fixture
def user():
    """A Telegram user who has agreed to the privacy notice and confirmed their timezone."""
    row = db.get_or_create_user("telegram", "1001", "1001", "en")
    db.set_fields(1001, consent_at=1.0, tz_set=1, intake_state="done")
    return db.get_user(row["user_id"])
