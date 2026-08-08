from datetime import datetime, timedelta

from threading import Lock

from fastapi import APIRouter

from pydantic import BaseModel

router = APIRouter(prefix="/monitor", tags=["monitor"])

_lock = Lock()

# client_id -> 最後心跳時間

_online_clients: dict[str, datetime] = {}

# 今日曾進站的 client_id

_today_clients: set[str] = set()

_today_date = datetime.now().date()

# KataGo 狀態

_katago_active = 0

_katago_waiting = 0

class ClientRequest(BaseModel):

    client_id: str

def _cleanup() -> None:

    """移除超過 90 秒沒有心跳的使用者。"""

    global _today_date, _today_clients

    now = datetime.now()

    expired = [

        client_id

        for client_id, last_seen in _online_clients.items()

        if now - last_seen > timedelta(seconds=90)

    ]

    for client_id in expired:

        _online_clients.pop(client_id, None)

    # 每日零時重新計算今日訪客

    if now.date() != _today_date:

        _today_date = now.date()

        _today_clients = set()

@router.post("/heartbeat")

def heartbeat(request: ClientRequest):

    with _lock:

        _cleanup()

        now = datetime.now()

        _online_clients[request.client_id] = now

        _today_clients.add(request.client_id)

        return {

            "status": "ok",

            "online": len(_online_clients),

            "today": len(_today_clients),

        }

@router.get("/stats")

def stats():

    with _lock:

        _cleanup()

        return {

            "status": "ok",

            "online": len(_online_clients),

            "katago": _katago_active,

            "today": len(_today_clients),

            "queue": _katago_waiting,

        }

@router.post("/katago/start")

def katago_start():

    global _katago_active

    with _lock:

        _katago_active += 1

        return {

            "status": "ok",

            "katago": _katago_active,

            "queue": _katago_waiting,

        }

@router.post("/katago/end")

def katago_end():

    global _katago_active

    with _lock:

        _katago_active = max(0, _katago_active - 1)

        return {

            "status": "ok",

            "katago": _katago_active,

            "queue": _katago_waiting,

        }

@router.post("/queue/join")

def queue_join():

    global _katago_waiting

    with _lock:

        _katago_waiting += 1

        return {

            "status": "ok",

            "queue": _katago_waiting,

        }

@router.post("/queue/leave")

def queue_leave():

    global _katago_waiting

    with _lock:

        _katago_waiting = max(0, _katago_waiting - 1)

        return {

            "status": "ok",

            "queue": _katago_waiting,

        }

def analysis_request_started() -> None:
    """Record one analysis HTTP request. One KataGo process is serialized, so extras are queued."""
    global _katago_active, _katago_waiting
    with _lock:
        if _katago_active == 0:
            _katago_active = 1
        else:
            _katago_waiting += 1

def analysis_request_finished() -> None:
    """Release one analysis request and promote the next queued request, if any."""
    global _katago_active, _katago_waiting
    with _lock:
        if _katago_waiting > 0:
            _katago_waiting -= 1
            _katago_active = 1
        else:
            _katago_active = 0

