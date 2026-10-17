"""Per-user settings and Blender discovery; no host settings change on import."""
import json
import os
from pathlib import Path
import shutil
import re
import subprocess
import sys


def home():
    override = os.environ.get("COMPANION_ANIMATION_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "companion-animation"
    return Path.home() / ".local/share/companion-animation"


def settings():
    path = home() / "settings.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save_settings(value):
    home().mkdir(parents=True, exist_ok=True)
    path = home() / "settings.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def hidden_run(argv, **kwargs):
    return subprocess.run(argv, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kwargs)


def blender_path(explicit=None):
    requested = explicit or os.environ.get("BLENDER_BINARY")
    if requested:
        candidate = Path(shutil.which(str(requested)) or requested).expanduser().resolve()
        if not candidate.is_file():
            raise FileNotFoundError(f"Blender executable not found: {candidate}")
        return candidate
    candidates = [settings().get("blender"), shutil.which("blender")]
    if sys.platform == "win32":
        folder = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Blender Foundation"
        candidates.extend(str(p) for p in sorted(folder.glob("Blender*/blender.exe"), reverse=True))
    elif sys.platform == "darwin":
        candidates.append("/Applications/Blender.app/Contents/MacOS/Blender")
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    raise FileNotFoundError("Blender is missing. Run companion-animation setup to install it automatically.")


def blender_version(output):
    first = output.splitlines()[0] if output else ""
    match = re.match(r"Blender (\d+)\.(\d+)", first)
    if not match or tuple(map(int, match.groups())) < (4, 5):
        raise ValueError("Blender 4.5 or newer is required")
    return first


def doctor():
    from . import __version__
    result = {"version": __version__, "python": sys.version.split()[0], "home": str(home())}
    try:
        executable = blender_path()
        process = hidden_run([str(executable), "--version"], capture_output=True, text=True, timeout=30, check=True)
        result.update(blender=str(executable), blender_version=blender_version(process.stdout), status="ready")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result.update(status="needs_setup", error=str(error))
    return result
