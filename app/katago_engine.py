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

        moves: list[str],

        next_player: str,

        komi: float = 7.5,

    ) -> dict[str, Any]:

        readiness = self.readiness()

        if not readiness["ready"]:

            return {

                "status": "unavailable",

                "mode": "katago",

                "message": "KataGo 執行檔、模型或設定檔尚未完整安裝。",

                "readiness": readiness,

            }

        query = {

            "id": "build0196-analysis",

            "moves": self._convert_moves(moves),

            "initialStones": [],

            "rules": "tromp-taylor",

            "komi": komi,

            "boardXSize": board_size,

            "boardYSize": board_size,

            "includePolicy": True,

            "maxVisits": 50,

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

                "message": "KataGo 分析逾時。",

            }

        if process.returncode != 0:

            return {

                "status": "error",

                "mode": "katago",

                "message": "KataGo 執行失敗。",

                "stderr": process.stderr[-1000:],

            }

        output_lines = [

            line for line in process.stdout.splitlines() if line.strip()

        ]

        if not output_lines:

            return {

                "status": "error",

                "mode": "katago",

                "message": "KataGo 沒有回傳分析結果。",

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

    def _convert_moves(moves: list[str]) -> list[list[str]]:

        converted: list[list[str]] = []

        for index, coordinate in enumerate(moves):

            color = "B" if index % 2 == 0 else "W"

            converted.append([color, coordinate])

        return converted
