"""Install a verified private Blender build, the skill, and selected MCP registration."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

from .environment import blender_version, blender_path, home, hidden_run, save_settings, settings

HERE = Path(__file__).parent
MANIFEST = json.loads((HERE / "data/blender-checksums.json").read_text())


def archive_name(system=None, machine=None):
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    architecture = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    extension = {"Windows": "zip", "Linux": "tar.xz", "Darwin": "dmg"}.get(system)
    family = {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}.get(system)
    name = f"blender-{MANIFEST['version']}-{family}-{architecture}.{extension}"
    if name not in MANIFEST["sha256"]:
        raise RuntimeError(f"No managed Blender build for {system}/{machine}; pass --blender with an existing executable.")
    return name


def download_blender_archive(name, directory):
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / name
    expected = MANIFEST["sha256"][name]
    if destination.is_file():
        with destination.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() == expected:
                return destination
    bases = ["https://download.blender.org/release/Blender4.5/",
             "https://ftp.nluug.nl/pub/graphics/blender/release/Blender4.5/",
             "https://mirrors.dotsrc.org/blender/release/Blender4.5/"]
    errors = []
    for base in bases:
        partial = destination.with_name(name + ".part")
        try:
            request = urllib.request.Request(base + name + "?download=1", headers={"User-Agent": "companion-animation-pipeline/0.1"})
            with urllib.request.urlopen(request, timeout=60) as source, partial.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            with partial.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != expected:
                raise RuntimeError(f"Checksum mismatch for {name}; refusing installation")
            partial.replace(destination)
            return destination
        except (OSError, RuntimeError) as error:
            partial.unlink(missing_ok=True)
            errors.append(f"{base}: {error}")
    raise RuntimeError("Blender download failed. " + "; ".join(errors))


def extract_archive(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                path = (destination / member.filename).resolve()
                if not path.is_relative_to(destination.resolve()):
                    raise ValueError("Unsafe path in Blender archive")
            bundle.extractall(destination)
    elif archive.name.endswith(".tar.xz"):
        with tarfile.open(archive) as bundle:
            bundle.extractall(destination, filter="data")
    else:
        mount = destination / "volume"
        mount.mkdir()
        hidden_run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mount), str(archive)], check=True, capture_output=True)
        try:
            hidden_run(["ditto", str(mount / "Blender.app"), str(destination / "Blender.app")], check=True)
        finally:
            hidden_run(["hdiutil", "detach", str(mount)], check=True, capture_output=True)
            mount.rmdir()


def ensure_blender(explicit=None, allow_download=True, managed=False):
    if not managed or explicit:
        try:
            return blender_path(explicit)
        except FileNotFoundError:
            if explicit or not allow_download:
                raise
    if not allow_download:
        raise RuntimeError("Managed installation needs downloads enabled")
    name = archive_name()
    folder = home() / "blender" / MANIFEST["version"]
    executable_name = "blender.exe" if sys.platform == "win32" else "Blender" if sys.platform == "darwin" else "blender"
    existing = list(folder.rglob(executable_name)) if folder.exists() else []
    if existing:
        return existing[0].resolve()
    archive = download_blender_archive(name, home() / "downloads")
    folder.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="extract-", dir=folder.parent) as temporary:
        stage = Path(temporary) / "build"
        extract_archive(archive, stage)
        executables = list(stage.rglob(executable_name))
        if not executables:
            raise RuntimeError("Downloaded Blender archive contains no executable")
        if sys.platform != "win32":
            executables[0].chmod(executables[0].stat().st_mode | 0o111)
        hidden_run([str(executables[0]), "--version"], check=True, capture_output=True, timeout=30)
        stage.replace(folder)
    return next(folder.rglob(executable_name)).resolve()


def json_config(path, server):
    value = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    servers = value.setdefault("mcpServers", {})
    if "companion-animation" in servers and servers["companion-animation"] != server:
        raise RuntimeError(f"Existing companion-animation server differs in {path}; retain it or remove it explicitly before setup")
    servers["companion-animation"] = server
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".companion-backup"))
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def register_client(client, project, server):
    if client == "auto":
        found = [name for name in ("codex", "claude") if shutil.which(name)]
        client = "codex" if found == ["codex"] else "claude-code" if found == ["claude"] else "generic"
    project = Path(project).resolve()
    if client == "codex":
        executable = shutil.which("codex")
        if not executable:
            raise RuntimeError("Codex CLI is not installed. Use --client generic to produce portable MCP settings.")
        existing = hidden_run([executable, "mcp", "get", "companion-animation", "--json"], capture_output=True, text=True)
        if existing.returncode == 0:
            value = json.loads(existing.stdout).get("transport", {})
            if value.get("command") != server["command"] or value.get("args") != server["args"] or value.get("env", {}) != server["env"]:
                raise RuntimeError("An existing Codex companion-animation server differs; retain it or remove it explicitly")
            return "Existing Codex MCP registration"
        hidden_run([executable, "mcp", "add", "companion-animation", "--env",
                    "COMPANION_ANIMATION_HOME=" + server["env"]["COMPANION_ANIMATION_HOME"],
                    "--", server["command"], *server["args"]], check=True)
        return "Codex MCP registration"
    paths = {"generic": project / "companion-mcp.json", "claude-code": project / ".mcp.json",
             "cursor": project / ".cursor/mcp.json"}
    if client == "claude-desktop":
        if sys.platform == "win32":
            path = Path(os.environ["APPDATA"]) / "Claude/claude_desktop_config.json"
        elif sys.platform == "darwin":
            path = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
        else:
            path = Path.home() / ".config/Claude/claude_desktop_config.json"
    else:
        path = paths[client]
    json_config(path, server)
    return str(path)


def install_skill(destination):
    source = HERE / "skill"
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        source_files = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
        target_files = {p.relative_to(destination): p.read_bytes() for p in destination.rglob("*") if p.is_file()}
        if source_files != target_files:
            raise RuntimeError(f"Skill already exists with local changes: {destination}; use another --skill-dir")
        return destination
    shutil.copytree(source, destination)
    return destination


def setup(client="auto", project=".", blender=None, download=True, managed=False, skill_dir=None, no_skill=False):
    if client == "auto":
        found = [name for name in ("codex", "claude") if shutil.which(name)]
        client = "codex" if found == ["codex"] else "claude-code" if found == ["claude"] else "generic"
    executable = ensure_blender(blender, download, managed)
    check = hidden_run([str(executable), "--version"], capture_output=True, text=True, check=True, timeout=30)
    version = blender_version(check.stdout)
    state = settings()
    state["blender"] = str(executable)
    save_settings(state)
    # Use the installed interpreter directly: GUI clients need no refreshed PATH.
    server = {"command": sys.executable, "args": ["-m", "companion_animation.server"],
              "env": {"COMPANION_ANIMATION_HOME": str(home())}}
    registration = register_client(client, project, server)
    if skill_dir is None:
        folder = {"claude-code": ".claude/skills", "cursor": ".cursor/skills"}.get(client, ".agents/skills")
        skill_dir = Path.home() / folder / "companion-animation-pipeline"
    skill = None if no_skill else str(install_skill(skill_dir))
    return {"status": "ready", "blender": str(executable), "blender_version": version,
            "mcp": registration, "skill": skill, "restart_client": True}
