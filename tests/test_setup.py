import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from companion_animation import setup
from companion_animation.environment import blender_path, blender_version, settings


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="companion setup ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {"COMPANION_ANIMATION_HOME": str(self.root / "user data")})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_blender_version_requirement(self):
        self.assertEqual(blender_version("Blender 4.5.3 LTS\n"), "Blender 4.5.3 LTS")
        for output in ("Python 3.12", "Blender 3.6.0", ""):
            with self.assertRaises(ValueError):
                blender_version(output)

    def test_pinned_platforms_and_unsupported_architecture(self):
        for system, machine in [("Windows", "AMD64"), ("Windows", "ARM64"), ("Linux", "x86_64"), ("Darwin", "arm64")]:
            self.assertIn(setup.archive_name(system, machine), setup.MANIFEST["sha256"])
        with self.assertRaises(RuntimeError):
            setup.archive_name("Linux", "unrecognized-cpu")

    def test_no_download_on_explicit_missing_path(self):
        with patch.object(setup, "download_blender_archive") as download:
            with self.assertRaises(FileNotFoundError):
                setup.ensure_blender(self.root / "missing", allow_download=False)
            download.assert_not_called()

    def test_existing_other_servers_survive_and_conflicts_do_not_overwrite(self):
        path = self.root / "settings.json"
        path.write_text(json.dumps({"mcpServers": {"existing": {"command": "other"}}, "unrelated": 42}))
        setup.json_config(path, {"command": "new"})
        value = json.loads(path.read_text())
        self.assertEqual(value["mcpServers"]["existing"], {"command": "other"})
        self.assertEqual(value["unrelated"], 42)
        previous = path.read_bytes()
        with self.assertRaises(RuntimeError):
            setup.json_config(path, {"command": "conflict"})
        self.assertEqual(path.read_bytes(), previous)
        self.assertTrue(path.with_name("settings.json.companion-backup").exists())

    def test_checksum_failure_never_creates_an_installed_archive(self):
        name = setup.archive_name("Windows", "AMD64")
        directory = self.root / "download"
        with patch.object(setup.urllib.request, "urlopen", side_effect=lambda *a, **k: io.BytesIO(b"incorrect content")):
            with self.assertRaisesRegex(RuntimeError, "Checksum mismatch"):
                setup.download_blender_archive(name, directory)
        self.assertFalse((directory / name).exists())
        self.assertFalse((directory / (name + ".part")).exists())

    def test_verified_download_is_reused_without_network(self):
        name = setup.archive_name("Windows", "AMD64")
        data = b"verified archive fixture"
        checks = {**setup.MANIFEST, "sha256": {name: hashlib.sha256(data).hexdigest()}}
        with patch.object(setup, "MANIFEST", checks), patch.object(setup.urllib.request, "urlopen", return_value=io.BytesIO(data)):
            path = setup.download_blender_archive(name, self.root)
        with patch.object(setup, "MANIFEST", checks), patch.object(setup.urllib.request, "urlopen") as network:
            self.assertEqual(setup.download_blender_archive(name, self.root), path)
            network.assert_not_called()

    def test_zip_path_traversal_is_rejected(self):
        archive = self.root / "unsafe.zip"
        with zipfile.ZipFile(archive, "w") as stream:
            stream.writestr("../escaped.txt", "bad")
        with self.assertRaises(ValueError):
            setup.extract_archive(archive, self.root / "extracted")
        self.assertFalse((self.root / "escaped.txt").exists())

    def test_setup_installs_skill_and_propagates_isolated_home(self):
        executable = self.root / "blender"
        executable.write_bytes(b"fixture")
        with patch.object(setup, "hidden_run", return_value=subprocess.CompletedProcess([], 0, "Blender 4.5.3\n", "")):
            result = setup.setup("generic", self.root / "project", str(executable), False,
                                 skill_dir=self.root / "skills/companion-animation-pipeline")
        self.assertEqual(result["status"], "ready")
        server = json.loads((self.root / "project/companion-mcp.json").read_text())["mcpServers"]["companion-animation"]
        self.assertEqual(server["env"]["COMPANION_ANIMATION_HOME"], str(self.root / "user data"))
        self.assertEqual(settings()["blender"], str(executable))
        self.assertTrue((Path(result["skill"]) / "SKILL.md").exists())
        self.assertEqual(blender_path(), executable)
        skill_file = Path(result["skill"]) / "SKILL.md"
        skill_file.write_text("user modifications")
        with self.assertRaises(RuntimeError):
            setup.install_skill(skill_file.parent)
        self.assertEqual(skill_file.read_text(), "user modifications")


if __name__ == "__main__":
    unittest.main()
