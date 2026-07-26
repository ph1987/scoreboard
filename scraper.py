from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime

import httpx

INTERVALO_SEGUNDOS = 30

URL_RODADA = "https://ge.globo.com/futebol/brasileirao-serie-a/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

STATUS_POR_BROADCAST = {
    "LIVE": "ao_vivo",
    "ENCERRADA": "encerrado",
}

# "moment" vem relativo ao período (zera a cada tempo); somamos a base de cada
# período para obter o minuto padrão de partida (base 90)
BASE_MINUTO_POR_PERIODO = {
    "PRIMEIRO_TEMPO": 0,
    "SEGUNDO_TEMPO": 45,
    "PRIMEIRO_TEMPO_PRORROGACAO": 90,
    "SEGUNDO_TEMPO_PRORROGACAO": 105,
}

DIR_ESCUDOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads", "escudos")

DIAS_SEMANA = {
    0: "Segunda-feira",
    1: "Terça-feira",
    2: "Quarta-feira",
    3: "Quinta-feira",
    4: "Sexta-feira",
    5: "Sábado",
    6: "Domingo",
}


def _data_hora_formatada(data_realizacao: str) -> str:
    dt = datetime.fromisoformat(data_realizacao)
    dia_semana = DIAS_SEMANA[dt.weekday()]
    return f"{dia_semana} ({dt.strftime('%d/%m')}) — {dt.strftime('%H:%M')}"


def _extrair_bloco_balanceado(texto: str, indice_abertura: int) -> str:
    """Extrai um literal JSON (objeto ou array) a partir do índice de '{' ou '[',
    respeitando aninhamento e strings — regex simples falha com JSON grande/aninhado."""
    profundidade = 0
    em_string = False
    escapando = False
    for i in range(indice_abertura, len(texto)):
        c = texto[i]
        if em_string:
            if escapando:
                escapando = False
            elif c == "\\":
                escapando = True
            elif c == '"':
                em_string = False
        else:
            if c == '"':
                em_string = True
            elif c in "[{":
                profundidade += 1
            elif c in "]}":
                profundidade -= 1
                if profundidade == 0:
                    return texto[indice_abertura : i + 1]
    raise ValueError("bloco JSON não fechado corretamente")


def _status_partida(jogo: dict) -> str:
    broadcast_id = jogo["transmissao"]["broadcast"]["id"]
    if broadcast_id in STATUS_POR_BROADCAST:
        return STATUS_POR_BROADCAST[broadcast_id]
    return "agendado"


def _placar_por_eventos(eventos: list[dict], sigla_casa: str, sigla_fora: str) -> tuple[int, int]:
    """Conta os gols na própria lista de eventos (já ajustada p/ gol contra) em vez de
    confiar no placar 'oficial' do resumo da rodada, que atualiza com defasagem em
    relação ao lance a lance e pode ficar dessincronizado por alguns ciclos."""
    gols_casa = sum(1 for e in eventos if e["tipo"] == "gol" and e["time"] == sigla_casa)
    gols_fora = sum(1 for e in eventos if e["tipo"] == "gol" and e["time"] == sigla_fora)
    return gols_casa, gols_fora


def _minuto_partida(play: dict) -> tuple[int, str]:
    """Converte o 'moment' (tempo dentro do período) para o minuto padrão de
    partida de futebol (base 90). Retorna (valor numérico p/ ordenação, rótulo "N'")."""
    moment = play.get("moment") or ""
    period_id = (play.get("period") or {}).get("id")
    minutos_str = moment.split(":", 1)[0]

    try:
        minutos = int(minutos_str)
    except ValueError:
        return (0, moment)

    minuto_total = BASE_MINUTO_POR_PERIODO.get(period_id, 0) + minutos + 1
    return (minuto_total, f"{minuto_total}'")


