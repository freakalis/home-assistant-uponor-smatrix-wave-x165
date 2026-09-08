"""Incremental RF burst detection with state spanning input blocks."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class LiveBurst:
    start_sample: int
    end_sample: int
    samples: np.ndarray
    threshold_power: float
    noise_db: float


class StreamingBurstDetector:
    def __init__(
        self,
        sample_rate: int,
        *,
        detection_block_ms: float = 1.0,
        threshold_db: float = 8.0,
        min_burst_ms: float = 2.0,
        merge_gap_ms: float = 1.5,
        padding_ms: float = 1.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.chunk_samples = max(1, round(sample_rate * detection_block_ms / 1000.0))
        self.threshold_db = threshold_db
        self.min_signal_chunks = max(1, round(min_burst_ms / detection_block_ms))
        self.max_gap_chunks = max(1, round(merge_gap_ms / detection_block_ms))
        self.padding_chunks = max(1, round(padding_ms / detection_block_ms))
        self._pending = np.empty(0, dtype=np.complex64)
        self._processed = 0
        self._noise_db: deque[float] = deque(maxlen=max(200, round(5_000 / detection_block_ms)))
        self._cached_noise_db: float | None = None
        self._noise_update_counter = 0
        self._prebuffer: deque[tuple[int, np.ndarray]] = deque(maxlen=self.padding_chunks)
        self._active = False
        self._burst_chunks: list[np.ndarray] = []
        self._burst_start = 0
        self._active_chunks = 0
        self._gap_chunks = 0
        self._burst_threshold_db = 0.0
        self._burst_noise_db = 0.0

    @property
    def noise_db(self) -> float | None:
        return self._cached_noise_db

    def feed(self, samples: np.ndarray) -> list[LiveBurst]:
        samples = np.asarray(samples, dtype=np.complex64)
        data = np.concatenate((self._pending, samples)) if len(self._pending) else samples
        output: list[LiveBurst] = []
        offset = 0
        while len(data) - offset >= self.chunk_samples:
            chunk = data[offset:offset + self.chunk_samples].copy()
            start = self._processed
            self._processed += self.chunk_samples
            offset += self.chunk_samples
            burst = self._process_chunk(start, chunk)
            if burst is not None:
                output.append(burst)
        self._pending = data[offset:].copy()
        return output

    def flush(self) -> list[LiveBurst]:
        output: list[LiveBurst] = []
        if len(self._pending):
            padded = np.pad(self._pending, (0, self.chunk_samples - len(self._pending)))
            burst = self._process_chunk(self._processed, padded.astype(np.complex64))
            self._processed += len(self._pending)
            self._pending = np.empty(0, dtype=np.complex64)
            if burst is not None:
                output.append(burst)
        if self._active:
            burst = self._finish_burst()
            if burst is not None:
                output.append(burst)
        return output

    def _process_chunk(self, start: int, chunk: np.ndarray) -> LiveBurst | None:
        power = float(np.mean(chunk.real.astype(float) ** 2 + chunk.imag.astype(float) ** 2))
        power_db = 10.0 * np.log10(max(power, 1e-12))
        update_interval = 5 if len(self._noise_db) < 20 else 25
        if self._cached_noise_db is None or self._noise_update_counter >= update_interval:
            self._cached_noise_db = float(np.median(self._noise_db)) if self._noise_db else power_db
            self._noise_update_counter = 0
        noise_db = self._cached_noise_db
        trained = len(self._noise_db) >= 20
        signal = trained and power_db >= noise_db + self.threshold_db
        completed = None

        if not self._active and signal:
            self._active = True
            prefix = list(self._prebuffer)
            self._burst_start = prefix[0][0] if prefix else start
            self._burst_chunks = [saved for _, saved in prefix]
            self._burst_chunks.append(chunk)
            self._active_chunks = 1
            self._gap_chunks = 0
            self._burst_threshold_db = noise_db + self.threshold_db
            self._burst_noise_db = noise_db
        elif self._active:
            self._burst_chunks.append(chunk)
            if signal:
                self._active_chunks += 1
                self._gap_chunks = 0
            else:
                self._gap_chunks += 1
                if self._gap_chunks > self.max_gap_chunks:
                    completed = self._finish_burst()

        if not signal and not self._active:
            self._noise_db.append(power_db)
            self._noise_update_counter += 1
        self._prebuffer.append((start, chunk))
        return completed

    def _finish_burst(self) -> LiveBurst | None:
        chunks = self._burst_chunks
        start = self._burst_start
        active_chunks = self._active_chunks
        trailing = min(self._gap_chunks, self.padding_chunks)
        if self._gap_chunks > trailing:
            chunks = chunks[:len(chunks) - (self._gap_chunks - trailing)]
        samples = np.concatenate(chunks) if chunks else np.empty(0, dtype=np.complex64)
        self._active = False
        self._burst_chunks = []
        self._active_chunks = 0
        self._gap_chunks = 0
        if active_chunks < self.min_signal_chunks:
            return None
        return LiveBurst(
            start_sample=start,
            end_sample=start + len(samples),
            samples=samples,
            threshold_power=10.0 ** (self._burst_threshold_db / 10.0),
            noise_db=self._burst_noise_db,
        )
