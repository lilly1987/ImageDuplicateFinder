"""
로깅 모듈.

콘솔(Rich) + 파일 로깅을 동시 지원.
- 콘솔: RichHandler로 컬러 출력
- 파일: 날짜별 로그 파일 생성 (logs/ 디렉토리)
- 비동기: QueueHandler/QueueListener로 백그라운드 처리
"""

import os
import logging
import queue
import atexit
from datetime import datetime
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from rich.console import Console
from rich.logging import RichHandler

# ============================================================
# 로그 디렉토리 및 파일 경로
# ============================================================
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f"app_{datetime.now().strftime('%Y%m%d')}.log")

# ============================================================
# 콘솔 핸들러 (Rich)
# ============================================================
console = Console()
rich_handler = RichHandler(
    console=console,
    rich_tracebacks=True,
    show_path=True,
    markup=True
)
rich_handler.setLevel(logging.INFO)

# ============================================================
# 파일 핸들러 (RotatingFileHandler)
# ============================================================
# 최대 10MB, 30개 파일 보관
file_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=10 * 1024 * 1024,
    backupCount=30,
    encoding="utf-8",
)
file_handler.setLevel(logging.DEBUG)  # 파일에는 DEBUG 이상 모두 기록

# 파일 로그 포맷 (시간, 레벨, 파일명:줄수, 메시지)
file_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
file_handler.setFormatter(file_formatter)

# ============================================================
# 비동기 로그 처리 (QueueHandler/QueueListener)
# ============================================================
log_queue = queue.Queue(-1)
queue_handler = QueueHandler(log_queue)

logger = logging.getLogger("ImageDuplicateFinder")
logger.setLevel(logging.DEBUG)  # 전체 로거는 DEBUG, 각 핸들러에서 필터링
logger.addHandler(queue_handler)

# QueueListener를 통해 백그라운드 스레드에서 콘솔 + 파일로 전달
listener = QueueListener(log_queue, rich_handler, file_handler, respect_handler_level=True)
listener.start()

# 프로그램 종료 시 큐의 남은 로그 처리 및 리스너 종료
atexit.register(listener.stop)


def get_log_file_path():
    """현재 로그 파일 경로 반환"""
    return LOG_FILE


def get_log_dir():
    """로그 디렉토리 경로 반환"""
    return LOG_DIR