import re
import unicodedata


def normalizar_nome(nome: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", sem_acento.lower()).strip()


def mesmo_time(nome_a: str, nome_b: str) -> bool:
    """Compara nomes de time entre fontes que abreviam de formas diferentes
    (ex: "Vasco" vs "Vasco da Gama"): exige que ao menos metade das palavras
    do nome mais curto apareça no outro."""
    a, b = normalizar_nome(nome_a), normalizar_nome(nome_b)
    if a == b:
        return True
    palavras_a, palavras_b = set(a.split()), set(b.split())
    if not palavras_a or not palavras_b:
        return False
    intersecao = palavras_a & palavras_b
    return bool(intersecao) and len(intersecao) / min(len(palavras_a), len(palavras_b)) >= 0.5


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
