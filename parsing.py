def extrair_bloco_balanceado(texto: str, indice_abertura: int) -> str:
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
