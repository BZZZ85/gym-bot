import copy
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from test_auth import NOW, TOKEN, signed_data
from webapp.main import app


class Transaction:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        self.before = copy.deepcopy((self.connection.rows, self.connection.submissions))

    async def __aexit__(self, error_type, error, trace):
        if error_type:
            self.connection.rows, self.connection.submissions = self.before


class Connection:
    def __init__(self):
        self.rows, self.submissions = [], set()
        self.fail_write = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def transaction(self):
        return Transaction(self)

    async def execute(self, *args):
        return "OK"

    async def fetch(self, sql, user_id, exercise):
        # Assert the real API uses a parameter-bound ownership predicate.
        assert "WHERE user_id=$1" in sql
        return sorted([row for row in self.rows if row["user_id"] == user_id and (exercise is None or row["exercise"] == exercise)], key=lambda row: row["date"], reverse=True)

    async def fetchval(self, sql, *args):
        if sql == "SELECT 1":
            return 1
        key = tuple(args)
        if key in self.submissions:
            return None
        self.submissions.add(key)
        return args[1]

    async def executemany(self, sql, rows):
        if self.fail_write:
            raise RuntimeError("Test write failure")
        for user_id, exercise, reps, weight in rows:
            self.rows.append({"user_id": user_id, "exercise": exercise, "reps": reps, "weight": weight, "date": datetime.now(timezone.utc)})


class Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return self.connection

    async def close(self):
        pass


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.connection = Connection()
        for target in (
            patch.dict(os.environ, {"BOT_TOKEN": TOKEN, "DATABASE_URL": "postgresql://test-only"}),
            patch("webapp.auth.time.time", return_value=NOW),
            patch("webapp.main.asyncpg.create_pool", new=AsyncMock(return_value=Pool(self.connection))),
        ):
            target.start()
            self.addCleanup(target.stop)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {"X-Telegram-Init-Data": signed_data()}

    def payload(self, **extra):
        return {"exercise": "Жим лёжа", "entries": [{"weight": 52.5, "reps": 10}, {"weight": 0, "reps": 12}], "request_id": str(uuid4()), **extra}

    def test_missing_or_tampered_auth_is_rejected(self):
        self.assertEqual(self.client.get("/api/summary").status_code, 401)
        self.assertEqual(self.client.get("/api/summary", headers={"X-Telegram-Init-Data": signed_data() + "bad"}).status_code, 401)

    def test_save_then_read_back_through_summary_history_progress(self):
        self.assertEqual(self.client.post("/api/records", json=self.payload(), headers=self.headers).json()["added"], 2)
        summary = self.client.get("/api/summary", headers=self.headers).json()
        self.assertEqual(summary["today"]["sets"], 2)
        self.assertEqual(summary["today"]["volume"], 525)
        self.assertEqual(summary["total_sessions"], 1)
        history = self.client.get("/api/history", headers=self.headers)
        self.assertEqual(history.headers["Cache-Control"], "no-store")
        self.assertEqual(history.json()[0]["entries"][1]["weight"], 0)
        progress = self.client.get("/api/exercise/history", params={"exercise": "Жим лёжа"}, headers=self.headers).json()
        self.assertEqual(progress["max_weight"], 52.5)

    def test_same_request_does_not_double_save(self):
        payload = self.payload()
        self.client.post("/api/records", json=payload, headers=self.headers)
        retry = self.client.post("/api/records", json=payload, headers=self.headers)
        self.assertTrue(retry.json()["duplicate"])
        self.assertEqual(len(self.connection.rows), 2)

    def test_another_user_cannot_see_saved_records(self):
        self.client.post("/api/records", json=self.payload(), headers=self.headers)
        other = {"X-Telegram-Init-Data": signed_data(user={"id": 43})}
        self.assertEqual(self.client.get("/api/history", headers=other).json(), [])
        self.assertEqual(self.client.get("/api/exercises", headers=other).json(), [])
        self.assertEqual(self.client.get("/api/summary", headers=other).json()["total_sessions"], 0)

    def test_invalid_payload_cannot_create_records(self):
        for changes in ({"exercise": "   "}, {"entries": []}, {"entries": [{"weight": -1, "reps": 10}]},
                        {"entries": [{"weight": "NaN", "reps": 10}]}, {"entries": [{"weight": 5, "reps": 1.5}]},
                        {"entries": [{"weight": 5, "reps": True}]}, {"entries": [{"weight": 5, "reps": 0}]},
                        {"user_id": 43}, {"entries": [{"weight": 5, "reps": 10}] * 31}):
            with self.subTest(changes=changes):
                response = self.client.post("/api/records", json=self.payload(**changes), headers=self.headers)
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.connection.rows, [])

    def test_failed_transaction_can_be_retried(self):
        payload = self.payload()
        self.connection.fail_write = True
        self.assertEqual(self.client.post("/api/records", json=payload, headers=self.headers).status_code, 500)
        self.assertEqual(self.connection.submissions, set())
        self.connection.fail_write = False
        self.assertEqual(self.client.post("/api/records", json=payload, headers=self.headers).json()["added"], 2)

    def test_exercise_with_slash_and_unicode_uses_query_route(self):
        self.client.post("/api/records", json=self.payload(exercise="Тяга / узкий хват"), headers=self.headers)
        response = self.client.get("/api/exercise/history", params={"exercise": "Тяга / узкий хват"}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["sessions"]), 1)

    def test_health_and_static_frontend(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/app.js").status_code, 200)


if __name__ == "__main__":
    unittest.main()
