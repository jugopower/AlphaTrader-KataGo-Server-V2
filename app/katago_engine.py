import json

import os

import subprocess

from typing import Any

class KataGoEngine:

    def __init__(self) -> None:

        self.binary = os.getenv("KATAGO_BIN", "/app/bin/katago")

        self.model = os.getenv("KATAGO_MODEL", "/app/models/model.bin.gz")

        self.config = os.getenv(

            "KATAGO_CONFIG",

            "/app/config/analysis.cfg",

        )

    def readiness(self) -> dict[str, Any]:

        checks = {

            "binary_exists": os.path.isfile(self.binary),

            "binary_executable": os.access(self.binary, os.X_OK),

            "model_exists": os.path.isfile(self.model),

            "config_exists": os.path.isfile(self.config),

        }

        return {

            "ready": all(checks.values()),

            "checks": checks,

            "paths": {

                "binary": self.binary,

                "model": self.model,

                "config": self.config,

            },

        }

    def analyze(

        self,

        board_size: int,

        moves: list[Any],

        initial_stones: list[dict[str, str]],

        next_player: str,

        komi: float = 7.5,

        max_visits: int = 50,

    ) -> dict[str, Any]:

        readiness = self.readiness()

        if not readiness["ready"]:

            return {

                "status": "unavailable",

                "mode": "katago",

                "message": "KataGo å·è¡æªãæ¨¡åæè¨­å®æªå°æªå®æ´å®è£ã",

                "readiness": readiness,

            }

        query = {

            "id": "build0196-analysis",

            "moves": self._convert_moves(moves),

            "initialStones": self._convert_initial_stones(initial_stones),

            "rules": "tromp-taylor",

            "komi": komi,

            "boardXSize": board_size,

            "boardYSize": board_size,

            "includePolicy": True,

            "maxVisits": max(1, min(int(max_visits), 5000)),

        }

        try:

            process = subprocess.run(

                [

                    self.binary,

                    "analysis",

                    "-config",

                    self.config,

                    "-model",

                    self.model,

                ],

                input=json.dumps(query) + "\n",

                text=True,

                capture_output=True,

                timeout=60,

                check=False,

            )

        except subprocess.TimeoutExpired:

            return {

                "status": "error",

                "mode": "katago",

                "message": "KataGo åæé¾æã",

            }

        if process.returncode != 0:

            return {

                "status": "error",

                "mode": "katago",

                "message": "KataGo å·è¡å¤±æã",

                "stderr": process.stderr[-1000:],

            }

        output_lines = [

            line for line in process.stdout.splitlines() if line.strip()

        ]

        if not output_lines:

            return {

                "status": "error",

                "mode": "katago",

                "message": "KataGo æ²æåå³åæçµæã",

            }

        result = json.loads(output_lines[-1])

        root_info = result.get("rootInfo", {})

        return {

            "status": "ok",

            "mode": "katago",

            "board_size": board_size,

            "move_count": len(moves),

            "next_player": next_player,

            "winrate": root_info.get("winrate"),

            "score_lead": root_info.get("scoreLead"),

            "visits": root_info.get("visits"),

            "move_infos": result.get("moveInfos", []),

        }

    @staticmethod

    def _convert_moves(moves: list[Any]) -> list[list[str]]:

        converted: list[list[str]] = []

        for index, move in enumerate(moves):

            if isinstance(move, dict):

                color = str(move.get("color", "B")).upper()

                coordinate = str(move.get("coordinate", "pass"))

            elif isinstance(move, (list, tuple)) and len(move) >= 2:

                color, coordinate = str(move[0]).upper(), str(move[1])

            else:

                color = "B" if index % 2 == 0 else "W"

                coordinate = str(move)

            converted.append([color, coordinate])

        return converted

    @staticmethod

    def _convert_initial_stones(stones: list[dict[str, str]]) -> list[list[str]]:

        converted: list[list[str]] = []

        for stone in stones:

            color = str(stone.get("color", "B")).upper()

            coordinate = str(stone.get("coordinate", ""))

            if color in {"B", "W"} and coordinate:

                converted.append([color, coordinate])

        return converted
