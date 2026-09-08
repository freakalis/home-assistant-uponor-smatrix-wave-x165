"""Adapter around the proven offline burst demodulation implementation."""

from __future__ import annotations

import numpy as np

# Reuse the exact frequency discriminator, tone clustering, clock estimator,
# timing search, sync search, and frame-length logic already validated against
# the SDR++ WAV captures. This import has no CLI side effects.
from .dsp import (
    demodulate,
    estimate_bitrate,
    refine_active_region,
    robust_two_tones,
    smooth_same,
)


def u8_iq_to_complex(raw: bytes) -> np.ndarray:
    """Convert rtl_sdr/librtlsdr unsigned interleaved I,Q bytes to complex64."""
    values = np.frombuffer(raw, dtype=np.uint8)
    if len(values) & 1:
        values = values[:-1]
    iq = values.reshape(-1, 2).astype(np.float32) - 127.5
    return iq[:, 0] + 1j * iq[:, 1]


def demodulate_live_burst(
    samples: np.ndarray,
    *,
    sample_rate: int,
    threshold_power: float,
) -> dict | None:
    """Run the proven offline DSP chain on one streaming-detected burst."""
    active, _, _ = refine_active_region(samples, 0, threshold_power, sample_rate)
    if len(active) < 80:
        return None
    raw_frequency = np.angle(active[1:] * np.conj(active[:-1])) * sample_rate / (2.0 * np.pi)
    frequency = smooth_same(raw_frequency, 3)
    power = np.minimum(np.abs(active[1:]) ** 2, np.abs(active[:-1]) ** 2)
    valid = power >= threshold_power * 0.65
    low, high, quality, balance = robust_two_tones(frequency, valid)
    midpoint = 0.5 * (low + high)
    bitrate, clock_score = estimate_bitrate(frequency, midpoint, valid, sample_rate)
    result = demodulate(frequency, midpoint, sample_rate, bitrate, max_sync_errors=0, fast=True)
    if result is None:
        return None
    result.update({
        "tone_low_hz": low,
        "tone_high_hz": high,
        "fsk_quality": quality,
        "tone_balance": balance,
        "estimated_bitrate": bitrate,
        "clock_score": clock_score,
    })
    return result
