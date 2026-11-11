"""
CompetitionAnalyzer 로컬 화면 (http://127.0.0.1:5055)

    run.bat  (또는 .venv\\Scripts\\python app.py)

1. 검색: YouTube 에서 대회 영상 찾기
2. 분석: 고른 영상의 소리를 임시로 받아 음원 인식 → 곡 목록(.txt)을 results 폴더에 저장 (받은 소리는 바로 지움)
3. 목록: 텍스트를 고치고 형식 검사 → 저장. 공홈 관리자 화면 /music 에서 이 파일을 올린다.
"""

from __future__ import annotations

import os
import queue
import re
import unicodedata
import subprocess
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from flask import Flask, abort, redirect, render_template_string, request, send_file, url_for

from analyzer.core import analyze_video, fmt, search, video_info
from analyzer.listfile import dumps, from_analysis, loads

ROOT = Path(__file__).parent
RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)

app = Flask(__name__)


# ── 분석 작업 (한 번에 한 영상씩 차례로) ─────────────────
@dataclass
class Job:
    id: str
    video_id: str
    title: str
    step: int
    status: str = "대기"  # 대기 / 분석 중 / 완료 / 실패
    log: list[str] = field(default_factory=list)
    result: str | None = None
    created: float = field(default_factory=time.time)


JOBS: dict[str, Job] = {}
QUEUE: "queue.Queue[str]" = queue.Queue()


def worker() -> None:
    while True:
        job = JOBS[QUEUE.get()]
        job.status = "분석 중"
        say = job.log.append
        try:
            a = analyze_video(job.video_id, step=job.step, progress=say)
            say(f"인식 창 {a.windows}개 중 {a.unrecognized}개는 인식 안 됨")
            sl = from_analysis(a, title=job.title, progress=say)
            if not sl.rows:
                say("인식된 곡이 없어요. 빈 목록 파일을 만들었어요 — 직접 채워 넣으세요.")
            path = RESULTS / f"{safe_name(job.title)}.txt"
            i = 2
            while path.exists():
                path = RESULTS / f"{safe_name(job.title)} ({i}).txt"
                i += 1
            path.write_text(dumps(sl), encoding="utf-8")
            job.result = path.name
            job.status = "완료"
            say(f"저장: results/{path.name}")
        except Exception as e:
            job.status = "실패"
            say(f"오류: {e}")
            say(traceback.format_exc(limit=3))
        finally:
            QUEUE.task_done()


threading.Thread(target=worker, daemon=True).start()


def fold(s: str) -> str:
    """소문자 + 악센트 제거 (exhibición → exhibicion)"""
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn").strip()


def video_id_of(q: str) -> str | None:
    """YouTube 주소나 11자리 영상 ID면 그 ID"""
    import re

    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/live/)([A-Za-z0-9_-]{11})", q)
    if m:
        return m.group(1)
    return q if re.fullmatch(r"[A-Za-z0-9_-]{11}", q) and not q.isalpha() else None


def safe_name(s: str) -> str:
    import re

    return re.sub(r'[\\/:*?"<>|]+', " ", s).strip()[:120] or "목록"


