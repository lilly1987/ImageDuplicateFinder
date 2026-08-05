# 이미지 중복 탐색기 (Image Duplicate Finder)

대용량 이미지 수집 및 고속 perceptual hash 비교를 지원하는 다국어 GUI 이미지 중복 탐색 프로그램입니다.  
수십만 건의 대규모 이미지 라이브러리에서도 비동기 멀티스레드 파이프라인과 FAISS/BK-Tree 인덱싱을 통해 효율적으로 중복 및 유사 이미지를 탐지합니다.

---

## 🌟 주요 기능

### 1. 고성능 비교 파이프라인 & 멀티스레딩
- **비동기 듀얼 스레드 구조**: 이미지 해시 계산(Hasher)과 해시 간 비교(Comparator)가 동시 실행됩니다.
- **FAISS & BK-Tree 고속 검색**:
  - `FAISS` Vector Index (Flat/IVF) 기반 대용량 비트 햄밍 거리를 초고속 탐색합니다.
  - BK-Tree 알고리즘을 통한 유사 해시 범위(BKT) 검색을 지원합니다.
- **SQLite3 해시 DB 캐싱**: 계산된 이미지 해시 결과를 저장하여 다음 실행 시 재계산 없이 바로 비교합니다.
- **대용량 UI 프리징 방지 (Chunked Rendering & Incremental Delete)**:
  - 수만 건의 결과 그룹도 200개 청크 단위로 나누어 트리뷰(Treeview)에 즉시 렌더링합니다.
  - 목록 삭제 및 실제 파일 삭제 시 전체 트리를 리빌드하지 않는 증분 노드 제거(O(1)) 및 백그라운드 비동기 JSON 업데이트를 사용합니다.

### 2. 다양한 지능형 이미지 탐지 옵션
- **Perceptual Hash 선택**: `ahash` (Average), `phash` (Perceptual), `dhash` (Difference), `whash` (Wavelet) 지원.
- **해시 해상도 설정**: `h8` (8x8 = 64bit)부터 `h16` (16x16 = 256bit)까지 정확도 조정 가능.
- **비교 임계값(Tolerance Rate) 조절**: 0.0 ~ 1.0 비율 입력을 통해 정밀 일치부터 유사 이미지 탐색까지 설정.
- **종횡비(Aspect Ratio) 허용오차 필터**: 이미지 해상도가 달라도 가로세로 비율이 유사한 이미지 그룹만 추출.
- **스마트 폴더 선택**:
  - 하위 디렉토리 포함 깊이(Depth) 설정 가능.
  - 특정 폴더 체크/체크 해제를 통해 검사 대상 및 결과창 표시 항목 제어.
- **실시간 비동기 이미지 미리보기**:
  - 이미지 디코딩 및 리사이즈를 백그라운드 스레드에서 처리하고 메모리 캐시를 적용하여 빠른 미리보기 제공.

### 3. 유연한 결과 관리
- **중복 결과 관리 창**:
  - 결과 목록의 개별 파일 / 그룹 단위 선택 및 선택 반전/전체선택 기능.
  - **경로 필터 & 폴더 간/폴더 내 중복 보기**: 원하는 경로 키워드나 폴더 관계 조건으로 실시간 필터링.
  - **실제 파일 삭제 vs 목록에서만 제거**: 실제 디스크 파일 삭제(`Shift+Delete`) 또는 단순 결과 목록 제외(`Delete`).
  - **폴더 연동 필터링**: 폴더 선택창에서 체크 해제된 디렉토리의 파일은 결과 창에서 O(깊이) 최적화로 빠른 제외.

### 4. 다국어 지원 & 설정 저장
- **YAML 기반 다국어 팩**: 한국어(`lang.ko.yml`), 영어(`lang.en.yml`) 등 지원.
- **자동 설정 동기화**: `config.yml`을 통해 사용자가 설정한 옵션 및 UI 상태가 자동 유지됩니다.

---

## 🛠️ 기술 스택

- **GUI**: Python `tkinter`, `ttk`
- **Image Processing**: `Pillow (PIL)`, `OpenCV` (선택)
- **Image Hashing**: `imagehash`, `PyWavelets`
- **Vector Search / Math**: `FAISS`, `NumPy`
- **Concurrency**: `concurrent.futures`, `threading`, `queue`
- **Database**: SQLite3 (`database.py`)
- **Logging & CLI**: `rich` logging

---

## 🚀 시작하기

### 실행 요구사항
- Python 3.8 이상

### Windows - 패키지 설치/업데이트 (`update.bat`)
- 프로젝트 폴더의 **`update.bat`** 파일을 더블클릭하면
  `requirements.txt`에 정의된 모든 패키지를 자동 설치 및 업데이트합니다.

```bat
:: update.bat 내부 동작
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

> 참고: 수동 설치 명령은 아래와 같습니다.
> ```bash
> pip install -r requirements.txt
> ```
> *(Pillow, numpy, faiss-cpu, PyWavelets, pyyaml, rich, imagehash 등 포함)*

### 프로그램 실행
```bash
python run.py
```
또는 프로젝트 폴더의 **`run.bat`** 파일을 더블클릭합니다.

---

## 📁 주요 파일 구조

```
ImageDuplicateFinder/
├── run.py                 # 메인 실행 엔트리포인트
├── ui_options.py          # 옵션/설정 및 메인 탐색 UI
├── ui_results.py          # 중복 결과 표시 및 삭제/관리 창 (Chunked Treeview)
├── folder_list.py         # 검사 대상 폴더 목록 및 체크 상태 관리 UI
├── collector.py           # 파이프라인 제어 및 파일 수집 (Collector)
├── hasher.py              # 멀티프로세싱/스레드 기반 이미지 해시 계산기
├── comparator.py          # FAISS / BK-Tree 기반 해시 비교 엔진
├── compare.py             # 파이프라인 관리 및 JSON/DB 인터페이스
├── database.py            # SQLite3 DB 캐시 읽기/쓰기 및 세션 관리
├── ui_cache.py            # 캐시 및 DB 관리 UI
├── lang.py                # 다국어(i18n) 설정 로더 (YAML)
├── lang.ko.yml            # 한국어 언어팩
├── lang.en.yml            # 영어 언어팩
└── tests/                 # 구문 및 파이프라인 검증 테스트 코드
```

---

## 📄 라이선스
MIT License