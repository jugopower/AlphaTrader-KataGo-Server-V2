from typing import Any, Literal

from fastapi import FastAPI

from pydantic import BaseModel, Field

from app.katago_engine import KataGoEngine

app = FastAPI(

    title="AlphaTrader KataGo Server V2",

    version="0.1.6",

)

engine = KataGoEngine()

class AnalyzeRequest(BaseModel):

    board_size: Literal[9, 13, 19] = 19

    moves: list[str] = Field(default_factory=list)

    next_player: Literal["B", "W"] = "B"

    komi: float = 7.5

@app.get("/")

def root() -> dict[str, Any]:

    return {

        "status": "ok",

        "service": "AlphaTrader KataGo Server V2",

        "build": "Build019.6",

    }

@app.get("/health")

def health() -> dict[str, Any]:

    readiness = engine.readiness()

    return {

        "status": "healthy",

        "build": "Build019.6",

        "engine_ready": readiness["ready"],

    }

@app.get("/engine-status")

def engine_status() -> dict[str, Any]:

    return {

        "status": "ok",

        "build": "Build019.6",

        "engine": engine.readiness(),

    }

@app.post("/analyze")

def analyze(request: AnalyzeRequest) -> dict[str, Any]:

    result = engine.analyze(

        board_size=request.board_size,

        moves=request.moves,

        next_player=request.next_player,

        komi=request.komi,

    )

    result["build"] = "Build019.6"

    return result
