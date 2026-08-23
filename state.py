from copy import deepcopy
from datetime import datetime, timedelta, timezone

from odds import COMPETICOES_BETANO, encontrar_odds

# competições com odds coletadas — hoje é o que a Betano cobre. Times em comum entre essas
# competições na mesma semana (ex: Palmeiras jogando Brasileirão e Libertadores) só recebem
# a cotação certa porque encontrar_odds recebe o id da competição e cada odd carrega o seu
COMPETICOES_COM_ODDS = set(COMPETICOES_BETANO)

FUSO_BRASIL = timezone(timedelta(hours=-3))

# a partida só entra no board quando falta no máximo isso para começar
ANTECEDENCIA_MAXIMA = timedelta(days=1)

# e sai do board esse tanto de tempo depois de terminar
PERMANENCIA_APOS_FIM = timedelta(hours=1)

# num restart perdemos o registro de quando cada partida terminou, e "agora" viraria
# o fim de qualquer jogo antigo que ainda apareça na fonte; esse corte evita
# ressuscitar partidas de dias atrás
IDADE_MAXIMA_APOS_INICIO = timedelta(hours=6)

# por quanto tempo seguramos os últimos dados bons de uma competição que falhou.
# Sem isso, um erro momentâneo numa competição some com ela do board até a próxima
# coleta bem-sucedida -- e se ela for a única com jogos, o board fica vazio.
TOLERANCIA_COMPETICAO_FORA = timedelta(minutes=10)


