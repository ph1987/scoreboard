from __future__ import annotations

import asyncio
import json
import os
import re
import traceback
from datetime import datetime, timedelta, timezone

import httpx

from parsing import extrair_bloco_balanceado

INTERVALO_SEGUNDOS = 30
# sem nenhuma partida hoje, não há por que ficar batendo na fonte a cada 30s
INTERVALO_SEM_JOGO_HOJE_SEGUNDOS = 3 * 60 * 60

# o servidor pode rodar em outro fuso (ex: deploy na Europa); "hoje" precisa ser
# sempre calculado no horário do Brasil, que não observa horário de verão desde 2019
FUSO_BRASIL = timezone(timedelta(hours=-3))

# "id" identifica a competição para o resto do sistema — as odds, por exemplo, só
# existem para o Brasileirão e não podem vazar para uma copa com os mesmos times
COMPETICOES = [
    {
        "id": "brasileirao",
        "nome": "Brasileirão Série A",
        "url": "https://ge.globo.com/futebol/brasileirao-serie-a/",
    },
    {
        "id": "copa-do-brasil",
        "nome": "Copa do Brasil",
        "url": "https://ge.globo.com/futebol/copa-do-brasil/",
    },
    {
        "id": "libertadores",
        "nome": "Libertadores",
        "url": "https://ge.globo.com/futebol/libertadores/",
    },
    {
        "id": "sul-americana",
        "nome": "Sul-Americana",
        "url": "https://ge.globo.com/futebol/copa-sul-americana/",
    },
]

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

# "FIM_DE_JOGO" é o apito final; "POS_JOGO" é a cobertura editorial que vem depois —
# ambos indicam que a partida já encerrou no lance a lance
PERIODOS_ENCERRADOS = {"FIM_DE_JOGO", "POS_JOGO"}

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


def _data_hora_formatada(data_realizacao: str | None, hora_realizacao: str | None = None) -> str:
    # times de rodadas futuras às vezes ainda não têm data confirmada pela fonte
    if not data_realizacao:
        return "Data a definir"
    dt = datetime.fromisoformat(data_realizacao)
    dia_semana = DIAS_SEMANA[dt.weekday()]
    # pontos corridos trazem "2026-08-08T16:00"; mata-mata traz só "2026-08-01",
    # com o horário num campo à parte
    hora = dt.strftime("%H:%M") if "T" in data_realizacao else (hora_realizacao or "")
    data = f"{dia_semana} ({dt.strftime('%d/%m')})"
    return f"{data} — {hora}" if hora else data


def _inicio_partida(jogo: dict) -> datetime | None:
    """Horário de início da partida, com fuso explícito.

    A fonte entrega em dois formatos: pontos corridos já traz a hora junto
    ("2026-08-08T16:00"), mata-mata traz só a data e a hora num campo à parte.
    """
    data_realizacao = jogo.get("data_realizacao")
    if not data_realizacao:
        return None

    dt = datetime.fromisoformat(data_realizacao)
    if "T" not in data_realizacao:
        hora = jogo.get("hora_realizacao") or ""
        partes = hora.split(":")
        if len(partes) >= 2 and partes[0].isdigit() and partes[1].isdigit():
            dt = dt.replace(hour=int(partes[0]), minute=int(partes[1]))

    return dt.replace(tzinfo=FUSO_BRASIL)


# partida em andamento primeiro; depois as que estão por vir, e por último as encerradas
ORDEM_POR_STATUS = {"ao_vivo": 0, "agendado": 1, "encerrado": 2}


def _ordenar_partidas(partidas: list[dict]) -> list[dict]:
    # "9999" joga as partidas sem data definida para o fim do próprio grupo
    return sorted(
        partidas,
        key=lambda p: (ORDEM_POR_STATUS.get(p["status"], 9), p.get("inicio") or "9999"),
    )


def _tem_partida_hoje(jogos: list[dict]) -> bool:
    hoje = datetime.now(FUSO_BRASIL).date()
    for jogo in jogos:
        data_realizacao = jogo.get("data_realizacao")
        if data_realizacao and datetime.fromisoformat(data_realizacao).date() == hoje:
            return True
    return False


def _status_partida(jogo: dict) -> str:
    transmissao = jogo.get("transmissao")
    if not transmissao:
        return "agendado"
    broadcast_id = transmissao["broadcast"]["id"]
    if broadcast_id in STATUS_POR_BROADCAST:
        return STATUS_POR_BROADCAST[broadcast_id]
    return "agendado"


def _nome_fase(dados_competicao: dict) -> str:
    """Nome da fase atual do mata-mata (ex: "Oitavas de final")."""
    for fase in dados_competicao.get("fases_navegacao") or []:
        if fase.get("atual"):
            return fase.get("nome") or ""
    return ""


