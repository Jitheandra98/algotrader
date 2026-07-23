"""Repository base class. Just a typed shell to enforce DI of the Database."""

from __future__ import annotations

from persistence.db import Database


class Repository:
    def __init__(self, db: Database) -> None:
        self._db = db