class MatchState:
    """Guarda o snapshot mais recente das partidas em memória."""

    def __init__(self):
        self._current = {}
        self._odds_por_casa = []  # lista de listas: uma por casa de apostas, atualizada num ciclo separado
        self._tem_partida_hoje = True  # otimista até o primeiro fetch: evita ficar "preguiçoso" de largada
        self._encerrado_em = {}  # chave da partida -> quando a vimos encerrada pela 1ª vez
        self._ultima_competicao = {}  # id -> (dados, quando vieram)
        self._ordem_competicoes = []  # ids na ordem em que a fonte entrega
        self._atualizado_em = None  # quando a última coleta bem-sucedida terminou
        # (competicao_id, time_casa, time_fora) -> {casa_de_apostas: odds}, última odd
        # boa vista de cada partida em cartaz -- ver _aplicar_odds
        self._ultima_odds_partida = {}

    def get_current(self):
        if not self._current:
            return {}
        # expõe a idade do snapshot: sem isso, um loop de coleta parado é
        # indistinguível de um jogo sem lance novo, visto de fora
        return {**self._current, "atualizado_em": self._atualizado_em.isoformat()}

    def tem_partida_hoje(self) -> bool:
        return self._tem_partida_hoje

    def update(self, new_data: dict) -> bool:
        """
        Atualiza o estado se houver mudança.
        Retorna True se algo mudou (novo gol, cartão, etc), False caso contrário.
        """
        self._tem_partida_hoje = new_data.pop("_tem_partida_hoje", True)
        self._completar_competicoes_ausentes(new_data)
        self._filtrar_visiveis(new_data)
        self._aplicar_odds(new_data)
        self._atualizado_em = datetime.now(FUSO_BRASIL)
        changed = new_data != self._current
        if changed:
            self._current = new_data
        return changed

    def update_odds(self, odds_por_casa: list[list[dict]]):
        self._odds_por_casa = odds_por_casa
        self._aplicar_odds(self._current)

    def _completar_competicoes_ausentes(self, dados: dict):
        """Repõe as competições que falharam na coleta com os últimos dados bons.

        O scraper omite a competição que deu erro, e como o snapshot é substituído
        inteiro, uma falha de um ciclo apagaria o que já estava no ar. Guardamos o
        último resultado por um tempo curto: o suficiente para atravessar uma falha
        passageira, e curto o bastante para não exibir placar velho indefinidamente.
        """
        agora = datetime.now(FUSO_BRASIL)
        recebidas = {c["id"]: c for c in dados.get("competicoes", [])}

        for id_competicao, competicao in recebidas.items():
            if id_competicao not in self._ordem_competicoes:
                self._ordem_competicoes.append(id_competicao)
            # cópia porque o filtro de visibilidade altera "partidas" no lugar;
            # sem isso o guardado encolheria a cada ciclo em que fosse reposto
            self._ultima_competicao[id_competicao] = (deepcopy(competicao), agora)

        completas = []
        for id_competicao in self._ordem_competicoes:
            if id_competicao in recebidas:
                completas.append(recebidas[id_competicao])
                continue
            guardada = self._ultima_competicao.get(id_competicao)
            if guardada and agora - guardada[1] <= TOLERANCIA_COMPETICAO_FORA:
                completas.append(deepcopy(guardada[0]))

        dados["competicoes"] = completas

    def _filtrar_visiveis(self, dados: dict):
        agora = datetime.now(FUSO_BRASIL)
        encerrado_em = {}  # remontado a cada ciclo p/ não crescer indefinidamente
        diagnostico = []

        for competicao in dados.get("competicoes", []):
            visiveis = []
            for partida in competicao.get("partidas", []):
                if self._deve_exibir(competicao, partida, agora, encerrado_em):
                    visiveis.append(partida)
            diagnostico.append(
                f"{competicao['id']}[{competicao.get('subtitulo')!r}]"
                f" {len(visiveis)}/{len(competicao.get('partidas', []))}"
            )
            competicao["partidas"] = visiveis

        self._encerrado_em = encerrado_em

        # board totalmente vazio é o sintoma que o usuário enxerga como "nenhuma
        # partida encontrada"; registrar o que chegou vs. o que passou no filtro
        # é o que permite distinguir falha de coleta de exclusão pela janela
        if not any(c["partidas"] for c in dados.get("competicoes", [])):
            print(f"Board vazio em {agora.isoformat()} | recebido: {' '.join(diagnostico)}")

    def _deve_exibir(self, competicao: dict, partida: dict, agora, encerrado_em: dict) -> bool:
        inicio = partida.get("inicio")
        inicio = datetime.fromisoformat(inicio) if inicio else None

        if partida["status"] == "ao_vivo":
            # "ao vivo" preso (transmissão que nunca virou ENCERRADA, ou lance a
            # lance fora do ar) é dado velho, não uma partida real acontecendo
            # agora; sem esse teto o jogo nunca sai do board
            if inicio is not None and agora - inicio > IDADE_MAXIMA_APOS_INICIO:
                return False
            return True

        if partida["status"] == "agendado":
            # sem data confirmada, a partida ainda está longe de acontecer
            if inicio is None:
                return False
            return inicio - agora <= ANTECEDENCIA_MAXIMA

        # encerrada: fica no board por mais um tempo depois do apito final
        if inicio is not None and agora - inicio > IDADE_MAXIMA_APOS_INICIO:
            return False

        chave = (competicao["id"], partida["time_casa"], partida["time_fora"], partida.get("inicio"))
        fim = self._encerrado_em.get(chave, agora)
        encerrado_em[chave] = fim
        return agora - fim < PERMANENCIA_APOS_FIM

    def _aplicar_odds(self, dados: dict):
        """Casa as odds de cada casa de apostas com a partida.

        Ao vivo, as fontes costumam tirar a partida da própria listagem de
        pré-jogo (Betnacional) ou trocar o mercado que fica na posição 0
        (Betano) assim que a bola rola -- sem aviso, e sem que a odd em si
        tenha deixado de valer. Sem guardar a última odd boa por partida, ela
        sumiria do board no primeiro ciclo de coleta depois do apito inicial,
        exatamente quando o jogo (agora ao vivo) mais precisa aparecer.
        """
        chaves_vistas = set()
        for competicao in dados.get("competicoes", []):
            if competicao.get("id") not in COMPETICOES_COM_ODDS:
                continue
            for partida in competicao.get("partidas", []):
                chave = (competicao["id"], partida["time_casa"], partida["time_fora"])
                chaves_vistas.add(chave)
                cache_partida = self._ultima_odds_partida.setdefault(chave, {})
                for lista_odds in self._odds_por_casa:
                    encontrado = encontrar_odds(
                        competicao["id"], partida["time_casa"], partida["time_fora"], lista_odds
                    )
                    if encontrado:
                        cache_partida[encontrado["casa_de_apostas"]] = encontrado
                partida["odds"] = list(cache_partida.values())

        # descarta o histórico de partidas que já saíram do board -- senão a
        # memória cresce um pouco a cada rodada nova, pra sempre
        self._ultima_odds_partida = {
            chave: valor for chave, valor in self._ultima_odds_partida.items() if chave in chaves_vistas
        }
