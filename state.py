class MatchState:
    """Guarda o snapshot mais recente das partidas em memória."""

    def __init__(self):
        self._current = {}  # TODO: definir formato final (rodada + lista de partidas)

    def get_current(self):
        return self._current

    def update(self, new_data: dict) -> bool:
        """
        Atualiza o estado se houver mudança.
        Retorna True se algo mudou (novo gol, cartão, etc), False caso contrário.
        """
        changed = new_data != self._current  # TODO: comparação mais fina por partida/evento
        if changed:
            self._current = new_data
        return changed
