from datetime import datetime, timezone
from pathlib import Path
import shutil
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.admin import router as admin_router, page_router
from app.competition import read_settings, scoreboard_hidden, score_entries, validate_schedule, lock_competition, solved_for
from app.config import settings
from app.database import get_db
from app.files import save_upload
from app.schemas import (
    ChallengeUpdateRequest,
    FlagUpdateRequest,
    LoginRequest,
    SettingsUpdateRequest,
    SubmitFlagRequest,
    UserRegisterRequest,
    VisibilityUpdateRequest,
)
from app.security import (
    create_access_token,
    hash_flag,
    hash_password,
    optional_user,
    require_admin,
    require_user,
    verify_flag,
    verify_password,
)

app = FastAPI(title=f"{settings.app_name} API", debug=settings.debug)
app.include_router(admin_router)
app.include_router(page_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def ok(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data}


@app.exception_handler(HTTPException)
async def formatted_http_exception_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/files/") and exc.status_code == 200:
        return await http_exception_handler(request, exc)
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "message": str(exc.detail)},
        headers=exc.headers,
    )


def bool_value(value: Any) -> bool:
    return bool(value)


def file_download_url(file_id: int) -> str:
    return f"/api/files/{file_id}/download"


def fetch_files(db: Session, challenge_id: int, admin: bool = False) -> list[dict[str, Any]]:
    columns = """
        id, original_filename, stored_filename, file_path, file_size, mime_type, created_at
    """ if admin else "id, original_filename, file_size"
    rows = db.execute(
        text(f"""
            SELECT {columns}
            FROM challenge_files
            WHERE challenge_id = :challenge_id
            ORDER BY id ASC
        """),
        {"challenge_id": challenge_id},
    ).mappings().all()
    files = [dict(row) for row in rows]
    for file in files:
        filename = file["original_filename"]
        download_url = file_download_url(file["id"])
        file["filename"] = filename
        file["name"] = filename
        file["size"] = file["file_size"]
        file["download_url"] = download_url
        file["downloadUrl"] = download_url
        file["url"] = download_url
    return files


