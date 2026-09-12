from __future__ import annotations

import argparse
import logging
import signal
import threading

from .coach import refresh_coach
from .sensors.audio import AudioLevelAdapter
from .sensors.camera import CameraPresenceAdapter
from .state_store import StateStore


def device_argument(value: str) -> int | str:
    return int(value) if value.isdecimal() else value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default="data/state.json")
    parser.add_argument("--camera", action="store_true")
    parser.add_argument("--audio", action="store_true")
    parser.add_argument("--camera-device", type=device_argument, default=0)
    parser.add_argument(
        "--expression-models", metavar="DIRECTORY",
        help="Opt in to offline 7-class expression estimates using verified YuNet/MobileFaceNet weights",
    )
    parser.add_argument("--audio-device", type=device_argument)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    if not args.camera and not args.audio:
        raise SystemExit("Enable at least one adapter with --camera or --audio")
    if args.expression_models is not None and not args.camera:
        parser.error("--expression-models requires --camera")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    adapters = []
    store = StateStore(args.state)
    try:
        if args.camera:
            adapters.append(CameraPresenceAdapter(
                device=args.camera_device, expression_models=args.expression_models,
            ))
        if args.audio:
            adapters.append(AudioLevelAdapter(device=args.audio_device))
        logging.info(
            "Sensors enabled: camera=%s microphone=%s offline_expression_estimates=%s. No recordings are saved.",
            args.camera, args.audio, args.expression_models is not None,
        )
        while not stopped.is_set():
            updates = {}
            for adapter in adapters:
                updates.update(adapter.sample())

            def mutate(state):
                for key, value in updates.items():
                    if hasattr(state.metrics, key):
                        setattr(state.metrics, key, value)
                refresh_coach(state)

            store.update(mutate)
            stopped.wait(max(0.1, args.interval))
    except Exception:
        logging.exception("Sensor failure. Check device/model configuration; sensors are being marked inactive.")
        raise
    finally:
        for adapter in adapters:
            try:
                adapter.close()
            except Exception:
                logging.exception("Could not close sensor cleanly")

        def mark_inactive(state):
            if args.camera:
                state.metrics.camera_active = False
                state.metrics.face_present = False
                state.metrics.face_motion = None
                state.metrics.expression_active = False
                state.metrics.expression_model_active = False
                state.metrics.expression_label = ""
                state.metrics.expression_confidence = 0.0
                state.metrics.expression_observed_at = 0.0
                state.metrics.smile_detected = None
            if args.audio:
                state.metrics.microphone_active = False

        store.update(mark_inactive)
        logging.info("Sensors stopped.")


if __name__ == "__main__":
    main()
