"""Strict finite-record spectral analysis for scalar observables.

This module intentionally implements a narrow, auditable FFT contract.  It
accepts only finite, strictly increasing, nearly uniform samples; it never
interpolates, fills gaps, or silently changes the time grid.  Every estimate
uses arithmetic-mean removal followed by a *periodic* Hann window.

Two normalizations are supported:

``amplitude``
    A one-sided coherent-tone amplitude spectrum.  Interior positive-frequency
    bins are ``2*abs(FFT)/sum(window)``; DC and the even-length Nyquist bin are
    not doubled.  A bin-centred sinusoid therefore recovers its peak amplitude,
    but a non-bin-centred sinusoid suffers Hann scalloping and spectral leakage.

``psd``
    A one-sided periodogram in ``input_unit^2/Hz``.  The two-sided definition is
    ``abs(FFT)^2 / (fs*sum(window^2))`` and interior bins are doubled.  Its bin
    sum obeys the discrete Parseval identity

    ``sum(psd)*df == sum((window*demeaned_signal)^2)/sum(window^2)``

    up to floating-point rounding.  This is the window-weighted mean-square of
    the demeaned record, not an unbiased estimate of a stochastic process.

The peak helpers are deliberately conservative.  They exclude DC, the bin
adjacent to DC, the high-frequency endpoint, and its adjacent bin.  Returned
frequencies are FFT-bin centres with resolution ``df``; no sub-bin fit is
performed.  A finite-record peak is exploratory evidence only.  It is not a
linear-stability eigenvalue, growth rate, or Floquet multiplier.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Integral
from typing import Any, Literal, Sequence

import numpy as np

from ._common import DataValidationError


SpectrumScaling = Literal["amplitude", "psd"]

_ABSOLUTE_MINIMUM_SAMPLES = 8
_DEFAULT_MINIMUM_SAMPLES = 16
_DEFAULT_UNIFORM_RTOL = 1.0e-4
_MAXIMUM_UNIFORM_RTOL = 1.0e-2
_GENERIC_UNITS = frozenset({"si", "unknown", "unspecified", "n/a", "na", "?"})
_EVIDENCE_LIMIT = (
    "Exploratory finite-record spectrum only; a spectral peak is not evidence "
    "of linear instability, exponential growth, or a Floquet multiplier."
)


def _readonly_float_vector(value: Any, name: str) -> np.ndarray:
    """Convert *value* to a defensive, read-only, real float64 vector."""

    try:
        raw = np.asarray(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DataValidationError(
            f"{name} must be a one-dimensional real sequence"
        ) from exc
    if np.iscomplexobj(raw):
        raise DataValidationError(f"{name} must be real, not complex")
    try:
        vector = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DataValidationError(f"{name} must be a one-dimensional real sequence") from exc
    if vector.ndim != 1:
        raise DataValidationError(f"{name} must be one-dimensional")
    vector = vector.copy()
    vector.setflags(write=False)
    return vector


def _validated_unit(value_unit: str) -> str:
    if not isinstance(value_unit, str) or not value_unit.strip():
        raise DataValidationError("value_unit must be a non-empty explicit unit")
    unit = value_unit.strip()
    if any(ord(character) < 32 or ord(character) == 127 for character in unit):
        raise DataValidationError("value_unit must not contain control characters")
    if unit.casefold() in _GENERIC_UNITS:
        raise DataValidationError(
            "value_unit must identify a physical dimension; use '1' for a "
            "dimensionless observable rather than a generic SI/unknown label"
        )
    return unit


def _psd_unit(value_unit: str) -> str:
    if value_unit in {"1", "dimensionless"}:
        return "1/Hz"
    return f"({value_unit})^2/Hz"


def _validated_minimum_samples(minimum_samples: int) -> int:
    if isinstance(minimum_samples, bool) or not isinstance(minimum_samples, Integral):
        raise DataValidationError("minimum_samples must be an integer")
    result = int(minimum_samples)
    if result < _ABSOLUTE_MINIMUM_SAMPLES:
        raise DataValidationError(
            f"minimum_samples must be at least {_ABSOLUTE_MINIMUM_SAMPLES}; "
            "shorter records have no useful interior peak region"
        )
    return result


def _validated_uniform_rtol(uniform_rtol: float) -> float:
    try:
        result = float(uniform_rtol)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DataValidationError("uniform_rtol must be a finite real number") from exc
    if not math.isfinite(result) or result < 0.0:
        raise DataValidationError("uniform_rtol must be finite and non-negative")
    if result > _MAXIMUM_UNIFORM_RTOL:
        raise DataValidationError(
            f"uniform_rtol must not exceed {_MAXIMUM_UNIFORM_RTOL:g}; "
            "larger jitter is not nearly uniform for this FFT API"
        )
    return result


@dataclass(frozen=True)
class SpectrumEstimate:
    """One-sided spectrum and its complete normalization/sampling contract."""

    frequencies_hz: np.ndarray
    spectral_values: np.ndarray
    scaling: SpectrumScaling
    input_unit: str
    spectrum_unit: str
    sample_count: int
    dt_s: float
    fs_hz: float
    df_hz: float
    sample_span_s: float
    fft_record_length_s: float
    window: str
    detrend: str
    coherent_gain: float
    power_gain: float
    enbw_hz: float
    enbw_bins: float
    relative_dt_jitter: float
    uniform_rtol: float

    def __post_init__(self) -> None:
        frequencies = _readonly_float_vector(self.frequencies_hz, "frequencies_hz")
        values = _readonly_float_vector(self.spectral_values, "spectral_values")
        if len(frequencies) != len(values) or len(frequencies) < 2:
            raise DataValidationError(
                "frequencies_hz and spectral_values must have the same length >= 2"
            )
        if not np.all(np.isfinite(frequencies)) or not np.all(np.isfinite(values)):
            raise DataValidationError("spectrum contains a non-finite value")
        if np.any(frequencies < 0.0) or not np.all(np.diff(frequencies) > 0.0):
            raise DataValidationError("spectrum frequencies must be non-negative and increasing")
        if np.any(values < 0.0):
            raise DataValidationError("spectrum values must be non-negative")
        if self.scaling not in {"amplitude", "psd"}:
            raise DataValidationError("scaling must be 'amplitude' or 'psd'")
        input_unit = _validated_unit(self.input_unit)
        expected_unit = input_unit if self.scaling == "amplitude" else _psd_unit(input_unit)
        if self.spectrum_unit != expected_unit:
            raise DataValidationError(
                f"spectrum_unit {self.spectrum_unit!r} does not match {expected_unit!r}"
            )
        if self.sample_count < _ABSOLUTE_MINIMUM_SAMPLES:
            raise DataValidationError("sample_count is below the absolute spectral minimum")
        for name in (
            "dt_s",
            "fs_hz",
            "df_hz",
            "sample_span_s",
            "fft_record_length_s",
            "coherent_gain",
            "power_gain",
            "enbw_hz",
            "enbw_bins",
        ):
            number = float(getattr(self, name))
            if not math.isfinite(number) or number <= 0.0:
                raise DataValidationError(f"{name} must be finite and positive")
        tolerance = _validated_uniform_rtol(self.uniform_rtol)
        try:
            jitter = float(self.relative_dt_jitter)
        except (TypeError, ValueError, OverflowError) as exc:
            raise DataValidationError(
                "relative_dt_jitter must be a finite non-negative number"
            ) from exc
        if not math.isfinite(jitter) or jitter < 0.0 or jitter > tolerance:
            raise DataValidationError("relative_dt_jitter exceeds the declared tolerance")
        object.__setattr__(self, "frequencies_hz", frequencies)
        object.__setattr__(self, "spectral_values", values)
        object.__setattr__(self, "input_unit", input_unit)
        object.__setattr__(self, "relative_dt_jitter", jitter)
        object.__setattr__(self, "uniform_rtol", tolerance)

    @property
    def metadata(self) -> dict[str, object]:
        """Return fresh, JSON-serializable analysis and unit semantics."""

        normalization = (
            "one-sided coherent-tone amplitude: 2*abs(rfft)/sum(window) for "
            "interior bins; DC and even-N Nyquist are not doubled"
            if self.scaling == "amplitude"
            else "one-sided periodogram: abs(rfft)^2/(fs*sum(window^2)); "
            "interior bins doubled; sum(PSD)*df equals the Hann-window-weighted "
            "mean-square of the demeaned record"
        )
        return {
            "sample_count": self.sample_count,
            "dt_s": self.dt_s,
            "fs_hz": self.fs_hz,
            "df_hz": self.df_hz,
            "sample_span_s": self.sample_span_s,
            "fft_record_length_s": self.fft_record_length_s,
            "sampling_relative_jitter": self.relative_dt_jitter,
            "sampling_uniform_rtol": self.uniform_rtol,
            "interpolation": "none",
            "window": self.window,
            "detrend": self.detrend,
            "coherent_gain": self.coherent_gain,
            "power_gain": self.power_gain,
            "enbw_hz": self.enbw_hz,
            "enbw_bins": self.enbw_bins,
            "scaling": self.scaling,
            "normalization": normalization,
            "time_unit": "s",
            "frequency_unit": "Hz",
            "input_value_unit": self.input_unit,
            "spectrum_value_unit": self.spectrum_unit,
            "dc_handling": "arithmetic mean removed; DC retained only as a residual bin",
            "nyquist_handling": (
                "DC and even-N Nyquist are not doubled; both spectral endpoints "
                "and their adjacent bins are excluded by peak detection"
            ),
            "peak_frequency_semantics": (
                "FFT-bin centre without sub-bin interpolation; resolution is df"
            ),
            "evidence_limit": _EVIDENCE_LIMIT,
        }


@dataclass(frozen=True)
class SpectralPeak:
    """A conservative interior FFT-bin peak, never a fitted modal frequency."""

    bin_index: int
    frequency_hz: float
    spectral_value: float
    spectral_unit: str
    resolution_hz: float
    relative_height: float
    interpolated: bool = False
    evidence_limit: str = _EVIDENCE_LIMIT

    @property
    def frequency_interval_hz(self) -> tuple[float, float]:
        """Nominal half-bin interval, not a statistical confidence interval."""

        half_bin = 0.5 * self.resolution_hz
        return (self.frequency_hz - half_bin, self.frequency_hz + half_bin)


def compute_hann_spectrum(
    time_s: Sequence[float] | np.ndarray,
    values: Sequence[float] | np.ndarray,
    *,
    value_unit: str,
    scaling: SpectrumScaling = "amplitude",
    minimum_samples: int = _DEFAULT_MINIMUM_SAMPLES,
    uniform_rtol: float = _DEFAULT_UNIFORM_RTOL,
) -> SpectrumEstimate:
    """Compute a strict, one-sided periodic-Hann spectrum.

    Parameters
    ----------
    time_s, values:
        One-dimensional sequences of equal length.  ``time_s`` is explicitly in
        seconds and must be finite, strictly increasing, and nearly uniform.
    value_unit:
        Explicit physical unit of ``values``.  Use ``"1"`` for dimensionless
        data; ambiguous placeholders such as ``"SI"`` or ``"unknown"`` fail.
    scaling:
        ``"amplitude"`` for coherent-tone peak amplitude, or ``"psd"`` for a
        Parseval-normalized periodogram.
    minimum_samples:
        Record-length gate.  It may be raised by callers but never set below 8.
    uniform_rtol:
        Maximum ``max(abs(diff(time)-dt))/dt``.  It is capped at 1 percent.

    Notes
    -----
    No interpolation, gap filling, zero padding, frequency interpolation, or
    Welch averaging is performed.  ``df=1/(N*dt)``; the sample span is
    ``(N-1)*dt`` and is reported separately.
    """

    if scaling not in {"amplitude", "psd"}:
        raise DataValidationError("scaling must be 'amplitude' or 'psd'")
    unit = _validated_unit(value_unit)
    required_samples = _validated_minimum_samples(minimum_samples)
    tolerance = _validated_uniform_rtol(uniform_rtol)
    times = _readonly_float_vector(time_s, "time_s")
    samples = _readonly_float_vector(values, "values")
    if len(times) != len(samples):
        raise DataValidationError("time_s and values must have the same length")
    if len(times) < required_samples:
        raise DataValidationError(
            f"record has {len(times)} samples; at least {required_samples} are required"
        )
    if not np.all(np.isfinite(times)) or not np.all(np.isfinite(samples)):
        raise DataValidationError("time_s and values must contain only finite samples")

    with np.errstate(over="ignore", invalid="ignore"):
        intervals = np.diff(times)
    if not np.all(np.isfinite(intervals)):
        raise DataValidationError("successive time differences must be finite")
    if not np.all(intervals > 0.0):
        raise DataValidationError("time_s must be strictly increasing with no duplicates")
    dt_s = math.fsum(float(value) for value in intervals) / len(intervals)
    if not math.isfinite(dt_s) or dt_s <= 0.0:
        raise DataValidationError("mean sampling interval is not finite and positive")
    relative_jitter = float(np.max(np.abs(intervals - dt_s)) / dt_s)
    if not math.isfinite(relative_jitter) or relative_jitter > tolerance:
        raise DataValidationError(
            "sampling is not sufficiently uniform for an FFT: relative interval "
            f"jitter {relative_jitter:.6g} exceeds uniform_rtol {tolerance:.6g}; "
            "this API does not interpolate"
        )

    sample_count = len(times)
    fs_hz = 1.0 / dt_s
    df_hz = fs_hz / sample_count
    fft_record_length_s = sample_count * dt_s
    with np.errstate(over="ignore", invalid="ignore"):
        sample_span_s = float(times[-1] - times[0])
    if not all(
        math.isfinite(item) and item > 0.0
        for item in (fs_hz, df_hz, fft_record_length_s, sample_span_s)
    ):
        raise DataValidationError("derived sampling metadata is not finite and positive")

    indices = np.arange(sample_count, dtype=np.float64)
    window = 0.5 - 0.5 * np.cos(2.0 * np.pi * indices / sample_count)
    coherent_sum = float(np.sum(window))
    window_power_sum = float(np.sum(window * window))
    coherent_gain = coherent_sum / sample_count
    power_gain = window_power_sum / sample_count
    enbw_hz = fs_hz * window_power_sum / (coherent_sum * coherent_sum)
    enbw_bins = enbw_hz / df_hz

    maximum_absolute_sample = float(np.max(np.abs(samples)))
    if maximum_absolute_sample == 0.0:
        sample_mean = 0.0
    else:
        # Scaling before averaging avoids overflow for large but finite records.
        sample_mean = maximum_absolute_sample * float(
            np.mean(samples / maximum_absolute_sample)
        )
    with np.errstate(over="ignore", invalid="ignore"):
        demeaned = samples - sample_mean
        windowed = demeaned * window
    if not np.all(np.isfinite(windowed)):
        raise DataValidationError("demeaning/windowing overflowed; rescale the input values")
    transformed = np.fft.rfft(windowed)
    if not np.all(np.isfinite(transformed)):
        raise DataValidationError("FFT produced a non-finite value; rescale the input values")

    one_sided_factor = np.full(len(transformed), 2.0, dtype=np.float64)
    one_sided_factor[0] = 1.0
    if sample_count % 2 == 0:
        one_sided_factor[-1] = 1.0
    if scaling == "amplitude":
        spectral_values = one_sided_factor * np.abs(transformed) / coherent_sum
        spectrum_unit = unit
    else:
        with np.errstate(over="ignore", invalid="ignore"):
            spectral_values = (
                one_sided_factor
                * np.abs(transformed) ** 2
                / (fs_hz * window_power_sum)
            )
        spectrum_unit = _psd_unit(unit)
    if not np.all(np.isfinite(spectral_values)):
        raise DataValidationError("spectral normalization overflowed; rescale input values")
    frequencies_hz = np.fft.rfftfreq(sample_count, d=dt_s)

    return SpectrumEstimate(
        frequencies_hz=frequencies_hz,
        spectral_values=spectral_values,
        scaling=scaling,
        input_unit=unit,
        spectrum_unit=spectrum_unit,
        sample_count=sample_count,
        dt_s=dt_s,
        fs_hz=fs_hz,
        df_hz=df_hz,
        sample_span_s=sample_span_s,
        fft_record_length_s=fft_record_length_s,
        window="hann_periodic",
        detrend="arithmetic_mean",
        coherent_gain=coherent_gain,
        power_gain=power_gain,
        enbw_hz=enbw_hz,
        enbw_bins=enbw_bins,
        relative_dt_jitter=relative_jitter,
        uniform_rtol=tolerance,
    )


def find_spectral_peaks(
    spectrum: SpectrumEstimate,
    *,
    min_frequency_hz: float | None = None,
    max_frequency_hz: float | None = None,
    minimum_relative_height: float = 0.05,
    max_peaks: int | None = None,
) -> tuple[SpectralPeak, ...]:
    """Return conservative interior local maxima, strongest first.

    DC (bin 0), its adjacent bin, the high-frequency endpoint, and its adjacent
    bin are unconditionally excluded.  This guards against presenting the Hann
    main lobes of DC or Nyquist content as resolved interior peaks.  Candidates
    must be local maxima and at least ``minimum_relative_height`` times the
    strongest admissible-bin value.  Adjacent equal-height plateau candidates
    are collapsed to one lower-frequency bin.  No sub-bin interpolation is used.
    """

    if not isinstance(spectrum, SpectrumEstimate):
        raise DataValidationError("spectrum must be a SpectrumEstimate")
    try:
        relative_height = float(minimum_relative_height)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DataValidationError("minimum_relative_height must be a real number") from exc
    if not math.isfinite(relative_height) or not 0.0 <= relative_height <= 1.0:
        raise DataValidationError("minimum_relative_height must lie in [0, 1]")
    if max_peaks is not None:
        if isinstance(max_peaks, bool) or not isinstance(max_peaks, Integral):
            raise DataValidationError("max_peaks must be a positive integer or None")
        if int(max_peaks) <= 0:
            raise DataValidationError("max_peaks must be a positive integer or None")
        max_peaks = int(max_peaks)

    lower = 0.0 if min_frequency_hz is None else _finite_frequency(
        min_frequency_hz, "min_frequency_hz"
    )
    upper = math.inf if max_frequency_hz is None else _finite_frequency(
        max_frequency_hz, "max_frequency_hz"
    )
    if upper < lower:
        raise DataValidationError("max_frequency_hz must be >= min_frequency_hz")

    frequencies = spectrum.frequencies_hz
    values = spectrum.spectral_values
    # k=0 and k=1 guard DC; k=M-2 and k=M-1 guard the upper endpoint.
    admissible_indices = np.arange(2, max(2, len(values) - 2), dtype=int)
    if len(admissible_indices) == 0:
        return ()
    in_band = (
        (frequencies[admissible_indices] >= lower)
        & (frequencies[admissible_indices] <= upper)
    )
    admissible_indices = admissible_indices[in_band]
    if len(admissible_indices) == 0:
        return ()
    band_maximum = float(np.max(values[admissible_indices]))
    global_maximum = float(np.max(values))
    numerical_floor = max(
        np.finfo(np.float64).tiny,
        64.0 * np.finfo(np.float64).eps * global_maximum,
    )
    if band_maximum <= numerical_floor:
        return ()
    height_floor = max(relative_height * band_maximum, numerical_floor)

    candidates: list[int] = []
    for index in admissible_indices:
        value = values[index]
        if value < height_floor:
            continue
        left = values[index - 1]
        right = values[index + 1]
        if value >= left and value >= right and (value > left or value > right):
            candidates.append(int(index))

    # A half-bin tone can produce two equal adjacent maxima.  Treat that as one
    # unresolved main lobe rather than two physical peaks.
    collapsed: list[int] = []
    for index in candidates:
        if collapsed and index == collapsed[-1] + 1:
            previous = collapsed[-1]
            if values[index] > values[previous]:
                collapsed[-1] = index
            continue
        collapsed.append(index)

    collapsed.sort(key=lambda index: (-float(values[index]), float(frequencies[index])))
    if max_peaks is not None:
        collapsed = collapsed[:max_peaks]
    return tuple(
        SpectralPeak(
            bin_index=index,
            frequency_hz=float(frequencies[index]),
            spectral_value=float(values[index]),
            spectral_unit=spectrum.spectrum_unit,
            resolution_hz=spectrum.df_hz,
            relative_height=float(values[index] / band_maximum),
        )
        for index in collapsed
    )


def _finite_frequency(value: float, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DataValidationError(f"{name} must be a finite non-negative number") from exc
    if not math.isfinite(result) or result < 0.0:
        raise DataValidationError(f"{name} must be a finite non-negative number")
    return result


def dominant_spectral_peak(
    spectrum: SpectrumEstimate,
    *,
    min_frequency_hz: float | None = None,
    max_frequency_hz: float | None = None,
    minimum_relative_height: float = 0.05,
) -> SpectralPeak | None:
    """Return the strongest resolved-bin candidate, or ``None`` honestly."""

    peaks = find_spectral_peaks(
        spectrum,
        min_frequency_hz=min_frequency_hz,
        max_frequency_hz=max_frequency_hz,
        minimum_relative_height=minimum_relative_height,
        max_peaks=1,
    )
    return peaks[0] if peaks else None


__all__ = [
    "SpectralPeak",
    "SpectrumEstimate",
    "SpectrumScaling",
    "compute_hann_spectrum",
    "dominant_spectral_peak",
    "find_spectral_peaks",
]
