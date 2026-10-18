"""Optional video proof: a deterministic Pillow + ffmpeg replay drawn only from a bundle's scrubbed evidence.json.

It is a replay of recorded evidence, not a screen recording, and every frame says so. Nothing here reads run
artifacts; `lines(evidence)` is the complete text that `render` draws, so a caller can check it before rendering.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

MISSING = "Video proof needs: pip install 'litetraffic[video]' (pillow, imageio-ffmpeg) or a system ffmpeg plus pillow."
WIDTH, HEIGHT, FPS = 1280, 720, 10
GIF_WIDTH, GIF_HEIGHT, GIF_FPS = 640, 360, 6
MP4_LIMIT = int(9.5 * 1024 * 1024)  # under the 10 MB upload limit of GitHub's Free plan
GIF_LIMIT = 5 * 1024 * 1024
FFMPEG_TIMEOUT_SECONDS = 120
REVEAL_SECONDS, HOLD_SECONDS = 10, 4
FONT_SIZE, LINE_HEIGHT, MARGIN, TOP = 18, 24, 24, 52
MAX_CHARS = 108  # JetBrains Mono at 18 px is 11 px a glyph; 1280 px less margins
FONT_FILE = Path(__file__).parent / "dashboard_ui" / "assets" / "jetbrains-mono-latin-wght-normal-B9CIFXIH.woff2"
NOTICE = "Replayed from LiteTraffic run artifacts — not a live screen recording"

BG, BAR, FG, DIM = (24, 24, 27), (39, 39, 42), (228, 228, 231), (150, 150, 160)
RED, GREEN, YELLOW, BLUE = (248, 113, 113), (74, 222, 128), (250, 204, 21), (125, 211, 252)


def ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - imageio-ffmpeg raises a bare RuntimeError when its binary is missing
        return None


def _has_pillow() -> bool:
    try:
        import PIL.Image  # noqa: F401
        import PIL.ImageDraw  # noqa: F401
    except ImportError:
        return False
    return True


def available() -> str | None:
    """The ffmpeg path when Pillow and ffmpeg are both usable, else None (callers show MISSING)."""
    return ffmpeg() if _has_pillow() else None


def missing() -> list[str]:
    """What video proof still needs: some of "pillow", "ffmpeg"; empty when it can render."""
    return ([] if _has_pillow() else ["pillow"]) + ([] if ffmpeg() else ["ffmpeg"])


def _short(value: object, limit: int = 60) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _matcher(matcher: object) -> str:
    if isinstance(matcher, dict) and len(matcher) == 1:
        (key, value), = matcher.items()
        symbol = {"eq": "=", "gte": ">=", "lte": "<=", "len": "length ="}.get(key)
        if symbol:
            return f"{symbol} {_short(value, 30)}"
        if key == "exists":
            return "present" if value else "absent"
    return f"= {_short(matcher, 30)}"


def _intro(evidence: dict) -> list[tuple[str, tuple]]:
    run, target = evidence.get("run") or {}, evidence.get("target") or {}
    phases = [p for p in run.get("schedule") or [] if isinstance(p, dict)]
    schedule = ", ".join(f"{p.get('name', 'phase')} {p.get('rate', '?')}/s for {p.get('seconds', '?')}s" for p in phases)
    return [
        ("LiteTraffic replay", FG), (NOTICE, YELLOW),
        (_short(evidence.get("title") or "", MAX_CHARS), FG),
        (f"litetraffic {evidence.get('litetraffic_version', '?')}   fingerprint {evidence.get('fingerprint', '?')}", DIM),
        ("", FG),
        ("$ litetraffic run   (as recorded)", BLUE),
        (f"run       {run.get('run_id', '?')}", FG),
        (f"scenario  {run.get('scenario', '?')}   sha256 {str(run.get('scenario_sha256') or '?')[:16]}", FG),
        (f"target    {target.get('origin', '$TARGET')}   origin sha256 {str(target.get('origin_sha256') or '?')[:12]}", FG),
        (f"version   {_short(target.get('version') or 'unknown', 90)}", DIM),
        (f"seed {run.get('seed', '?')}   engine {_short(str(run.get('engine') or '?').split(' (')[0], 30)}"
         f"   journeys {run.get('planned_journeys', '?')}   schedule {_short(schedule or '?', 50)}", DIM),
        (f"window    {run.get('started_at', '?')} -> {run.get('finished_at', '?')}", DIM),
    ]


def _traffic(evidence: dict) -> list[tuple[str, tuple]]:
    traffic = evidence.get("traffic") or {}
    out = [("", FG), (f"traffic   {'operation':<24} {'method':<6} {'path':<36} {'requests':>8}  statuses", BLUE)]
    for op in (traffic.get("operations") or [])[:10]:
        statuses = " ".join(f"{code}x{count}" for code, count in sorted((op.get("statuses") or {}).items()))
        out.append((f"          {_short(op.get('operation'), 24):<24} {_short(op.get('method') or '?', 6):<6} "
                    f"{_short(op.get('path') or '?', 36):<36} {op.get('requests', '?'):>8}  {_short(statuses, 18)}", FG))
    overlap = traffic.get("overlap") or {}
    if overlap:
        out.append((f"          peak in flight: {_short(', '.join(f'{k} {v}' for k, v in sorted(overlap.items())), 80)}", DIM))
    return out


def _failing(evidence: dict) -> list[tuple[str, tuple]]:
    out = [("", FG), ("checks    (what the run asserted, and what it saw)", BLUE)]
    for item in (evidence.get("failing") or [])[:6]:
        count = f"   {item['failed']}/{item.get('samples', '?')} samples failed" if item.get("failed") is not None else ""
        out.append((f"  {_short(item.get('id'), 40)}{count}", YELLOW))
        for check in (item.get("checks") or [])[:4]:
            out.append((f"    {_short(check.get('pointer'), 34):<34} expected {_matcher(check.get('expected')):<16} "
                        f"actual {_short(check.get('actual'), 24)}   VIOLATED", RED))
        if not item.get("checks"):
            for failure in (item.get("failures") or [])[:3]:
                failure = failure if isinstance(failure, dict) else {}
                out.append((f"    expected {_short(failure.get('expected'), 36)}   actual {_short(failure.get('actual'), 36)}   VIOLATED", RED))
    return out


def _outro(evidence: dict) -> list[tuple[str, tuple]]:
    verdict = str(evidence.get("verdict") or "?")
    kind = (evidence.get("classification") or {}).get("kind", "?")
    out = [("", FG), (f"VERDICT  {verdict.upper()}   ({kind}; completeness {evidence.get('completeness', '?')})",
                      RED if verdict == "fail" else GREEN)]
    out += [(f"  limitation: {_short(text, 90)}", DIM) for text in (evidence.get("limitations") or [])[:2]]
    out.append(("source artifacts (sha256, check against artifacts.json):", DIM))
    out += [(f"  {name:<22} {digest}", DIM) for name, digest in sorted((evidence.get("source_sha256") or {}).items())]
    return out


def lines(evidence: dict) -> list[tuple[str, tuple]]:
    """Every line the replay draws, with its colour; built from evidence.json alone and clipped to the frame width."""
    drawn = _intro(evidence) + _traffic(evidence) + _failing(evidence) + _outro(evidence)
    return [(text if len(text) <= MAX_CHARS else text[: MAX_CHARS - 1] + "…", colour) for text, colour in drawn]


def _fonts():
    from PIL import ImageFont

    try:
        return ImageFont.truetype(str(FONT_FILE), FONT_SIZE), ImageFont.truetype(str(FONT_FILE), FONT_SIZE + 10)
    except OSError:  # a Pillow built without WOFF2 (brotli) support
        return ImageFont.load_default(size=FONT_SIZE), ImageFont.load_default(size=FONT_SIZE + 10)


def _frame(shown: list[tuple[str, tuple]], fonts, final: bool) -> bytes:
    from PIL import Image, ImageDraw

    font, big = fonts
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, WIDTH, 36], fill=BAR)
    for index, colour in enumerate([(239, 68, 68), (234, 179, 8), (34, 197, 94)]):
        draw.ellipse([16 + index * 22, 12, 28 + index * 22, 24], fill=colour)
    draw.text((100, 8), "litetraffic report issue — replay", font=font, fill=DIM)
    rows = (HEIGHT - TOP - 44 - (10 if final else 0)) // LINE_HEIGHT  # the final frame's verdict line is taller
    y = TOP
    for text, colour in shown[-rows:]:
        large = final and text.startswith("VERDICT")
        draw.text((MARGIN, y - (4 if large else 0)), text, font=big if large else font, fill=colour)
        y += LINE_HEIGHT + (10 if large else 0)
    draw.rectangle([0, HEIGHT - 32, WIDTH, HEIGHT], fill=BAR)
    draw.text((MARGIN, HEIGHT - 27), NOTICE, font=font, fill=YELLOW)
    return image.tobytes()


def _frames(drawn: list[tuple[str, tuple]]):
    """The whole video as raw RGB frames: each line revealed in turn over REVEAL_SECONDS, then the result held."""
    fonts = _fonts()
    per_line = max(1, REVEAL_SECONDS * FPS // max(1, len(drawn)))
    for count in range(1, len(drawn) + 1):
        frame = _frame(drawn[:count], fonts, final=False)
        for _ in range(per_line):
            yield frame
    final = _frame(drawn, fonts, final=True)
    for _ in range(HOLD_SECONDS * FPS):
        yield final


def _ffmpeg(args: list[str], frames=None) -> None:
    proc = subprocess.Popen([args[0], "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args[1:]],
                            stdin=subprocess.PIPE if frames is not None else subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        if frames is not None:
            try:
                for frame in frames:
                    proc.stdin.write(frame)
            finally:
                proc.stdin.close()
        _, err = proc.communicate(timeout=FFMPEG_TIMEOUT_SECONDS)
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg exited {proc.returncode}: {err.decode('utf-8', 'replace').strip()[-300:]}")


BITEXACT = ["-threads", "1", "-fflags", "+bitexact", "-flags", "+bitexact", "-map_metadata", "-1"]


def render(evidence: dict, out_dir) -> list[Path]:
    """Write proof.mp4 (and proof.gif) into out_dir from the scrubbed evidence document; byte-identical across runs."""
    exe = ffmpeg() if _has_pillow() else None
    if exe is None:
        raise RuntimeError(MISSING)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    mp4, gif = out / "proof.mp4", out / "proof.gif"
    try:
        _ffmpeg([exe, "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-",
                 "-c:v", "libx264", "-preset", "medium", "-crf", "28", "-pix_fmt", "yuv420p", "-x264-params", "threads=1",
                 *BITEXACT, "-movflags", "+faststart", str(mp4)], _frames(lines(evidence)))
        _ffmpeg([exe, "-i", str(mp4), "-vf", f"fps={GIF_FPS},scale={GIF_WIDTH}:{GIF_HEIGHT}:flags=lanczos,split[a][b];"
                 "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4", *BITEXACT, str(gif)])
    except BaseException:
        for path in (mp4, gif):
            path.unlink(missing_ok=True)
        raise
    kept = []
    for path, limit in ((mp4, MP4_LIMIT), (gif, GIF_LIMIT)):
        if path.stat().st_size < limit:
            kept.append(path)
        else:
            path.unlink()  # too large to upload; the other format may still fit
    if not kept:
        raise RuntimeError("the rendered video is over the upload size limits")
    return kept
