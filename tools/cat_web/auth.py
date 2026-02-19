from __future__ import annotations

"""Flask-Login setup and user model for CAT Web."""

import bcrypt
from flask import redirect, url_for
from flask_login import LoginManager, UserMixin

import db

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "warning"


class User(UserMixin):
    def __init__(self, id: int, username: str):
        self.id = id
        self.username = username


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    row = db.get_user_by_id(int(user_id))
    if row:
        return User(row["id"], row["username"])
    return None


def init_app(app) -> None:
    login_manager.init_app(app)


def check_password(stored_hash: str, password: str) -> bool:
    if isinstance(stored_hash, str):
        stored_hash = stored_hash.encode()
    return bcrypt.checkpw(password.encode(), stored_hash)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
