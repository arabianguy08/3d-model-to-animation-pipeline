"""Terminal entry point; the original staged runner remains directly usable."""
import argparse
import json
from pathlib import Path
import sys
import subprocess

from .environment import blender_path, doctor, hidden_run
from .pipeline import run_pipeline


def demo(directory, blender=None, through="preview"):
    root = Path(directory).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError("Demo output must be a new or empty directory")
    root.mkdir(parents=True, exist_ok=True)
    executable = blender_path(blender)
    generator = run_pipeline.HERE / "make_smoke_fixture.py"
    with (root / "fixture.log").open("w") as stream:
        hidden_run([str(executable), "--background", "--factory-startup", "--disable-autoexec",
                    "--python-exit-code", "1", "--python", str(generator), "--", str(root)],
                   stdout=stream, stderr=-2, check=True)
    config = root / "smoke.json"
    value = run_pipeline.read(config)
    value["blender"] = str(executable)
    run_pipeline.write(config, value)
    result = run_pipeline.Runner(config).run(through)
    result["config"] = str(config)
    return result


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("init", "inspect", "run", "status", "diagnose", "review"):
        if argv[0] == "init" and "--blender" not in argv:
            try:
                argv += ["--blender", str(blender_path())]
            except FileNotFoundError as error:
                print(json.dumps({"status": "needs_setup", "error": str(error)}))
                return 2
        return run_pipeline.main(argv)
    parser = argparse.ArgumentParser(description="Blender animation pipeline, local MCP tools, and one-command setup")
    parser.add_argument("--version", action="version", version="0.1.0")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in [("init", "Initialize an isolated model configuration"),
                            ("inspect", "Inspect the source model in Blender"),
                            ("run", "Execute or resume pipeline stages"),
                            ("status", "Read stage status and cache validity"),
                            ("diagnose", "Record an established failure diagnosis"),
                            ("review", "Record real visual review for the current candidate")]:
        commands.add_parser(name, help=help_text)
    commands.add_parser("doctor", help="Read-only runtime and Blender diagnostics")
    setup = commands.add_parser("setup", help="Install Blender, the skill, and MCP registration")
    setup.add_argument("--client", choices=("auto", "generic", "codex", "claude-code", "claude-desktop", "cursor"), default="auto")
    setup.add_argument("--project", default=".")
    setup.add_argument("--blender")
    setup.add_argument("--no-download", action="store_true")
    setup.add_argument("--managed-blender", action="store_true", help="Install the pinned build even if another Blender exists")
    setup.add_argument("--skill-dir")
    setup.add_argument("--no-skill", action="store_true")
    example = commands.add_parser("demo", help="Generate a synthetic rig, then export, certify, and preview it")
    example.add_argument("directory")
    example.add_argument("--blender")
    example.add_argument("--through", choices=run_pipeline.STAGES, default="preview")
    commands.add_parser("mcp", help="Start the local stdio MCP server")
    args = parser.parse_args(argv)
    try:
        if args.command == "mcp":
            from .server import main as server_main
            server_main()
            return 0
        if args.command == "doctor":
            result = doctor()
        elif args.command == "demo":
            result = demo(args.directory, args.blender, args.through)
        else:
            from .setup import setup as install
            result = install(args.client, args.project, args.blender, not args.no_download,
                             args.managed_blender, args.skill_dir, args.no_skill)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] in ("success", "ready") else 2
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, run_pipeline.PipelineError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
