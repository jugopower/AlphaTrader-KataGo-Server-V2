from typing import Any, Literal, Union

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.katago_engine import KataGoEngine

BUILD = "Build027.0"

app = FastAPI(title="AlphaTrader KataGo Server V2", version="0.27.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = KataGoEngine()

class StoneInput(BaseModel):
    color: Literal["B", "W"]
    coordinate: str

class MoveInput(BaseModel):
    color: Literal["B", "W"]
    coordinate: str

class AnalyzeRequest(BaseModel):
    board_size: Literal[9, 13, 19] = 19
    moves: list[Union[str, MoveInput]] = Field(default_factory=list)
    # Accept both object form and compact ["B", "D4"] form from the frontend.
    initial_stones: list[Any] = Field(default_factory=list)
    next_player: Literal["B", "W"] = "B"
    komi: float = 7.5
    max_visits: int = Field(default=50, ge=1, le=5000)

class SolveLifeDeathRequest(AnalyzeRequest):
    question_no: str = ""
    problem_type: Literal["black_kill_white", "white_kill_black", "black_live", "white_live"] = "black_kill_white"
    target_coordinate: str = Field(min_length=2, max_length=4)
    region_radius: int = Field(default=4, ge=2, le=8)
    visits_per_move: int = Field(default=80, ge=10, le=1000)
    max_solution_moves: int = Field(default=12, ge=1, le=30)

class VerifyLifeDeathRequest(AnalyzeRequest):
    question_no: str = ""
    problem_type: Literal["black_kill_white", "white_kill_black", "black_live", "white_live"] = "black_kill_white"
    verification_visits: int = Field(default=300, ge=50, le=2000)
    target_coordinate: str = Field(min_length=2, max_length=4)
    region_radius: int = Field(default=4, ge=2, le=8)

@app.get("/")
def root() -> dict[str, Any]:
    return {"status": "ok", "service": "AlphaTrader KataGo Server V2", "build": BUILD}

@app.get("/health")
def health() -> dict[str, Any]:
    readiness = engine.readiness()
    return {"status": "healthy", "build": BUILD, "engine_ready": readiness["ready"]}

@app.get("/engine-status")
def engine_status() -> dict[str, Any]:
    return {"status": "ok", "build": BUILD, "engine": engine.readiness()}

@app.post("/analyze")
def analyze(request: AnalyzeRequest) -> dict[str, Any]:
    moves = [m if isinstance(m, str) else m.model_dump() for m in request.moves]
    initial_stones: list[dict[str, str]] = []
    for index, stone in enumerate(request.initial_stones):
        color = "B"
        coordinate = ""

        if isinstance(stone, StoneInput):
            color = stone.color
            coordinate = stone.coordinate
        elif isinstance(stone, dict):
            color = str(stone.get("color", stone.get("player", "B"))).upper().strip()
            coordinate = str(
                stone.get("coordinate", stone.get("vertex", stone.get("move", "")))
            ).strip()
        elif isinstance(stone, (list, tuple)) and len(stone) >= 2:
            color = str(stone[0]).upper().strip()
            coordinate = str(stone[1]).strip()
        elif isinstance(stone, str):
            raw = stone.strip()
            if raw and raw[0].upper() in {"B", "W"}:
                color = raw[0].upper()
                coordinate = raw[1:].lstrip(" ,:").strip()

        if color not in {"B", "W"}:
            color = "B" if index % 2 == 0 else "W"

        if coordinate:
            initial_stones.append({"color": color, "coordinate": coordinate})

    result = engine.analyze(
        board_size=request.board_size,
        moves=moves,
        initial_stones=initial_stones,
        next_player=request.next_player,
        komi=request.komi,
        max_visits=request.max_visits,
    )
    result["build"] = BUILD
    return result


@app.post("/verify-life-death")
def verify_life_death(request: VerifyLifeDeathRequest) -> dict[str, Any]:
    moves = [m if isinstance(m, str) else m.model_dump() for m in request.moves]
    initial_stones: list[dict[str, str]] = []
    for index, stone in enumerate(request.initial_stones):
        color = "B"
        coordinate = ""
        if isinstance(stone, StoneInput):
            color, coordinate = stone.color, stone.coordinate
        elif isinstance(stone, dict):
            color = str(stone.get("color", stone.get("player", "B"))).upper().strip()
            coordinate = str(stone.get("coordinate", stone.get("vertex", stone.get("move", "")))).strip()
        elif isinstance(stone, (list, tuple)) and len(stone) >= 2:
            color, coordinate = str(stone[0]).upper().strip(), str(stone[1]).strip()
        elif isinstance(stone, str):
            raw = stone.strip()
            if raw and raw[0].upper() in {"B", "W"}:
                color, coordinate = raw[0].upper(), raw[1:].lstrip(" ,:").strip()
        if color not in {"B", "W"}:
            color = "B" if index % 2 == 0 else "W"
        if coordinate:
            initial_stones.append({"color": color, "coordinate": coordinate})

    next_player = "W" if request.problem_type.startswith("white_") else "B"
    try:
        local_region = engine.local_region_from_target(
            request.board_size, request.target_coordinate, request.region_radius
        )
    except ValueError as exc:
        return {"status": "error", "mode": "life_death_verification", "message": str(exc), "build": BUILD}

    result = engine.analyze(
        board_size=request.board_size,
        moves=moves,
        initial_stones=initial_stones,
        next_player=next_player,
        komi=request.komi,
        max_visits=request.verification_visits,
        timeout_seconds=120.0,
        local_region=local_region,
    )
    report = engine.build_life_death_report(
        result=result,
        question_no=request.question_no,
        problem_type=request.problem_type,
    )
    report["build"] = BUILD
    return report

@app.post("/solve-life-death")
def solve_life_death(request: SolveLifeDeathRequest) -> dict[str, Any]:
    moves = [m if isinstance(m, str) else m.model_dump() for m in request.moves]
    initial_stones: list[dict[str, str]] = []
    for index, stone in enumerate(request.initial_stones):
        color = "B"
        coordinate = ""
        if isinstance(stone, StoneInput):
            color, coordinate = stone.color, stone.coordinate
        elif isinstance(stone, dict):
            color = str(stone.get("color", stone.get("player", "B"))).upper().strip()
            coordinate = str(stone.get("coordinate", stone.get("vertex", stone.get("move", "")))).strip()
        elif isinstance(stone, (list, tuple)) and len(stone) >= 2:
            color, coordinate = str(stone[0]).upper().strip(), str(stone[1]).strip()
        elif isinstance(stone, str):
            raw = stone.strip()
            if raw and raw[0].upper() in {"B", "W"}:
                color, coordinate = raw[0].upper(), raw[1:].lstrip(" ,:").strip()
        if color not in {"B", "W"}:
            color = "B" if index % 2 == 0 else "W"
        if coordinate:
            initial_stones.append({"color": color, "coordinate": coordinate})

    first_player = "W" if request.problem_type.startswith("white_") else "B"
    try:
        local_region = engine.local_region_from_target(
            request.board_size, request.target_coordinate, request.region_radius
        )
    except ValueError as exc:
        return {"status": "error", "mode": "life_death_solution_line", "message": str(exc), "build": BUILD}

    result = engine.solve_life_death_line(
        board_size=request.board_size,
        moves=moves,
        initial_stones=initial_stones,
        next_player=first_player,
        komi=request.komi,
        visits_per_move=request.visits_per_move,
        max_moves=request.max_solution_moves,
        local_region=local_region,
    )
    result.update({
        "build": BUILD,
        "question_no": request.question_no,
        "problem_type": request.problem_type,
    })
    return result

