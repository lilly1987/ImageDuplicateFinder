import logging
import queue
import atexit
from logging.handlers import QueueHandler, QueueListener
from rich.console import Console
from rich.logging import RichHandler

# Console 인스턴스
console = Console()

# 실제 출력을 담당할 RichHandler
rich_handler = RichHandler(
    console=console,
    rich_tracebacks=True,
    show_path=True,
    markup=True
)

# 비동기 로그 처리를 위한 Queue 및 QueueHandler/QueueListener 설정
log_queue = queue.Queue(-1)
queue_handler = QueueHandler(log_queue)

logger = logging.getLogger("ImageDuplicateFinder")
logger.setLevel(logging.INFO)
logger.addHandler(queue_handler)

# QueueListener를 통해 백그라운드 스레드에서 RichHandler로 전달
listener = QueueListener(log_queue, rich_handler, respect_handler_level=True)
listener.start()

# 프로그램 종료 시 큐의 남은 로그 처리 및 리스너 종료
atexit.register(listener.stop)

