from typing import Any, Literal, Union

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.katago_engine import KataGoEngine

BUILD = "Build020.5"

app = FastAPI(title="AlphaTrader KataGo Server V2", version="0.2.5")
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
    initial_stones: list[StoneInput] = Field(default_factory=list)
    next_player: Literal["B", "W"] = "B"
    komi: float = 7.5
    max_visits: int = Field(default=50, ge=1, le=5000)

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
    initial_stones = [s.model_dump() for s in request.initial_stones]
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
