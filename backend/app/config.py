import os
from pathlib import Path
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent

# Load .env from project root first, then backend/.env if present
load_dotenv(PROJECT_ROOT / ".env")
if (BACKEND_DIR / ".env").exists():
    load_dotenv(BACKEND_DIR / ".env", override=True)


def _resolve_db_path(raw_path: str) -> str:
    p = Path(raw_path)
    if not p.is_absolute():
        p = (BACKEND_DIR / p).resolve()
    return str(p)


def _parse_cors_origins(raw: str | None) -> list[str]:
    if raw is None or not raw.strip():
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


class Settings:
    JWT_SECRET: str = os.getenv("JWT_SECRET", "csir-net-cbt-dev-secret-change-in-prod")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_DAYS: int = 7

    CORS_ORIGINS: list[str] = _parse_cors_origins(os.getenv("CORS_ORIGINS"))

    LOCAL_DB_PATH: str = _resolve_db_path(os.getenv("LOCAL_DB_PATH", "../csir_net.db"))
    TURSO_DATABASE_URL: str = os.getenv("TURSO_DATABASE_URL", "").strip()
    TURSO_AUTH_TOKEN: str = os.getenv("TURSO_AUTH_TOKEN", "").strip()

    @property
    def use_turso(self) -> bool:
        return bool(self.TURSO_DATABASE_URL and self.TURSO_AUTH_TOKEN)

    @property
    def db_mode(self) -> str:
        return "turso" if self.use_turso else "local"


settings = Settings()
