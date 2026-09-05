"""Profile schema: versioned pydantic models for saved EQ configurations."""

from typing import Any

from pydantic import BaseModel, Field, field_validator

from eqspace.core.dsp.filter_design import EQBand, VALID_BAND_TYPES

SCHEMA_VERSION = 1


class BandModel(BaseModel):
    """JSON-serializable mirror of :class:`EQBand`."""

    band_type: str
    freq_hz: float = Field(gt=0.0)
    gain_db: float
    q: float = Field(gt=0.0)
    enabled: bool = True

    @field_validator("band_type")
    @classmethod
    def _check_band_type(cls, value: str) -> str:
        if value not in VALID_BAND_TYPES:
            raise ValueError(f"unknown band_type {value!r}; expected one of {VALID_BAND_TYPES}")
        return value

    def to_eqband(self) -> EQBand:
        return EQBand(
            band_type=self.band_type,
            freq_hz=self.freq_hz,
            gain_db=self.gain_db,
            q=self.q,
            enabled=self.enabled,
        )

    @classmethod
    def from_eqband(cls, band: EQBand) -> "BandModel":
        return cls(
            band_type=band.band_type,
            freq_hz=band.freq_hz,
            gain_db=band.gain_db,
            q=band.q,
            enabled=band.enabled,
        )


class EQProfile(BaseModel):
    """A named, versioned snapshot of the full EQ-Space configuration."""

    name: str = Field(min_length=1)
    version: int = SCHEMA_VERSION
    bands: list[BandModel] = Field(default_factory=list)
    spatial: dict[str, Any] = Field(default_factory=dict)
    mic: dict[str, Any] = Field(default_factory=dict)
    output_device: str | None = None
    volume: float = Field(default=1.0, ge=0.0, le=2.0)

    def to_bands(self) -> list[EQBand]:
        return [band.to_eqband() for band in self.bands]

    @classmethod
    def from_bands(cls, name: str, bands: list[EQBand], **kwargs: Any) -> "EQProfile":
        return cls(name=name, bands=[BandModel.from_eqband(b) for b in bands], **kwargs)
