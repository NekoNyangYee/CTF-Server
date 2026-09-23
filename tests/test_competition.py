import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.competition import scoreboard_hidden
from app.database import get_db
from app.main import app
from app.security import create_access_token, hash_flag, hash_password


class ScheduleBoundaryTests(unittest.TestCase):
    def test_start_inclusive_end_exclusive(self):
        start = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
        end = start + timedelta(hours=1)
        values = {"scoreboard_hidden_from": start.isoformat(), "scoreboard_hidden_until": end.isoformat()}
        self.assertFalse(scoreboard_hidden(values, start - timedelta(microseconds=1)))
        self.assertTrue(scoreboard_hidden(values, start))
        self.assertTrue(scoreboard_hidden(values, end - timedelta(microseconds=1)))
        self.assertFalse(scoreboard_hidden(values, end))
        self.assertFalse(scoreboard_hidden({}, start))


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL to a disposable MySQL database")
class CompetitionAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["TEST_DATABASE_URL"])
        if not cls.engine.url.database.startswith("ctf_test_"):
            raise ValueError("Test database name must start with ctf_test_")

    def setUp(self):
        schema = Path("schema.sql").read_text(encoding="utf-8")
        schema = schema[schema.index("CREATE TABLE admins"):]
        with self.engine.connect() as db:
            db.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for table in ("solves", "submissions", "challenge_files", "challenges", "users", "teams", "admins", "settings"):
                db.execute(text(f"DROP TABLE IF EXISTS {table}"))
            db.execute(text("SET FOREIGN_KEY_CHECKS=1"))
            for statement in schema.split(";"):
                if statement.strip():
                    db.execute(text(statement))
            db.execute(text("INSERT INTO admins (username, password_hash) VALUES ('admin', :hash)"), {"hash": hash_password("admin-pass")})
            db.execute(text("""INSERT INTO challenges (title, slug, category, score, flag_hash, is_public)
                VALUES ('One', 'one', 'web', 100, :flag, 1), ('Two', 'two', 'web', 100, :flag, 1)"""), {"flag": hash_flag("flag{test}")})
            db.commit()

        def database():
            with Session(self.engine) as session:
                yield session
        app.dependency_overrides[get_db] = database
        self.client = TestClient(app)
        self.admin = {"Authorization": "Bearer " + create_access_token(1, "admin")}

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()

    def register(self, username):
        response = self.client.post("/api/users/register", json={"username": username, "nickname": "same", "password": "secret"})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        return data["user"]["id"], {"Authorization": "Bearer " + data["access_token"]}

    def team(self, name):
        response = self.client.post("/api/admin/teams", headers=self.admin, json={"name": name})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["data"]["id"]

    def update_user(self, user_id, **body):
        response = self.client.patch(f"/api/admin/users/{user_id}", headers=self.admin, json=body)
        self.assertEqual(response.status_code, 200, response.text)

    def settings(self, **body):
        return self.client.put("/api/admin/settings", headers=self.admin, json=body)

    def submit(self, headers, slug="one"):
        response = self.client.post(f"/api/challenges/{slug}/submit", headers=headers, json={"flag": "flag{test}"})
        self.assertEqual(response.status_code, 200, response.text)

    def test_team_scoring_mode_switch_and_membership(self):
        a, auth_a = self.register("alice")
        b, auth_b = self.register("bob")
        self.submit(auth_a)
        self.submit(auth_b)
        self.submit(auth_b, "two")
        self.submit(auth_b)
        self.assertEqual(len(self.client.get("/api/scoreboard").json()["data"]), 2)
        team = self.team("Blue")
        self.update_user(a, team_id=team)
        self.update_user(b, team_id=team)
        self.assertEqual(self.settings(competition_mode="team").status_code, 200)
        scores = self.client.get("/api/scoreboard").json()["data"]
        self.assertEqual((len(scores), scores[0]["total_score"], scores[0]["solved_count"]), (1, 200, 2))
        self.assertTrue(self.client.get("/api/challenges/two", headers=auth_a).json()["data"]["solved"])
        self.update_user(b, team_id=None)
        self.assertEqual(self.client.get("/api/scoreboard").json()["data"][0]["total_score"], 100)
        self.assertEqual(self.client.post("/api/challenges/one/submit", headers=auth_b, json={"flag": "flag{test}"}).status_code, 403)
        self.assertEqual(self.client.get("/api/users/me", headers=auth_a).json()["data"]["team_id"], team)
        self.assertEqual(self.client.delete(f"/api/admin/teams/{team}", headers=self.admin).status_code, 200)
        self.assertIsNone(self.client.get("/api/users/me", headers=auth_a).json()["data"]["team_id"])
        self.assertEqual(self.settings(competition_mode="individual").status_code, 200)
        self.assertEqual(len(self.client.get("/api/scoreboard").json()["data"]), 2)

    def test_admin_authorization(self):
        _, user = self.register("alice")
        requests = [("GET", "/api/admin/users", None), ("PATCH", "/api/admin/users/1", {"is_active": False}),
                    ("GET", "/api/admin/teams", None), ("POST", "/api/admin/teams", {"name": "Blue"}),
                    ("PUT", "/api/admin/teams/1", {"name": "Red"}), ("DELETE", "/api/admin/teams/1", None),
                    ("PUT", "/api/admin/settings", {"competition_mode": "team"}), ("GET", "/api/admin/scoreboard", None)]
        for method, path, body in requests:
            for headers in ({}, user):
                with self.subTest(method=method, path=path, user=bool(headers)):
                    self.assertIn(self.client.request(method, path, headers=headers, json=body).status_code, (401, 403))

    def test_hide_schedule_validation_and_admin_bypass(self):
        now = datetime.now(timezone.utc)
        start, end = (now - timedelta(hours=1)).isoformat(), (now + timedelta(hours=1)).isoformat()
        self.assertEqual(self.settings(scoreboard_hidden_from=start).status_code, 422)
        self.assertEqual(self.settings(scoreboard_hidden_from=end, scoreboard_hidden_until=start).status_code, 422)
        self.assertEqual(self.settings(scoreboard_hidden_from="2026-01-01T00:00:00", scoreboard_hidden_until=end).status_code, 422)
        self.assertEqual(self.settings(scoreboard_hidden_from=start, scoreboard_hidden_until=end).status_code, 200)
        public = self.client.get("/api/scoreboard")
        self.assertEqual(public.status_code, 403)
        self.assertEqual(public.headers["cache-control"], "no-store")
        self.assertNotIn("data", public.json())
        self.assertEqual(self.client.get("/api/admin/scoreboard", headers=self.admin).status_code, 200)
        self.assertTrue(self.client.get("/api/settings").json()["data"]["scoreboard_hidden"])
        self.assertEqual(self.settings(site_name="New CTF").status_code, 200)
        self.assertEqual(self.settings(scoreboard_hidden_from=None).status_code, 422)
        self.assertEqual(self.settings(scoreboard_hidden_from=None, scoreboard_hidden_until=None).status_code, 200)
        self.assertEqual(self.client.get("/api/scoreboard").status_code, 200)
        self.assertEqual(self.settings(competition_mode="invalid").status_code, 422)

    def test_user_management_disable_and_password_reset(self):
        user_id, auth = self.register("alice")
        self.submit(auth)
        self.update_user(user_id, nickname="Changed", password="new-secret", is_active=False)
        self.assertEqual(self.client.get("/api/users/me", headers=auth).status_code, 401)
        self.assertEqual(self.client.get("/api/challenges/one", headers=auth).status_code, 403)
        self.assertEqual(self.client.post("/api/challenges/one/submit", headers=auth, json={"flag": "flag{test}"}).status_code, 401)
        self.assertEqual(self.client.post("/api/users/login", json={"username": "alice", "password": "new-secret"}).status_code, 401)
        self.assertEqual(self.client.get("/api/scoreboard").json()["data"], [])
        self.update_user(user_id, is_active=True)
        self.assertEqual(self.client.post("/api/users/login", json={"username": "alice", "password": "new-secret"}).status_code, 200)
        self.assertEqual(self.client.post("/api/users/login", json={"username": "alice", "password": "secret"}).status_code, 401)
        data = self.client.get("/api/admin/users?keyword=Changed&size=1", headers=self.admin).json()["data"]
        self.assertEqual(data["total"], 1)
        self.assertNotIn("password_hash", data["items"][0])
        self.assertEqual(self.client.patch(f"/api/admin/users/{user_id}", headers=self.admin, json={"team_id": 9999}).status_code, 404)
        self.assertEqual(self.client.patch(f"/api/admin/users/{user_id}", headers=self.admin, json={"nickname": " "}).status_code, 422)

    def test_team_validation_and_page(self):
        self.team("Blue")
        self.assertEqual(self.client.post("/api/admin/teams", headers=self.admin, json={"name": "Blue"}).status_code, 409)
        self.assertEqual(self.client.post("/api/admin/teams", headers=self.admin, json={"name": " "}).status_code, 422)
        self.assertEqual(self.client.delete("/api/admin/teams/9999", headers=self.admin).status_code, 404)
        page = self.client.get("/admin")
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="users"', page.text)


if __name__ == "__main__":
    unittest.main()
