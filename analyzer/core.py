"""
대회 영상 분석 핵심.

1. search(): YouTube 검색 (yt-dlp, API 키 없음)
2. analyze_video(): 영상 소리를 임시 폴더에 받아 일정 간격으로 잘라 음원 인식(Shazam) → 같은 곡이 이어지는 구간으로 묶음
   - 받은 소리는 분석이 끝나면 바로 지운다. 저장·공유하지 않는다.
3. 결과는 사람이 고칠 수 있는 텍스트 목록(listfile.py)으로 저장한다.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import imageio_ffmpeg
import yt_dlp
from shazamio import Shazam

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


@dataclass
class Video:
    id: str
    title: str
    channel: str
    duration: int | None
    url: str
    thumbnail: str


@dataclass
class Hit:
    """한 구간(창)의 인식 결과"""

    at: float
    key: str | None
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    released: str | None = None
    shazam_url: str | None = None


@dataclass
class Segment:
    """같은 곡이 이어진 구간"""

    start: float
    end: float
    title: str
    artist: str
    hits: int
    album: str | None = None
    released: str | None = None
    shazam_url: str | None = None


@dataclass
class Analysis:
    video: Video
    description: str
    segments: list[Segment] = field(default_factory=list)
    windows: int = 0
    unrecognized: int = 0


def _ydl(extra: dict | None = None) -> yt_dlp.YoutubeDL:
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "ffmpeg_location": FFMPEG}
    opts.update(extra or {})
    return yt_dlp.YoutubeDL(opts)


def search(query: str, limit: int = 15) -> list[Video]:
    with _ydl({"extract_flat": True}) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    out: list[Video] = []
    for e in info.get("entries") or []:
        if not e or not e.get("id"):
            continue
        vid = e["id"]
        out.append(
            Video(
                id=vid,
                title=e.get("title") or "",
                channel=e.get("channel") or e.get("uploader") or "",
                duration=int(e["duration"]) if e.get("duration") else None,
                url=f"https://www.youtube.com/watch?v={vid}",
                thumbnail=f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
            )
        )
    return out


def video_info(video_id: str) -> tuple[Video, str]:
    with _ydl() as ydl:
        e = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    v = Video(
        id=e["id"],
        title=e.get("title") or "",
        channel=e.get("channel") or e.get("uploader") or "",
        duration=int(e["duration"]) if e.get("duration") else None,
        url=f"https://www.youtube.com/watch?v={e['id']}",
        thumbnail=f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg",
    )
    return v, e.get("description") or ""


def _download_audio(video_id: str, folder: Path) -> Path:
    with _ydl({"format": "bestaudio/best", "outtmpl": str(folder / "audio.%(ext)s")}) as ydl:
        ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
    files = [p for p in folder.iterdir() if p.name.startswith("audio.")]
    if not files:
        raise RuntimeError("소리를 받지 못했습니다")
    return files[0]


def _cut(src: Path, start: float, length: float, dst: Path) -> bool:
    r = subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-ss", str(start), "-t", str(length), "-i", str(src),
         "-ac", "1", "-ar", "16000", "-f", "wav", str(dst)],
        capture_output=True,
    )
    return r.returncode == 0 and dst.exists() and dst.stat().st_size > 10_000


def _meta(track: dict, name: str) -> str | None:
    for sec in track.get("sections") or []:
        for m in sec.get("metadata") or []:
            if m.get("title") == name:
                return m.get("text")
    return None


async def _recognize_all(
    audio: Path, duration: float, step: float, window: float, work: Path,
    progress: Callable[[str], None] | None, precision: float = 12,
) -> list[Hit]:
    """
    인식 서버가 3번에 1번꼴로 15~100초씩 기다리게 하므로 인식 횟수를 줄인다.
    1) step 초마다 한 번씩 인식 (기본 45초 — 한 곡 3분이면 4번쯤)
    2) 이웃한 두 지점의 결과가 다르면(곡이 바뀜) 그 사이를 반씩 나눠 precision 초 안으로 경계를 좁힌다.
    """
    shazam = Shazam()
    probes: dict[float, Hit] = {}
    count = 0

    async def probe(t: float) -> Hit:
        nonlocal count
        if t in probes:
            return probes[t]
        count += 1
        clip = work / f"w{count}.wav"
        hit = Hit(at=t, key=None)
        if _cut(audio, t, window, clip):
            try:
                res = await shazam.recognize(str(clip))
            except Exception as e:  # 인식 서버 오류는 그 지점만 건너뛴다
                res = {}
                if progress:
                    progress(f"  {fmt(t)} 인식 오류: {e}")
            track = res.get("track") if isinstance(res, dict) else None
            if track:
                hit = Hit(
                    at=t, key=str(track.get("key")), title=track.get("title"), artist=track.get("subtitle"),
                    album=_meta(track, "Album"), released=_meta(track, "Released"), shazam_url=track.get("url"),
                )
            clip.unlink(missing_ok=True)
        probes[t] = hit
        if progress:
            label = f"{hit.artist} - {hit.title}" if hit.key else "인식 안 됨"
            progress(f"[{count}] {fmt(t)} {label}")
        return hit

    async def refine(t1: float, h1: Hit, t2: float, h2: Hit, depth: int = 0) -> None:
        if h1.key == h2.key or t2 - t1 <= precision or depth > 5:
            return
        mid = round((t1 + t2) / 2)
        hm = await probe(mid)
        if hm.key == h1.key:
            await refine(mid, hm, t2, h2, depth + 1)
        elif hm.key == h2.key:
            await refine(t1, h1, mid, hm, depth + 1)
        else:  # 사이에 다른 곡·박수 등이 더 있다
            await refine(t1, h1, mid, hm, depth + 1)
            await refine(mid, hm, t2, h2, depth + 1)

    last = max(0.0, duration - window)
    points = [float(t) for t in range(0, int(last) + 1, int(step))]
    if points and last - points[-1] > precision:
        points.append(last)
    if progress:
        progress(f"1단계: {len(points)}개 지점 인식 ({int(step)}초 간격)")
    for t in points:
        await probe(t)
    if progress:
        progress("2단계: 곡이 바뀌는 곳 좁히기")
    for a, b in zip(points, points[1:]):
        await refine(a, probes[a], b, probes[b])
    if progress:
        progress(f"인식 {count}번")
    return [probes[t] for t in sorted(probes)]


def merge(hits: list[Hit], window: float, min_hits: int = 2, gap_tolerance: int = 1) -> list[Segment]:
    """
    시각 순서의 인식 결과에서 같은 곡이 이어진 지점들을 한 구간으로 묶는다.
    - 사이에 인식 안 된 지점이 gap_tolerance 개까지 끼어 있어도 같은 구간으로 본다 (박수·해설 등).
    - min_hits 번 미만으로만 잡힌 곡은 우연히 섞인 소리일 수 있어 뺀다.
    """
    segs: list[Segment] = []
    cur: Segment | None = None
    cur_key: str | None = None
    misses = 0
    for h in hits:
        if h.key and h.key == cur_key and cur:
            cur.end = h.at + window
            cur.hits += 1
            misses = 0
            continue
        if not h.key and cur and misses < gap_tolerance:
            misses += 1
            continue
        if cur:
            segs.append(cur)
        cur, cur_key, misses = None, None, 0
        if h.key:
            cur = Segment(start=h.at, end=h.at + window, title=h.title or "", artist=h.artist or "", hits=1,
                          album=h.album, released=h.released, shazam_url=h.shazam_url)
            cur_key = h.key
    if cur:
        segs.append(cur)
    return [s for s in segs if s.hits >= min_hits]


def analyze_video(
    video_id: str, step: float = 45, window: float = 12, min_hits: int = 2,
    progress: Callable[[str], None] | None = None,
) -> Analysis:
    video, description = video_info(video_id)
    work = Path(tempfile.mkdtemp(prefix="ca_"))
    try:
        if progress:
            progress(f"소리 받는 중: {video.title}")
        audio = _download_audio(video_id, work)
        duration = float(video.duration or 0) or 3600
        started = time.time()
        hits = asyncio.run(_recognize_all(audio, duration, step, window, work, progress))
        segs = merge(hits, window, min_hits=min_hits)
        if progress:
            progress(f"끝: {len(segs)}곡, {int(time.time() - started)}초")
        return Analysis(video=video, description=description, segments=segs, windows=len(hits),
                        unrecognized=sum(1 for h in hits if not h.key))
    finally:
        # 받은 소리는 남기지 않는다
        shutil.rmtree(work, ignore_errors=True)


def fmt(sec: float) -> str:
    s = int(round(sec))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
