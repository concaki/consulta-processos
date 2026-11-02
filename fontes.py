"""Clientes das fontes públicas: DataJud (CNJ), DJEN (CNJ) e BrasilAPI (Receita Federal)."""
import os
import re
import httpx

from identificadores import so_digitos, tribunal_do_cnj

# Chave pública do DataJud. O CNJ pode trocá-la: veja https://datajud-wiki.cnj.jus.br/api-publica/acesso
DATAJUD_KEY = os.getenv(
    "DATAJUD_API_KEY",
    "cDZHYzlZa0JadVREZDJCendQbXY6SkJlTzNjLV9TRENyQk1RdnFKZGRQdw==",
)
DATAJUD_URL = "https://api-publica.datajud.cnj.jus.br/api_publica_{alias}/_search"
DJEN_URL = "https://comunicaapi.pje.jus.br/api/v1/comunicacao"
BRASILAPI_CNPJ = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"

HEADERS = {"User-Agent": "consulta-processos/0.1 (uso pessoal)"}


def _erro(fonte: str, e: Exception) -> dict:
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        dica = " (o DJEN bloqueia IPs fora do Brasil)" if fonte == "DJEN" and code == 403 else ""
        return {"fonte": fonte, "ok": False, "erro": f"HTTP {code}{dica}"}
    if isinstance(e, httpx.TimeoutException):
        return {"fonte": fonte, "ok": False, "erro": "Tempo esgotado (a fonte está lenta, tente de novo)"}
    return {"fonte": fonte, "ok": False, "erro": str(e) or e.__class__.__name__}


# ---------------------------------------------------------------- DataJud
def link_consulta_publica(cnj: str, alias: str | None) -> str | None:
    """Página de consulta pública do processo no tribunal (a lista completa de documentos fica lá,
    atrás de CAPTCHA). Hoje só montamos o endereço direto para a Justiça do Trabalho (PJe-JT)."""
    from identificadores import formatar_cnj
    if alias and alias.startswith("trt"):
        return f"https://pje.{alias}.jus.br/consultaprocessual/detalhe-processo/{formatar_cnj(cnj)}"
    if alias == "tst":
        return f"https://pje.tst.jus.br/consultaprocessual/detalhe-processo/{formatar_cnj(cnj)}"
    return None
async def datajud_por_numero(client: httpx.AsyncClient, cnj: str) -> dict:
    alias = tribunal_do_cnj(cnj)
    if not alias:
        return {"fonte": "DataJud", "ok": False, "erro": "Tribunal não coberto pelo DataJud (ex.: STF)"}
    try:
        r = await client.post(
            DATAJUD_URL.format(alias=alias),
            headers={**HEADERS, "Authorization": f"APIKey {DATAJUD_KEY}"},
            json={"query": {"match": {"numeroProcesso": so_digitos(cnj)}}, "size": 10},
            timeout=70,
        )
        r.raise_for_status()
        hits = r.json().get("hits", {}).get("hits", [])
        return {"fonte": "DataJud", "ok": True, "tribunal": alias.upper(),
                "consulta_publica": link_consulta_publica(cnj, alias),
                "registros": [_normalizar_datajud(h.get("_source", {})) for h in hits]}
    except Exception as e:
        return _erro("DataJud", e)


def _normalizar_datajud(s: dict) -> dict:
    movs = sorted(s.get("movimentos") or [], key=lambda m: m.get("dataHora") or "", reverse=True)
    return {
        "numero": s.get("numeroProcesso"),
        "tribunal": s.get("tribunal"),
        "grau": s.get("grau"),
        "classe": (s.get("classe") or {}).get("nome"),
        "assuntos": [a.get("nome") for a in (s.get("assuntos") or []) if isinstance(a, dict)],
        "orgao_julgador": (s.get("orgaoJulgador") or {}).get("nome"),
        "sistema": (s.get("sistema") or {}).get("nome"),
        "formato": (s.get("formato") or {}).get("nome"),
        "data_ajuizamento": s.get("dataAjuizamento"),
        "ultima_atualizacao": s.get("dataHoraUltimaAtualizacao"),
        "sigilo": s.get("nivelSigilo"),
        "movimentos": [_normalizar_mov(m) for m in movs],
    }


RE_DOC = re.compile(r"documento|peti[cç][aã]o|peca|pe[cç]a|anexo", re.I)
RE_JUNTADA = re.compile(r"juntada|juntad[oa]|peti[cç][aã]o inicial|protocolad|recebid[oa] a peti", re.I)


