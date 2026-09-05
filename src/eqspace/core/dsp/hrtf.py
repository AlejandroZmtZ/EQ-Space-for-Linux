"""HRTF / SOFA impulse-response extraction for the spatial backend.

Given a SOFA file, extract stereo IR pairs for the standard virtual
speaker azimuths (±30°, ±90°, ±110°, 0°), resample them to the session
sample rate, and cache the results as ``.npy`` files under
``$XDG_CACHE_HOME/eqspace/hrtf`` (default ``~/.cache/eqspace/hrtf``).

pysofa is an optional dependency: if it is not importable,
:class:`PySofaExtractor` raises :class:`HRTFUnavailable`. All extraction
goes through the :class:`IRExtractor` protocol so tests (and future
backends) can inject a synthetic extractor without pysofa.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Protocol, Sequence, Tuple

import numpy as np

#: Standard virtual-speaker azimuths in degrees (negative = left).
SPEAKER_AZIMUTHS = (-110.0, -90.0, -30.0, 0.0, 30.0, 90.0, 110.0)

#: Tolerance in degrees when matching a SOFA measurement to an azimuth.
AZIMUTH_TOLERANCE_DEG = 2.5


class HRTFUnavailable(ImportError):
    """Raised when HRTF support is unavailable (pysofa not installed)."""


class IRExtractor(Protocol):
    """Extraction backend: returns stereo IR pairs per azimuth.

    ``extract(path, azimuths_deg)`` must return a mapping from azimuth
    (float degrees) to ``(left_ir, right_ir)`` float arrays plus the
    source sample rate of the IRs.
    """

    def extract(
        self, path: Path, azimuths_deg: Sequence[float]
    ) -> "ExtractedIRs":
        ...


@dataclass(frozen=True)
class ExtractedIRs:
    """Raw extraction result from a SOFA file."""

    sample_rate: float
    irs: Dict[float, Tuple[np.ndarray, np.ndarray]]


class PySofaExtractor:
    """IRExtractor backed by the optional ``pysofa`` package."""

    def __init__(self) -> None:
        try:
            import pysofa  # noqa: F401
        except ImportError as exc:
            raise HRTFUnavailable(
                "HRTF support requires the 'pysofa' package, which is not "
                "installed; install it to load SOFA files"
            ) from exc
        self._pysofa = pysofa

    def extract(self, path: Path, azimuths_deg: Sequence[float]) -> ExtractedIRs:
        sofa = self._pysofa.SofaFile(str(path))
        try:
            fs = float(sofa.getSamplingRate())
            positions = sofa.getSourcePosition()  # (M, 3): azimuth, elevation, distance
            irs: Dict[float, Tuple[np.ndarray, np.ndarray]] = {}
            for target in azimuths_deg:
                idx = _nearest_azimuth(positions, float(target))
                ir = sofa.getDataIR()[idx]  # (receivers, samples); 0=L, 1=R
                irs[float(target)] = (
                    np.asarray(ir[0], dtype=np.float64),
                    np.asarray(ir[1], dtype=np.float64),
                )
            return ExtractedIRs(sample_rate=fs, irs=irs)
        finally:
            close = getattr(sofa, "close", None)
            if callable(close):
                close()


def _nearest_azimuth(positions: np.ndarray, target_deg: float) -> int:
    azimuths = np.asarray(positions, dtype=np.float64)[:, 0]
    diff = np.abs(((azimuths - target_deg + 180.0) % 360.0) - 180.0)
    idx = int(np.argmin(diff))
    if diff[idx] > AZIMUTH_TOLERANCE_DEG:
        raise ValueError(
            f"no SOFA measurement within {AZIMUTH_TOLERANCE_DEG}° of "
            f"azimuth {target_deg}° (nearest is {azimuths[idx]}°)"
        )
    return idx


def default_cache_dir() -> Path:
    """IR cache directory: ``$XDG_CACHE_HOME/eqspace/hrtf``."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "eqspace" / "hrtf"


def resample_ir(ir: np.ndarray, src_fs: float, dst_fs: float) -> np.ndarray:
    """Linear-interpolation resample of an IR to the session rate."""
    if src_fs <= 0 or dst_fs <= 0:
        raise ValueError("sample rates must be positive")
    if src_fs == dst_fs:
        return np.asarray(ir, dtype=np.float64)
    ratio = dst_fs / src_fs
    out_len = max(1, int(round(len(ir) * ratio)))
    src_x = np.arange(len(ir), dtype=np.float64)
    dst_x = np.linspace(0.0, len(ir) - 1, out_len)
    return np.interp(dst_x, src_x, ir).astype(np.float64)


def _cache_key(sofa_path: Path, sample_rate: float, azimuths: Sequence[float]) -> str:
    stat = sofa_path.stat()
    payload = (
        f"{sofa_path.resolve()}:{stat.st_size}:{int(stat.st_mtime)}:"
        f"{sample_rate}:{','.join(str(a) for a in azimuths)}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def _azimuth_tag(azimuth: float) -> str:
    return f"az{azimuth:+g}".replace("-", "m").replace("+", "p").replace(".", "_")


def extract_speaker_irs(
    sofa_path: Path,
    sample_rate: float,
    azimuths_deg: Sequence[float] = SPEAKER_AZIMUTHS,
    cache_dir: Optional[Path] = None,
    extractor: Optional[IRExtractor] = None,
) -> Dict[float, Tuple[Path, Path]]:
    """Extract (or load from cache) stereo IR pairs per azimuth.

    Returns a mapping from azimuth to ``(left_npy_path, right_npy_path)``
    of IRs resampled to ``sample_rate``.
    """
    sofa_path = Path(sofa_path)
    if not sofa_path.exists():
        raise FileNotFoundError(f"SOFA file not found: {sofa_path}")
    cache_dir = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    key = _cache_key(sofa_path, sample_rate, azimuths_deg)
    entry_dir = cache_dir / key

    paths: Dict[float, Tuple[Path, Path]] = {}
    missing = False
    for az in azimuths_deg:
        tag = _azimuth_tag(float(az))
        left = entry_dir / f"{tag}_L.npy"
        right = entry_dir / f"{tag}_R.npy"
        paths[float(az)] = (left, right)
        if not (left.exists() and right.exists()):
            missing = True

    if not missing:
        return paths

    extractor = extractor or PySofaExtractor()
    extracted = extractor.extract(sofa_path, azimuths_deg)
    entry_dir.mkdir(parents=True, exist_ok=True)
    for az, (left_path, right_path) in paths.items():
        left_ir, right_ir = extracted.irs[az]
        np.save(left_path, resample_ir(left_ir, extracted.sample_rate, sample_rate))
        np.save(right_path, resample_ir(right_ir, extracted.sample_rate, sample_rate))
    return paths
