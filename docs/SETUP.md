# Setup and supported clients

## What the one-shot installer does

1. Reuse uv, or install the pinned uv 0.9.7 user-level bootstrap from Astral.
2. Install a managed Python 3.12 if needed, without replacing shell Python commands.
3. Install this package and its MCP SDK dependencies in a uv tool environment.
4. Reuse Blender or download portable Blender 4.5.3. Archives must match the
   SHA-256 values shipped in this package, copied from Blender's release manifest.
   The official download host is tried first, with NLUUG and dotsrc mirrors as
   fallbacks. It never installs an unchecked archive.
5. Save the Blender executable path in the package's per-user settings.
6. Register the selected client's local stdio MCP server and install the skill.

No administrator rights are normally needed. Windows may require a PowerShell
policy that permits a downloaded script; inspect the script and follow your
organization's policy. The installer does not change that policy. Linux Blender
still needs the system graphics libraries its official binary requires; a
minimal server image may need OS packages. Network access is needed for setup,
not for subsequent local Blender processing.

The managed downloads cover Windows x64/ARM64, Linux x64, and macOS x64/ARM64.
Other platforms can supply their own Blender with `--blender`. A supported
download target does not imply it was tested on every operating system; see
[verification](VERIFICATION.md).

## Client selection

| `--client` / `-Client` | Installed MCP connection | Default skill directory |
| --- | --- | --- |
| `codex` | Calls the installed `codex mcp add` CLI | `~/.agents/skills/companion-animation-pipeline` |
| `claude-code` | Project `.mcp.json` | `~/.claude/skills/companion-animation-pipeline` |
| `claude-desktop` | User `claude_desktop_config.json` | `~/.agents/skills/companion-animation-pipeline` |
| `cursor` | Project `.cursor/mcp.json` | `~/.cursor/skills/companion-animation-pipeline` |
| `generic` | Project `companion-mcp.json` for import into another MCP host | `~/.agents/skills/companion-animation-pipeline` |
| `auto` | A single detected Codex/Claude CLI; generic if ambiguous | Based on selected client |

Pass `--project PATH` / `-Project PATH` to select the project. Existing unrelated
MCP servers and other settings are retained. JSON files receive a backup before
modification. A conflicting server of the same name or a locally modified skill
is refused rather than silently overwritten. Use `--skill-dir` / `-SkillDir`
to install the skill elsewhere, or `--no-skill` / `-NoSkill` to skip that copy.
Claude Desktop hosts MCP tools but does not automatically turn Markdown skills
into a native skill feature; reference the copied skill or ask its assistant to
use the MCP tools. MCP-only clients can use the tools without skill support.

Restart the selected client, approve the project MCP connection if prompted,
and ask it to call `environment_info`. Some hosted/web AI products cannot run
local stdio tools at all; they need their own local connector. This package
does not bypass that product's execution or approval rules.

## Existing Python / offline installations

```sh
python -m pip install https://github.com/arabianguy08/companion-animation-pipeline/archive/refs/tags/v0.1.1.zip
companion-animation setup --client generic --blender /path/to/blender --no-download
```

That alternative requires Python 3.12+ and available dependency wheels. For an
offline machine, prepare the wheels and Blender archive on a connected machine
first. The one-shot installer itself requires internet access.

`COMPANION_ANIMATION_HOME` changes the package's settings, Blender, download and
job directory. `BLENDER_BINARY` overrides Blender discovery. MCP registration
records an absolute interpreter path and the package home, so a GUI client
does not need a restarted shell PATH or a checked-out repository.

The installer prints the absolute runner path. If uv's tool directory is not on
PATH, use that path or run `uv tool update-shell` and open a new shell. MCP uses
its absolute interpreter path regardless.

Use `companion-animation doctor` for diagnostics. A missing or failing Blender
is an error to resolve, not a successful installation. Repair or uninstall uv
tools using uv's normal `tool` commands. Remove this server and skill through
your client settings if no longer wanted; user models and derivative workspaces
are separate and are never removed by setup.

## Reference documentation

- [Astral uv installation](https://docs.astral.sh/uv/getting-started/installation/)
- [Blender release checksums](https://download.blender.org/release/Blender4.5/blender-4.5.3.sha256)
- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.26.0)
- [Codex MCP](https://developers.openai.com/codex/mcp/)
