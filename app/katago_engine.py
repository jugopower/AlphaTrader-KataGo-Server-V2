import json
import os
import select
import subprocess
import threading
import time
import uuid
from typing import Any


class KataGoEngine:
    """Persistent KataGo analysis engine.

    Build 026.0 keeps one KataGo process alive and adds a preliminary life-and-death verification report.
    Requests are serialized with a lock because one analysis subprocess reads
    and writes through a single stdin/stdout stream.
    """

    def __init__(self) -> None:
        self.binary = os.getenv("KATAGO_BIN", "/app/bin/katago")
        self.model = os.getenv("KATAGO_MODEL", "/app/models/model.bin.gz")
        self.config = os.getenv("KATAGO_CONFIG", "/app/config/analysis.cfg")
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._started_at: float | None = None

    def readiness(self) -> dict[str, Any]:
        checks = {
            "binary_exists": os.path.isfile(self.binary),
            "binary_executable": os.access(self.binary, os.X_OK),
            "model_exists": os.path.isfile(self.model),
            "config_exists": os.path.isfile(self.config),
        }
        running = self._process is not None and self._process.poll() is None
        return {
            "ready": all(checks.values()),
            "running": running,
            "persistent": True,
            "uptime_seconds": (
                round(time.monotonic() - self._started_at, 1)
                if running and self._started_at is not None
                else 0
            ),
            "checks": checks,
            "paths": {
                "binary": self.binary,
                "model": self.model,
                "config": self.config,
            },
        }

    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return

        readiness = self.readiness()
        if not readiness["ready"]:
            raise RuntimeError("KataGo 執行檔、模型或設定檔尚未完整安裝。")

        self.stop()
        self._process = subprocess.Popen(
            [
                self.binary,
                "analysis",
                "-config",
                self.config,
                "-model",
                self.model,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            # KataGo can be verbose on stderr. Discarding it prevents a full
            # stderr pipe from blocking the persistent process.
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._started_at = time.monotonic()

    def stop(self) -> None:
        process = self._process
        self._process = None
        self._started_at = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def analyze(
        self,
        board_size: int,
        moves: list[Any],
        initial_stones: list[dict[str, str]],
        next_player: str,
        komi: float = 7.5,
        max_visits: int = 50,
        timeout_seconds: float = 45.0,
    ) -> dict[str, Any]:
        readiness = self.readiness()
        if not readiness["ready"]:
            return {
                "status": "unavailable",
                "mode": "katago",
                "message": "KataGo 執行檔、模型或設定檔尚未完整安裝。",
                "readiness": readiness,
            }

        requested_visits = max(1, min(int(max_visits), 5000))

        # Fast-play tuning: the current human-vs-AI UI uses modest visit values,
        # while deep analysis normally requests 80 visits or more. Keep deep
        # analysis unchanged, but shorten ordinary play requests substantially.
        fast_play = requested_visits <= 50
        if fast_play:
            if requested_visits <= 10:
                effective_visits = requested_visits
            elif requested_visits <= 20:
                effective_visits = 10
            else:
                effective_visits = 12
        else:
            effective_visits = requested_visits

        query_id = f"build02511-{uuid.uuid4().hex}"
        query = {
            "id": query_id,
            "moves": self._convert_moves(moves),
            "initialStones": self._convert_initial_stones(initial_stones),
            "rules": "tromp-taylor",
            "komi": komi,
            "boardXSize": board_size,
            "boardYSize": board_size,
            # Policy ownership arrays are not needed for the current UI and
            # increase response size and compute cost.
            "includePolicy": False,
            "includeOwnership": False,
            "analysisPVLen": 2 if fast_play else 4,
            "maxVisits": effective_visits,
        }

        started = time.perf_counter()
        with self._lock:
            for attempt in range(2):
                try:
                    self.start()
                    result = self._send_and_receive(query, timeout_seconds)
                    break
                except (BrokenPipeError, EOFError, OSError, RuntimeError) as exc:
                    self.stop()
                    if attempt == 1:
                        return {
                            "status": "error",
                            "mode": "katago",
                            "message": "KataGo 長駐引擎連線失敗。",
                            "detail": str(exc),
                        }
            else:
                return {
                    "status": "error",
                    "mode": "katago",
                    "message": "KataGo 長駐引擎無法啟動。",
                }

        elapsed_ms = round((time.perf_counter() - started) * 1000)

        if result.get("error"):
            return {
                "status": "error",
                "mode": "katago",
                "message": str(result.get("error")),
                "katago_raw": result,
                "elapsed_ms": elapsed_ms,
            }

        root_info = result.get("rootInfo", {})
        move_infos = result.get("moveInfos", [])
        if not move_infos:
            return {
                "status": "error",
                "mode": "katago",
                "message": "KataGo 沒有回傳推薦落點。",
                "katago_raw": result,
                "elapsed_ms": elapsed_ms,
            }

        return {
            "status": "ok",
            "mode": "katago",
            "engine_mode": "persistent",
            "board_size": board_size,
            "move_count": len(moves),
            "next_player": next_player,
            "winrate": root_info.get("winrate"),
            "score_lead": root_info.get("scoreLead"),
            "visits": root_info.get("visits"),
            "requested_visits": requested_visits,
            "effective_visits": effective_visits,
            "fast_play": fast_play,
            "elapsed_ms": elapsed_ms,
            # Human play only needs the best move. Deep analysis keeps all
            # candidates so the existing analysis panel remains unchanged.
            "move_infos": move_infos[:1] if fast_play else move_infos,
        }


    @staticmethod
    def build_life_death_report(
        result: dict[str, Any], question_no: str, problem_type: str
    ) -> dict[str, Any]:
        """Build a cautious, preliminary report from one KataGo analysis.

        This deliberately does not claim a mathematically proven kill, life, ko,
        or unique solution. It ranks candidate first moves so a teacher can
        review far fewer variations manually.
        """
        labels = {
            "black_kill_white": "黑先殺白",
            "white_kill_black": "白先殺黑",
            "black_live": "黑先做活",
            "white_live": "白先做活",
        }
        if result.get("status") != "ok":
            return {
                "status": "error",
                "mode": "life_death_verification",
                "question_no": question_no,
                "problem_type": problem_type,
                "problem_label": labels.get(problem_type, problem_type),
                "message": result.get("message", "KataGo 驗證失敗"),
                "analysis": result,
            }

        infos = list(result.get("move_infos") or [])
        candidates = []
        for idx, item in enumerate(infos[:8]):
            candidates.append({
                "rank": idx + 1,
                "move": item.get("move", "pass"),
                "visits": int(item.get("visits", item.get("edgeVisits", 0)) or 0),
                "winrate": item.get("winrate"),
                "score_lead": item.get("scoreLead", item.get("scoreMean")),
                "pv": item.get("pv", []),
                "prior": item.get("prior"),
            })

        best = candidates[0] if candidates else None
        second = candidates[1] if len(candidates) > 1 else None
        best_visits = best["visits"] if best else 0
        second_visits = second["visits"] if second else 0
        ratio = round(best_visits / max(1, second_visits), 2) if best else 0
        if not best:
            uniqueness = "無法判定"
        elif second is None or second_visits == 0 or ratio >= 4:
            uniqueness = "疑似唯一第一手"
        elif ratio >= 1.8:
            uniqueness = "第一選較明顯，仍需複核"
        else:
            uniqueness = "可能多解或候選接近"

        warnings = [
            "此報告是半自動初判，不等同淨殺、劫殺或唯一解的數學證明。",
            "請檢查最佳變化中是否出現劫爭、雙活、倒脫靴或局部以外手段。",
        ]
        return {
            "status": "ok",
            "mode": "life_death_verification",
            "verification_level": "preliminary",
            "question_no": question_no,
            "problem_type": problem_type,
            "problem_label": labels.get(problem_type, problem_type),
            "next_player": result.get("next_player"),
            "best_move": best["move"] if best else None,
            "best_pv": best["pv"] if best else [],
            "candidate_ratio": ratio,
            "uniqueness_hint": uniqueness,
            "root_winrate": result.get("winrate"),
            "root_score_lead": result.get("score_lead"),
            "visits": result.get("visits"),
            "elapsed_ms": result.get("elapsed_ms"),
            "candidates": candidates,
            "warnings": warnings,
            "teacher_decision_required": True,
            "analysis": result,
        }

    def _send_and_receive(
        self, query: dict[str, Any], timeout_seconds: float
    ) -> dict[str, Any]:
        process = self._process
        if process is None or process.poll() is not None:
            raise RuntimeError("KataGo process is not running")
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("KataGo pipes are unavailable")

        process.stdin.write(json.dumps(query, separators=(",", ":")) + "\n")
        process.stdin.flush()

        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.stop()
                raise RuntimeError("KataGo 分析逾時。")

            readable, _, _ = select.select([process.stdout], [], [], remaining)
            if not readable:
                self.stop()
                raise RuntimeError("KataGo 分析逾時。")

            line = process.stdout.readline()
            if line == "":
                raise EOFError("KataGo process closed stdout")
            line = line.strip()
            if not line:
                continue

            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                # Ignore non-JSON diagnostic lines if any.
                continue

            if payload.get("id") == query["id"]:
                return payload

    @staticmethod
    def _convert_moves(moves: list[Any]) -> list[list[str]]:
        converted: list[list[str]] = []
        for index, move in enumerate(moves):
            if isinstance(move, dict):
                color = str(move.get("color", "B")).upper().strip()
                coordinate = str(move.get("coordinate", "pass")).strip()
            elif isinstance(move, (list, tuple)) and len(move) >= 2:
                color = str(move[0]).upper().strip()
                coordinate = str(move[1]).strip()
            else:
                raw_move = str(move).strip()
                color, coordinate = KataGoEngine._parse_compact_move(
                    raw_move, fallback_color="B" if index % 2 == 0 else "W"
                )
            if color not in {"B", "W"}:
                color = "B" if index % 2 == 0 else "W"
            converted.append([color, coordinate or "pass"])
        return converted

    @staticmethod
    def _parse_compact_move(raw_move: str, fallback_color: str) -> tuple[str, str]:
        cleaned = raw_move.strip()
        if not cleaned:
            return fallback_color, "pass"
        first = cleaned[0].upper()
        if first in {"B", "W"}:
            coordinate = cleaned[1:].lstrip(" ,:").strip()
            if coordinate:
                return first, coordinate
        return fallback_color, cleaned

    @staticmethod
    def _convert_initial_stones(stones: list[dict[str, str]]) -> list[list[str]]:
        converted: list[list[str]] = []
        for stone in stones:
            color = str(stone.get("color", "B")).upper().strip()
            coordinate = str(stone.get("coordinate", "")).strip()
            if color in {"B", "W"} and coordinate:
                converted.append([color, coordinate])
        return converted
