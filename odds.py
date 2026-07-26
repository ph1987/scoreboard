from __future__ import annotations

import asyncio
import json
import re
import unicodedata

import httpx

from parsing import extrair_bloco_balanceado

# scraping direto e gratuito — sem limite de créditos como numa API paga,
# mas ainda assim não há motivo pra bater no site com mais frequência que o placar
ODDS_INTERVALO_SEGUNDOS = 10 * 60

URL_RODADA = "https://www.betano.bet.br/sport/futebol/brasil/brasileirao-serie-a-betano/10016/"
BOOKMAKER_LABEL = "Betano"

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


async def fetch_odds() -> list[dict]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(URL_RODADA, headers=HEADERS)
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
                "casa_de_apostas": BOOKMAKER_LABEL,
            }
        )

    return odds


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
            odds = await fetch_odds()
            state.update_odds(odds)
        except Exception as e:
            print(f"Erro ao buscar odds: {e}")
        await asyncio.sleep(ODDS_INTERVALO_SEGUNDOS)
