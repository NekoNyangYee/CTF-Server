from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.competition import read_settings, score_entries, lock_competition
from app.database import get_db
from app.schemas import TeamRequest, UserUpdateRequest
from app.security import hash_password, require_admin

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])
page_router = APIRouter()


@page_router.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(Path(__file__).parent / "static" / "admin.html")


@router.get("/users")
def users(keyword: str = "", page: int = Query(1, ge=1),
          size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    params = {"keyword": f"%{keyword}%", "limit": size, "offset": (page - 1) * size}
    where = "WHERE u.username LIKE :keyword OR u.nickname LIKE :keyword"
    total = db.execute(text(f"SELECT COUNT(*) FROM users u {where}"), params).scalar_one()
    rows = db.execute(text(f"""
        SELECT u.id, u.username, u.nickname, u.is_active, u.team_id,
               t.name AS team_name, u.created_at
        FROM users u LEFT JOIN teams t ON t.id = u.team_id {where}
        ORDER BY u.id DESC LIMIT :limit OFFSET :offset
    """), params).mappings()
    return {"success": True, "data": {"items": [dict(r) for r in rows],
            "page": page, "size": size, "total": total}}


@router.patch("/users/{user_id}")
def update_user(user_id: int, payload: UserUpdateRequest, db: Session = Depends(get_db)):
    lock_competition(db)
    if not db.execute(text("SELECT id FROM users WHERE id = :id"), {"id": user_id}).first():
        raise HTTPException(404, "User not found")
    values: dict[str, Any] = payload.model_dump(exclude_unset=True)
    if any(values.get(key) is None for key in values if key != "team_id"):
        raise HTTPException(422, "Only team_id may be null")
    if values.get("team_id") is not None:
        if not db.execute(text("SELECT id FROM teams WHERE id = :id"),
                          {"id": values["team_id"]}).first():
            raise HTTPException(404, "Team not found")
    if "password" in values:
        values["password_hash"] = hash_password(values.pop("password"))
    if values:
        assignments = ", ".join(f"{key} = :{key}" for key in values)
        try:
            db.execute(text(f"UPDATE users SET {assignments} WHERE id = :id"),
                       {**values, "id": user_id})
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(409, "User update conflicts with current data") from exc
    return {"success": True, "data": {"id": user_id, "message": "updated"}}


@router.get("/teams")
def teams(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT t.id, t.name, COUNT(u.id) AS member_count
        FROM teams t LEFT JOIN users u ON u.team_id = t.id
        GROUP BY t.id, t.name ORDER BY t.name, t.id
    """)).mappings()
    return {"success": True, "data": [dict(r) for r in rows]}


@router.post("/teams", status_code=201)
def create_team(payload: TeamRequest, db: Session = Depends(get_db)):
    lock_competition(db)
    try:
        result = db.execute(text("INSERT INTO teams (name) VALUES (:name)"), payload.model_dump())
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Team name already exists") from exc
    return {"success": True, "data": {"id": result.lastrowid, "name": payload.name}}


@router.put("/teams/{team_id}")
def rename_team(team_id: int, payload: TeamRequest, db: Session = Depends(get_db)):
    lock_competition(db)
    if not db.execute(text("SELECT id FROM teams WHERE id = :id"), {"id": team_id}).first():
        raise HTTPException(404, "Team not found")
    try:
        db.execute(text("UPDATE teams SET name = :name WHERE id = :id"),
                   {"id": team_id, "name": payload.name})
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Team name already exists") from exc
    return {"success": True, "data": {"id": team_id, "name": payload.name}}


@router.delete("/teams/{team_id}")
def delete_team(team_id: int, db: Session = Depends(get_db)):
    lock_competition(db)
    # Membership is cleared by ON DELETE SET NULL; individual solve history stays intact.
    result = db.execute(text("DELETE FROM teams WHERE id = :id"), {"id": team_id})
    if not result.rowcount:
        raise HTTPException(404, "Team not found")
    db.commit()
    return {"success": True, "data": {"message": "deleted"}}


@router.get("/scoreboard")
def admin_scoreboard(db: Session = Depends(get_db)):
    return {"success": True, "data": score_entries(db, read_settings(db)["competition_mode"])}
