"""
공홈 /music 곡 목록 업로드 파일 (.txt, UTF-8).

    제목: 2024 Mundial 결승 R4
    대회영상: https://www.youtube.com/watch?v=XXXXXXXXXXX
    메모: (선택, 한 줄)

    # 시작-끝 | 곡명 | 아티스트 | 음악 YouTube 주소(선택) | 장르(선택: tango/vals/milonga)
    00:15-03:57 | Nochero Soy | Osvaldo Pugliese | https://www.youtube.com/watch?v=YYYYYYYYYYY |

- '#' 로 시작하는 줄·빈 줄은 무시. 칸 구분은 ' | '.
- 시작-끝은 대회영상 안의 시각(mm:ss 또는 h:mm:ss), 모르면 '-'.
- 공홈 쪽 형식과 같게 유지한다 (c:\\work\\web 의 /music 업로드).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yt_dlp

from .core import Analysis, fmt

GENRES = ("tango", "vals", "milonga")
# 공홈 업로드도 같은 별칭을 받는다
GENRE_ALIASES = {"탱고": "tango", "발스": "vals", "valse": "vals", "waltz": "vals", "밀롱가": "milonga"}


@dataclass
class Row:
    start: str  # "00:15" 또는 "-"
    end: str
    title: str
    artist: str
    music_url: str = ""
    genre: str = ""
    note: str = ""  # 파일에는 줄 위 주석(#)으로 쓴다


@dataclass
class SongList:
    title: str
    video_url: str = ""
    memo: str = ""
    rows: list[Row] = field(default_factory=list)


def _fold(s: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def find_music_url(artist: str, title: str) -> tuple[str, str]:
    """음악 YouTube 주소 찾기. '- Topic' 공식 음원 채널을 먼저 고른다. (주소, 채널) 또는 ("", "")"""
    q = f"{artist} {title}".strip()
    if not q:
        return "", ""
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True}) as ydl:
            info = ydl.extract_info(f"ytsearch8:{q}", download=False)
    except Exception:
        return "", ""
    entries = [e for e in (info.get("entries") or []) if e and e.get("id")]
    if not entries:
        return "", ""
    words = [w for w in re.findall(r"\w+", _fold(title)) if len(w) > 2]
    # 오케스트라 이름의 성(가장 긴 단어)이 채널·제목에 있으면 같은 연주로 본다 (예: arienzo, pugliese)
    artist_words = sorted(re.findall(r"\w+", _fold(artist.split("&")[0])), key=len, reverse=True)[:1]

    def score(e: dict) -> tuple[int, int, int, int]:
        ch = _fold(e.get("channel") or e.get("uploader") or "")
        t = _fold(e.get("title") or "")
        topic = ch.endswith(" - topic")
        title_ok = all(w in t for w in words) if words else True
        artist_ok = all(w in f"{ch} {t}" for w in artist_words)
        dur = e.get("duration") or 0
        song_len = 90 <= dur <= 420  # 곡 한 곡 길이 (공연·모음 영상 제외)
        return (int(title_ok and artist_ok and topic), int(title_ok and artist_ok and song_len),
                int(title_ok and artist_ok), int(title_ok))

    best = max(entries, key=score)
    if score(best)[3] == 0:
        return "", ""
    return f"https://www.youtube.com/watch?v={best['id']}", best.get("channel") or best.get("uploader") or ""


def from_analysis(a: Analysis, title: str, memo: str = "", find_urls: bool = True, progress=None) -> SongList:
    sl = SongList(title=title, video_url=a.video.url, memo=memo)
    for s in a.segments:
        url, channel = ("", "")
        if find_urls:
            url, channel = find_music_url(s.artist, s.title)
            if progress:
                progress(f"음악 주소: {s.artist} - {s.title} → {url or '못 찾음'} {f'({channel})' if channel else ''}")
        bits = [f"Shazam {s.hits}회 인식"]
        if s.album:
            bits.append(f"음반 {s.album}{f' ({s.released})' if s.released else ''}")
        if channel:
            bits.append(f"음악 영상: {channel}")
        sl.rows.append(Row(start=fmt(s.start), end=fmt(s.end), title=s.title, artist=s.artist,
                           music_url=url, note=" · ".join(bits)))
    return sl


def _cell(v: str) -> str:
    return v.replace("|", "/").replace("\n", " ").strip()


def dumps(sl: SongList) -> str:
    lines = [f"제목: {_cell(sl.title)}"]
    if sl.video_url:
        lines.append(f"대회영상: {sl.video_url}")
    if sl.memo:
        lines.append(f"메모: {_cell(sl.memo)}")
    lines += ["", "# 시작-끝 | 곡명 | 아티스트 | 음악 YouTube 주소(선택) | 장르(선택: tango/vals/milonga)"]
    for r in sl.rows:
        if r.note:
            lines.append(f"# {_cell(r.note)}")
        span = "-" if r.start == "-" else f"{r.start}-{r.end}"
        lines.append(" | ".join([span, _cell(r.title), _cell(r.artist), _cell(r.music_url), _cell(r.genre)]))
    return "\n".join(lines) + "\n"


_TIME = r"\d{1,2}:\d{2}(?::\d{2})?"


def loads(text: str) -> tuple[SongList, list[str]]:
    """파일 읽기 + 형식 검사. (목록, 문제 목록)"""
    sl = SongList(title="")
    problems: list[str] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(제목|대회영상|메모)\s*:\s*(.*)$", line)
        if m:
            key, val = m.groups()
            if key == "제목":
                sl.title = val.strip()
            elif key == "대회영상":
                sl.video_url = val.strip()
            else:
                sl.memo = val.strip()
            continue
        cells = [c.strip() for c in line.split("|")]
        cells += [""] * (5 - len(cells))
        span, title, artist, url, genre = cells[:5]
        if span == "-":
            start = end = "-"
        else:
            sm = re.match(rf"^({_TIME})\s*-\s*({_TIME})$", span)
            if not sm:
                problems.append(f"{n}번째 줄: 시작-끝 '{span}' 형식이 아니에요 (예: 00:12-03:05, 모르면 -)")
                continue
            start, end = sm.groups()
        if not title:
            problems.append(f"{n}번째 줄: 곡명이 비어 있어요")
        if url and not re.search(r"(youtube\.com/watch\?v=|youtu\.be/|music\.youtube\.com/)", url):
            problems.append(f"{n}번째 줄: YouTube 주소가 아니에요")
        g = GENRE_ALIASES.get(genre.strip().lower(), genre.strip().lower())
        if g and g not in GENRES:
            problems.append(f"{n}번째 줄: 장르는 tango/vals/milonga(탱고/발스/밀롱가) 중 하나예요")
        sl.rows.append(Row(start=start, end=end, title=title, artist=artist, music_url=url, genre=g))
    if not sl.title:
        problems.append("'제목:' 줄이 없어요")
    if not 1 <= len(sl.rows) <= 200:
        problems.append(f"곡 줄은 1~200개여야 해요 (지금 {len(sl.rows)}개)")
    return sl, problems


def save(sl: SongList, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    name = re.sub(r'[\\/:*?"<>|]+', " ", sl.title).strip() or "목록"
    path = folder / f"{name}.txt"
    i = 2
    while path.exists():
        path = folder / f"{name} ({i}).txt"
        i += 1
    path.write_text(dumps(sl), encoding="utf-8")
    return path
