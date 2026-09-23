"""Competition settings and scoring shared by public and administrator APIs."""
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session


def read_settings(db: Session) -> dict[str, Any]:
    values = {row.setting_key: row.setting_value for row in db.execute(
        text("SELECT setting_key, setting_value FROM settings")
    )}
    values.setdefault("competition_mode", "individual")
    values.setdefault("scoreboard_hidden_from", None)
    values.setdefault("scoreboard_hidden_until", None)
    return values


def lock_competition(db: Session) -> None:
    # Close the authentication snapshot before serializing mode/member changes and submissions.
    db.commit()
    suffix = " FOR UPDATE" if db.bind.dialect.name == "mysql" else ""
    db.execute(text("SELECT setting_value FROM settings WHERE setting_key = 'competition_mode'" + suffix)).first()


def solved_for(db: Session, user: dict[str, Any], mode: str) -> dict[int, Any]:
    if mode == "team":
        if not user.get("team_id"):
            return {}
        condition, participant = "u.team_id = :participant", user["team_id"]
    else:
        condition, participant = "u.id = :participant", user["id"]
    rows = db.execute(text(f"""
        SELECT s.challenge_id, MIN(s.solved_at) AS solved_at
        FROM solves s JOIN users u ON u.id = s.user_id
        WHERE {condition} AND u.is_active = 1 GROUP BY s.challenge_id
    """), {"participant": participant}).mappings()
    return {row["challenge_id"]: row["solved_at"] for row in rows}


def scoreboard_hidden(values: dict[str, Any], now: datetime | None = None) -> bool:
    start, end = values.get("scoreboard_hidden_from"), values.get("scoreboard_hidden_until")
    if not start or not end:
        return False
    now = now or datetime.now(timezone.utc)
    return datetime.fromisoformat(start) <= now < datetime.fromisoformat(end)


def validate_schedule(values: dict[str, Any]) -> None:
    start, end = values.get("scoreboard_hidden_from"), values.get("scoreboard_hidden_until")
    if bool(start) != bool(end):
        raise HTTPException(422, "Both scoreboard hide start and end are required")
    if start and datetime.fromisoformat(start) >= datetime.fromisoformat(end):
        raise HTTPException(422, "Scoreboard hide end must be after start")


def score_entries(db: Session, mode: str) -> list[dict[str, Any]]:
    if mode == "team":
        query = """
            SELECT t.id AS team_id, t.name AS nickname, t.name AS team_name,
                   SUM(c.score) AS total_score, COUNT(*) AS solved_count,
                   MAX(s.solved_at) AS last_solved_at
            FROM (
                SELECT u.team_id, sol.challenge_id, MIN(sol.solved_at) AS solved_at
                FROM solves sol JOIN users u ON u.id = sol.user_id
                WHERE u.team_id IS NOT NULL AND u.is_active = 1
                GROUP BY u.team_id, sol.challenge_id
            ) s
            JOIN teams t ON t.id = s.team_id
            JOIN challenges c ON c.id = s.challenge_id
            WHERE c.is_public = 1
            GROUP BY t.id, t.name
            ORDER BY total_score DESC, last_solved_at ASC, t.id ASC
        """
    else:
        query = """
            SELECT s.user_id, COALESCE(u.username, s.nickname) AS username,
                   COALESCE(u.nickname, s.nickname) AS nickname,
                   SUM(c.score) AS total_score, COUNT(*) AS solved_count,
                   MAX(s.solved_at) AS last_solved_at
            FROM solves s JOIN challenges c ON c.id = s.challenge_id
            LEFT JOIN users u ON s.user_id = u.id
            WHERE c.is_public = 1 AND (u.id IS NULL OR u.is_active = 1)
            GROUP BY s.user_id, COALESCE(u.username, s.nickname), COALESCE(u.nickname, s.nickname)
            ORDER BY total_score DESC, last_solved_at ASC, nickname ASC, s.user_id ASC
        """
    entries = [dict(row) for row in db.execute(text(query)).mappings()]
    for rank, entry in enumerate(entries, 1):
        entry["rank"] = rank
    return entries
