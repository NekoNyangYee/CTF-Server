# Simple CTF Backend

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
