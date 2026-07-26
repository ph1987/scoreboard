from __future__ import annotations

import asyncio
import json
import re
import unicodedata

import httpx

from parsing import extrair_bloco_balanceado

# scraping direto e gratuito — sem limite de créditos como numa API paga,
# mas ainda assim não há motivo pra bater nos sites com mais frequência que o placar
ODDS_INTERVALO_SEGUNDOS = 10 * 60

URL_BETANO = "https://www.betano.bet.br/sport/futebol/brasil/brasileirao-serie-a-betano/10016/"
URL_BETNACIONAL = "https://betnacional.bet.br/apostas-brasileirao-serie-a"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def _normalizar(nome: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", sem_acento.lower()).strip()


def _mesmo_time(nome_a: str, nome_b: str) -> bool:
    a, b = _normalizar(nome_a), _normalizar(nome_b)
    if a == b:
        return True
    palavras_a, palavras_b = set(a.split()), set(b.split())
    if not palavras_a or not palavras_b:
        return False
    intersecao = palavras_a & palavras_b
    return bool(intersecao) and len(intersecao) / min(len(palavras_a), len(palavras_b)) >= 0.5


async def _fetch_odds_betano() -> list[dict]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(URL_BETANO, headers=HEADERS)
        resp.raise_for_status()
        html = resp.text

    marcador = '"events":[{"stats"'
    idx = html.find(marcador)
    if idx == -1:
        return []
    inicio_array = html.rfind("[", 0, idx + len(marcador))
    eventos = json.loads(extrair_bloco_balanceado(html, inicio_array))

    odds = []
    for evento in eventos:
        participantes = evento.get("participants") or []
        markets = evento.get("markets") or []
        if len(participantes) != 2 or not markets:
            continue

        selecoes = markets[0].get("selections") or []
        precos = {s["name"]: s["price"] for s in selecoes}
        if not {"1", "X", "2"} <= precos.keys():
            continue

        odds.append(
            {
                "time_casa": participantes[0]["name"],
                "time_fora": participantes[1]["name"],
                "casa": precos["1"],
                "empate": precos["X"],
                "fora": precos["2"],
                "casa_de_apostas": "Betano",
            }
        )

    return odds


async def _fetch_odds_betnacional() -> list[dict]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(URL_BETNACIONAL, headers=HEADERS)
        resp.raise_for_status()
        html = resp.text

    marcador_tag = "__NEXT_DATA__"
    idx = html.find(marcador_tag)
    if idx == -1:
        return []
    marcador_json = 'type="application/json">'
    inicio = html.find(marcador_json, idx)
    if inicio == -1:
        return []
    inicio += len(marcador_json)
    dados = json.loads(extrair_bloco_balanceado(html, inicio))

    cache = dados["props"]["pageProps"]["initialState"]["cache"]
    eventos = cache["events"]["entities"]
    outcomes = cache["outcomes"]["entities"]

    odds = []
    for evento_id, evento in eventos.items():
        if evento.get("type") != "prematch":
            continue
        if evento.get("tournament", {}).get("name") != "Brasileirão Série A":
            continue
        home = evento.get("home")
        away = evento.get("away")
        if not home or not away:
            continue

        prefixo = f"{evento_id}_"
        precos = {}
        for chave, outcome in outcomes.items():
            if not chave.startswith(prefixo):
                continue
            preco = (outcome.get("odd") or {}).get("effective")
            if outcome.get("name") in (home["name"], away["name"], "Empate") and preco is not None:
                precos[outcome["name"]] = preco

        if not {home["name"], away["name"], "Empate"} <= precos.keys():
            continue

        odds.append(
            {
                "time_casa": home["name"],
                "time_fora": away["name"],
                "casa": precos[home["name"]],
                "empate": precos["Empate"],
                "fora": precos[away["name"]],
                "casa_de_apostas": "Betnacional",
            }
        )

    return odds


async def fetch_odds() -> list[list[dict]]:
    """Busca as odds de cada casa em paralelo. Retorna uma lista por casa,
    pra que uma fonte fora do ar não derrube as demais."""
    resultados = await asyncio.gather(
        _fetch_odds_betano(), _fetch_odds_betnacional(), return_exceptions=True
    )
    return [r if not isinstance(r, Exception) else [] for r in resultados]


def encontrar_odds(time_casa: str, time_fora: str, lista_odds: list[dict]) -> dict | None:
    for item in lista_odds:
        if _mesmo_time(item["time_casa"], time_casa) and _mesmo_time(item["time_fora"], time_fora):
            return {
                "casa": item["casa"],
                "empate": item["empate"],
                "fora": item["fora"],
                "casa_de_apostas": item["casa_de_apostas"],
            }
    return None


async def odds_loop(state):
    while True:
        try:
            listas_odds = await fetch_odds()
            state.update_odds(listas_odds)
        except Exception as e:
            print(f"Erro ao buscar odds: {e}")
        await asyncio.sleep(ODDS_INTERVALO_SEGUNDOS)
