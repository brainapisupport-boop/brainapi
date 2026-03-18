from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.normalized_database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    from .models import APIKey, EmailEvent, PasswordResetToken, SignupLead, UsageEvent, UserAccount  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_schema_updates()


def _ensure_schema_updates() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "api_keys" not in tables:
        return

    columns = {col["name"] for col in inspector.get_columns("api_keys")}
    if "is_paid" in columns and "trial_ends_at" in columns:
        return

    dialect = engine.dialect.name
    statements: list[str] = []

    if "is_paid" not in columns:
        if dialect == "postgresql":
            statements.append("ALTER TABLE api_keys ADD COLUMN is_paid BOOLEAN NOT NULL DEFAULT FALSE")
        else:
            statements.append("ALTER TABLE api_keys ADD COLUMN is_paid BOOLEAN NOT NULL DEFAULT 0")

    if "trial_ends_at" not in columns:
        if dialect == "postgresql":
            statements.append("ALTER TABLE api_keys ADD COLUMN trial_ends_at TIMESTAMPTZ NULL")
        else:
            statements.append("ALTER TABLE api_keys ADD COLUMN trial_ends_at DATETIME NULL")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
