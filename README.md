# CompetitionAnalyzer

탱고 대회 영상(YouTube)에서 쓰인 곡을 찾아 **공홈 /music 에 올릴 곡 목록(.txt)** 을 만드는 로컬 프로그램.

- Python 프로그램이고, 화면은 이 컴퓨터에서만 열리는 브라우저 화면(http://127.0.0.1:5055)이다.
- 영상 소리는 분석용 임시 파일로만 받고, 분석이 끝나면 바로 지운다. 저장·공유하지 않는다.

## 실행

`run.bat` 을 더블클릭. 처음이면 `.venv` 를 만들고 패키지를 설치한 뒤 브라우저가 열린다.

수동으로 실행하려면:

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

## 사용 순서

1. **검색**: 검색어를 넣고, 필요하면 제외 단어를 쉼표로 여러 개 넣는다(제목·채널에 들어간 영상을 뺌). YouTube 주소나 영상 ID도 된다.
2. **분석**: 영상을 고르고 목록 제목(공홈에 보일 이름)을 다듬은 뒤 '고른 영상 분석'. 한 번에 한 영상씩 차례로 분석한다.
3. **곡 목록 파일**: `results/` 에 생긴 .txt 를 화면에서 고치고 '형식 검사 후 저장'.
4. 공홈 관리자 화면 `/music` 에서 그 .txt 파일을 올린다.

## 분석 방법

1. 영상 소리를 받아 **45초 간격**으로 12초씩 잘라 Shazam 으로 인식한다.
2. 이웃한 두 지점의 곡이 다르면 그 사이를 반씩 나눠 **12초 안으로** 곡이 바뀌는 곳을 좁힌다.
3. 같은 곡이 이어진 지점을 한 구간(시작-끝)으로 묶는다. 2번 미만으로만 잡힌 곡은 우연히 섞인 소리로 보고 뺀다.
4. 곡마다 음악 YouTube 주소를 찾는다 (`- Topic` 공식 음원 채널 우선, 곡명·오케스트라 이름이 맞는 영상만).

Shazam 인식 서버가 3번에 1번꼴로 15~100초씩 기다리게 해서, 1시간 영상이면 20~30분쯤 걸린다.

**인식 결과는 확정이 아니다.** 아티스트는 Shazam 표기 그대로이고(가수가 있으면 `오케스트라 & 가수`), 음반 정보는 녹음 연도가 아니라 음반 발매 정보다. 공홈에 올리기 전에 영상을 보며 확인한다.

## 목록 파일 형식 (공홈 업로드와 같음)

```
제목: 2024 Mundial 결승 R4
대회영상: https://www.youtube.com/watch?v=vfNkamPH9-M
메모: (선택, 한 줄)

# 시작-끝 | 곡명 | 아티스트 | 음악 YouTube 주소(선택) | 장르(선택: tango/vals/milonga)
# Shazam 15회 인식 · 음반 La Guitarrita (2011)
00:15-03:57 | Nochero Soy | Osvaldo Pugliese | https://www.youtube.com/watch?v=moHqQCNki0Q |
```

- `#` 로 시작하는 줄·빈 줄은 무시. 칸 구분은 ` | `.
- 시작-끝은 대회영상 안의 시각(mm:ss 또는 h:mm:ss), 모르면 `-`.
- `제목:` 필수, 곡 줄 1~200개. 장르는 tango/vals/milonga(탱고/발스/밀롱가)만.

## 파일

- `app.py` — 로컬 화면 (Flask)
- `analyzer/core.py` — 검색(yt-dlp), 소리 받기, 인식(shazamio), 구간 묶기
- `analyzer/listfile.py` — 목록 파일 쓰기·읽기·형식 검사, 음악 YouTube 주소 찾기
- `results/` — 만든 목록 (git 에 넣지 않음)
