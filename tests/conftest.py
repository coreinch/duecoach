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

from coach import core, db  # noqa: E402


@pytest.fixture(autouse=True)
def database(tmp_path):
    """A fresh, empty database for every test."""
    db.init(str(tmp_path / "coach.db"))
    core._locks.clear()
    core._recent.clear()
    core._warned.clear()
    yield
    db.close()


@pytest.fixture
def user():
    """A Telegram user who has agreed to the privacy notice and confirmed their timezone."""
    row = db.get_or_create_user("telegram", "1001", "1001", "en")
    db.set_fields(1001, consent_at=1.0, tz_set=1)
    return db.get_user(row["user_id"])
