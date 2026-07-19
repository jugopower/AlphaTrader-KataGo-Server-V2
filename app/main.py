from typing import Literal

from fastapi import FastAPI

from pydantic import BaseModel, Field

app = FastAPI(

    title="AlphaTrader KataGo Server V2",

    version="0.1.5",

)

class AnalyzeRequest(BaseModel):

    board_size: Literal[9, 13, 19] = 19

    moves: list[str] = Field(default_factory=list)

    next_player: Literal["B", "W"] = "B"

class AnalyzeResponse(BaseModel):

    status: str

    build: str

    mode: str

    board_size: int

    move_count: int

    next_player: str

    black_winrate: float

    white_winrate: float

    message: str

@app.get("/")

def root():

    return {

        "status": "ok",

        "service": "AlphaTrader KataGo Server V2",

        "build": "Build019.5",

    }

@app.get("/health")

def health():

    return {

        "status": "healthy",

        "build": "Build019.5",

    }

@app.post("/analyze", response_model=AnalyzeResponse)

def analyze(request: AnalyzeRequest):

    move_count = len(request.moves)

    adjustment = min(move_count * 0.001, 0.08)

    black_winrate = 0.50

    if request.next_player == "B":

        black_winrate += adjustment

    else:

        black_winrate -= adjustment

    black_winrate = round(max(0.01, min(0.99, black_winrate)), 3)

    white_winrate = round(1.0 - black_winrate, 3)

    return AnalyzeResponse(

        status="ok",

        build="Build019.5",

        mode="demo",

        board_size=request.board_size,

        move_count=move_count,

        next_player=request.next_player,

        black_winrate=black_winrate,

        white_winrate=white_winrate,

        message="API 已正常運作；目前為示範分析，尚未連接真正 KataGo 引擎。",

    )
