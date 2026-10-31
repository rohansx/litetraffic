"""Optional video proof: a deterministic Pillow + ffmpeg replay drawn only from a bundle's scrubbed evidence.json.

It is a replay of recorded evidence, not a screen recording, and every frame says so: a title card (what was tested),
the per-journey samples revealed row by row, what the app recorded afterwards against the expectation, and the verdict
with the HTTP statuses that hid it. Nothing here reads run artifacts; `lines(evidence)` is the complete text that
`render` draws, so a caller can check it before rendering. Text is ASCII only, so no glyph can be missing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

MISSING = "Video proof needs: pip install 'litetraffic[video]' (pillow, imageio-ffmpeg) or a system ffmpeg plus pillow."
WIDTH, HEIGHT, FPS = 1280, 720, 10
GIF_WIDTH, GIF_HEIGHT, GIF_FPS = 640, 360, 6
MP4_LIMIT = int(9.5 * 1024 * 1024)  # under the 10 MB upload limit of GitHub's Free plan
GIF_LIMIT = 5 * 1024 * 1024
FFMPEG_TIMEOUT_SECONDS = 120
FONT_SIZE, BIG_SIZE, LINE_HEIGHT, MARGIN, TOP = 18, 30, 26, 40, 48
MAX_CHARS = 108  # JetBrains Mono at 18 px is 11 px a glyph; 1280 px less margins
MAX_ROWS, MAX_COLUMNS, CELL = 12, 6, 18
FONT_FILE = Path(__file__).parent / "dashboard_ui" / "assets" / "jetbrains-mono-latin-wght-normal-B9CIFXIH.woff2"
NOTICE = "Replay of recorded LiteTraffic evidence, not a screen recording"
# (scene, seconds): 10 s in all. The samples scene reveals its rows over its time; the others are shown whole.
TIMING = (("title", 2), ("samples", 4), ("invariant", 2), ("verdict", 2))

BG, FG, DIM = (24, 24, 27), (228, 228, 231), (150, 150, 160)
RED, GREEN, YELLOW, BLUE = (248, 113, 113), (74, 222, 128), (250, 204, 21), (125, 211, 252)
ASCII = {"\u2264": "<=", "\u2265": ">=", "\u2014": "-", "\u2013": "-", "\u2026": "...", "\u00d7": "x", "\u2192": "->"}


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


def _ascii(text: str) -> str:
    """ASCII only, so no glyph can render as a missing-character box."""
    for char, plain in ASCII.items():
        text = text.replace(char, plain)
    return text.encode("ascii", "replace").decode("ascii")


def _short(value: object, limit: int = 60) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
    text = _ascii(" ".join(str(text).split()))
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _matcher(matcher: object) -> str:
    if isinstance(matcher, dict) and len(matcher) == 1:
        (key, value), = matcher.items()
        symbol = {"eq": "=", "gte": ">=", "lte": "<=", "len": "length ="}.get(key)
        if symbol:
            return f"{symbol} {_short(value, 30)}"
        if key == "exists":
            return "present" if value else "absent"
    return f"= {_short(matcher, 30)}"


def _title(evidence: dict) -> list[tuple[str, tuple]]:
    run, target, known = evidence.get("run") or {}, evidence.get("target") or {}, evidence.get("known_issue") or {}
    out = [(NOTICE, YELLOW), ("", FG), (_short(evidence.get("title") or "", MAX_CHARS), FG), ("", FG),
           (f"Target version: {_short(target.get('version') or 'not recorded', 80)}", FG),
           (f"Scenario {_short(run.get('scenario', '?'), 50)}, seed {run.get('seed', '?')}, "
            f"{run.get('planned_journeys', '?')} journeys", DIM)]
    if known.get("reporter"):
        out.append((f"Known issue: {_short(known['reporter'], 90)}", DIM))
    return out


def _samples(evidence: dict) -> list[tuple[str, tuple]]:
    """The per-journey table: one line per journey, columns clipped so the row fits the frame."""
    table = evidence.get("journey_samples") or {}
    columns, rows = list(table.get("columns") or [])[:MAX_COLUMNS], list(table.get("rows") or [])[:MAX_ROWS]
    if not rows:
        return [("What each journey recorded", BLUE), ("(the run recorded no per-journey samples)", DIM)]
    cells = [[_short(row.get("journey"), CELL), *(_short(v, CELL) for v in (row.get("values") or [])[:MAX_COLUMNS])] for row in rows]
    header = ["journey", *(_short(c, CELL) for c in columns)]
    widths = [max(len(line[i]) if i < len(line) else 0 for line in [header, *cells]) for i in range(len(header))]

    def joined(values: list[str]) -> str:
        return "  ".join(value.ljust(width) for value, width in zip(values, widths)).rstrip()

    out = [("What each journey recorded", BLUE), (joined(header), DIM)]
    out += [(joined(line), RED if row.get("failed") else FG) for line, row in zip(cells, rows)]
    if (table.get("total") or 0) > len(rows):
        out.append((f"first {len(rows)} of {table['total']} journeys", DIM))
    return out


def _invariant(evidence: dict) -> list[tuple[str, tuple]]:
    out = [("What the app recorded afterwards", BLUE)]
    for item in (evidence.get("failing") or [])[:4]:
        out.append((_short(item.get("id"), 60), YELLOW))
        for check in (item.get("checks") or [])[:3]:
            out.append((f"  {_short(check.get('pointer'), 40)}   expected {_matcher(check.get('expected'))}   "
                        f"actual {_short(check.get('actual'), 24)}   VIOLATED", RED))
        if not item.get("checks"):
            for failure in (item.get("failures") or [])[:3]:
                failure = failure if isinstance(failure, dict) else {}
                out.append((f"  expected {_short(failure.get('expected'), 36)}   actual {_short(failure.get('actual'), 36)}   VIOLATED", RED))
    return out


def _verdict(evidence: dict) -> list[tuple[str, tuple]]:
    verdict = str(evidence.get("verdict") or "?").upper()
    kind = (evidence.get("classification") or {}).get("kind", "?")
    out = [(f"{verdict}   {kind}, evidence {evidence.get('completeness', '?')}", RED if verdict == "FAIL" else GREEN), ("", FG)]
    operations = ((evidence.get("traffic") or {}).get("operations") or [])[:6]
    if operations:
        out.append(("HTTP responses during the run:", DIM))
        for op in operations:
            statuses = ", ".join(f"{code} x{count}" for code, count in sorted((op.get("statuses") or {}).items()))
            out.append((f"  {_short(op.get('operation'), 30)}: {_short(statuses, 60)}", FG))
    return out


def scenes(evidence: dict) -> list[tuple[str, list[tuple[str, tuple]]]]:
    builders = {"title": _title, "samples": _samples, "invariant": _invariant, "verdict": _verdict}
    return [(name, [(text[:MAX_CHARS], colour) for text, colour in builders[name](evidence)]) for name, _ in TIMING]


def lines(evidence: dict) -> list[tuple[str, tuple]]:
    """Every line the replay draws (scenes and the footer notice), built from evidence.json alone."""
    return [line for _, drawn in scenes(evidence) for line in drawn] + [(NOTICE, YELLOW)]


def frame_plan(evidence: dict):
    """(scene index, lines shown) for every frame: TIMING's seconds per scene, the samples rows revealed in turn."""
    for index, ((name, drawn), (_, seconds)) in enumerate(zip(scenes(evidence), TIMING)):
        frames = seconds * FPS
        fixed = 2 if name == "samples" else len(drawn)  # the samples heading and column header stay; rows appear
        for frame in range(frames):
            shown = len(drawn) if name != "samples" else min(len(drawn), fixed + (len(drawn) - fixed) * (frame + 1) // max(1, frames - FPS))
            yield index, shown


def _fonts():
    from PIL import ImageFont

    try:
        return ImageFont.truetype(str(FONT_FILE), FONT_SIZE), ImageFont.truetype(str(FONT_FILE), BIG_SIZE)
    except OSError:  # a Pillow built without WOFF2 (brotli) support
        return ImageFont.load_default(size=FONT_SIZE), ImageFont.load_default(size=BIG_SIZE)


def _frame(drawn: list[tuple[str, tuple]], big_first: bool, footer: bool, fonts) -> bytes:
    from PIL import Image, ImageDraw

    font, big = fonts
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    y = TOP
    for number, (text, colour) in enumerate(drawn):
        large = big_first and number == 0
        draw.text((MARGIN, y), text, font=big if large else font, fill=colour)
        y += LINE_HEIGHT + (14 if large else 0)
    if footer:  # the title card already leads with the notice
        draw.text((MARGIN, HEIGHT - 34), NOTICE, font=font, fill=DIM)
    return image.tobytes()


def _frames(evidence: dict):
    """The whole video as raw RGB frames, drawn per frame_plan; identical frames are drawn once."""
    fonts, drawn_scenes, cache = _fonts(), scenes(evidence), {}
    for index, shown in frame_plan(evidence):
        key = (index, shown)
        if key not in cache:
            name, drawn = drawn_scenes[index]
            cache[key] = _frame(drawn[:shown], name in ("title", "verdict"), name != "title", fonts)
        yield cache[key]


def _ffmpeg(args: list[str], frames=None) -> None:
    """Run ffmpeg, streaming `frames` to its stdin; stderr goes to a temp file so neither pipe can fill and block.

    stdin is closed after the last frame and the process is then waited on: calling communicate() after closing
    stdin raises 'flush of closed file' on Python 3.12, which used to fail every render.
    """
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen([args[0], "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args[1:]],
                                stdin=subprocess.PIPE if frames is not None else subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=err)
        try:
            if frames is not None:
                try:
                    for frame in frames:
                        proc.stdin.write(frame)
                except BrokenPipeError:
                    pass  # ffmpeg exited early; its exit status and stderr say why
                finally:
                    try:
                        proc.stdin.close()
                    except BrokenPipeError:
                        pass
            proc.wait(timeout=FFMPEG_TIMEOUT_SECONDS)
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        if proc.returncode != 0:
            err.seek(0)
            detail = err.read().decode("utf-8", "replace").strip()[-300:]
            raise RuntimeError(f"ffmpeg exited {proc.returncode}: {detail}")


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
                 *BITEXACT, "-movflags", "+faststart", str(mp4)], _frames(evidence))
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
