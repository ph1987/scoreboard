from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
import asyncio
import os


class EstaticosComCache(StaticFiles):
    """StaticFiles com Cache-Control explícito.

    Sem esse cabeçalho o navegador aplica cache heurístico e pode continuar
    rodando o JS antigo depois de um deploy, mesmo com a aba recarregada.
    """

    def __init__(self, *args, cache_control: str, **kwargs):
        super().__init__(*args, **kwargs)
        self._cache_control = cache_control

    def file_response(self, *args, **kwargs) -> Response:
        resposta = super().file_response(*args, **kwargs)
        # vale também no 304, senão a política some na revalidação
        resposta.headers["Cache-Control"] = self._cache_control
        return resposta

from state import MatchState
from scraper import scrape_loop
from odds import odds_loop

app = FastAPI()

# cada cliente busca /api/partidas a cada 30s; o JSON é bem repetitivo e encolhe
# cerca de 9x comprimido. Abaixo de 500 bytes o ganho não paga o overhead.
app.add_middleware(GZipMiddleware, minimum_size=500)

state = MatchState()

@app.on_event("startup")
async def startup_event():
    # dispara o scraper e o buscador de odds rodando em background, sem bloquear a API
    asyncio.create_task(scrape_loop(state))
    asyncio.create_task(odds_loop(state))

@app.get("/api/partidas")
async def get_partidas():
    return state.get_current()

# uploads/: arquivos baixados/enviados em tempo de execução (escudos, áudio de alerta).
# Mudam raramente e sempre com o mesmo nome, então um dia de cache é seguro.
os.makedirs("uploads/escudos", exist_ok=True)
app.mount(
    "/uploads",
    EstaticosComCache(directory="uploads", cache_control="public, max-age=86400"),
    name="uploads",
)

# frontend: "no-cache" não é "não guarde", é "revalide antes de usar". Com o ETag
# que o StaticFiles já envia, a revalidação vira um 304 barato e o deploy chega
# na hora em quem está com a aba aberta.
app.mount(
    "/",
    EstaticosComCache(directory="static", html=True, cache_control="no-cache"),
    name="static",
)
