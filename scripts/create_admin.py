import getpass
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.database import get_db
from app.security import hash_password
from pymysql.err import OperationalError as PyMySQLOperationalError
from sqlalchemy.exc import OperationalError as SQLAlchemyOperationalError
from sqlalchemy import text


def explain_database_error(exc: Exception) -> None:
    message = str(exc)
    if "auth_gssapi_client" in message:
        print(
            "\nDatabase login failed: ctf_user is using MariaDB's GSSAPI "
            "authentication plugin, but PyMySQL cannot use that plugin.\n"
            "Create or alter the DB user with password authentication, for example:\n\n"
            "  CREATE USER IF NOT EXISTS 'ctf_user'@'localhost' IDENTIFIED BY 'ctf_password';\n"
            "  ALTER USER 'ctf_user'@'localhost' IDENTIFIED BY 'ctf_password';\n"
            "  GRANT ALL PRIVILEGES ON ctf_platform.* TO 'ctf_user'@'localhost';\n"
            "  FLUSH PRIVILEGES;\n"
        )
    else:
        print(f"\nDatabase login failed: {exc}")


def main() -> None:
    username = input("Admin username: ").strip()
    if not username:
        raise SystemExit("Username is required")

    password = getpass.getpass("Admin password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("Passwords do not match")

    try:
        db = next(get_db())
    except (PyMySQLOperationalError, SQLAlchemyOperationalError) as exc:
        explain_database_error(exc)
        raise SystemExit(1) from exc

    try:
        db.execute(
            text("""
                INSERT INTO admins (username, password_hash)
                VALUES (:username, :password_hash)
                ON DUPLICATE KEY UPDATE password_hash = VALUES(password_hash)
            """),
            {"username": username, "password_hash": hash_password(password)},
        )
        db.commit()
    except (PyMySQLOperationalError, SQLAlchemyOperationalError) as exc:
        db.rollback()
        explain_database_error(exc)
        raise SystemExit(1) from exc
    finally:
        db.close()

    print(f"Admin account ready: {username}")


if __name__ == "__main__":
    main()
