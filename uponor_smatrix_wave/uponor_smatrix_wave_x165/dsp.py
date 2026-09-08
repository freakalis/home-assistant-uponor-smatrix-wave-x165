"""Shared receive-only DSP, extracted unchanged from the proven offline analyzer."""
from __future__ import annotations
import numpy as np

SYNC = bytes.fromhex("AA AA AA AA D3 91 D3 91")


KNOWN_IDS: dict[bytes, str] = {}


def boolean_runs(mask: np.ndarray) -> np.ndarray:
    edges = np.flatnonzero(np.diff(np.r_[False, mask, False]))
    return edges.reshape(-1, 2)


def bridge_short_gaps(mask: np.ndarray, max_gap: int) -> np.ndarray:
    out = mask.copy()
    false_runs = boolean_runs(~out)
    for a, b in false_runs:
        if a > 0 and b < len(out) and b - a <= max_gap:
            out[a:b] = True
    return out


def smooth_same(x: np.ndarray, width: int) -> np.ndarray:
    if width <= 1:
        return x.copy()
    return np.convolve(x, np.ones(width, dtype=float) / width, mode="same")


def refine_active_region(
    z: np.ndarray, absolute_start: int, threshold_power: float, fs: int
) -> tuple[np.ndarray, int, int]:
    power = z.real.astype(float) ** 2 + z.imag.astype(float) ** 2
    local = smooth_same(power, max(8, round(fs * 0.00012)))
    active = local >= threshold_power
    active = bridge_short_gaps(active, max(1, round(fs * 0.00020)))
    runs = boolean_runs(active)
    if not len(runs):
        return z[:0], absolute_start, absolute_start
    # The coarse detector should contain one burst. Selecting the longest run
    # rejects isolated impulses in the padding.
    a, b = max(runs, key=lambda ab: ab[1] - ab[0])
    guard = max(2, round(fs * 0.00004))
    a = max(0, int(a) - guard)
    b = min(len(z), int(b) + guard)
    return z[a:b], absolute_start + a, absolute_start + b


def robust_two_tones(freq: np.ndarray, valid: np.ndarray) -> tuple[float, float, float, float]:
    values = freq[valid]
    if len(values) < 20:
        raise ValueError("too few valid discriminator samples")
    centers = np.percentile(values, [20, 80]).astype(float)
    for _ in range(30):
        labels = np.abs(values[:, None] - centers[None, :]).argmin(axis=1)
        new = np.array([
            np.median(values[labels == k]) if np.any(labels == k) else centers[k]
            for k in range(2)
        ])
        if np.max(np.abs(new - centers)) < 0.01:
            break
        centers = new
    centers.sort()
    labels = np.abs(values[:, None] - centers[None, :]).argmin(axis=1)
    residual = values - centers[labels]
    robust_sigma = 1.4826 * float(np.median(np.abs(residual - np.median(residual))))
    separation = float(centers[1] - centers[0])
    fsk_quality = separation / max(robust_sigma, 1.0)
    balance = float(min(np.mean(labels == 0), np.mean(labels == 1)))
    return float(centers[0]), float(centers[1]), fsk_quality, balance


def estimate_bitrate(freq: np.ndarray, mid: float, valid: np.ndarray, fs: int) -> tuple[float, float]:
    hard = freq >= mid
    edges = np.flatnonzero(hard[1:] != hard[:-1]) + 1
    edges = edges[valid[np.minimum(edges, len(valid) - 1)]]
    # Reject discriminator chatter: real symbol edges cannot be one sample apart.
    if len(edges) > 1:
        edges = edges[np.r_[True, np.diff(edges) >= 2]]
    if len(edges) < 8:
        raise ValueError("too few FSK transitions for clock estimation")

    def scores(rates: np.ndarray) -> np.ndarray:
        phase = 2j * np.pi * edges[:, None] * rates[None, :] / fs
        return np.abs(np.exp(phase).mean(axis=0))

    coarse = np.arange(20000.0, 60000.1, 20.0)
    coarse_scores = scores(coarse)
    rate0 = float(coarse[int(np.argmax(coarse_scores))])
    fine = np.arange(rate0 - 100.0, rate0 + 100.01, 0.5)
    fine_scores = scores(fine)
    i = int(np.argmax(fine_scores))
    return float(fine[i]), float(fine_scores[i])


def sync_patterns() -> list[tuple[str, bool, np.ndarray]]:
    result = []
    for order in ("MSB-first", "LSB-first"):
        bit_positions = range(7, -1, -1) if order == "MSB-first" else range(8)
        base = np.array([(byte >> bit) & 1 for byte in SYNC for bit in bit_positions], dtype=np.uint8)
        for inverted in (False, True):
            result.append((order, inverted, base ^ int(inverted)))
    return result


def bits_to_bytes(bits: np.ndarray, order: str) -> bytes:
    usable = len(bits) - len(bits) % 8
    bits = bits[:usable].reshape(-1, 8)
    weights = (1 << np.arange(7, -1, -1)) if order == "MSB-first" else (1 << np.arange(8))
    return bytes((bits * weights).sum(axis=1).astype(np.uint8))


