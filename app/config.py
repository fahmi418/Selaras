from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    telegram_bot_token: SecretStr = Field(default=SecretStr(""))
    telegram_webhook_secret: SecretStr = Field(default=SecretStr("selaras-tele-secret-999"))

    # Gemini
    gemini_api_key: SecretStr = Field(default=SecretStr(""))
    gemini_model: str = "gemini-1.5-flash-latest"

    # NVIDIA NIM (Vision fallback)
    nvidia_nim_api_key: SecretStr = Field(default=SecretStr(""))
    nvidia_nim_model: str = "meta/muse-glimmer-30b"
    nvidia_nim_base_url: str = "https://integrate.api.nvidia.com/v1"

    # Database
    database_url: str = "sqlite+aiosqlite:///./selaras.db"

    # App
    app_base_url: str = "http://localhost:8000"
    secret_key: SecretStr = Field(default=SecretStr("selaras-secret-key-32chars-minimum-demo"))

    # Auth
    admin_api_key: SecretStr = Field(default=SecretStr("admin-dev-key-12345"))
    internal_api_key: SecretStr = Field(default=SecretStr("internal-dev-key-12345"))

    # Tokens
    case_token_ttl_hours: int = 24

    # Synthetic
    synth_seed: int = 42

    # Mode
    demo_mode: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class ContributionRules:
    total_rate: float
    employer_rate: float
    worker_rate: float
    wage_cap: int
    wage_floor: str  # "umk"

    def __init__(self, d: dict) -> None:
        self.total_rate = d["total_rate"]
        self.employer_rate = d["employer_rate"]
        self.worker_rate = d["worker_rate"]
        self.wage_cap = int(d["wage_cap"])
        self.wage_floor = d["wage_floor"]


class ToleranceRules:
    absolute_idr: int
    relative: float

    def __init__(self, d: dict) -> None:
        self.absolute_idr = int(d["absolute_idr"])
        self.relative = float(d["relative"])

    def is_within(self, actual: int, expected: int) -> bool:
        """True iff |actual - expected| ≤ max(abs_tol, rel_tol × expected)."""
        diff = abs(actual - expected)
        threshold = max(self.absolute_idr, self.relative * expected)
        return diff <= threshold


class RulesConfig:
    """Loaded once at startup from config/rules.yaml; immutable thereafter."""

    version: str
    contribution: ContributionRules
    tolerance: ToleranceRules
    escalation: dict
    scoring: dict
    planner: dict
    rate_limit: dict
    retention: dict
    risk_labels: dict

    def __init__(self, path: Path) -> None:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.version = raw["rule_version"]
        self.contribution = ContributionRules(raw["contribution"])
        self.tolerance = ToleranceRules(raw["tolerance"])
        self.escalation = raw["escalation"]
        self.scoring = raw["scoring"]
        self.planner = raw["planner"]
        self.rate_limit = raw["rate_limit"]
        self.retention = raw["retention"]
        self.risk_labels = raw["risk_labels"]

    @property
    def min_independent_reports(self) -> int:
        return self.escalation["min_independent_reports"]

    @property
    def require_data_anomaly(self) -> bool:
        return self.escalation["require_data_anomaly"]

    @property
    def data_only_risk_threshold(self) -> int:
        return self.escalation["data_only_risk_threshold"]


_RULES_PATH = Path(__file__).parent.parent / "config" / "rules.yaml"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


@lru_cache(maxsize=1)
def get_rules() -> RulesConfig:
    return RulesConfig(_RULES_PATH)
