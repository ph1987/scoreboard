from datetime import datetime, timedelta, timezone

from odds import encontrar_odds

# as odds que coletamos são do Brasileirão; um mesmo confronto pode acontecer
# também numa copa, e sem esse recorte a cotação vazaria para o jogo errado
COMPETICAO_COM_ODDS = "brasileirao"

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

    def get_current(self):
        return self._current

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
            self._ultima_competicao[id_competicao] = (competicao, agora)

        completas = []
        for id_competicao in self._ordem_competicoes:
            if id_competicao in recebidas:
                completas.append(recebidas[id_competicao])
                continue
            guardada = self._ultima_competicao.get(id_competicao)
            if guardada and agora - guardada[1] <= TOLERANCIA_COMPETICAO_FORA:
                completas.append(guardada[0])

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
        for competicao in dados.get("competicoes", []):
            if competicao.get("id") != COMPETICAO_COM_ODDS:
                continue
            for partida in competicao.get("partidas", []):
                odds_partida = []
                for lista_odds in self._odds_por_casa:
                    encontrado = encontrar_odds(
                        partida["time_casa"], partida["time_fora"], lista_odds
                    )
                    if encontrado:
                        odds_partida.append(encontrado)
                partida["odds"] = odds_partida
