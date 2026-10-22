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
    # gemini-1.5-* was shut down; ai.google.dev/gemini-api/docs/deprecations lists 3.8 Flash for new projects.
    gemini_model: str = "gemini-3.8-flash"

    # NVIDIA NIM (Vision fallback)
    nvidia_nim_api_key: SecretStr = Field(default=SecretStr(""))
    nvidia_nim_model: str = "meta/llama-3.2-11b-vision-instruct"
    nvidia_nim_base_url: str = "https://integrate.api.nvidia.com/v1"

    # Database
    database_url: str = "sqlite+aiosqlite:///./selaras.db"

    # App
    app_base_url: str = "http://localhost:8000"
    public_base_url: str = ""   # PUBLIC_BASE_URL — domain publik HTTPS untuk webhook bila beda
    secret_key: SecretStr = Field(default=SecretStr("selaras-secret-key-32chars-minimum-demo"))

    # Auth
    admin_api_key: SecretStr = Field(default=SecretStr("admin-dev-key-12345"))
    internal_api_key: SecretStr = Field(default=SecretStr("internal-dev-key-12345"))

    # Officer login for /portal and /dashboard. Empty = no login (allowed only in demo_mode).
    portal_password: SecretStr = Field(default=SecretStr(""))
    session_ttl_hours: int = 12

    # Tokens
    case_token_ttl_hours: int = 24

    # Synthetic
    synth_seed: int = 42

    # Mode
    demo_mode: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


_INSECURE_VALUES = {
    "admin-dev-key-12345",
    "internal-dev-key-12345",
    "selaras-secret-key-32chars-minimum-demo",
    "selaras-tele-secret-999",
    "selaras-webhook-secret-token",
}


def insecure_settings(s: Settings) -> list[str]:
    """Names of settings that still hold a published default or are too weak for production."""
    problems = []
    for name in ("admin_api_key", "internal_api_key", "secret_key", "telegram_webhook_secret"):
        value = getattr(s, name).get_secret_value()
        if value in _INSECURE_VALUES or len(value) < 16:
            problems.append(name.upper())
    if not s.portal_password.get_secret_value():
        problems.append("PORTAL_PASSWORD (kosong: /portal dan /dashboard tanpa login)")
    return problems


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


_REQUIRED_RULE_KEYS = (
    "rule_version",
    "contribution",
    "tolerance",
    "extraction",
    "escalation",
    "scoring",
    "estimator",
    "planner",
    "cases",
    "thresholds",
    "features",
    "company",
    "rate_limit",
    "retention",
    "morning_message",
    "risk_labels",
    "briefing",
    "xai",
    "crossdoc",
    "voice",
    "forecast",
    "sandbox",
)


class RulesConfig:
    """Loaded once at startup from config/rules.yaml; immutable thereafter."""

    def __init__(self, path: Path) -> None:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        missing = [key for key in _REQUIRED_RULE_KEYS if key not in raw]
        if missing:
            raise RuntimeError(
                f"config/rules.yaml kehilangan bagian wajib: {', '.join(missing)}. "
                "Lengkapi file konfigurasi agar sistem dapat berjalan deterministik."
            )
        self.version = raw["rule_version"]
        self.contribution = ContributionRules(raw["contribution"])
        self.tolerance = ToleranceRules(raw["tolerance"])
        self.extraction = raw["extraction"]
        self.escalation = raw["escalation"]
        self.scoring = raw["scoring"]
        self.estimator = raw["estimator"]
        self.planner = raw["planner"]
        self.cases = raw["cases"]
        self.thresholds = raw["thresholds"]
        self.features = raw["features"]
        self.company = raw["company"]
        self.rate_limit = raw["rate_limit"]
        self.retention = raw["retention"]
        self.morning_message = raw["morning_message"]
        self.risk_labels = raw["risk_labels"]
        self.briefing = raw["briefing"]
        self.xai = raw["xai"]
        self.crossdoc = raw["crossdoc"]
        self.voice = raw["voice"]
        self.forecast = raw["forecast"]
        self.sandbox = raw["sandbox"]

    @property
    def min_independent_reports(self) -> int:
        return self.escalation["min_independent_reports"]

    @property
    def require_data_anomaly(self) -> bool:
        return self.escalation["require_data_anomaly"]

    @property
    def data_only_risk_threshold(self) -> int:
        return self.escalation["data_only_risk_threshold"]

    @property
    def size_bucket_caps(self) -> dict[str, int | None]:
        return {k: (None if v is None else int(v)) for k, v in self.company["size_buckets"].items()}

    @property
    def default_umk(self) -> int:
        return int(self.thresholds["default_umk"])


_RULES_PATH = Path(__file__).parent.parent / "config" / "rules.yaml"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


@lru_cache(maxsize=1)
def get_rules() -> RulesConfig:
    return RulesConfig(_RULES_PATH)