def _extrair_jogos(dados_competicao: dict) -> tuple[list[dict], str]:
    """Retorna (jogos, subtítulo) para os dois formatos que a fonte usa.

    Pontos corridos (Brasileirão) entregam os jogos numa lista plana em
    "lista_jogos". Mata-mata (Copa do Brasil, Libertadores) deixa "lista_jogos"
    nulo e espalha os jogos em secao -> chave -> jogos, dois por chave (ida e volta).
    """
    jogos = dados_competicao.get("lista_jogos")
    if jogos:
        rodada = (dados_competicao.get("rodada") or {}).get("atual")
        return jogos, f"{rodada}ª rodada" if rodada else ""

    jogos = []
    for secao in dados_competicao.get("secao") or []:
        for chave in secao.get("chave") or []:
            for jogo in chave.get("jogos") or []:
                # a fonte marca explicitamente os jogos que não devem aparecer
                if jogo.get("exibir_jogo") is False:
                    continue
                jogos.append(jogo)
    return jogos, _nome_fase(dados_competicao)


def _placar_por_eventos(eventos: list[dict], sigla_casa: str, sigla_fora: str) -> tuple[int, int]:
    """Conta os gols na própria lista de eventos, já ajustada para gol contra."""
    gols_casa = sum(1 for e in eventos if e["tipo"] == "gol" and e["time"] == sigla_casa)
    gols_fora = sum(1 for e in eventos if e["tipo"] == "gol" and e["time"] == sigla_fora)
    return gols_casa, gols_fora