def get_challenge_or_404(db: Session, challenge_id: int) -> dict[str, Any]:
    row = db.execute(
        text("""
            SELECT id, title, slug, category, description, score, is_public,
                   created_at, updated_at
            FROM challenges
            WHERE id = :id
        """),
        {"id": challenge_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Challenge not found")
    item = dict(row)
    item["is_public"] = bool_value(item["is_public"])
    return item


def paginated(items: list[dict[str, Any]], page: int, size: int, total: int) -> dict[str, Any]:
    return {"items": items, "page": page, "size": size, "total": total}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/auth/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    admin = db.execute(
        text("SELECT id, username, password_hash FROM admins WHERE username = :username"),
        {"username": payload.username},
    ).mappings().first()
    if not admin or not verify_password(payload.password, admin["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token_data = {
        "access_token": create_access_token(admin["id"], admin["username"]),
        "token_type": "bearer",
    }
    return {**ok(token_data), **token_data}


@app.get("/api/auth/me")
def me(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return ok({"id": admin["id"], "username": admin["username"], "role": "admin"})


@app.post("/api/users/register")
def register_user(payload: UserRegisterRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    username = payload.username.strip()
    nickname = (payload.nickname or username).strip()
    if not username:
        raise HTTPException(status_code=422, detail="Username is required")
    if not nickname:
        raise HTTPException(status_code=422, detail="Nickname is required")

    try:
        result = db.execute(
            text("""
                INSERT INTO users (username, nickname, password_hash)
                VALUES (:username, :nickname, :password_hash)
            """),
            {
                "username": username,
                "nickname": nickname,
                "password_hash": hash_password(payload.password),
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Username already exists") from exc

    token_data = {
        "access_token": create_access_token(result.lastrowid, username, role="user"),
        "token_type": "bearer",
        "user": {"id": result.lastrowid, "username": username, "nickname": nickname},
    }
    return {**ok(token_data), **token_data}


@app.post("/api/users/login")
def login_user(payload: LoginRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    user = db.execute(
        text("SELECT id, username, nickname, password_hash, is_active FROM users WHERE username = :username"),
        {"username": payload.username},
    ).mappings().first()
    if not user or not user["is_active"] or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token_data = {
        "access_token": create_access_token(user["id"], user["username"], role="user"),
        "token_type": "bearer",
        "user": {"id": user["id"], "username": user["username"], "nickname": user["nickname"]},
    }
    return {**ok(token_data), **token_data}


@app.get("/api/users/me")
def user_me(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    return ok(
        {
            "id": user["id"],
            "username": user["username"],
            "nickname": user["nickname"],
            "role": "user",
            "team_id": user["team_id"],
        }
    )


@app.get("/api/challenges")
def list_public_challenges(current_user: dict[str, Any] | None = Depends(optional_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.execute(
        text("""
            SELECT c.id, c.title, c.slug, c.category, c.score,
                   COUNT(s.id) AS solved_count, c.created_at
            FROM challenges c
            LEFT JOIN solves s ON s.challenge_id = c.id
            WHERE c.is_public = 1
            GROUP BY c.id, c.title, c.slug, c.category, c.score, c.created_at
            ORDER BY c.id DESC
        """)
    ).mappings().all()
    items = [dict(row) for row in rows]
    solved = solved_for(db, current_user, read_settings(db)["competition_mode"]) if current_user else {}
    for item in items:
        item["points"] = item["score"]
        item["solved"] = item["id"] in solved
    return ok(items)


@app.get("/api/challenges/{slug}")
def get_public_challenge(
    slug: str,
    current_user: dict[str, Any] | None = Depends(optional_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = db.execute(
        text("""
            SELECT id, title, slug, category, description, score, created_at
            FROM challenges
            WHERE slug = :slug AND is_public = 1
        """),
        {"slug": slug},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Challenge not found")

    item = dict(row)
    item["points"] = item["score"]
    item["files"] = fetch_files(db, item["id"], admin=False)
    item["solved"] = False
    item["solved_at"] = None
    mode = read_settings(db)["competition_mode"]
    if current_user:
        solved = solved_for(db, current_user, mode)
        item["solved"] = item["id"] in solved
        item["solved_at"] = solved.get(item["id"])
    item["can_submit"] = bool(current_user) and not item["solved"] and (
        mode != "team" or bool(current_user.get("team_id"))
    )
    return ok(item)


@app.post("/api/challenges/{slug}/submit")
def submit_flag(
    slug: str,
    payload: SubmitFlagRequest,
    request: Request,
    current_user: dict[str, Any] = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    lock_competition(db)
    current_user = dict(db.execute(text("SELECT id, username, nickname, team_id, is_active FROM users WHERE id = :id"),
                                   {"id": current_user["id"]}).mappings().one())
    if not current_user["is_active"]:
        raise HTTPException(401, "사용이 중지된 계정입니다.")
    mode = read_settings(db)["competition_mode"]
    if mode == "team" and not current_user["team_id"]:
        raise HTTPException(403, "팀 배정 후 정답을 제출할 수 있습니다. 관리자에게 문의하세요.")
    user_id = current_user["id"]
    nickname = (current_user["nickname"] or current_user["username"]).strip()
    flag = payload.flag.strip()
    if not flag:
        raise HTTPException(status_code=422, detail="Flag is required")

    challenge = db.execute(
        text("""
            SELECT id, flag_hash
            FROM challenges
            WHERE slug = :slug AND is_public = 1
        """),
        {"slug": slug},
    ).mappings().first()
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    if challenge["id"] in solved_for(db, current_user, mode):
        return ok({"correct": True, "already_solved": True, "solved": True, "message": "이미 푸셨습니다!"})
    correct = verify_flag(flag, challenge["flag_hash"])
    db.execute(
        text("""
            INSERT INTO submissions
                (challenge_id, user_id, nickname, submitted_flag, is_correct, ip_address, user_agent)
            VALUES
                (:challenge_id, :user_id, :nickname, :submitted_flag, :is_correct, :ip_address, :user_agent)
        """),
        {
            "challenge_id": challenge["id"],
            "user_id": user_id,
            "nickname": nickname,
            "submitted_flag": flag,
            "is_correct": int(correct),
            "ip_address": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
        },
    )

    if correct:
        db.execute(
            text("""
                INSERT INTO solves (challenge_id, user_id, nickname)
                VALUES (:challenge_id, :user_id, :nickname)
            """),
            {"challenge_id": challenge["id"], "user_id": user_id, "nickname": nickname},
        )
    db.commit()

    return ok(
        {
            "correct": correct,
            "message": "정답입니다!" if correct else "정답이 아닙니다.",
            "solved": correct,
        }
    )


@app.get("/api/files/{file_id}/download")
def download_file(file_id: int, db: Session = Depends(get_db)) -> FileResponse:
    row = db.execute(
        text("""
            SELECT f.original_filename, f.file_path
            FROM challenge_files f
            JOIN challenges c ON f.challenge_id = c.id
            WHERE f.id = :id AND c.is_public = 1
        """),
        {"id": file_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="File not found")

    path = Path(row["file_path"])
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Stored file not found")
    return FileResponse(path, filename=row["original_filename"])


@app.get("/api/scoreboard")
def scoreboard(db: Session = Depends(get_db)) -> JSONResponse:
    values = read_settings(db)
    headers = {"Cache-Control": "no-store"}
    if scoreboard_hidden(values):
        return JSONResponse(status_code=403, headers=headers, content={
            "success": False, "message": "Scoreboard is temporarily hidden",
            "hidden_until": values["scoreboard_hidden_until"],
        })
    from fastapi.encoders import jsonable_encoder
    return JSONResponse(content=jsonable_encoder(ok(score_entries(db, values["competition_mode"]))),
                        headers=headers)


@app.get("/api/settings")
def get_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    values = read_settings(db)
    values["scoreboard_hidden"] = scoreboard_hidden(values)
    return ok(values)


@app.put("/api/admin/settings")
def update_settings(
    payload: SettingsUpdateRequest,
    _: dict[str, Any] = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    # Serialize concurrent settings changes so partial schedule edits remain valid.
    lock_competition(db)
    values = payload.model_dump(exclude_unset=True)
    for key, value in list(values.items()):
        if isinstance(value, datetime):
            values[key] = value.astimezone(timezone.utc).isoformat()
        elif value is None and key not in {"scoreboard_hidden_from", "scoreboard_hidden_until"}:
            raise HTTPException(422, f"{key} may not be null")
    validate_schedule({**read_settings(db), **values})
    for key, value in values.items():
        exists = db.execute(text("SELECT id FROM settings WHERE setting_key = :key"), {"key": key}).first()
        statement = "UPDATE settings SET setting_value = :value WHERE setting_key = :key" if exists else \
            "INSERT INTO settings (setting_key, setting_value) VALUES (:key, :value)"
        db.execute(text(statement), {"key": key, "value": value})
    db.commit()
    return ok({"message": "updated"})


@app.get("/api/admin/challenges")
def list_admin_challenges(
    category: str | None = None,
    keyword: str | None = None,
    is_public: bool | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    _: dict[str, Any] = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    if category:
        conditions.append("c.category = :category")
        params["category"] = category
    if keyword:
        conditions.append("(c.title LIKE :keyword OR c.slug LIKE :keyword)")
        params["keyword"] = f"%{keyword}%"
    if is_public is not None:
        conditions.append("c.is_public = :is_public")
        params["is_public"] = int(is_public)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    total = db.execute(
        text(f"SELECT COUNT(*) AS total FROM challenges c {where}"),
        params,
    ).scalar_one()

    params["limit"] = size
    params["offset"] = (page - 1) * size
    rows = db.execute(
        text(f"""
            SELECT
                c.id, c.title, c.slug, c.category, c.score, c.is_public,
                COUNT(DISTINCT f.id) AS file_count,
                COUNT(DISTINCT sub.id) AS submission_count,
                COUNT(DISTINCT sol.id) AS solve_count,
                c.created_at, c.updated_at
            FROM challenges c
            LEFT JOIN challenge_files f ON f.challenge_id = c.id
            LEFT JOIN submissions sub ON sub.challenge_id = c.id
            LEFT JOIN solves sol ON sol.challenge_id = c.id
            {where}
            GROUP BY c.id, c.title, c.slug, c.category, c.score, c.is_public,
                     c.created_at, c.updated_at
            ORDER BY c.id DESC
            LIMIT :limit OFFSET :offset
        """),
        params,
    ).mappings().all()
    items = [dict(row) for row in rows]
    for item in items:
        item["is_public"] = bool_value(item["is_public"])
    return ok(paginated(items, page, size, int(total)))


@app.post("/api/admin/challenges")
def create_challenge(
    title: Annotated[str, Form()],
    slug: Annotated[str, Form()],
    category: Annotated[str, Form()],
    score: Annotated[int, Form()],
    flag: Annotated[str, Form()],
    description: Annotated[str | None, Form()] = None,
    is_public: Annotated[bool, Form()] = False,
    files: Annotated[list[UploadFile] | None, File()] = None,
    _: dict[str, Any] = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        result = db.execute(
            text("""
                INSERT INTO challenges
                    (title, slug, category, description, score, flag_hash, is_public)
                VALUES
                    (:title, :slug, :category, :description, :score, :flag_hash, :is_public)
            """),
            {
                "title": title,
                "slug": slug,
                "category": category,
                "description": description,
                "score": score,
                "flag_hash": hash_flag(flag),
                "is_public": int(is_public),
            },
        )
        challenge_id = result.lastrowid
        for upload in files or []:
            stored, path, size = save_upload(upload, challenge_id, slug)
            db.execute(
                text("""
                    INSERT INTO challenge_files
                        (challenge_id, original_filename, stored_filename,
                         file_path, file_size, mime_type)
                    VALUES
                        (:challenge_id, :original_filename, :stored_filename,
                         :file_path, :file_size, :mime_type)
                """),
                {
                    "challenge_id": challenge_id,
                    "original_filename": upload.filename or stored,
                    "stored_filename": stored,
                    "file_path": path,
                    "file_size": size,
                    "mime_type": upload.content_type,
                },
            )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Challenge slug already exists") from exc

    return ok({"id": challenge_id, "title": title, "slug": slug})


@app.get("/api/admin/challenges/{challenge_id}")
def get_admin_challenge(
    challenge_id: int,
    _: dict[str, Any] = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = get_challenge_or_404(db, challenge_id)
    item["files"] = fetch_files(db, challenge_id, admin=True)
    return ok(item)


@app.put("/api/admin/challenges/{challenge_id}")
def update_challenge(
    challenge_id: int,
    payload: ChallengeUpdateRequest,
    _: dict[str, Any] = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        result = db.execute(
            text("""
                UPDATE challenges
                SET title = :title,
                    slug = :slug,
                    category = :category,
                    description = :description,
                    score = :score,
                    is_public = :is_public
                WHERE id = :id
            """),
            {
                "id": challenge_id,
                "title": payload.title,
                "slug": payload.slug,
                "category": payload.category,
                "description": payload.description,
                "score": payload.score,
                "is_public": int(payload.is_public),
            },
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Challenge not found")
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Challenge slug already exists") from exc
    return ok({"id": challenge_id, "message": "updated"})


@app.patch("/api/admin/challenges/{challenge_id}/flag")
def update_challenge_flag(
    challenge_id: int,
    payload: FlagUpdateRequest,
    _: dict[str, Any] = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    result = db.execute(
        text("UPDATE challenges SET flag_hash = :flag_hash WHERE id = :id"),
        {"id": challenge_id, "flag_hash": hash_flag(payload.flag)},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Challenge not found")
    db.commit()
    return ok({"message": "flag updated"})


@app.patch("/api/admin/challenges/{challenge_id}/visibility")
def update_challenge_visibility(
    challenge_id: int,
    payload: VisibilityUpdateRequest,
    _: dict[str, Any] = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    result = db.execute(
        text("UPDATE challenges SET is_public = :is_public WHERE id = :id"),
        {"id": challenge_id, "is_public": int(payload.is_public)},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Challenge not found")
    db.commit()
    return ok({"id": challenge_id, "is_public": payload.is_public})


@app.delete("/api/admin/challenges/{challenge_id}")
def delete_challenge(
    challenge_id: int,
    _: dict[str, Any] = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    challenge = get_challenge_or_404(db, challenge_id)
    files = db.execute(
        text("SELECT file_path FROM challenge_files WHERE challenge_id = :challenge_id"),
        {"challenge_id": challenge_id},
    ).mappings().all()
    result = db.execute(text("DELETE FROM challenges WHERE id = :id"), {"id": challenge_id})
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Challenge not found")
    db.commit()

    for item in files:
        path = Path(item["file_path"])
        if path.exists() and path.is_file():
            path.unlink()
    challenge_dir = settings.upload_dir / "challenges" / f"{challenge_id}_{challenge['slug']}"
    if challenge_dir.exists() and challenge_dir.is_dir():
        shutil.rmtree(challenge_dir)
    return ok({"message": "deleted"})


@app.post("/api/admin/challenges/{challenge_id}/files")
def upload_challenge_files(
    challenge_id: int,
    files: Annotated[list[UploadFile], File()],
    _: dict[str, Any] = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    challenge = get_challenge_or_404(db, challenge_id)
    uploaded: list[dict[str, Any]] = []
    for upload in files:
        stored, path, size = save_upload(upload, challenge_id, challenge["slug"])
        result = db.execute(
            text("""
                INSERT INTO challenge_files
                    (challenge_id, original_filename, stored_filename,
                     file_path, file_size, mime_type)
                VALUES
                    (:challenge_id, :original_filename, :stored_filename,
                     :file_path, :file_size, :mime_type)
            """),
            {
                "challenge_id": challenge_id,
                "original_filename": upload.filename or stored,
                "stored_filename": stored,
                "file_path": path,
                "file_size": size,
                "mime_type": upload.content_type,
            },
        )
        uploaded.append(
            {
                "id": result.lastrowid,
                "original_filename": upload.filename or stored,
                "file_size": size,
            }
        )
    db.commit()
    return ok(uploaded)


@app.delete("/api/admin/files/{file_id}")
def delete_challenge_file(
    file_id: int,
    _: dict[str, Any] = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = db.execute(
        text("SELECT file_path FROM challenge_files WHERE id = :id"),
        {"id": file_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="File not found")
    db.execute(text("DELETE FROM challenge_files WHERE id = :id"), {"id": file_id})
    db.commit()

    path = Path(row["file_path"])
    if path.exists() and path.is_file():
        path.unlink()
    return ok({"message": "file deleted"})


@app.get("/api/admin/submissions")
def list_admin_submissions(
    challenge_id: int | None = None,
    nickname: str | None = None,
    is_correct: bool | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    _: dict[str, Any] = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    if challenge_id:
        conditions.append("s.challenge_id = :challenge_id")
        params["challenge_id"] = challenge_id
    if nickname:
        conditions.append("(s.nickname LIKE :nickname OR u.username LIKE :nickname)")
        params["nickname"] = f"%{nickname}%"
    if is_correct is not None:
        conditions.append("s.is_correct = :is_correct")
        params["is_correct"] = int(is_correct)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    total = db.execute(
        text(f"""
            SELECT COUNT(*) AS total
            FROM submissions s
            LEFT JOIN users u ON s.user_id = u.id
            {where}
        """),
        params,
    ).scalar_one()

    params["limit"] = size
    params["offset"] = (page - 1) * size
    rows = db.execute(
        text(f"""
            SELECT s.id, s.challenge_id, s.user_id, c.title AS challenge_title,
                   COALESCE(u.username, s.nickname) AS username,
                   COALESCE(u.nickname, s.nickname) AS nickname,
                   s.submitted_flag, s.is_correct, s.ip_address, s.submitted_at
            FROM submissions s
            JOIN challenges c ON s.challenge_id = c.id
            LEFT JOIN users u ON s.user_id = u.id
            {where}
            ORDER BY s.submitted_at DESC
            LIMIT :limit OFFSET :offset
        """),
        params,
    ).mappings().all()
    items = [dict(row) for row in rows]
    for item in items:
        item["is_correct"] = bool_value(item["is_correct"])
    return ok(paginated(items, page, size, int(total)))


@app.get("/api/admin/challenges/{challenge_id}/submissions")
def list_challenge_submissions(
    challenge_id: int,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    _: dict[str, Any] = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_admin_submissions(
        challenge_id=challenge_id,
        page=page,
        size=size,
        db=db,
    )


@app.get("/api/admin/challenges/{challenge_id}/solves")
def list_challenge_solves(
    challenge_id: int,
    _: dict[str, Any] = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    rows = db.execute(
        text("""
            SELECT
                s.id,
                s.challenge_id,
                s.user_id,
                COALESCE(u.username, s.nickname) AS username,
                COALESCE(u.nickname, s.nickname) AS nickname,
                s.solved_at
            FROM solves s
            LEFT JOIN users u ON s.user_id = u.id
            WHERE s.challenge_id = :challenge_id
            ORDER BY s.solved_at ASC
        """),
        {"challenge_id": challenge_id},
    ).mappings().all()
    return ok([dict(row) for row in rows])
