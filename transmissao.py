from __future__ import annotations

import asyncio
import json

import httpx

from parsing import extrair_bloco_balanceado, mesmo_time

# a agenda do dia é a mesma pra todo mundo e muda pouco (a emissora de um jogo
# não costuma trocar depois de anunciada); 15 min já é bem mais que suficiente
TRANSMISSAO_INTERVALO_SEGUNDOS = 15 * 60
TRANSMISSAO_INTERVALO_SEM_JOGO_HOJE_SEGUNDOS = 3 * 60 * 60

URL_GUIADABOLA = "https://guiadabola.com.br/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


async def fetch_transmissoes() -> list[dict]:
    """Busca onde passa cada jogo do dia no Guia da Bola.

    A página embute a agenda inteira (todas as competições do dia, com os
    canais de cada jogo) num único bloco `window.__SSR_AGENDA__` -- uma
    requisição já cobre qualquer campeonato que tenha jogo hoje, sem
    precisar de uma URL por competição.
    """
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(URL_GUIADABOLA, headers=HEADERS)
        resp.raise_for_status()
        html = resp.text

    marcador = "window.__SSR_AGENDA__="
    idx = html.find(marcador)
    if idx == -1:
        return []
    dados = json.loads(extrair_bloco_balanceado(html, idx + len(marcador)))

    # a mesma partida aparece repetida em mais de uma seção, vinda de fontes
    # diferentes (uma com nomes de canal formatados, outra tudo em
    # maiúsculas) -- fica valendo a primeira ocorrência de cada confronto
    partidas: dict[tuple[str, str], dict] = {}
    for secao in dados.get("sections") or []:
        for jogo in secao.get("matches") or []:
            time_casa = jogo.get("homeTeam")
            time_fora = jogo.get("awayTeam")
            canais = [
                {"nome": canal["name"], "gratis": bool(canal.get("free"))}
                for canal in jogo.get("channels") or []
                if canal.get("name")
            ]
            if not time_casa or not time_fora or not canais:
                continue

            chave = (time_casa, time_fora)
            if chave not in partidas:
                partidas[chave] = {"time_casa": time_casa, "time_fora": time_fora, "canais": canais}

    return list(partidas.values())


def encontrar_transmissao(time_casa: str, time_fora: str, transmissoes: list[dict]) -> list[dict] | None:
    for item in transmissoes:
        if mesmo_time(item["time_casa"], time_casa) and mesmo_time(item["time_fora"], time_fora):
            return item["canais"]
    return None


async def transmissao_loop(state):
    while True:
        intervalo = TRANSMISSAO_INTERVALO_SEGUNDOS
        try:
            transmissoes = await fetch_transmissoes()
            state.update_transmissoes(transmissoes)
            intervalo = (
                TRANSMISSAO_INTERVALO_SEGUNDOS
                if state.tem_partida_hoje()
                else TRANSMISSAO_INTERVALO_SEM_JOGO_HOJE_SEGUNDOS
            )
        except Exception as e:
            print(f"Erro ao buscar transmissões: {e}")
        await asyncio.sleep(intervalo)
