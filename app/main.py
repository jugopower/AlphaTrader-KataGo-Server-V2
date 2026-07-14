from fastapi import FastAPI

app = FastAPI(

    title="AlphaTrader KataGo Server V2",

    version="0.1.0"

)

@app.get("/")

def root():

    return {

        "status": "ok",

        "service": "AlphaTrader KataGo Server V2"

    }

@app.get("/health")

def health():

    return {

        "status": "healthy",

        "build": "Build019.1"

    }
