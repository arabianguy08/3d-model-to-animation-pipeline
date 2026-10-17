"""Local stdio MCP server. No HTTP listener or third-party Blender add-on."""
import json
from pathlib import Path
import sys

from mcp.server.fastmcp import FastMCP, Image

from . import jobs
from .environment import blender_path, doctor, hidden_run
from .pipeline import run_pipeline

mcp = FastMCP("Companion Animation Pipeline")


def invoke_runner(arguments):
    process = hidden_run([sys.executable, "-m", "companion_animation.cli", *arguments],
                         capture_output=True, text=True, timeout=60)
    try:
        result = json.loads(process.stdout)
    except ValueError as error:
        raise RuntimeError("Runner produced no JSON result: " + process.stderr[-2000:]) from error
    return {"exit_code": process.returncode, "result": result}


@mcp.tool()
def environment_info() -> dict:
    """Check the installed runner and Blender without changing configuration."""
    return doctor()


@mcp.tool()
def pipeline_init(config: str, model: str, workspace: str) -> dict:
    """Create an isolated config for a supplied model; never overwrite an existing config."""
    return invoke_runner(["init", config, "--model", model, "--workspace", workspace,
                          "--blender", str(blender_path())])


@mcp.tool()
def pipeline_status(config: str) -> dict:
    """Read cached stages, failures, pending calibration and review requirements."""
    return invoke_runner(["status", config])


@mcp.tool()
def pipeline_run(config: str, through: str = "preview") -> dict:
    """Start/resume stages as a background job. Poll job_status; exit 2 means work/review remains."""
    if through not in run_pipeline.STAGES:
        raise ValueError("Unknown pipeline stage")
    path = Path(config).expanduser().resolve(strict=True)
    return jobs.start([sys.executable, "-m", "companion_animation.cli", "run", str(path), "--through", through], path.parent)


@mcp.tool()
def blender_script(script: str, workspace: str, blend_file: str = "") -> dict:
    """Run a trusted local Blender Python script in a hidden background process.

    The script must be inside workspace; scripts have normal user permissions,
    not a security sandbox. Author to derivative paths; never overwrite the source.
    sys.argv after -- contains the workspace path.
    """
    root = Path(workspace).expanduser().resolve(strict=True)
    path = Path(script).expanduser().resolve(strict=True)
    if not root.is_dir() or not path.is_relative_to(root) or path.suffix != ".py":
        raise ValueError("Script must be a .py file inside the supplied workspace")
    argv = [str(blender_path()), "--background", "--factory-startup", "--disable-autoexec"]
    if blend_file:
        argv.append(str(Path(blend_file).expanduser().resolve(strict=True)))
    argv += ["--python-exit-code", "1", "--python", str(path), "--", str(root)]
    return jobs.start(argv, root)


@mcp.tool()
def job_status(job_id: str) -> dict:
    """Read background job completion and its log tail. Does not infer visual approval."""
    return jobs.status(job_id)


@mcp.tool()
def record_review(config: str, evidence: str) -> dict:
    """Record real candidate-bound visual review from an existing evidence JSON file."""
    return invoke_runner(["review", config, "--evidence", evidence])


@mcp.tool()
def diagnose_stage(config: str, stage: str, note: str) -> dict:
    """Record an established failure cause before retrying a stage. Does not relax tolerances."""
    return invoke_runner(["diagnose", config, "--stage", stage, "--note", note])


@mcp.tool()
def view_artifact(path: str, workspace: str) -> Image:
    """Display an existing PNG/JPEG inside a model workspace; maximum 10 MiB."""
    root = Path(workspace).expanduser().resolve(strict=True)
    image = Path(path).expanduser().resolve(strict=True)
    if not image.is_relative_to(root) or image.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        raise ValueError("Expected a PNG/JPEG inside workspace")
    if image.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("Image exceeds 10 MiB; render a smaller review view")
    return Image(path=str(image))


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