def _placar_combinado(
    eventos: list[dict], jogo: dict, sigla_casa: str, sigla_fora: str
) -> tuple[int, int]:
    """Junta as duas fontes de placar ficando com o maior de cada lado.

    Elas se atrasam em direções opostas e não dá para eleger uma vencedora:
    o resumo da rodada às vezes ainda não registrou o gol que o lance a lance já
    publicou, e o lance a lance às vezes ainda não publicou o gol que o resumo já
    contabilizou. O maior valor é sempre o mais atualizado dos dois.
    """
    gols_casa, gols_fora = _placar_por_eventos(eventos, sigla_casa, sigla_fora)

    oficial_casa = jogo.get("placar_oficial_mandante")
    oficial_fora = jogo.get("placar_oficial_visitante")

    if oficial_casa is not None:
        gols_casa = max(gols_casa, oficial_casa)
    if oficial_fora is not None:
        gols_fora = max(gols_fora, oficial_fora)

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
) -> tuple[list[dict], bool]:
    resp = await client.get(url_jogo, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    html = resp.text

    marcador = "plays: Array.from("
    idx = html.find(marcador)
    if idx == -1:
        return [], False
    inicio_array = html.find("[", idx)
    plays = json.loads(extrair_bloco_balanceado(html, inicio_array))

    # o lance mais recente do blog ao vivo é mais confiável que o status "oficial"
    # do resumo da rodada, que às vezes ainda diz "LIVE" com o jogo já encerrado
    partida_encerrada = bool(plays) and (plays[0].get("period") or {}).get("id") in PERIODOS_ENCERRADOS

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
        elif tipo_jogada == "CARD" and detalhes.get("kind") == "RED" and jogador:
            # sem jogador associado (ex: cartão pra comissão técnica), não há o que notificar
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

    return eventos, partida_encerrada


async def _montar_partidas(client: httpx.AsyncClient, jogos: list[dict]) -> list[dict]:
    tarefas_eventos = []
    tarefas_escudos = []
    for jogo in jogos:
        # no minuto em que a partida começa a fonte já marca "jogo_ja_comecou"
        # mas às vezes ainda não publicou a transmissão; sem essa guarda o
        # acesso direto estoura e derruba a competição inteira
        url_jogo = (jogo.get("transmissao") or {}).get("url")
        if jogo.get("jogo_ja_comecou") and url_jogo:
            sigla_casa = jogo["equipes"]["mandante"]["sigla"]
            sigla_fora = jogo["equipes"]["visitante"]["sigla"]
            tarefas_eventos.append(
                _buscar_eventos_partida(client, url_jogo, sigla_casa, sigla_fora)
            )
        else:
            tarefas_eventos.append(asyncio.sleep(0, result=([], False)))

        tarefas_escudos.append(_garantir_escudo_local(client, jogo["equipes"]["mandante"]))
        tarefas_escudos.append(_garantir_escudo_local(client, jogo["equipes"]["visitante"]))

    listas_eventos = await asyncio.gather(*tarefas_eventos, return_exceptions=True)
    escudos = await asyncio.gather(*tarefas_escudos, return_exceptions=True)

    partidas = []
    for i, (jogo, eventos_resultado) in enumerate(zip(jogos, listas_eventos)):
        eventos_ok = not isinstance(eventos_resultado, Exception)
        eventos, partida_encerrada = eventos_resultado if eventos_ok else ([], False)

        escudo_casa = escudos[i * 2] if not isinstance(escudos[i * 2], Exception) else None
        escudo_fora = escudos[i * 2 + 1] if not isinstance(escudos[i * 2 + 1], Exception) else None

        sigla_casa = jogo["equipes"]["mandante"]["sigla"]
        sigla_fora = jogo["equipes"]["visitante"]["sigla"]
        escudo_por_sigla = {sigla_casa: escudo_casa, sigla_fora: escudo_fora}

        for evento in eventos:
            evento["escudo_time"] = escudo_por_sigla.get(evento["time"])

        if jogo.get("jogo_ja_comecou") and eventos_ok:
            placar_casa, placar_fora = _placar_combinado(eventos, jogo, sigla_casa, sigla_fora)
            # o lance a lance é mais confiável que o status "oficial" do resumo da
            # rodada, que às vezes ainda diz "ao vivo" com o jogo já encerrado
            status = "encerrado" if partida_encerrada else "ao_vivo"
        else:
            # partida ainda não começou, ou não foi possível buscar o lance a lance:
            # usa o placar/status oficiais do resumo da rodada como melhor informação disponível
            placar_casa = jogo["placar_oficial_mandante"]
            placar_fora = jogo["placar_oficial_visitante"]
            status = _status_partida(jogo)

        inicio = _inicio_partida(jogo)

        partidas.append(
            {
                "time_casa": jogo["equipes"]["mandante"]["nome_popular"],
                "time_fora": jogo["equipes"]["visitante"]["nome_popular"],
                "escudo_casa": escudo_casa,
                "escudo_fora": escudo_fora,
                "placar_casa": placar_casa,
                "placar_fora": placar_fora,
                "eventos": eventos,
                "status": status,
                # o estado usa o início p/ decidir quando a partida entra e sai do board
                "inicio": inicio.isoformat() if inicio else None,
                "data_hora": _data_hora_formatada(
                    jogo.get("data_realizacao"), jogo.get("hora_realizacao")
                ),
            }
        )

    return _ordenar_partidas(partidas)


async def _fetch_competicao(client: httpx.AsyncClient, competicao: dict) -> dict:
    resp = await client.get(competicao["url"], headers=HEADERS, timeout=15)
    resp.raise_for_status()
    html = resp.text

    match = re.search(r"const classificacao = \{", html)
    if not match:
        raise ValueError(f"bloco 'classificacao' não encontrado em {competicao['url']}")
    dados_competicao = json.loads(extrair_bloco_balanceado(html, match.end() - 1))

    jogos, subtitulo = _extrair_jogos(dados_competicao)

    return {
        "id": competicao["id"],
        "nome": competicao["nome"],
        "subtitulo": subtitulo,
        "partidas": await _montar_partidas(client, jogos),
        "tem_partida_hoje": _tem_partida_hoje(jogos),
    }


async def fetch_dados() -> dict:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resultados = await asyncio.gather(
            *(_fetch_competicao(client, c) for c in COMPETICOES), return_exceptions=True
        )

    competicoes = []
    tem_partida_hoje = False
    for competicao, resultado in zip(COMPETICOES, resultados):
        # uma competição fora do ar não pode derrubar as demais
        if isinstance(resultado, Exception):
            # com o traceback dá pra achar a linha; só o tipo do erro não diz nada
            print(f"Erro ao buscar {competicao['nome']}:")
            traceback.print_exception(
                type(resultado), resultado, resultado.__traceback__
            )
            continue
        tem_partida_hoje = tem_partida_hoje or resultado.pop("tem_partida_hoje")
        competicoes.append(resultado)

    if not competicoes:
        raise RuntimeError("nenhuma competição pôde ser carregada")

    return {"competicoes": competicoes, "_tem_partida_hoje": tem_partida_hoje}


async def scrape_loop(state):
    while True:
        intervalo = INTERVALO_SEGUNDOS
        try:
            dados = await fetch_dados()
            state.update(dados)
            intervalo = INTERVALO_SEGUNDOS if state.tem_partida_hoje() else INTERVALO_SEM_JOGO_HOJE_SEGUNDOS
        except Exception as e:
            print(f"Erro ao buscar dados: {e}")
        await asyncio.sleep(intervalo)
