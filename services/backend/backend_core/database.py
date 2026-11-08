import os
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/omnikestrapim.db")
database_url = make_url(DATABASE_URL)
if database_url.get_backend_name() == "sqlite" and database_url.database not in {
    None,
    "",
    ":memory:",
}:
    Path(database_url.database).expanduser().resolve().parent.mkdir(
        parents=True, exist_ok=True
    )
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def run_migrations() -> None:
    if DATABASE_URL.startswith("sqlite"):
        Base.metadata.create_all(bind=engine)
        return

    config = AlembicConfig(str(Path(__file__).parent.parent / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))
    command.upgrade(config, "head")
