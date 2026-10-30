from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

FORBIDDEN_DATABASES = frozenset({"AIMS_local", "AthensDB", "admin", "config", "local"})

# pydantic-settings resolves a relative env_file from the process cwd. Uvicorn is
# started from backend/ and from the repo root, so pin the files to this repo.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILES = (
    _REPO_ROOT / ".env",
    _REPO_ROOT / "backend" / ".env",
)


class Settings(BaseSettings):
    """Runtime configuration. Secrets come from the environment, never from the repo."""

    model_config = SettingsConfigDict(env_file=_ENV_FILES, extra="ignore")

    mongodb_uri: str = ""
    github_app_id: str = ""
    github_app_installation_id: str = ""
    github_app_private_key_path: str = ""
    database_name: str = "fetchiq"
    github_api_base: str = "https://api.github.com"
    smtp_host: str = "smtp.hostinger.com"
    smtp_port: int = 465
    smtp_ssl: bool = True
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    imap_host: str = "imap.hostinger.com"
    imap_port: int = 993
    imap_ssl: bool = True

    @field_validator("github_app_private_key_path")
    @classmethod
    def key_path_is_relative_to_repo(cls, value: str) -> str:
        """A relative key path means the same file whichever directory uvicorn starts in."""

        cleaned = value.strip()
        if not cleaned:
            return ""
        path = Path(cleaned).expanduser()
        return str(path if path.is_absolute() else _REPO_ROOT / path)

    @field_validator("database_name")
    @classmethod
    def database_must_be_fetchiq(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned in FORBIDDEN_DATABASES:
            raise ValueError(f"database {cleaned} is not available to FetchIQ")
        if not cleaned:
            raise ValueError("database name is required")
        return cleaned
