from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, Setting

DEFAULT_SETTINGS = {
    "default_model": "s2.1-pro",
    "price_per_m_utf8_bytes": "15.0",
    "default_output_format": "mp3",
}


class Database:
    def __init__(self, db_path: Path):
        self.engine = create_engine(
            f"sqlite:///{db_path.as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        event.listen(self.engine, "connect", self._configure_sqlite)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def _configure_sqlite(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)
        with self.Session.begin() as session:
            for key, value in DEFAULT_SETTINGS.items():
                if session.get(Setting, key) is None:
                    session.add(Setting(key=key, value=value))

    def settings(self, session: Session) -> dict[str, str]:
        result = dict(session.execute(select(Setting.key, Setting.value)).all())
        return {**DEFAULT_SETTINGS, **result}

    def close(self) -> None:
        self.engine.dispose()


def set_setting(session: Session, key: str, value: str) -> None:
    item = session.get(Setting, key)
    if item:
        item.value = value
    else:
        session.add(Setting(key=key, value=value))
