"""NeonTracker backend API tests - health, auth, goals, progress, board, websocket."""
import asyncio
import json
import pytest
import websockets
from conftest import BASE_URL


# -------------------- Health --------------------
def test_root_health(unauth_client):
    r = unauth_client.get(f"{BASE_URL}/api/")
    assert r.status_code == 200
    assert r.json() == {"message": "NeonTracker API"}


# -------------------- Auth --------------------
class TestAuth:
    def test_session_invalid_id_returns_401(self, unauth_client):
        r = unauth_client.post(f"{BASE_URL}/api/auth/session", json={"session_id": "definitely-invalid-id-xyz"})
        assert r.status_code == 401

    def test_session_missing_id_returns_400(self, unauth_client):
        r = unauth_client.post(f"{BASE_URL}/api/auth/session", json={})
        assert r.status_code == 400

    def test_auth_me_unauthorized(self, unauth_client):
        r = unauth_client.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 401

    def test_auth_me_authorized(self, api_client, test_user):
        r = api_client.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 200
        data = r.json()
        assert data["user_id"] == test_user["user_id"]
        assert data["email"] == test_user["email"]
        assert "_id" not in data

    def test_logout(self, test_user):
        # Use a dedicated session token created here so we don't break the shared fixture
        import subprocess
        from datetime import datetime, timezone, timedelta
        token = f"test_session_logout_{int(datetime.now().timestamp()*1000)}"
        expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        created = datetime.now(timezone.utc).isoformat()
        subprocess.run(["mongosh", "--quiet", "--eval", f"""
            use('test_database');
            db.user_sessions.insertOne({{user_id:'{test_user['user_id']}', session_token:'{token}', expires_at:'{expires}', created_at:'{created}'}});
        """], capture_output=True, text=True)

        import requests
        # Logout reads cookie, not header - test both behaviors
        r = requests.post(f"{BASE_URL}/api/auth/logout", cookies={"session_token": token})
        assert r.status_code == 200
        assert r.json().get("ok") is True

        # Verify session deleted - subsequent call with that token should 401
        r2 = requests.get(f"{BASE_URL}/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r2.status_code == 401


# -------------------- Goals --------------------
class TestGoals:
    def test_get_default_goals(self, api_client, test_user):
        r = api_client.get(f"{BASE_URL}/api/goals/me")
        assert r.status_code == 200
        g = r.json()
        assert g["user_id"] == test_user["user_id"]
        assert g["base_run_km"] == 10.0
        assert g["base_pushups"] == 500
        assert g["base_pullups"] == 50
        assert g["weekly_increase"] == 0.10
        assert "start_date" in g
        assert "_id" not in g

    def test_update_goals(self, api_client, test_user):
        payload = {"base_run_km": 12.0, "base_pushups": 600, "base_pullups": 60}
        r = api_client.put(f"{BASE_URL}/api/goals/me", json=payload)
        assert r.status_code == 200
        g = r.json()
        assert g["base_run_km"] == 12.0
        assert g["base_pushups"] == 600
        assert g["base_pullups"] == 60
        assert "_id" not in g

        # Verify persistence
        r2 = api_client.get(f"{BASE_URL}/api/goals/me")
        assert r2.json()["base_run_km"] == 12.0

        # Reset back
        api_client.put(f"{BASE_URL}/api/goals/me", json={"base_run_km": 10.0, "base_pushups": 500, "base_pullups": 50})

    def test_reset_start_date(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/goals/me/reset-start")
        assert r.status_code == 200
        g = r.json()
        assert "start_date" in g
        assert "_id" not in g


# -------------------- Progress --------------------
class TestProgress:
    def test_get_default_progress(self, api_client, test_user):
        r = api_client.get(f"{BASE_URL}/api/progress/me")
        assert r.status_code == 200
        p = r.json()
        assert p["user_id"] == test_user["user_id"]
        assert p["run_km"] == 0.0
        assert p["pushups"] == 0
        assert p["pullups"] == 0
        assert "week_number" in p
        assert "_id" not in p

    def test_update_progress(self, api_client, test_user):
        payload = {"week_number": 1, "run_km": 3.5, "pushups": 100, "pullups": 10}
        r = api_client.put(f"{BASE_URL}/api/progress/me", json=payload)
        assert r.status_code == 200
        p = r.json()
        assert p["run_km"] == 3.5
        assert p["pushups"] == 100
        assert p["pullups"] == 10
        assert p["week_number"] == 1
        assert "_id" not in p

        # Verify GET returns persisted data
        r2 = api_client.get(f"{BASE_URL}/api/progress/me?week=1")
        d = r2.json()
        assert d["run_km"] == 3.5
        assert d["pushups"] == 100

    def test_update_progress_upsert(self, api_client):
        # Update twice for same week, should upsert (not duplicate)
        api_client.put(f"{BASE_URL}/api/progress/me", json={"week_number": 2, "run_km": 5.0, "pushups": 200, "pullups": 20})
        api_client.put(f"{BASE_URL}/api/progress/me", json={"week_number": 2, "run_km": 7.0, "pushups": 250, "pullups": 25})
        r = api_client.get(f"{BASE_URL}/api/progress/me?week=2")
        assert r.json()["run_km"] == 7.0
        assert r.json()["pushups"] == 250


# -------------------- Board --------------------
class TestBoard:
    def test_board_unauthorized(self, unauth_client):
        r = unauth_client.get(f"{BASE_URL}/api/board")
        assert r.status_code == 401

    def test_board_returns_users(self, api_client, test_user):
        r = api_client.get(f"{BASE_URL}/api/board")
        assert r.status_code == 200
        data = r.json()
        assert "users" in data
        assert isinstance(data["users"], list)
        # Find our test user
        me = next((u for u in data["users"] if u["user_id"] == test_user["user_id"]), None)
        assert me is not None, "test user should appear in board"
        assert "goals" in me
        assert "progress" in me
        assert "_id" not in me

    def test_board_week_math(self, api_client, test_user):
        """Week 2: base * 1.10^1 → run=11, pushups=550, pullups=55."""
        # Ensure baseline goals are set
        api_client.put(f"{BASE_URL}/api/goals/me", json={"base_run_km": 10.0, "base_pushups": 500, "base_pullups": 50})
        r = api_client.get(f"{BASE_URL}/api/board?week=2")
        assert r.status_code == 200
        users = r.json()["users"]
        me = next(u for u in users if u["user_id"] == test_user["user_id"])
        assert me["goals"]["run_km"] == 11.0
        assert me["goals"]["pushups"] == 550
        assert me["goals"]["pullups"] == 55

    def test_board_week1_math(self, api_client, test_user):
        r = api_client.get(f"{BASE_URL}/api/board?week=1")
        users = r.json()["users"]
        me = next(u for u in users if u["user_id"] == test_user["user_id"])
        assert me["goals"]["run_km"] == 10.0
        assert me["goals"]["pushups"] == 500
        assert me["goals"]["pullups"] == 50


# -------------------- WebSocket --------------------
class TestWebSocket:
    def test_ws_receives_progress_broadcast(self, test_user, api_client):
        ws_url = BASE_URL.replace("https://", "wss://").replace("http://", "ws://") + "/api/ws"

        async def runner():
            async with websockets.connect(ws_url, open_timeout=10) as ws:
                # Trigger broadcast
                await asyncio.sleep(0.5)
                api_client.put(f"{BASE_URL}/api/progress/me", json={"week_number": 3, "run_km": 1.5, "pushups": 50, "pullups": 5})
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                except asyncio.TimeoutError:
                    pytest.fail("Did not receive WebSocket broadcast within 5s")
                return json.loads(msg)

        data = asyncio.run(runner())
        assert data["type"] == "progress_updated"
        assert data["user_id"] == test_user["user_id"]
        assert data["week_number"] == 3
        assert data["run_km"] == 1.5
