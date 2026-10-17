import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_handshake_tools_and_error_boundary(self):
        with tempfile.TemporaryDirectory(prefix="companion mcp ") as temporary:
            root = Path(temporary)
            params = StdioServerParameters(command=sys.executable, args=["-m", "companion_animation.server"],
                                          env={**os.environ, "COMPANION_ANIMATION_HOME": str(root / "settings")})
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    initialized = await session.initialize()
                    self.assertEqual(initialized.serverInfo.name, "Companion Animation Pipeline")
                    tools = {t.name for t in (await session.list_tools()).tools}
                    self.assertTrue({"environment_info", "pipeline_run", "blender_script", "job_status", "record_review"} <= tools)
                    result = await session.call_tool("environment_info", {})
                    self.assertFalse(result.isError)
                    # A script elsewhere must be refused before any Blender process starts.
                    external = root / "outside.py"
                    external.write_text("raise RuntimeError('must not execute')")
                    workspace = root / "workspace"
                    workspace.mkdir()
                    denied = await session.call_tool("blender_script", {"script": str(external), "workspace": str(workspace)})
                    self.assertTrue(denied.isError)
                    invalid = await session.call_tool("job_status", {"job_id": "../escape"})
                    self.assertTrue(invalid.isError)


if __name__ == "__main__":
    unittest.main()