def _normalizar_mov(m: dict) -> dict:
    comps = [c for c in (m.get("complementosTabelados") or []) if isinstance(c, dict)]
    nome = m.get("nome") or ""
    # Tipo de documento: complemento cuja descrição fala em documento/petição (ex.: tipo_de_documento -> Procuração)
    docs = [c.get("nome") for c in comps if c.get("nome") and RE_DOC.search(str(c.get("descricao") or ""))]
    if not docs and RE_JUNTADA.search(nome):
        docs = [c.get("nome") for c in comps if c.get("nome")] or [nome]
    return {
        "data": m.get("dataHora"),
        "nome": nome,
        "complementos": [c.get("nome") or c.get("descricao") for c in comps],
        "documentos": docs,
    }


# ---------------------------------------------------------------- DJEN
async def djen(client: httpx.AsyncClient, *, numero: str | None = None,
               nome_parte: str | None = None, paginas: int = 3) -> dict:
    """Publicações do Diário de Justiça Eletrônico Nacional por número do processo ou nome da parte."""
    params = {"itensPorPagina": 100}
    if numero:
        params["numeroProcesso"] = so_digitos(numero)
    if nome_parte:
        params["nomeParte"] = nome_parte
    itens = []
    try:
        for pagina in range(1, paginas + 1):
            r = await client.get(DJEN_URL, params={**params, "pagina": pagina},
                                 headers=HEADERS, timeout=40)
            r.raise_for_status()
            lote = r.json().get("items") or []
            itens.extend(lote)
            if len(lote) < params["itensPorPagina"]:
                break
        return {"fonte": "DJEN", "ok": True, "publicacoes": [_normalizar_djen(i) for i in itens]}
    except Exception as e:
        if itens:  # devolve o que já veio
            return {"fonte": "DJEN", "ok": True, "parcial": True,
                    "publicacoes": [_normalizar_djen(i) for i in itens]}
        return _erro("DJEN", e)


def _texto_limpo(html: str | None) -> str:
    t = html or ""
    # preserva links: <a href="URL">texto</a> -> "texto (URL)"
    t = re.sub(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
               lambda m: m.group(2) if m.group(1) in m.group(2) else f"{m.group(2)} ({m.group(1)})",
               t, flags=re.I | re.S)
    t = re.sub(r"<\s*(br|/p|/div|/li)\s*/?>", "\n", t, flags=re.I)  # quebras de linha
    t = re.sub(r"<[^>]+>", " ", t)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    linhas = (re.sub(r"[ \t]+", " ", l).strip() for l in t.splitlines())
    return "\n".join(l for l in linhas if l)


def _normalizar_djen(i: dict) -> dict:
    dest = i.get("destinatarios") or []
    advs = i.get("destinatarioadvogados") or []
    return {
        "data": i.get("data_disponibilizacao") or i.get("datadisponibilizacao"),
        "tribunal": i.get("siglaTribunal"),
        "processo": i.get("numeroprocessocommascara") or i.get("numero_processo"),
        "tipo": i.get("tipoComunicacao"),
        "documento": i.get("tipoDocumento"),
        "orgao": i.get("nomeOrgao"),
        "classe": i.get("nomeClasse"),
        "partes": [{"nome": d.get("nome"), "polo": d.get("polo")} for d in dest if isinstance(d, dict)],
        "advogados": [
            {"nome": (a.get("advogado") or {}).get("nome"),
             "oab": f"{(a.get('advogado') or {}).get('numero_oab', '')}/{(a.get('advogado') or {}).get('uf_oab', '')}"}
            for a in advs if isinstance(a, dict)
        ],
        "texto": _texto_limpo(i.get("texto")),
        "link": i.get("link"),
    }


# ---------------------------------------------------------------- BrasilAPI
async def cnpj_receita(client: httpx.AsyncClient, cnpj: str) -> dict:
    try:
        r = await client.get(BRASILAPI_CNPJ.format(cnpj=so_digitos(cnpj)), headers=HEADERS, timeout=20)
        r.raise_for_status()
        e = r.json()
        return {
            "fonte": "BrasilAPI (Receita Federal)", "ok": True,
            "empresa": {
                "cnpj": e.get("cnpj"),
                "razao_social": e.get("razao_social"),
                "nome_fantasia": e.get("nome_fantasia"),
                "situacao": e.get("descricao_situacao_cadastral"),
                "abertura": e.get("data_inicio_atividade"),
                "natureza_juridica": e.get("natureza_juridica"),
                "porte": e.get("porte"),
                "capital_social": e.get("capital_social"),
                "cnae_principal": f"{e.get('cnae_fiscal')} - {e.get('cnae_fiscal_descricao')}",
                "municipio_uf": f"{e.get('municipio')}/{e.get('uf')}",
                "socios": [{"nome": s.get("nome_socio"), "qualificacao": s.get("qualificacao_socio")}
                           for s in (e.get("qsa") or [])],
            },
        }
    except Exception as ex:
        return _erro("BrasilAPI (Receita Federal)", ex)
