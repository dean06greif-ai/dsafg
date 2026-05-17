import os
import subprocess
import json
import pytest
import requests
from datetime import datetime, timezone, timedelta

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://fit-goals-squad.preview.emergentagent.com").rstrip("/")


def _mongosh(script: str) -> str:
    """Run a mongosh script and return stdout."""
    r = subprocess.run(
        ["mongosh", "--quiet", "--eval", script],
        capture_output=True, text=True, timeout=15
    )
    if r.returncode != 0:
        raise RuntimeError(f"mongosh failed: {r.stderr}")
    return r.stdout.strip()


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def test_user():
    """Create a test user + session + default goals in MongoDB."""
    ts = int(datetime.now().timestamp() * 1000)
    user_id = f"test-user-{ts}"
    email = f"test.user.{ts}@example.com"
    session_token = f"test_session_{ts}"
    expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    created = datetime.now(timezone.utc).isoformat()

    script = f"""
    use('test_database');
    db.users.insertOne({{
      user_id: '{user_id}', email: '{email}', name: 'Test User Alpha',
      picture: 'https://via.placeholder.com/150', created_at: '{created}'
    }});
    db.user_sessions.insertOne({{
      user_id: '{user_id}', session_token: '{session_token}',
      expires_at: '{expires}', created_at: '{created}'
    }});
    """
    _mongosh(script)

    yield {"user_id": user_id, "email": email, "session_token": session_token}

    # Cleanup
    cleanup = f"""
    use('test_database');
    db.users.deleteMany({{user_id: '{user_id}'}});
    db.user_sessions.deleteMany({{user_id: '{user_id}'}});
    db.user_goals.deleteMany({{user_id: '{user_id}'}});
    db.progress_entries.deleteMany({{user_id: '{user_id}'}});
    """
    try:
        _mongosh(cleanup)
    except Exception:
        pass


@pytest.fixture
def api_client(test_user):
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {test_user['session_token']}",
        "Content-Type": "application/json",
    })
    return s


@pytest.fixture
def unauth_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s
