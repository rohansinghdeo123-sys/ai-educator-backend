import os
import logging
import importlib
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

logger = logging.getLogger("ai_educator.database")


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


APP_ENV = (
    os.getenv("APP_ENV")
    or os.getenv("ENVIRONMENT")
    or os.getenv("ENV")
    or ""
).strip().lower()
REQUIRE_DATABASE_URL = (
    _env_truthy("REQUIRE_DATABASE_URL")
    or APP_ENV in {"prod", "production", "staging"}
)
ALLOW_SQLITE_FALLBACK = _env_truthy("ALLOW_SQLITE_FALLBACK") or not REQUIRE_DATABASE_URL

# --------------------------------------------------
# 1. Get the raw DATABASE_URL from environment
# --------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")

# --------------------------------------------------
# 2. Determine which driver to use for PostgreSQL
# --------------------------------------------------
def _choose_driver() -> str:
    """
    Try to import pg8000 (pure Python, no compilation).
    If not available, fall back to psycopg2-binary.
    If neither is installed, dev can use SQLite only when explicitly allowed.
    """
    try:
        importlib.import_module("pg8000")
        return "pg8000"
    except ImportError:
        pass

    try:
        importlib.import_module("psycopg2")
        return "psycopg2"
    except ImportError:
        pass

    return None

# --------------------------------------------------
# 3. Build the final connection URL
# --------------------------------------------------
USE_SQLITE = False

if not DATABASE_URL:
    if not ALLOW_SQLITE_FALLBACK:
        raise RuntimeError(
            "DATABASE_URL is required in production. Set DATABASE_URL or explicitly "
            "allow local development fallback with ALLOW_SQLITE_FALLBACK=true."
        )
    USE_SQLITE = True
    DATABASE_URL = "sqlite:///./ai_educator.db"
    logger.info("DATABASE: No remote URL set – using local SQLite.")
else:
    driver = _choose_driver()
    if driver:
        # Replace the scheme so SQLAlchemy uses the chosen driver
        if DATABASE_URL.startswith("postgresql://"):
            DATABASE_URL = DATABASE_URL.replace(
                "postgresql://", f"postgresql+{driver}://"
            )
        elif DATABASE_URL.startswith("postgresql+"):
            # Keep the existing driver (if any) but we still force our preferred one
            prefix, rest = DATABASE_URL.split("://", 1)
            DATABASE_URL = f"postgresql+{driver}://" + rest
        logger.info(f"DATABASE: Using PostgreSQL driver '{driver}'.")
    else:
        if not ALLOW_SQLITE_FALLBACK:
            raise RuntimeError(
                "DATABASE_URL is set, but no PostgreSQL driver is installed. "
                "Install pg8000 or psycopg2-binary."
            )
        logger.error(
            "Neither pg8000 nor psycopg2 is installed. "
            "Falling back to SQLite. Install pg8000 to use PostgreSQL."
        )
        USE_SQLITE = True
        DATABASE_URL = "sqlite:///./ai_educator.db"

# --------------------------------------------------
# 4. Create the engine
# --------------------------------------------------
if USE_SQLITE:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=False,
    )
else:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=10,
        max_overflow=20,
        echo=False,
    )

# --------------------------------------------------
# 5. Session & Declarative Base
# --------------------------------------------------
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

class Base(DeclarativeBase):
    pass

# --------------------------------------------------
# 6. Initialisation
# --------------------------------------------------
def init_db():
    Base.metadata.create_all(bind=engine)
    logger.info("DATABASE: All tables verified/created.")

def check_db_health() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"DATABASE HEALTH CHECK FAILED: {e}")
        return False
