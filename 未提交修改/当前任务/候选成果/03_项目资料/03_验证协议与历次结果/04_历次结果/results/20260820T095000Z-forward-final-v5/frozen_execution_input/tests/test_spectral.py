from __future__ import annotations

import unittest

import numpy as np

from airvortex.analysis import (
    DataValidationError,
    compute_hann_spectrum,
    dominant_spectral_peak,
    find_spectral_peaks,
)


class SpectralAnalysisTests(unittest.TestCase):
    def test_bin_centered_sine_recovers_coherent_amplitude_and_metadata(self) -> None:
        sample_count = 1024
        fs_hz = 256.0
        time_s = np.arange(sample_count) / fs_hz
        amplitude_m = 0.37
        frequency_hz = 20.0
        values_m = 2.1 + amplitude_m * np.sin(2.0 * np.pi * frequency_hz * time_s)

        spectrum = compute_hann_spectrum(
            time_s, values_m, value_unit="m", scaling="amplitude"
        )
        bin_index = int(round(frequency_hz / spectrum.df_hz))
        self.assertAlmostEqual(spectrum.frequencies_hz[bin_index], frequency_hz)
        self.assertAlmostEqual(spectrum.spectral_values[bin_index], amplitude_m, places=13)
        self.assertLess(spectrum.spectral_values[0], 1.0e-14)
        self.assertEqual(spectrum.spectrum_unit, "m")
        self.assertAlmostEqual(spectrum.dt_s, 1.0 / fs_hz, places=15)
        self.assertAlmostEqual(spectrum.fs_hz, fs_hz, places=13)
        self.assertAlmostEqual(spectrum.df_hz, fs_hz / sample_count, places=15)
        self.assertAlmostEqual(spectrum.enbw_bins, 1.5, places=13)
        self.assertAlmostEqual(spectrum.coherent_gain, 0.5, places=15)
        self.assertAlmostEqual(spectrum.power_gain, 0.375, places=15)
        self.assertEqual(spectrum.window, "hann_periodic")
        self.assertEqual(spectrum.detrend, "arithmetic_mean")
        self.assertEqual(spectrum.metadata["interpolation"], "none")
        self.assertIn("not evidence", spectrum.metadata["evidence_limit"])
        with self.assertRaises(ValueError):
            spectrum.spectral_values[0] = 1.0

        peak = dominant_spectral_peak(spectrum)
        self.assertIsNotNone(peak)
        assert peak is not None
        self.assertEqual(peak.bin_index, bin_index)
        self.assertEqual(peak.frequency_hz, frequency_hz)
        self.assertEqual(peak.resolution_hz, spectrum.df_hz)
        self.assertFalse(peak.interpolated)
        self.assertEqual(
            peak.frequency_interval_hz,
            (frequency_hz - spectrum.df_hz / 2.0, frequency_hz + spectrum.df_hz / 2.0),
        )

    def test_psd_bin_sum_obeys_windowed_parseval_identity(self) -> None:
        rng = np.random.default_rng(94815)
        sample_count = 2048
        fs_hz = 500.0
        time_s = np.arange(sample_count) / fs_hz
        values = (
            3.0
            + 0.8 * np.sin(2.0 * np.pi * 31.25 * time_s)
            + 0.2 * np.cos(2.0 * np.pi * 77.3 * time_s)
            + 0.05 * rng.standard_normal(sample_count)
        )
        spectrum = compute_hann_spectrum(
            time_s, values, value_unit="Pa", scaling="psd"
        )

        indices = np.arange(sample_count)
        window = 0.5 - 0.5 * np.cos(2.0 * np.pi * indices / sample_count)
        demeaned = values - np.mean(values)
        expected_window_weighted_mean_square = float(
            np.sum((window * demeaned) ** 2) / np.sum(window**2)
        )
        integrated_psd = float(np.sum(spectrum.spectral_values) * spectrum.df_hz)
        self.assertAlmostEqual(
            integrated_psd, expected_window_weighted_mean_square, places=13
        )
        self.assertEqual(spectrum.spectrum_unit, "(Pa)^2/Hz")
        self.assertIn("sum(PSD)*df", spectrum.metadata["normalization"])

    def test_mean_removal_makes_a_constant_record_peakless(self) -> None:
        time_s = np.arange(128) / 64.0
        spectrum = compute_hann_spectrum(
            time_s, np.full(128, 19.75), value_unit="K"
        )
        np.testing.assert_array_equal(spectrum.spectral_values, 0.0)
        self.assertIsNone(dominant_spectral_peak(spectrum))
        self.assertEqual(find_spectral_peaks(spectrum), ())

    def test_nyquist_is_not_doubled_or_reported_as_an_interior_peak(self) -> None:
        sample_count = 256
        fs_hz = 256.0
        time_s = np.arange(sample_count) / fs_hz
        amplitude = 0.4
        values = amplitude * (-1.0) ** np.arange(sample_count)
        spectrum = compute_hann_spectrum(time_s, values, value_unit="V")
        self.assertAlmostEqual(spectrum.frequencies_hz[-1], fs_hz / 2.0)
        self.assertAlmostEqual(spectrum.spectral_values[-1], amplitude, places=14)
        # The adjacent Hann main-lobe bin is also guarded, so Nyquist leakage is
        # not relabelled as a resolved interior mode.
        self.assertIsNone(dominant_spectral_peak(spectrum))

    def test_non_bin_centered_tone_reports_a_bin_not_a_fitted_frequency(self) -> None:
        sample_count = 2048
        fs_hz = 512.0
        time_s = np.arange(sample_count) / fs_hz
        true_frequency_hz = 40.35 * fs_hz / sample_count
        amplitude = 1.2
        values = amplitude * np.sin(2.0 * np.pi * true_frequency_hz * time_s + 0.31)
        spectrum = compute_hann_spectrum(time_s, values, value_unit="m/s")
        peak = dominant_spectral_peak(spectrum)
        self.assertIsNotNone(peak)
        assert peak is not None
        self.assertLessEqual(abs(peak.frequency_hz - true_frequency_hz), spectrum.df_hz / 2.0)
        self.assertIn(peak.frequency_hz, spectrum.frequencies_hz)
        self.assertFalse(peak.interpolated)
        # Hann scalloping is honestly retained: no hidden sub-bin amplitude fit.
        self.assertGreater(peak.spectral_value, 0.8 * amplitude)
        self.assertLess(peak.spectral_value, amplitude)

    def test_nonuniform_duplicate_reversed_nonfinite_and_short_inputs_fail(self) -> None:
        base_time = np.arange(64, dtype=float) / 100.0
        values = np.sin(2.0 * np.pi * 10.0 * base_time)

        nonuniform = base_time.copy()
        nonuniform[20:] += 2.0e-4
        with self.assertRaisesRegex(DataValidationError, "does not interpolate"):
            compute_hann_spectrum(nonuniform, values, value_unit="m")

        duplicate = base_time.copy()
        duplicate[20] = duplicate[19]
        with self.assertRaisesRegex(DataValidationError, "strictly increasing"):
            compute_hann_spectrum(duplicate, values, value_unit="m")

        reversed_time = base_time.copy()
        reversed_time[20], reversed_time[21] = reversed_time[21], reversed_time[20]
        with self.assertRaisesRegex(DataValidationError, "strictly increasing"):
            compute_hann_spectrum(reversed_time, values, value_unit="m")

        nonfinite_values = values.copy()
        nonfinite_values[8] = np.nan
        with self.assertRaisesRegex(DataValidationError, "finite"):
            compute_hann_spectrum(base_time, nonfinite_values, value_unit="m")

        nonfinite_time = base_time.copy()
        nonfinite_time[-1] = np.inf
        with self.assertRaisesRegex(DataValidationError, "finite"):
            compute_hann_spectrum(nonfinite_time, values, value_unit="m")

        with self.assertRaisesRegex(DataValidationError, "at least 16"):
            compute_hann_spectrum(base_time[:15], values[:15], value_unit="m")

    def test_units_scaling_and_peak_arguments_are_validated(self) -> None:
        time_s = np.arange(64) / 64.0
        values = np.sin(2.0 * np.pi * 8.0 * time_s)
        for invalid_unit in ("", "   ", "SI", "unknown"):
            with self.subTest(invalid_unit=invalid_unit):
                with self.assertRaises(DataValidationError):
                    compute_hann_spectrum(time_s, values, value_unit=invalid_unit)
        dimensionless = compute_hann_spectrum(
            time_s, values, value_unit="1", scaling="psd"
        )
        self.assertEqual(dimensionless.spectrum_unit, "1/Hz")
        with self.assertRaisesRegex(DataValidationError, "scaling"):
            compute_hann_spectrum(time_s, values, value_unit="m", scaling="power")  # type: ignore[arg-type]
        with self.assertRaisesRegex(DataValidationError, "at least 8"):
            compute_hann_spectrum(time_s, values, value_unit="m", minimum_samples=7)
        with self.assertRaisesRegex(DataValidationError, "must not exceed"):
            compute_hann_spectrum(time_s, values, value_unit="m", uniform_rtol=0.1)
        with self.assertRaises(DataValidationError):
            find_spectral_peaks(dimensionless, minimum_relative_height=1.1)
        with self.assertRaises(DataValidationError):
            find_spectral_peaks(dimensionless, min_frequency_hz=9.0, max_frequency_hz=8.0)
        with self.assertRaises(DataValidationError):
            find_spectral_peaks(dimensionless, max_peaks=0)


if __name__ == "__main__":
    unittest.main()
