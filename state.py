from odds import encontrar_odds


class MatchState:
    """Guarda o snapshot mais recente das partidas em memória."""

    def __init__(self):
        self._current = {}
        self._odds_por_casa = []  # lista de listas: uma por casa de apostas, atualizada num ciclo separado

    def get_current(self):
        return self._current

    def update(self, new_data: dict) -> bool:
        """
        Atualiza o estado se houver mudança.
        Retorna True se algo mudou (novo gol, cartão, etc), False caso contrário.
        """
        self._aplicar_odds(new_data)
        changed = new_data != self._current
        if changed:
            self._current = new_data
        return changed

    def update_odds(self, odds_por_casa: list[list[dict]]):
        self._odds_por_casa = odds_por_casa
        self._aplicar_odds(self._current)

    def _aplicar_odds(self, dados: dict):
        for partida in dados.get("partidas", []):
            odds_partida = []
            for lista_odds in self._odds_por_casa:
                encontrado = encontrar_odds(partida["time_casa"], partida["time_fora"], lista_odds)
                if encontrado:
                    odds_partida.append(encontrado)
            partida["odds"] = odds_partida