# ── 화면 ────────────────────────────────────────────
BASE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }} · CompetitionAnalyzer</title>
<style>
:root{--bg:#16110f;--card:#221a16;--fg:#ede4d3;--muted:#b8ab97;--line:rgba(237,228,211,.16);--gold:#d8bc84;--bad:#e07a6f;--ok:#8fc39a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 "Pretendard","Malgun Gothic",system-ui,sans-serif}
a{color:var(--gold)}header{display:flex;gap:18px;align-items:center;padding:14px 20px;border-bottom:1px solid var(--line)}
header b{font-family:Georgia,serif;font-style:italic;color:var(--gold);font-size:18px}main{max-width:1100px;margin:0 auto;padding:20px}
input,select,textarea,button{font:inherit;color:inherit}input[type=text],input[type=number],textarea{background:var(--card);border:1px solid var(--line);border-radius:4px;padding:8px 10px}
button,.btn{background:var(--gold);color:#1b1412;border:0;border-radius:3px;padding:8px 16px;cursor:pointer;text-decoration:none;display:inline-block}
.ghost{background:transparent;color:var(--fg);border:1px solid var(--line)}table{width:100%;border-collapse:collapse}
td,th{border-bottom:1px solid var(--line);padding:8px;text-align:left;vertical-align:middle}th{color:var(--muted);font-weight:500;font-size:13px}
.muted{color:var(--muted);font-size:13px}.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px;margin:12px 0}
pre{white-space:pre-wrap;font-size:12.5px;color:var(--muted);max-height:260px;overflow:auto;margin:6px 0 0}
.ok{color:var(--ok)}.bad{color:var(--bad)}textarea{width:100%;min-height:440px;font-family:Consolas,monospace;font-size:13.5px;line-height:1.55}
img{border-radius:4px;display:block}
</style>{% if refresh %}<meta http-equiv="refresh" content="{{ refresh }}">{% endif %}</head><body>
<header><b>CompetitionAnalyzer</b><a href="/">검색</a><a href="/jobs">분석 작업</a><a href="/lists">곡 목록 파일</a></header>
<main>{{ body|safe }}</main></body></html>"""


def page(title: str, body: str, refresh: int | None = None) -> str:
    return render_template_string(BASE, title=title, body=body, refresh=refresh)


def h(s: object) -> str:
    from markupsafe import escape

    return str(escape(s if s is not None else ""))


@app.get("/")
def home():
    q = request.args.get("q", "").strip()
    ex_raw = request.args.get("ex", "").strip()
    excludes = [fold(w) for w in re.split(r"[,\n]+", ex_raw) if w.strip()]
    rows = ""
    removed = 0
    if q:
        try:
            vid = video_id_of(q)
            # 제외 단어로 걸러지는 만큼 넉넉히 받아 온다
            vids = [video_info(vid)[0]] if vid else search(q, 40 if excludes else 20)
        except Exception as e:
            vids = []
            rows = f'<p class="bad">검색 오류: {h(e)}</p>'
        if excludes:
            kept = [v for v in vids if not any(w in fold(f"{v.title} {v.channel}") for w in excludes)]
            removed = len(vids) - len(kept)
            vids = kept
        for v in vids:
            rows += f"""<tr>
<td><input type="checkbox" name="v" value="{h(v.id)}"></td>
<td><a href="{h(v.url)}" target="_blank"><img src="{h(v.thumbnail)}" width="120" alt=""></a></td>
<td><input type="text" name="t_{h(v.id)}" value="{h(v.title)}" style="width:100%"><div class="muted">{h(v.channel)}</div></td>
<td class="muted">{fmt(v.duration) if v.duration else "?"}</td></tr>"""
        if q and not vids and not rows:
            rows = f'<p class="muted">결과가 없어요{f" (제외 단어로 {removed}개 뺌)" if removed else ""}.</p>'
        if vids:
            rows = f"""<form method="post" action="/analyze">
{f'<p class="muted">제외 단어로 {removed}개를 뺐어요.</p>' if removed else ''}
<p class="muted">분석할 영상을 고르고, 목록 제목(공홈에 보일 이름)을 다듬으세요. 예: 2024 Mundial 결승 R4</p>
<table><tr><th></th><th>영상</th><th>목록 제목 · 채널</th><th>길이</th></tr>{rows}</table>
<p>1차 인식 간격 <input type="number" name="step" value="45" min="15" max="90" style="width:70px">초
<span class="muted">(곡이 바뀌는 곳은 12초 안으로 자동으로 좁혀요. 인식 서버가 자주 기다리게 해서 1시간 영상이면 20~30분쯤 걸려요)</span></p>
<button>고른 영상 분석</button></form>"""
    body = f"""<h1>대회 영상 검색</h1>
<form style="display:grid;gap:8px;max-width:640px">
<input type="text" name="q" value="{h(q)}" placeholder="검색어 — 예: Mundial de Tango 2024 pista final">
<input type="text" name="ex" value="{h(ex_raw)}" placeholder="제외 단어 (쉼표로 여러 개) — 예: escenario, show, clase, exhibicion">
<div><button>검색</button></div></form>
<p class="muted">YouTube 주소나 영상 ID를 넣어도 돼요. 제외 단어가 제목·채널에 들어간 영상은 빼요 (대소문자·악센트 무시).
영상 소리는 분석에만 쓰고 끝나면 지웁니다.</p>
{rows}"""
    return page("검색", body)


@app.post("/analyze")
def analyze():
    ids = request.form.getlist("v")
    step = max(15, min(90, int(request.form.get("step") or 45)))
    for vid in ids:
        title = request.form.get(f"t_{vid}") or vid
        job = Job(id=uuid.uuid4().hex[:8], video_id=vid, title=title.strip(), step=step)
        JOBS[job.id] = job
        QUEUE.put(job.id)
    return redirect(url_for("jobs"))


@app.get("/jobs")
def jobs():
    items = sorted(JOBS.values(), key=lambda j: j.created, reverse=True)
    busy = any(j.status in ("대기", "분석 중") for j in items)
    cards = ""
    for j in items:
        cls = "ok" if j.status == "완료" else "bad" if j.status == "실패" else ""
        link = f' · <a href="/lists/{h(j.result)}">목록 열기</a>' if j.result else ""
        cards += f"""<div class="card"><b>{h(j.title)}</b> <span class="{cls}">{h(j.status)}</span>{link}
<div class="muted"><a href="https://www.youtube.com/watch?v={h(j.video_id)}" target="_blank">영상</a> · 1차 {j.step}초 간격</div>
<pre>{h(chr(10).join(j.log[-40:]))}</pre></div>"""
    body = f"<h1>분석 작업</h1>{cards or '<p class=muted>아직 작업이 없어요.</p>'}"
    return page("분석 작업", body, refresh=3 if busy else None)


@app.get("/lists")
def lists():
    files = sorted(RESULTS.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    rows = "".join(
        f'<tr><td><a href="/lists/{h(p.name)}">{h(p.stem)}</a></td>'
        f'<td class="muted">{time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))}</td></tr>'
        for p in files
    )
    body = f"""<h1>곡 목록 파일</h1>
<p class="muted">폴더: {h(RESULTS)} <a class="btn ghost" href="/open-folder">폴더 열기</a> <a class="btn ghost" href="/lists/new">빈 목록 만들기</a></p>
<table><tr><th>파일</th><th>수정</th></tr>{rows or '<tr><td class=muted colspan=2>없음</td></tr>'}</table>"""
    return page("곡 목록 파일", body)


def _file(name: str) -> Path:
    p = (RESULTS / name).resolve()
    if p.parent != RESULTS.resolve() or p.suffix != ".txt":
        abort(404)
    return p


TEMPLATE = """제목:
대회영상:
메모:

# 시작-끝 | 곡명 | 아티스트 | 음악 YouTube 주소(선택) | 장르(선택: tango/vals/milonga)
- | 곡명 | 아티스트 |  |
"""


@app.route("/lists/<name>", methods=["GET", "POST"])
def edit(name: str):
    if name == "new":
        path = None
        text = request.form.get("text", TEMPLATE)
    else:
        path = _file(name)
        if not path.exists():
            abort(404)
        text = request.form.get("text") if request.method == "POST" else path.read_text(encoding="utf-8")
    sl, problems = loads(text)
    msg = ""
    if request.method == "POST":
        if problems:
            msg = '<p class="bad">형식 문제가 있어 저장하지 않았어요.</p>'
        else:
            if path is None:
                path = RESULTS / f"{safe_name(sl.title)}.txt"
                if path.exists():
                    msg = '<p class="bad">같은 이름의 파일이 있어요. 제목을 바꿔 주세요.</p>'
                    path = None
            if path:
                path.write_text(text.replace("\r\n", "\n"), encoding="utf-8")
                return redirect(url_for("edit", name=path.name, saved=1))
    if request.args.get("saved"):
        msg = '<p class="ok">저장했어요.</p>'
    check = (
        "<ul class=bad>" + "".join(f"<li>{h(p)}</li>" for p in problems) + "</ul>"
        if problems
        else f'<p class="ok">형식 이상 없음 · 곡 {len(sl.rows)}개 · 음악 주소 있는 곡 {sum(1 for r in sl.rows if r.music_url)}개</p>'
    )
    preview = "".join(
        f"<tr><td class=muted>{h(r.start if r.start == '-' else r.start + '-' + r.end)}</td><td>{h(r.title)}</td>"
        f"<td>{h(r.artist)}</td><td>{f'<a href={h(r.music_url)} target=_blank>음악 ↗</a>' if r.music_url else '<span class=muted>없음</span>'}</td>"
        f"<td class=muted>{h(r.genre)}</td></tr>"
        for r in sl.rows
    )
    dl = f' <a class="btn ghost" href="/download/{h(path.name)}">파일 받기</a>' if path else ""
    body = f"""<h1>{h(sl.title or '새 목록')}</h1>{msg}{check}
<form method="post"><textarea name="text" spellcheck="false">{h(text)}</textarea>
<p><button>형식 검사 후 저장</button>{dl}
<span class="muted">공홈 관리자 화면 /music 에서 이 .txt 파일을 올리세요.</span></p></form>
{('<div class=card><b>' + h(sl.title) + '</b> ' + (f'<a href={h(sl.video_url)} target=_blank>대회영상 ↗</a>' if sl.video_url else '') + '<table>' + preview + '</table></div>') if sl.rows else ''}"""
    return page(sl.title or "새 목록", body)


@app.get("/download/<name>")
def download(name: str):
    return send_file(_file(name), as_attachment=True, download_name=name)


@app.get("/open-folder")
def open_folder():
    if os.name == "nt":
        subprocess.Popen(["explorer", str(RESULTS)])
    return redirect(url_for("lists"))


if __name__ == "__main__":
    # 이 컴퓨터에서만 연다 (127.0.0.1)
    app.run(host="127.0.0.1", port=5055, debug=False, threaded=True)
