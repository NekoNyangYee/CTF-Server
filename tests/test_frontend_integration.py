import os
import unittest
from datetime import datetime, timezone

os.environ['DATABASE_URL'] = 'sqlite://'

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import get_db
from app.competition import scoreboard_hidden as hidden_now
from app.security import create_access_token, hash_flag, hash_password


class CompetitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password('test-password')

    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        statements = [
            'CREATE TABLE admins (id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT)',
            'CREATE TABLE teams (id INTEGER PRIMARY KEY, name TEXT UNIQUE)',
            'CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, nickname TEXT, password_hash TEXT, is_active INTEGER DEFAULT 1, team_id INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP)',
            'CREATE TABLE settings (id INTEGER PRIMARY KEY, setting_key TEXT UNIQUE, setting_value TEXT)',
            'CREATE TABLE challenges (id INTEGER PRIMARY KEY, title TEXT, slug TEXT, category TEXT, description TEXT, score INTEGER, flag_hash TEXT, is_public INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP)',
            'CREATE TABLE solves (id INTEGER PRIMARY KEY, challenge_id INTEGER, user_id INTEGER, nickname TEXT, solved_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(challenge_id, user_id))',
            'CREATE TABLE submissions (id INTEGER PRIMARY KEY, challenge_id INTEGER, user_id INTEGER, nickname TEXT, submitted_flag TEXT, is_correct INTEGER, ip_address TEXT, user_agent TEXT)',
            'CREATE TABLE challenge_files (id INTEGER PRIMARY KEY, challenge_id INTEGER, original_filename TEXT, file_size INTEGER)',
        ]
        with self.engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
            connection.execute(text("INSERT INTO settings (setting_key, setting_value) VALUES ('competition_mode', 'individual')"))
            connection.execute(text("INSERT INTO admins VALUES (1, 'admin', :password)"), {'password': self.password_hash})
            connection.execute(text("INSERT INTO teams VALUES (1, '첫 팀'), (2, '둘째 팀')"))
            for user_id, team_id in [(1, 1), (2, 1), (3, None)]:
                connection.execute(text('INSERT INTO users (id, username, nickname, password_hash, team_id) VALUES (:id, :name, :name, :password, :team)'),
                                   {'id': user_id, 'name': f'user{user_id}', 'password': self.password_hash, 'team': team_id})
            for challenge_id, score in [(1, 100), (2, 200)]:
                connection.execute(text("INSERT INTO challenges (id, title, slug, category, description, score, flag_hash, is_public) VALUES (:id, '문제', :slug, 'web', '설명', :score, :flag, 1)"),
                                   {'id': challenge_id, 'slug': f'challenge-{challenge_id}', 'score': score, 'flag': hash_flag('flag{answer}')})

        def database():
            with Session(self.engine) as session:
                yield session
        app.dependency_overrides[get_db] = database
        self.client = TestClient(app)
        self.admin = {'Authorization': 'Bearer ' + create_access_token(1, 'admin')}
        self.user = {'Authorization': 'Bearer ' + create_access_token(1, 'user1', role='user')}
        self.teammate = {'Authorization': 'Bearer ' + create_access_token(2, 'user2', role='user')}
        self.unassigned = {'Authorization': 'Bearer ' + create_access_token(3, 'user3', role='user')}

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()

    def settings(self, **values):
        return self.client.put('/api/admin/settings', headers=self.admin, json=values)

    def solve(self, challenge=1, headers=None):
        return self.client.post(f'/api/challenges/challenge-{challenge}/submit', headers=headers or self.user, json={'flag': 'flag{answer}'})

    def test_admin_permissions(self):
        for method, path, payload in [
            ('get', '/api/admin/users', None), ('get', '/api/admin/teams', None), ('get', '/api/admin/scoreboard', None),
            ('put', '/api/admin/settings', {'competition_mode': 'team'}), ('post', '/api/admin/teams', {'name': 'test'}),
            ('put', '/api/admin/teams/1', {'name': 'rename'}), ('delete', '/api/admin/teams/1', None),
            ('patch', '/api/admin/users/1', {'is_active': False}),
        ]:
            for headers in [{}, self.user]:
                response = self.client.request(method, path, headers=headers, **({'json': payload} if payload is not None else {}))
                self.assertIn(response.status_code, [401, 403], (method, path, response.text))

    def test_personal_solve_and_duplicate(self):
        self.assertTrue(self.solve().json()['data']['correct'])
        self.assertTrue(self.solve().json()['data']['already_solved'])
        detail = self.client.get('/api/challenges/challenge-1', headers=self.user).json()['data']
        self.assertTrue(detail['solved'])
        self.assertFalse(detail['can_submit'])
        self.assertTrue(self.client.get('/api/challenges', headers=self.user).json()['data'][1]['solved'])
        self.assertFalse(self.client.get('/api/challenges/challenge-1', headers=self.teammate).json()['data']['solved'])
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text('SELECT COUNT(*) FROM solves')).scalar_one(), 1)
            self.assertEqual(connection.execute(text('SELECT COUNT(*) FROM submissions')).scalar_one(), 1)

    def test_team_deduplicates_existing_solves_and_shares_completion(self):
        self.solve()
        self.solve(headers=self.teammate)
        self.solve(2, self.teammate)
        self.assertEqual(self.settings(competition_mode='team').status_code, 200)
        board = self.client.get('/api/scoreboard').json()['data']
        self.assertEqual(board[0]['total_score'], 300)
        self.assertEqual(board[0]['solved_count'], 2)
        self.assertTrue(self.client.get('/api/challenges/challenge-2', headers=self.user).json()['data']['solved'])
        self.assertTrue(self.solve(2).json()['data']['already_solved'])
        self.assertEqual(self.solve(headers=self.unassigned).status_code, 403)
        self.assertFalse(self.client.get('/api/challenges/challenge-1', headers=self.unassigned).json()['data']['can_submit'])

    def test_schedule_hides_scores_and_admin_bypasses(self):
        self.solve()
        response = self.settings(scoreboard_hidden_from='2000-01-01T09:00:00+09:00', scoreboard_hidden_until='2100-01-01T09:00:00+09:00')
        self.assertEqual(response.status_code, 200, response.text)
        for headers in [{}, self.user, self.admin]:
            response = self.client.get('/api/scoreboard', headers=headers)
            self.assertEqual(response.status_code, 403)
            self.assertNotIn('data', response.json())
        self.assertEqual(self.client.get('/api/admin/scoreboard', headers=self.admin).json()['data'][0]['total_score'], 100)
        self.assertEqual(self.settings(scoreboard_hidden_from=None, scoreboard_hidden_until=None).status_code, 200)
        self.assertEqual(self.client.get('/api/scoreboard').status_code, 200)

    def test_schedule_validation_and_boundaries(self):
        for values in [
            {'scoreboard_hidden_from': '2030-01-01T00:00:00Z'},
            {'scoreboard_hidden_from': '2030-01-02T00:00:00Z', 'scoreboard_hidden_until': '2030-01-01T00:00:00Z'},
            {'scoreboard_hidden_from': '2030-01-01T00:00:00', 'scoreboard_hidden_until': '2030-01-02T00:00:00'},
            {'competition_mode': None}, {'competition_mode': 'other'},
        ]:
            self.assertEqual(self.settings(**values).status_code, 422)
        values = {'scoreboard_hidden_from': '2030-01-01T09:00:00+09:00', 'scoreboard_hidden_until': '2030-01-01T10:00:00+09:00'}
        self.assertTrue(hidden_now(values, datetime(2030, 1, 1, 0, tzinfo=timezone.utc)))
        self.assertFalse(hidden_now(values, datetime(2030, 1, 1, 1, tzinfo=timezone.utc)))

    def test_user_management_team_lifecycle_and_disabled_sessions(self):
        response = self.client.post('/api/admin/teams', headers=self.admin, json={'name': '새 팀'})
        self.assertEqual(response.status_code, 201)
        team_id = response.json()['data']['id']
        self.assertEqual(self.client.post('/api/admin/teams', headers=self.admin, json={'name': '새 팀'}).status_code, 409)
        self.assertEqual(self.client.patch('/api/admin/users/1', headers=self.admin, json={'nickname': '새 닉네임', 'team_id': team_id}).status_code, 200)
        self.assertEqual(self.client.put(f'/api/admin/teams/{team_id}', headers=self.admin, json={'name': '수정된 팀'}).status_code, 200)
        users = self.client.get('/api/admin/users?keyword=새', headers=self.admin).json()['data']
        self.assertEqual(users['total'], 1)
        self.assertEqual(users['items'][0]['team_name'], '수정된 팀')
        self.assertNotIn('password_hash', users['items'][0])
        self.assertEqual(self.client.patch('/api/admin/users/1', headers=self.admin, json={'team_id': None, 'is_active': False}).status_code, 200)
        self.assertEqual(self.client.get('/api/users/me', headers=self.user).status_code, 401)
        self.assertEqual(self.solve().status_code, 401)
        self.assertEqual(self.client.post('/api/users/login', json={'username': 'user1', 'password': 'test-password'}).status_code, 401)
        self.assertEqual(self.client.delete(f'/api/admin/teams/{team_id}', headers=self.admin).status_code, 200)
        self.assertEqual(self.client.patch('/api/admin/users/1', headers=self.admin, json={'is_active': True, 'password': 'changed-password'}).status_code, 200)
        self.assertEqual(self.client.post('/api/users/login', json={'username': 'user1', 'password': 'changed-password'}).status_code, 200)

    def test_invalid_users_and_team_fields(self):
        for data, status in [({'team_id': 999}, 404), ({'nickname': ' '}, 422), ({'is_active': None}, 422), ({'password': '한' * 25}, 422)]:
            self.assertEqual(self.client.patch('/api/admin/users/1', headers=self.admin, json=data).status_code, status)
        self.assertEqual(self.client.patch('/api/admin/users/999', headers=self.admin, json={'nickname': 'test'}).status_code, 404)


if __name__ == '__main__':
    unittest.main()
