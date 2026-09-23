# Simple CTF Backend

## 팀전·개인전, 사용자 관리, 점수표 숨김 예약

기존 설치는 서버를 멈춘 후 `migrations/001_teams_users_schedule.sql`을 **한 번** 적용하고
서버를 다시 시작하세요. 신규 설치는 변경된 `schema.sql`만 실행합니다.
마이그레이션은 기존 사용자·문제·풀이 기록을 유지합니다.

```bash
mysql -u root -p ctf_platform < migrations/001_teams_users_schedule.sql
```

- `GET /api/admin/users?keyword=&page=1&size=20`: 가입 사용자 검색/페이지 조회
- `PATCH /api/admin/users/{id}`: 닉네임, 비밀번호, 활성 여부, 소속 팀 변경
  (`nickname`, `password`, `is_active`, `team_id`; 팀 해제는 `team_id: null`)
- `GET/POST /api/admin/teams`, `PUT/DELETE /api/admin/teams/{id}`: 팀 관리
- `PUT /api/admin/settings`: `competition_mode`는 `individual` 또는 `team`.
  `scoreboard_hidden_from`과 `scoreboard_hidden_until`은 시간대가 포함된 ISO 8601 시각.
  둘 다 `null`로 저장하면 예약을 해제합니다. 시작은 포함하고 종료는 포함하지 않습니다.
- `GET /api/scoreboard`: 순위 목록 반환. 숨김 시간에는 점수 데이터 없이 403과
  `hidden_until`을 반환합니다. 현재 모드 및 예약 시각은 `GET /api/settings`로 조회합니다.
- `GET /api/admin/scoreboard`: 관리자 인증 후 숨김 예약과 관계없이 점수 조회

팀은 관리자만 생성/배정할 수 있습니다. 팀전에서는 현재 소속 팀원의 기존 풀이를
문제별로 한 번만 집계하며 최초 풀이 시각을 사용합니다. 팀 이동/모드 변경 시 현재
소속을 기준으로 재계산됩니다. 팀 미배정 사용자는 팀전에서 정답을 제출할 수 없습니다.
팀원이 푼 문제는 팀 전체에 풀이 완료로 표시됩니다. 중지된 계정은 기존 토큰도
사용할 수 없으며 점수 집계에서 제외됩니다. 비밀번호 변경은 기존 토큰을 폐기하지는
않습니다. 팀 삭제 시 소속 사용자는 미배정 상태가 되며 개인 풀이 기록은 유지됩니다.

회귀 테스트는 운영 DB와 연결하지 않는 SQLite 메모리 DB를 사용합니다.
MySQL 마이그레이션 및 동시 요청 잠금은 별도 MySQL 환경에서 확인해야 합니다.

```bash
pip install httpx
python -m unittest discover -s tests -v
```

FastAPI + MySQL based CTF backend for a simple CRUD CTF service. Docker is only
needed if you choose to package the backend; there are no per-challenge
containers.

## Features

- Admin login with JWT
- Challenge CRUD
- Challenge file upload and download
- Flag submission
- Submission history
- Scoreboard

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
mysql -u root -p < schema.sql
python scripts/create_admin.py
uvicorn app.main:app --reload
```

Open API docs at `http://127.0.0.1:8000/docs`.

## API Groups

- `POST /api/auth/login`
- `GET /api/auth/me`
- `GET /api/challenges`
- `GET /api/challenges/{slug}`
- `POST /api/challenges/{slug}/submit`
- `GET /api/files/{file_id}/download`
- `GET /api/scoreboard`
- `GET /api/settings`
- `GET /api/admin/challenges`
- `POST /api/admin/challenges`
- `GET /api/admin/challenges/{id}`
- `PUT /api/admin/challenges/{id}`
- `PATCH /api/admin/challenges/{id}/flag`
- `PATCH /api/admin/challenges/{id}/visibility`
- `DELETE /api/admin/challenges/{id}`
- `POST /api/admin/challenges/{id}/files`
- `DELETE /api/admin/files/{file_id}`
- `GET /api/admin/submissions`
- `GET /api/admin/challenges/{id}/submissions`
- `PUT /api/admin/settings`

Normal JSON responses use:

```json
{
  "success": true,
  "data": {}
}
```

## Tables

- `admins`
- `challenges`
- `challenge_files`
- `submissions`
- `solves`
- `settings`

Docker/container tables and columns are intentionally not part of this schema.
