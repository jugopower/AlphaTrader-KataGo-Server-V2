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

    Build 027.1 hardens the automatic local principal-line solver with strict local bounds, legal-move replay, target-group capture detection, and an exact answer-move limit.
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
        local_region: dict[str, Any] | None = None,
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

        # Build 026.1: restrict both players to the selected local region for
        # the opening plies. This prevents whole-board moves such as D11 from
        # being recommended for a corner tsumego.
        if local_region:
            allowed = list(local_region.get("allowed_moves") or [])
            until_depth = max(1, min(int(local_region.get("until_depth", 20)), 100))
            if allowed:
                all_moves = self._all_board_coordinates(board_size) + ["pass"]
                outside = [move for move in all_moves if move not in set(allowed)]
                query["avoidMoves"] = [
                    {"player": "B", "moves": outside, "untilDepth": until_depth},
                    {"player": "W", "moves": outside, "untilDepth": until_depth},
                ]

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
            "local_region": local_region,
        }


    def solve_life_death_line(
        self,
        board_size: int,
        moves: list[Any],
        initial_stones: list[dict[str, str]],
        next_player: str,
        komi: float,
        visits_per_move: int,
        max_moves: int,
        local_region: dict[str, Any],
        target_coordinate: str,
        problem_type: str,
    ) -> dict[str, Any]:
        """Generate one strictly-local KataGo teaching line.

        Build 027.1 replays every move on a small Go rules engine, rejects any
        move outside the selected region, never returns more than max_moves,
        and stops immediately when the selected target stone is captured.
        This remains a principal variation, not a proof of life or uniqueness.
        """
        limit = max(1, min(int(max_moves), 30))
        allowed = {str(m).upper() for m in (local_region.get("allowed_moves") or [])}
        board = self._position_board(board_size, initial_stones, moves)
        target_xy = self._gtp_to_xy(board_size, target_coordinate)
        if target_xy is None:
            return {"status": "error", "mode": "life_death_solution_line", "message": "目標座標格式不正確"}
        tx, ty = target_xy
        target_color = board[ty][tx]
        if target_color not in {"B", "W"}:
            return {"status": "error", "mode": "life_death_solution_line", "message": "目標座標上沒有棋子"}

        line: list[dict[str, Any]] = []
        working_moves = list(moves)
        player = next_player if next_player in {"B", "W"} else "B"
        total_elapsed = 0
        stop_reason = "max_moves"
        last_result: dict[str, Any] | None = None
        previous_hash: str | None = None
        current_hash = self._board_hash(board)

        for ply in range(limit):
            result = self.analyze(
                board_size=board_size,
                moves=working_moves,
                initial_stones=initial_stones,
                next_player=player,
                komi=komi,
                max_visits=max(10, min(int(visits_per_move), 1000)),
                timeout_seconds=120.0,
                local_region=local_region,
            )
            last_result = result
            total_elapsed += int(result.get("elapsed_ms", 0) or 0)
            if result.get("status") != "ok":
                stop_reason = "analysis_error"
                break
            infos = list(result.get("move_infos") or [])
            if not infos:
                stop_reason = "no_move"
                break

            chosen = None
            for candidate in infos:
                coordinate = str(candidate.get("move", "") or "").upper()
                if coordinate and coordinate != "PASS" and coordinate in allowed:
                    xy = self._gtp_to_xy(board_size, coordinate)
                    if xy is None:
                        continue
                    trial = self._play_move(board, player, xy[0], xy[1], previous_hash)
                    if trial is not None:
                        chosen = (candidate, coordinate, trial)
                        break
            if chosen is None:
                stop_reason = "no_legal_local_move"
                break

            best, move, new_board = chosen
            item = {
                "number": len(line) + 1,
                "color": player,
                "move": move,
                "visits": int(best.get("visits", best.get("edgeVisits", 0)) or 0),
                "winrate": best.get("winrate"),
                "score_lead": best.get("scoreLead", best.get("scoreMean")),
            }
            line.append(item)
            working_moves.append({"color": player, "coordinate": move})
            previous_hash, current_hash = current_hash, self._board_hash(new_board)
            board = new_board

            # The original target coordinate disappears only when its group is captured.
            if board[ty][tx] != target_color:
                stop_reason = "target_captured"
                break
            player = "W" if player == "B" else "B"

        objective = "kill" if "kill" in problem_type else "live"
        target_captured = board[ty][tx] != target_color
        if objective == "kill" and target_captured:
            conclusion = "目標棋群已被提掉；此主變化達成殺棋。"
        elif objective == "kill":
            conclusion = "在指定手數內尚未提掉目標棋群。"
        else:
            conclusion = "已產生局部做活主變化；兩眼、劫活或雙活仍需老師確認。"

        return {
            "status": "ok" if line else "error",
            "mode": "life_death_solution_line",
            "solution_level": "strict_local_principal_line",
            "line": line[:limit],
            "move_count": min(len(line), limit),
            "max_solution_moves": limit,
            "next_player": player,
            "stop_reason": stop_reason,
            "elapsed_ms": total_elapsed,
            "last_analysis": last_result,
            "local_region": local_region,
            "target_coordinate": target_coordinate.upper(),
            "target_color": target_color,
            "target_captured": target_captured,
            "conclusion": conclusion,
            "warnings": [
                "所有解答手均已限制在選定局部範圍，並經合法落子重播檢查。",
                "此為 KataGo 局部主變化；唯一解、劫爭與做活證明仍需老師確認。",
            ],
        }

    @staticmethod
    def _gtp_to_xy(board_size: int, coordinate: str) -> tuple[int, int] | None:
        letters = "ABCDEFGHJKLMNOPQRSTUVWXYZ"
        raw = (coordinate or "").upper().strip()
        if raw == "PASS" or len(raw) < 2 or raw[0] not in letters:
            return None
        try:
            row = int(raw[1:])
        except ValueError:
            return None
        x, y = letters.index(raw[0]), board_size - row
        return (x, y) if 0 <= x < board_size and 0 <= y < board_size else None

    @staticmethod
    def _board_hash(board: list[list[str | None]]) -> str:
        return "".join("." if c is None else c for row in board for c in row)

    @classmethod
    def _position_board(cls, board_size: int, initial_stones: list[dict[str, str]], moves: list[Any]) -> list[list[str | None]]:
        board: list[list[str | None]] = [[None for _ in range(board_size)] for _ in range(board_size)]
        for stone in initial_stones:
            xy = cls._gtp_to_xy(board_size, str(stone.get("coordinate", "")))
            color = str(stone.get("color", "")).upper()
            if xy and color in {"B", "W"}:
                board[xy[1]][xy[0]] = color
        previous_hash = None
        for raw in moves:
            if isinstance(raw, dict):
                color = str(raw.get("color", raw.get("player", ""))).upper()
                coord = str(raw.get("coordinate", raw.get("move", "")))
            elif isinstance(raw, str):
                parts = raw.replace(",", " ").split()
                color, coord = (parts[0].upper(), parts[1]) if len(parts) >= 2 else ("", "")
            else:
                continue
            xy = cls._gtp_to_xy(board_size, coord)
            if color in {"B", "W"} and xy:
                new_board = cls._play_move(board, color, xy[0], xy[1], previous_hash)
                if new_board is not None:
                    previous_hash = cls._board_hash(board)
                    board = new_board
        return board

    @classmethod
    def _play_move(cls, board: list[list[str | None]], color: str, x: int, y: int, ko_hash: str | None) -> list[list[str | None]] | None:
        size = len(board)
        if not (0 <= x < size and 0 <= y < size) or board[y][x] is not None:
            return None
        result = [row[:] for row in board]
        result[y][x] = color
        enemy = "W" if color == "B" else "B"
        for nx, ny in cls._neighbors(size, x, y):
            if result[ny][nx] == enemy:
                group, liberties = cls._group_and_liberties(result, nx, ny)
                if not liberties:
                    for gx, gy in group:
                        result[gy][gx] = None
        _, own_liberties = cls._group_and_liberties(result, x, y)
        if not own_liberties:
            return None
        if ko_hash is not None and cls._board_hash(result) == ko_hash:
            return None
        return result

    @staticmethod
    def _neighbors(size: int, x: int, y: int) -> list[tuple[int, int]]:
        return [(nx, ny) for nx, ny in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)) if 0 <= nx < size and 0 <= ny < size]

    @classmethod
    def _group_and_liberties(cls, board: list[list[str | None]], x: int, y: int) -> tuple[set[tuple[int,int]], set[tuple[int,int]]]:
        color = board[y][x]
        if color is None:
            return set(), set()
        group, liberties, stack = set(), set(), [(x, y)]
        while stack:
            px, py = stack.pop()
            if (px, py) in group:
                continue
            group.add((px, py))
            for nx, ny in cls._neighbors(len(board), px, py):
                if board[ny][nx] is None:
                    liberties.add((nx, ny))
                elif board[ny][nx] == color and (nx, ny) not in group:
                    stack.append((nx, ny))
        return group, liberties


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
            "verification_level": "local_preliminary",
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
            "local_region": result.get("local_region"),
            "analysis": result,
        }


    @staticmethod
    def _all_board_coordinates(board_size: int) -> list[str]:
        letters = "ABCDEFGHJKLMNOPQRSTUVWXYZ"
        return [f"{letters[x]}{board_size-y}" for y in range(board_size) for x in range(board_size)]

    @staticmethod
    def local_region_from_target(board_size: int, target_coordinate: str, radius: int) -> dict[str, Any]:
        letters = "ABCDEFGHJKLMNOPQRSTUVWXYZ"
        raw = (target_coordinate or "").upper().strip()
        if len(raw) < 2 or raw[0] not in letters:
            raise ValueError("目標座標格式不正確")
        x = letters.index(raw[0])
        try:
            row = int(raw[1:])
        except ValueError as exc:
            raise ValueError("目標座標格式不正確") from exc
        y = board_size - row
        if not (0 <= x < board_size and 0 <= y < board_size):
            raise ValueError("目標座標超出棋盤")
        radius = max(2, min(int(radius), 8))
        min_x, max_x = max(0, x-radius), min(board_size-1, x+radius)
        min_y, max_y = max(0, y-radius), min(board_size-1, y+radius)
        allowed = [f"{letters[ix]}{board_size-iy}" for iy in range(min_y,max_y+1) for ix in range(min_x,max_x+1)]
        return {
            "target_coordinate": raw,
            "radius": radius,
            "bounds": {"min_x": min_x, "max_x": max_x, "min_y": min_y, "max_y": max_y},
            "allowed_moves": allowed,
            "until_depth": 24,
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
