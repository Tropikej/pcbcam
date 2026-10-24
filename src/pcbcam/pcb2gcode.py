"""Run pcb2gcode, by default from the pinned Docker image."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_IMAGE = "pcbcam/pcb2gcode:3.0.4"


class Pcb2GcodeError(Exception):
    def __init__(self, message: str, log: str):
        super().__init__(message)
        self.log = log


@dataclass(frozen=True)
class Runner:
    """``image`` runs pcb2gcode in Docker; ``binary`` runs a local executable instead."""

    image: str = DEFAULT_IMAGE
    binary: str | None = None

    @classmethod
    def from_env(cls) -> Runner:
        return cls(
            image=os.environ.get("PCBCAM_PCB2GCODE_IMAGE", DEFAULT_IMAGE),
            binary=os.environ.get("PCBCAM_PCB2GCODE_BIN"),
        )

    def _command(self, workdir: Path, args: list[str]) -> list[str]:
        if self.binary:
            return [self.binary, *args]
        docker = shutil.which("docker")
        if not docker:
            raise Pcb2GcodeError("docker not found (or set PCBCAM_PCB2GCODE_BIN)", "")
        cmd = [docker, "run", "--rm", "-v", f"{workdir.resolve()}:/work", "-w", "/work"]
        if hasattr(os, "getuid"):
            cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]
        return [*cmd, self.image, *args]

    def version(self) -> str:
        proc = subprocess.run(self._command(Path.cwd(), ["--version"]), capture_output=True, text=True)
        return (proc.stdout or proc.stderr).strip().splitlines()[0] if proc.returncode == 0 else "unknown"

    def run(self, workdir: Path) -> str:
        """Run pcb2gcode on ``workdir/millproject``; returns the log.

        Warnings are fatal on purpose (no ``--ignore-warnings``): a "contentions"
        warning means a cutter will eat into copper.
        """
        cwd = workdir if self.binary else None
        proc = subprocess.run(self._command(workdir, []), capture_output=True, text=True, cwd=cwd)
        log = proc.stdout + proc.stderr
        if proc.returncode != 0:
            raise Pcb2GcodeError(f"pcb2gcode failed with exit code {proc.returncode}", log)
        return log
