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

# sem guardar a referência o garbage collector pode recolher a task, e uma
# exceção que escape do loop morreria em silêncio -- a API seguiria servindo o
# último snapshot para sempre, que foi exatamente o que aconteceu em produção
_tarefas = set()

# intervalo antes de reiniciar um loop que morreu -- evita queimar CPU num
# reinício em looping apertado se o motivo for algo persistente (ex: bug que
# estoura em toda chamada)
ESPERA_REINICIO_SEGUNDOS = 10


def _supervisionar(fabrica_coro, nome: str):
    """Roda fabrica_coro() em background e reinicia se a task terminar.

    Os loops de coleta (scrape/odds) já engolem qualquer Exception internamente
    e rodam pra sempre -- chegar até aqui já é anormal (ex: um erro que escapa
    do próprio try/except, ou um cancelamento inesperado). Sem reiniciar, a API
    fica servindo o último snapshot pra sempre, com o board cada vez mais
    velho e sem ninguém notando -- que foi exatamente o que já aconteceu em
    produção.
    """

    def iniciar():
        tarefa = asyncio.create_task(fabrica_coro(), name=nome)
        _tarefas.add(tarefa)
        tarefa.add_done_callback(ao_terminar)
        return tarefa

    def ao_terminar(t: asyncio.Task):
        _tarefas.discard(t)
        if t.cancelled():
            motivo = "foi cancelado"
        else:
            motivo = f"terminou inesperadamente: {t.exception()!r}"
        print(f"Loop '{nome}' {motivo} -- reiniciando em {ESPERA_REINICIO_SEGUNDOS}s")

        async def reiniciar():
            await asyncio.sleep(ESPERA_REINICIO_SEGUNDOS)
            iniciar()

        tarefa_reinicio = asyncio.create_task(reiniciar(), name=f"{nome}-reinicio")
        _tarefas.add(tarefa_reinicio)
        tarefa_reinicio.add_done_callback(lambda t: _tarefas.discard(t))

    return iniciar()


@app.on_event("startup")
async def startup_event():
    # dispara o scraper e o buscador de odds rodando em background, sem bloquear a API
    _supervisionar(lambda: scrape_loop(state), "scrape")
    _supervisionar(lambda: odds_loop(state), "odds")

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
