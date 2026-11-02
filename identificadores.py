"""Detecção e validação de CNJ, CPF e CNPJ, e mapeamento CNJ -> tribunal (alias DataJud)."""
import re

UFS = ["ac", "al", "ap", "am", "ba", "ce", "df", "es", "go", "ma", "mt", "ms", "mg",
       "pa", "pb", "pr", "pe", "pi", "rj", "rn", "rs", "ro", "rr", "sc", "se", "sp", "to"]


def so_digitos(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def cnj_valido(num: str) -> bool:
    d = so_digitos(num)
    if len(d) != 20:
        return False
    n, dv, ano, j, tr, orig = d[0:7], d[7:9], d[9:13], d[13], d[14:16], d[16:20]
    calc = 98 - (int(n + ano + j + tr + orig + "00") % 97)
    return int(dv) == calc


def formatar_cnj(num: str) -> str:
    d = so_digitos(num)
    return f"{d[0:7]}-{d[7:9]}.{d[9:13]}.{d[13]}.{d[14:16]}.{d[16:20]}"


def cpf_valido(cpf: str) -> bool:
    d = so_digitos(cpf)
    if len(d) != 11 or d == d[0] * 11:
        return False
    for i in (9, 10):
        soma = sum(int(d[k]) * (i + 1 - k) for k in range(i))
        if (soma * 10 % 11) % 10 != int(d[i]):
            return False
    return True


def cnpj_valido(cnpj: str) -> bool:
    d = so_digitos(cnpj)
    if len(d) != 14 or d == d[0] * 14:
        return False
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6] + pesos1
    for pesos, pos in ((pesos1, 12), (pesos2, 13)):
        r = sum(int(d[k]) * pesos[k] for k in range(pos)) % 11
        if (0 if r < 2 else 11 - r) != int(d[pos]):
            return False
    return True


def detectar_tipo(q: str) -> str:
    d = so_digitos(q)
    if len(d) == 20 and cnj_valido(d):
        return "cnj"
    if len(d) == 14 and cnpj_valido(d):
        return "cnpj"
    if len(d) == 11 and cpf_valido(d):
        return "cpf"
    if re.search(r"[A-Za-zÀ-ÿ]{3,}", q or ""):
        return "nome"
    return "invalido"


def tribunal_do_cnj(num: str) -> str | None:
    """Retorna o alias DataJud (ex.: 'tjsp', 'trf3', 'trt2', 'tre-mg', 'stj')."""
    d = so_digitos(num)
    j, tr = d[13], int(d[14:16])
    if j == "8" and 1 <= tr <= 27:
        return "tjdft" if UFS[tr - 1] == "df" else f"tj{UFS[tr - 1]}"
    if j == "4" and 1 <= tr <= 6:
        return f"trf{tr}"
    if j == "5":
        return "tst" if tr == 0 else (f"trt{tr}" if 1 <= tr <= 24 else None)
    if j == "6":
        return "tse" if tr == 0 else (f"tre-{UFS[tr - 1]}" if 1 <= tr <= 27 else None)
    if j == "3":
        return "stj"
    if j == "7":
        return "stm"
    if j == "9":
        return {13: "tjmmg", 21: "tjmrs", 26: "tjmsp"}.get(tr)
    return None  # STF (J=1) e CNJ (J=2) não estão no DataJud
