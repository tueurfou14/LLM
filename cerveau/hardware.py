"""Détection du matériel et choix du palier de modèle.

Le débit de génération dépend presque uniquement de la mémoire disponible
pour le modèle et de sa bande passante. On sonde la machine une fois, on en
déduit un palier, et le palier fixe le modèle recommandé.
"""

from __future__ import annotations

import ctypes
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field


@dataclass
class Hardware:
    system: str            # "Darwin", "Windows", "Linux"
    machine: str           # "arm64", "AMD64", "x86_64"
    cpu: str
    ram_gb: float
    gpu: str | None = None
    vram_gb: float | None = None       # None si mémoire unifiée ou pas de GPU
    unified_memory: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def model_memory_gb(self) -> float:
        """Mémoire exploitable pour le modèle et son cache de contexte."""
        if self.unified_memory:
            # macOS garde 3 à 4 Go, et plafonne la part GPU vers deux tiers.
            return max(0.0, min(self.ram_gb - 4.0, self.ram_gb * 0.70))
        if self.vram_gb:
            return self.vram_gb - 0.5
        # CPU seul : on laisse 4 Go au système.
        return max(0.0, self.ram_gb - 4.0)


@dataclass
class Tier:
    name: str
    main_model_lmstudio: str
    main_model_ollama: str
    draft_model_ollama: str | None
    context_tokens: int
    expected_tps: str
    comment: str


TIERS: list[tuple[float, Tier]] = [
    # (mémoire minimale pour le modèle en Go, palier)
    (22.0, Tier(
        name="large",
        main_model_lmstudio="qwen2.5-coder-32b-instruct",
        main_model_ollama="qwen2.5-coder:32b",
        draft_model_ollama="qwen2.5-coder:0.5b",
        context_tokens=32768,
        expected_tps="15 à 40 tok/s",
        comment="32B en 4 bits : le meilleur pour l'audit de sécurité.",
    )),
    (12.0, Tier(
        name="medium",
        main_model_lmstudio="qwen2.5-coder-14b-instruct",
        main_model_ollama="qwen2.5-coder:14b",
        draft_model_ollama="qwen2.5-coder:0.5b",
        context_tokens=16384,
        expected_tps="10 à 25 tok/s",
        comment="14B en 4 bits : bon compromis si la machine refroidit bien.",
    )),
    (5.0, Tier(
        name="small",
        main_model_lmstudio="qwen2.5-coder-7b-instruct",
        main_model_ollama="qwen2.5-coder:7b",
        draft_model_ollama="qwen2.5-coder:0.5b",
        context_tokens=16384,
        expected_tps="20 à 60 tok/s",
        comment="7B en 4 bits : fluide, stable, recommandé sur un portable.",
    )),
    (0.0, Tier(
        name="tiny",
        main_model_lmstudio="qwen2.5-coder-1.5b-instruct",
        main_model_ollama="qwen2.5-coder:1.5b",
        draft_model_ollama=None,
        context_tokens=8192,
        expected_tps="variable",
        comment="Machine très limitée : prévoir un repli vers une API distante.",
    )),
]


def pick_tier(hw: Hardware) -> Tier:
    budget = hw.model_memory_gb
    for minimum, tier in TIERS:
        if budget >= minimum:
            return tier
    return TIERS[-1][1]


# --- Sondes par système ------------------------------------------------------

def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _detect_darwin() -> Hardware:
    ram = int(_run(["sysctl", "-n", "hw.memsize"]) or 0) / 1024**3
    cpu = _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.processor()
    hw = Hardware(system="Darwin", machine=platform.machine(), cpu=cpu, ram_gb=round(ram, 1))
    if platform.machine() == "arm64":
        hw.unified_memory = True
        hw.gpu = "Apple Silicon (mémoire unifiée)"
        hw.notes.append("Moteur conseillé : LM Studio avec MLX, ou Ollama.")
        hw.notes.append("Pour relever le plafond GPU : sudo sysctl iogpu.wired_limit_mb=<Mo>")
    return hw


def _detect_windows() -> Hardware:
    class MemStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemStatus()
    status.dwLength = ctypes.sizeof(MemStatus)
    ram = 0.0
    try:
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
        ram = status.ullTotalPhys / 1024**3
    except Exception:  # noqa: BLE001
        pass
    hw = Hardware(system="Windows", machine=platform.machine(), cpu=platform.processor(), ram_gb=round(ram, 1))
    _detect_nvidia(hw)
    if not hw.gpu:
        hw.notes.append("Pas de GPU NVIDIA détecté : Ollama utilisera le CPU ou Vulkan.")
    return hw


def _detect_linux() -> Hardware:
    ram = 0.0
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    ram = int(line.split()[1]) / 1024**2
                    break
    except OSError:
        pass
    cpu = platform.processor()
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    hw = Hardware(system="Linux", machine=platform.machine(), cpu=cpu, ram_gb=round(ram, 1))
    _detect_nvidia(hw)
    return hw


def _detect_nvidia(hw: Hardware) -> None:
    if not shutil.which("nvidia-smi"):
        return
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    if not out:
        return
    name, _, mem = out.splitlines()[0].partition(",")
    digits = re.sub(r"[^0-9.]", "", mem)
    hw.gpu = name.strip()
    hw.vram_gb = round(float(digits) / 1024, 1) if digits else None


def detect() -> Hardware:
    system = platform.system()
    if system == "Darwin":
        return _detect_darwin()
    if system == "Windows":
        return _detect_windows()
    return _detect_linux()


def describe(hw: Hardware, tier: Tier) -> str:
    lines = [
        f"Système      : {hw.system} {hw.machine}",
        f"Processeur   : {hw.cpu}",
        f"RAM          : {hw.ram_gb} Go" + (" (unifiée)" if hw.unified_memory else ""),
        f"GPU          : {hw.gpu or 'aucun'}" + (f" ({hw.vram_gb} Go VRAM)" if hw.vram_gb else ""),
        f"Budget modèle: {hw.model_memory_gb:.1f} Go",
        "",
        f"Palier       : {tier.name}",
        f"Modèle       : {tier.main_model_ollama} (Ollama) / {tier.main_model_lmstudio} (LM Studio)",
        f"Brouillon    : {tier.draft_model_ollama or 'aucun'}",
        f"Contexte     : {tier.context_tokens} tokens",
        f"Débit attendu: {tier.expected_tps}",
        f"Note         : {tier.comment}",
    ]
    for note in hw.notes:
        lines.append(f"  - {note}")
    return "\n".join(lines)