async def _garantir_escudo_local(client: httpx.AsyncClient, equipe: dict) -> str | None:
    """Baixa o escudo do time uma única vez e guarda em uploads/escudos/,
    para servir localmente em vez de referenciar a CDN da fonte a cada carregamento."""
    sigla = equipe["sigla"]
    caminho_local = os.path.join(DIR_ESCUDOS, f"{sigla}.svg")
    url_local = f"/uploads/escudos/{sigla}.svg"

    if os.path.exists(caminho_local):
        return url_local

    try:
        resp = await client.get(equipe["escudo"], headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except httpx.HTTPError:
        return None

    os.makedirs(DIR_ESCUDOS, exist_ok=True)
    with open(caminho_local, "wb") as f:
        f.write(resp.content)

    return url_local


async def _buscar_eventos_partida(
    client: httpx.AsyncClient, url_jogo: str, sigla_casa: str, sigla_fora: str
) -> list[dict]:
    resp = await client.get(url_jogo, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    html = resp.text

    marcador = "plays: Array.from("
    idx = html.find(marcador)
    if idx == -1:
        return []
    inicio_array = html.find("[", idx)
    plays = json.loads(_extrair_bloco_balanceado(html, inicio_array))

    def time_adversario(sigla: str) -> str:
        return sigla_fora if sigla == sigla_casa else sigla_casa

    eventos = []
    for play in plays:
        tipo_jogada = (play.get("playType") or {}).get("id")
        detalhes = play.get("details") or {}
        jogador = (detalhes.get("athlete") or {}).get("popularName")
        time_sigla = (detalhes.get("team") or {}).get("abbreviation")
        minuto_num, minuto_label = _minuto_partida(play)

        if tipo_jogada == "GOAL":
            gol_contra = detalhes.get("kind") == "OWN_GOAL"
            # gol contra favorece o adversário: o escudo/lado exibido deve ser o do time beneficiado,
            # não o time do jogador que marcou contra
            time_beneficiado = time_adversario(time_sigla) if gol_contra else time_sigla
            eventos.append(
                {
                    "tipo": "gol",
                    "time": time_beneficiado,
                    "minuto": minuto_label,
                    "jogador": jogador,
                    "contra": gol_contra,
                    "_minuto_ordenacao": minuto_num,
                }
            )
        elif tipo_jogada == "CARD" and detalhes.get("kind") == "RED":
            eventos.append(
                {
                    "tipo": "cartao_vermelho",
                    "time": time_sigla,
                    "minuto": minuto_label,
                    "jogador": jogador,
                    "_minuto_ordenacao": minuto_num,
                }
            )

    eventos.sort(key=lambda e: e["_minuto_ordenacao"])
    for evento in eventos:
        del evento["_minuto_ordenacao"]

    return eventos


async def fetch_dados() -> dict:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(URL_RODADA, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        html = resp.text

        match = re.search(r"const classificacao = \{", html)
        if not match:
            raise ValueError("bloco 'classificacao' não encontrado no HTML")
        inicio = match.end() - 1
        dados_rodada = json.loads(_extrair_bloco_balanceado(html, inicio))

        jogos = dados_rodada["lista_jogos"]

        tarefas_eventos = []
        tarefas_escudos = []
        for jogo in jogos:
            if jogo["jogo_ja_comecou"]:
                url_jogo = jogo["transmissao"]["url"]
                sigla_casa = jogo["equipes"]["mandante"]["sigla"]
                sigla_fora = jogo["equipes"]["visitante"]["sigla"]
                tarefas_eventos.append(
                    _buscar_eventos_partida(client, url_jogo, sigla_casa, sigla_fora)
                )
            else:
                tarefas_eventos.append(asyncio.sleep(0, result=[]))

            tarefas_escudos.append(_garantir_escudo_local(client, jogo["equipes"]["mandante"]))
            tarefas_escudos.append(_garantir_escudo_local(client, jogo["equipes"]["visitante"]))

        listas_eventos = await asyncio.gather(*tarefas_eventos, return_exceptions=True)
        escudos = await asyncio.gather(*tarefas_escudos, return_exceptions=True)

    partidas = []
    for i, (jogo, eventos_resultado) in enumerate(zip(jogos, listas_eventos)):
        eventos_ok = not isinstance(eventos_resultado, Exception)
        eventos = eventos_resultado if eventos_ok else []

        escudo_casa = escudos[i * 2] if not isinstance(escudos[i * 2], Exception) else None
        escudo_fora = escudos[i * 2 + 1] if not isinstance(escudos[i * 2 + 1], Exception) else None

        sigla_casa = jogo["equipes"]["mandante"]["sigla"]
        sigla_fora = jogo["equipes"]["visitante"]["sigla"]
        escudo_por_sigla = {sigla_casa: escudo_casa, sigla_fora: escudo_fora}

        for evento in eventos:
            evento["escudo_time"] = escudo_por_sigla.get(evento["time"])

        if jogo["jogo_ja_comecou"] and eventos_ok:
            placar_casa, placar_fora = _placar_por_eventos(eventos, sigla_casa, sigla_fora)
        else:
            # partida ainda não começou, ou não foi possível buscar o lance a lance:
            # usa o placar oficial do resumo da rodada como melhor informação disponível
            placar_casa = jogo["placar_oficial_mandante"]
            placar_fora = jogo["placar_oficial_visitante"]

        partidas.append(
            {
                "time_casa": jogo["equipes"]["mandante"]["nome_popular"],
                "time_fora": jogo["equipes"]["visitante"]["nome_popular"],
                "escudo_casa": escudo_casa,
                "escudo_fora": escudo_fora,
                "placar_casa": placar_casa,
                "placar_fora": placar_fora,
                "eventos": eventos,
                "status": _status_partida(jogo),
                "data_hora": _data_hora_formatada(jogo["data_realizacao"]),
            }
        )

    return {"rodada": dados_rodada["rodada"]["atual"], "partidas": partidas}


async def scrape_loop(state):
    while True:
        try:
            dados = await fetch_dados()
            state.update(dados)
        except Exception as e:
            print(f"Erro ao buscar dados: {e}")
        await asyncio.sleep(INTERVALO_SEGUNDOS)
