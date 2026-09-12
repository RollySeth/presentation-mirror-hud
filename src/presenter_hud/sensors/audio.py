from __future__ import annotations

import math

from .base import SensorAdapter


class AudioLevelAdapter(SensorAdapter):
    def __init__(
        self, seconds: float = 0.25, sample_rate: int | None = None, device: int | str | None = None
    ):
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError as error:
            raise RuntimeError("Install requirements-sensors.txt to use the audio adapter") from error
        self.np = np
        self.sd = sd
        self.device = device
        self.seconds = seconds
        self.sample_rate = sample_rate or int(sd.query_devices(device, "input")["default_samplerate"])
        sd.check_input_settings(
            device=device, channels=1, dtype="float32", samplerate=self.sample_rate
        )

    def sample(self) -> dict[str, float | bool]:
        samples = self.sd.rec(
            int(self.seconds * self.sample_rate),
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            device=self.device,
            blocking=True,
        )
        rms = math.sqrt(float(self.np.mean(self.np.square(samples))))
        return {"microphone_active": True, "volume": min(1.0, rms * 8)}