def exact_sync_matches(raw_bits: np.ndarray):
    """Yield exact sync locations using NumPy bit packing and bytes search.

    Live decoding accepts no sync errors. Packing each of the eight possible
    bit alignments avoids constructing and comparing every 64-bit sliding
    window for all four order/inversion combinations.
    """
    for order, bitorder in (("MSB-first", "big"), ("LSB-first", "little")):
        for inverted in (False, True):
            canonical = raw_bits ^ int(inverted)
            for offset in range(8):
                usable = len(canonical) - offset
                usable -= usable % 8
                if usable < 64:
                    continue
                packed = np.packbits(
                    canonical[offset:offset + usable], bitorder=bitorder
                ).tobytes()
                byte_index = packed.find(SYNC)
                while byte_index >= 0:
                    yield offset + 8 * byte_index, order, inverted
                    byte_index = packed.find(SYNC, byte_index + 1)


def demodulate(
    freq: np.ndarray,
    mid: float,
    fs: int,
    bitrate_est: float,
    max_sync_errors: int,
    fast: bool = False,
) -> dict | None:
    patterns = sync_patterns() if max_sync_errors else ()
    # Timing comes from the signal-only edge estimator. The local search absorbs
    # estimator jitter but is deliberately narrow (0.7%), not an assumed rate.
    sample_axis = np.arange(len(freq), dtype=float)
    best = None

    def search(rates: np.ndarray, phase_count: int, phase_center: float | None = None) -> None:
        nonlocal best
        for rate in rates:
            sps = fs / rate
            if phase_center is None:
                phases = np.linspace(0.0, sps, phase_count, endpoint=False)
            else:
                phases = (phase_center + np.linspace(-sps / 16.0, sps / 16.0, phase_count)) % sps
            for phase in phases:
                positions = phase + np.arange(max(0, int((len(freq) - 1 - phase) / sps) + 1)) * sps
                if len(positions) < 64:
                    continue
                sampled = np.interp(positions, sample_axis, freq)
                raw_bits = (sampled >= mid).astype(np.uint8)
                confidence = np.minimum(np.abs(sampled - mid) / 1000.0, 30.0)
                if max_sync_errors == 0:
                    candidates = exact_sync_matches(raw_bits)
                else:
                    windows = np.lib.stride_tricks.sliding_window_view(raw_bits, 64)
                    candidates = []
                    for order, inverted, pattern in patterns:
                        errors = np.count_nonzero(windows != pattern, axis=1)
                        j = int(np.argmin(errors))
                        candidates.append((j, order, inverted, int(errors[j])))
                for candidate in candidates:
                    if max_sync_errors == 0:
                        j, order, inverted = candidate
                        err = 0
                    else:
                        j, order, inverted, err = candidate
                    # Eye opening over the whole captured packet discriminates among
                    # rates that all happen to match the short sync exactly.
                    packet_conf = float(np.mean(confidence[j:])) if j < len(confidence) else 0.0
                    key = (err, -packet_conf, abs(rate - bitrate_est))
                    if best is None or key < best[0]:
                        best = (key, rate, phase, j, order, inverted, raw_bits, packet_conf)

    if fast:
        # Live mode uses the same timing search in two stages. The transition
        # estimator normally gives a rate close enough to find the exact sync
        # directly. Retain the wider coarse pass as a fallback for noisy bursts.
        search(np.asarray([bitrate_est]), 16)
        if best is None or best[0][0] > max_sync_errors:
            coarse_rates = np.arange(
                bitrate_est * 0.997, bitrate_est * 1.003 + 0.01, 8.0
            )
            search(coarse_rates, 16)
        if best is not None:
            search(np.arange(best[1] - 10.0, best[1] + 10.01, 1.0), 9, best[2])
    else:
        rates = np.arange(bitrate_est * 0.997, bitrate_est * 1.003 + 0.01, 2.0)
        search(rates, 32)
    if best is None or best[0][0] > max_sync_errors:
        return None
    _, rate, phase, sync_index, order, inverted, raw_bits, confidence = best
    canonical = raw_bits ^ int(inverted)
    packet = bits_to_bytes(canonical[sync_index:], order)
    # Across this capture byte 8 declares the bytes between itself and the
    # two-byte CRC. Therefore complete frame size is length + 11, including
    # 8 preamble/sync bytes, the length byte, body and CRC16.
    if len(packet) >= 9:
        declared_total = packet[8] + 11
        if 11 <= declared_total <= len(packet):
            packet = packet[:declared_total]
    matches = [label for needle, label in KNOWN_IDS.items() if needle in packet]
    return {
        "demod_rate": float(rate),
        "phase": float(phase),
        "sync_bit_index": int(sync_index),
        "sync_errors": int(best[0][0]),
        "bit_order": order,
        "inverted": bool(inverted),
        # Conjugating I/Q negates the discriminator and is exactly equivalent to
        # toggling this inversion. Reporting it makes that tested equivalence explicit.
        "iq_conjugate_equivalent": bool(inverted),
        "confidence": confidence,
        "packet": packet,
        "matches": matches,
    }
