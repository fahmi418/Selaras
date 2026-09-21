"""
Pydantic schemas for Gemini payslip extraction output.

Field-level `confidence` (0.0–1.0) is required for every extracted value.
Any field that Gemini cannot read clearly must be returned as null, never guessed.
"""

from __future__ import annotations

from typing import Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, Field, field_validator, model_validator

T = TypeVar("T")


class ExtractionField(BaseModel, Generic[T]):
    """Generic wrapper for a single extracted value with confidence."""

    value: Optional[T] = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)


class EarningsItem(BaseModel):
    label: str
    amount: Optional[int] = None  # IDR integer; null if unreadable
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)


class DeductionItem(BaseModel):
    label: str
    amount: Optional[int] = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)


class SlipPeriod(BaseModel):
    month: Optional[int] = Field(default=None, ge=1, le=12)
    year: Optional[int] = Field(default=None, ge=2000, le=2100)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)


class SlipQuality(BaseModel):
    legible: bool = True
    skew_ok: bool = True
    notes: str = ""


class PayslipExtraction(BaseModel):
    """
    Canonical output schema from Gemini extraction.
    Downstream rule engine reads this; LLM must not add computed fields.
    """

    is_payslip: bool
    period: Optional[SlipPeriod] = None
    employer_name: Optional[ExtractionField[str]] = None
    currency: str = "IDR"
    earnings: list[EarningsItem] = Field(default_factory=list)
    deductions: list[DeductionItem] = Field(default_factory=list)
    gross_total: Optional[ExtractionField[int]] = None
    net_total: Optional[ExtractionField[int]] = None
    quality: SlipQuality = Field(default_factory=SlipQuality)

    @field_validator("earnings", "deductions", mode="before")
    @classmethod
    def _empty_list_if_none(cls, v):
        return v or []

    def jkn_deduction(self) -> Optional[DeductionItem]:
        """Return the first deduction item classified as JKN, or None."""
        from app.rules.dictionary import is_jkn_deduction  # lazy import avoids circular dep

        return next((d for d in self.deductions if is_jkn_deduction(d.label)), None)

    def min_confidence_critical(self) -> float:
        """Minimum confidence across JKN deduction and earnings items."""
        jkn = self.jkn_deduction()
        scores = [item.confidence for item in self.earnings]
        if jkn:
            scores.append(jkn.confidence)
        return min(scores, default=0.0)


class ValidationResult(BaseModel):
    """Post-extraction consistency check outcome."""

    is_consistent: bool
    sum_earnings: int
    sum_deductions: int
    reported_net: Optional[int]
    discrepancy_pct: Optional[float]  # |computed_net - reported_net| / reported_net
    warnings: list[str] = Field(default_factory=list)
