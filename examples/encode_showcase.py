"""Optional documentation utility: pip install pillow imageio-ffmpeg.

python examples/encode_showcase.py FRAME_DIRECTORY MEDIA_DIRECTORY
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont
import imageio_ffmpeg


def encode(source, target):
    source, target = Path(source), Path(target)
    target.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((source / "frames.json").read_text())
    frames = []
    title = ImageFont.load_default(size=24)
    caption = ImageFont.load_default(size=14)
    for index, row in enumerate(manifest["frames"]):
        rendered = Image.open(source / row["file"]).convert("RGB")
        image = Image.new("RGB", (640, 572), (8, 15, 27))
        image.paste(rendered, (0, 62))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 640, 62), fill=(8, 15, 27))
        draw.text((20, 12), "PIP / " + row["clip"].upper(), font=title, fill=(220, 248, 248))
        draw.text((20, 41), "SYNTHETIC RIG  /  VERIFIED GLB  /  23 NAMED BONES", font=caption, fill=(117, 191, 201))
        draw.rectangle((0, 542, 640, 572), fill=(8, 15, 27))
        line = "Authored motion + LED blink" if row["clip"] == "Idle" else "Authored gesture + separate render-time charge VFX"
        draw.text((20, 549), line, font=caption, fill=(180, 205, 216))
        image.save(source / f"labeled-{index:04d}.png")
        frames.append(image)
    frames[0].save(target / "companion-demo.gif", save_all=True, append_images=frames[1:], duration=[round((i + 1) * 100 / manifest["fps"]) * 10 - round(i * 100 / manifest["fps"]) * 10 for i in range(len(frames))], loop=0, optimize=True)
    frames[44].save(target / "companion-demo.png")
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ffmpeg, "-y", "-framerate", str(manifest["fps"]), "-i", str(source / "labeled-%04d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart",
                    str(target / "companion-demo.mp4")], check=True, capture_output=True)
    print(json.dumps({p.name: {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                      for p in target.iterdir() if p.is_file()}, indent=2))


if __name__ == "__main__":
    encode(*sys.argv[1:])
