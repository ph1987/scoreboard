from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import asyncio
import os

from state import MatchState
from scraper import scrape_loop

app = FastAPI()
state = MatchState()

@app.on_event("startup")
async def startup_event():
    # dispara o scraper rodando em background, sem bloquear a API
    asyncio.create_task(scrape_loop(state))

@app.get("/api/partidas")
async def get_partidas():
    return state.get_current()

# uploads/: arquivos baixados/enviados em tempo de execução (escudos, áudio de alerta)
os.makedirs("uploads/escudos", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# serve os arquivos estáticos (index.html, css, js)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
