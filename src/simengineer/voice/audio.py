"""Sortie audio : choix du périphérique, lecture interruptible (sounddevice, import paresseux)."""

from __future__ import annotations

from typing import Any, Protocol

from simengineer.voice.tts import Audio


class AudioOutput(Protocol):
    def play(self, audio: Audio) -> None:
        """Joue le son et bloque jusqu'à la fin (ou jusqu'à `stop`)."""

    def stop(self) -> None: ...


class AudioUnavailableError(RuntimeError):
    pass


def _sd() -> Any:
    try:
        import sounddevice
    except (ImportError, OSError) as exc:
        raise AudioUnavailableError(
            "sounddevice indisponible : pip install simengineer[voice]"
        ) from exc
    return sounddevice


def list_devices() -> list[dict[str, Any]]:
    """Périphériques audio (index, nom, entrées, sorties)."""
    return [
        {
            "index": i,
            "name": d["name"],
            "inputs": int(d["max_input_channels"]),
            "outputs": int(d["max_output_channels"]),
        }
        for i, d in enumerate(_sd().query_devices())
    ]


def resolve_device(spec: str, devices: list[dict[str, Any]], kind: str = "outputs") -> int | None:
    """« 3 » -> index 3 ; « casque » -> premier périphérique dont le nom contient le texte."""
    spec = spec.strip()
    if not spec:
        return None
    if spec.isdigit():
        return int(spec)
    for d in devices:
        if spec.lower() in str(d["name"]).lower() and d[kind] > 0:
            return int(d["index"])
    raise AudioUnavailableError(f"Périphérique audio introuvable : {spec!r}")


class NullAudioOutput:
    def __init__(self) -> None:
        self.played: list[Audio] = []

    def play(self, audio: Audio) -> None:
        self.played.append(audio)

    def stop(self) -> None:
        return None


class SoundDeviceOutput:
    def __init__(self, device: str = "") -> None:
        self._sd = _sd()
        self._device = resolve_device(device, list_devices()) if device else None

    def play(self, audio: Audio) -> None:
        if len(audio.pcm) == 0:
            return
        self._sd.play(audio.pcm, audio.sample_rate, device=self._device)
        self._sd.wait()

    def stop(self) -> None:
        self._sd.stop()
