"""Durable background jobs keep expensive Blender stages outside MCP timeouts."""
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from .environment import home
from .pipeline.run_pipeline import pid_alive, read, write


def start(argv, cwd):
    job_id = uuid.uuid4().hex
    folder = home() / "jobs" / job_id
    folder.mkdir(parents=True)
    write(folder / "job.json", {"argv": argv, "cwd": str(Path(cwd).resolve()), "created_at": time.time()})
    with (folder / "worker.log").open("w") as log:
        process = subprocess.Popen([sys.executable, "-m", "companion_animation.jobs", str(folder)],
                                   stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                   start_new_session=sys.platform != "win32")
    write(folder / "owner.json", {"pid": process.pid})
    return {"job_id": job_id, "status": "running", "log": str(folder / "output.log")}


def status(job_id):
    if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        raise ValueError("Invalid job id")
    folder = home() / "jobs" / job_id
    result = folder / "result.json"
    if result.exists():
        report = read(result)
    else:
        owner = read(folder / "owner.json")
        report = {"status": "running" if pid_alive(owner["pid"]) else "interrupted"}
    output = folder / "output.log"
    report.update(job_id=job_id, log=str(output), tail=output.read_text(errors="replace")[-12000:] if output.exists() else "")
    return report


def main():
    folder = Path(sys.argv[1])
    job = read(folder / "job.json")
    try:
        with (folder / "output.log").open("w", encoding="utf-8") as output:
            process = subprocess.run(job["argv"], cwd=job["cwd"], stdout=output, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        report = {"status": "success" if process.returncode == 0 else "needs_attention" if process.returncode == 2 else "failed",
                  "exit_code": process.returncode}
    except Exception as error:
        report = {"status": "failed", "error": str(error)}
    write(folder / "result.json", report)


if __name__ == "__main__":
    main()
