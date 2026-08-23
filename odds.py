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
# sem partida hoje, as odds também podem esperar (mesmo sinal usado pelo scraper do placar)
ODDS_INTERVALO_SEM_JOGO_HOJE_SEGUNDOS = 3 * 60 * 60

COMPETICOES_BETANO = {
    "brasileirao": "https://www.betano.bet.br/sport/futebol/brasil/brasileirao-serie-a-betano/10016/",
    "copa-do-brasil": "https://www.betano.bet.br/sport/futebol/brasil/copa-betano-do-brasil/10008/",
    "libertadores": "https://www.betano.bet.br/sport/futebol/copa-libertadores/copa-libertadores/436g/",
    "sul-americana": "https://www.betano.bet.br/sport/futebol/copa-sul-americana/copa-sul-americana/435g/",
}

# a Betnacional migrou Libertadores e Sul-Americana (e não tem Copa do Brasil em destaque)
# pra um formato que busca os jogos via API depois do carregamento — sem HTML pra extrair,
# diferente do resto. Por ora ela continua cobrindo só o Brasileirão mesmo.
URL_BETNACIONAL = "https://betnacional.bet.br/apostas-brasileirao-serie-a"
NOME_TORNEIO_BETNACIONAL = "Brasileirão Série A"

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


async def _fetch_odds_betano(competicao_id: str, url: str) -> list[dict]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(url, headers=HEADERS)
        resp.raise_for_status()
        html = resp.text

    # a primeira chave de cada evento no JSON varia por competição -- o
    # Brasileirão vem com "sportId" primeiro, as demais com "stats"; sem
    # aceitar as duas formas, o Brasileirão caía direto no "não encontrado"
    # abaixo, silenciosamente, em todo ciclo
    marcadores_possiveis = ('"events":[{"stats"', '"events":[{"sportId"')
    marcador = next((m for m in marcadores_possiveis if m in html), None)
    if marcador is None:
        # sem log aqui, esse retorno vazio é indistinguível de "sem jogos hoje"
        # de fora -- foi assim que o Brasileirão ficou sem odds da Betano sem
        # nenhum sinal no log, em todo ciclo, ao vivo ou não
        print(
            f"Betano ({competicao_id}): marcador de eventos não encontrado "
            f"(status {resp.status_code}, url final {resp.url}, {len(html)} bytes)"
        )
        return []
    idx = html.find(marcador)
    inicio_array = html.rfind("[", 0, idx + len(marcador))
    eventos = json.loads(extrair_bloco_balanceado(html, inicio_array))

    odds = []
    descartados = []
    for evento in eventos:
        participantes = evento.get("participants") or []
        markets = evento.get("markets") or []
        if len(participantes) != 2 or not markets:
            continue

        selecoes = markets[0].get("selections") or []
        precos = {s["name"]: s["price"] for s in selecoes}
        if not {"1", "X", "2"} <= precos.keys():
            # suspeita: jogo ao vivo troca o mercado que fica na posição 0
            # (deixa de ser o 1X2), ou o evento nem devia ter chegado até aqui
            nome_par = f"{participantes[0].get('name')} x {participantes[1].get('name')}"
            descartados.append(f"{nome_par} (mercado[0]={markets[0].get('name')!r})")
            continue

        odds.append(
            {
                "competicao_id": competicao_id,
                "time_casa": participantes[0]["name"],
                "time_fora": participantes[1]["name"],
                "casa": precos["1"],
                "empate": precos["X"],
                "fora": precos["2"],
                "casa_de_apostas": "Betano",
            }
        )

    # contagem a cada ciclo: se o total cair na hora de um jogo em cartaz, é sinal
    # de que a fonte tira o evento da listagem de pré-jogo assim que ele começa
    # (em vez de só trocar de mercado, que o log de descarte acima já cobre)
    print(f"Betano ({competicao_id}): {len(eventos)} evento(s) na listagem, {len(odds)} com 1X2 válido")

    if descartados:
        print(
            f"Betano ({competicao_id}): {len(descartados)} evento(s) sem mercado 1X2 na posição 0: "
            + "; ".join(descartados)
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
        print(
            f"Betnacional: marcador __NEXT_DATA__ não encontrado "
            f"(status {resp.status_code}, url final {resp.url}, {len(html)} bytes)"
        )
        return []
    marcador_json = 'type="application/json">'
    inicio = html.find(marcador_json, idx)
    if inicio == -1:
        print("Betnacional: __NEXT_DATA__ encontrado mas sem bloco JSON em seguida")
        return []
    inicio += len(marcador_json)
    dados = json.loads(extrair_bloco_balanceado(html, inicio))

    cache = dados["props"]["pageProps"]["initialState"]["cache"]
    eventos = cache["events"]["entities"]
    outcomes = cache["outcomes"]["entities"]

    # confirmado com dado real de produção: ao vivo, o evento troca de "prematch"
    # pra "live", mas o outcome já vem com a odd corrente no mesmo payload -- não
    # tem nada a esperar de um canal separado, só aceitar os dois tipos
    TIPOS_VALIDOS = {"prematch", "live"}

    odds = []
    descartados_tipo = 0
    for evento_id, evento in eventos.items():
        if evento.get("type") not in TIPOS_VALIDOS:
            descartados_tipo += 1
            continue
        if evento.get("tournament", {}).get("name") != NOME_TORNEIO_BETNACIONAL:
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
                "competicao_id": "brasileirao",
                "time_casa": home["name"],
                "time_fora": away["name"],
                "casa": precos[home["name"]],
                "empate": precos["Empate"],
                "fora": precos[away["name"]],
                "casa_de_apostas": "Betnacional",
            }
        )

    print(
        f"Betnacional: {len(eventos)} evento(s) na listagem, {len(odds)} com 1X2 válido "
        f"do Brasileirão, {descartados_tipo} descartado(s) por tipo (nem 'prematch' nem 'live')"
    )

    return odds


async def fetch_odds() -> list[list[dict]]:
    """Busca as odds de cada fonte em paralelo. Retorna uma lista por fonte,
    pra que uma fonte fora do ar não derrube as demais."""
    nomes = [f"Betano ({competicao_id})" for competicao_id in COMPETICOES_BETANO]
    tarefas = [
        _fetch_odds_betano(competicao_id, url) for competicao_id, url in COMPETICOES_BETANO.items()
    ]
    nomes.append("Betnacional")
    tarefas.append(_fetch_odds_betnacional())

    resultados = await asyncio.gather(*tarefas, return_exceptions=True)

    listas = []
    for nome, resultado in zip(nomes, resultados):
        if isinstance(resultado, Exception):
            print(f"Erro ao buscar odds da {nome}: {type(resultado).__name__}: {resultado}")
            listas.append([])
        else:
            listas.append(resultado)
    return listas


def encontrar_odds(
    competicao_id: str, time_casa: str, time_fora: str, lista_odds: list[dict]
) -> dict | None:
    for item in lista_odds:
        # times que jogam a mesma competição do adversário nesta rodada podem se repetir
        # noutra copa na mesma semana — sem esse filtro a odd vazaria pro jogo errado
        if item["competicao_id"] != competicao_id:
            continue
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
        intervalo = ODDS_INTERVALO_SEGUNDOS
        try:
            listas_odds = await fetch_odds()
            state.update_odds(listas_odds)
            intervalo = ODDS_INTERVALO_SEGUNDOS if state.tem_partida_hoje() else ODDS_INTERVALO_SEM_JOGO_HOJE_SEGUNDOS
        except Exception as e:
            print(f"Erro ao buscar odds: {e}")
        await asyncio.sleep(intervalo)
