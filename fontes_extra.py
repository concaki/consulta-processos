"""Fontes adicionais: Querido Diário, Portal da Transparência (CGU), fallback de CNPJ,
Escavador (opcional, pago) e links para consultas manuais.

Cada função devolve o formato genérico:
  {"fonte": str, "ok": bool, "total": int, "itens": [{"titulo", "sub", "campos": {}, "texto", "link"}], "erro"?}
"""
import os
from urllib.parse import quote_plus

import httpx

from fontes import HEADERS, _erro
from identificadores import so_digitos


def g(obj, *caminhos, padrao=None):
    """Primeiro valor não vazio entre vários caminhos 'a.b.c'."""
    for c in caminhos:
        v = obj
        for parte in c.split("."):
            v = v.get(parte) if isinstance(v, dict) else None
        if v not in (None, "", [], {}):
            return v
    return padrao


def limpar(campos: dict) -> dict:
    return {k: v for k, v in campos.items() if v not in (None, "", [], {})}


def fmt_cnpj(c: str) -> str:
    d = so_digitos(c)
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def fmt_cpf(c: str) -> str:
    d = so_digitos(c)
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


def moeda(v):
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- CNPJ com fallback
async def cnpj_fallback(client: httpx.AsyncClient, cnpj: str) -> dict | None:
    """Se a BrasilAPI falhar, tenta Minha Receita e depois CNPJ.ws (mesmos dados abertos da Receita)."""
    d = so_digitos(cnpj)
    for nome, url in (("Minha Receita", f"https://minhareceita.org/{d}"),
                      ("CNPJ.ws", f"https://publica.cnpj.ws/cnpj/{d}")):
        try:
            r = await client.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            e = r.json()
            est = e.get("estabelecimento") or {}
            return {
                "fonte": f"{nome} (Receita Federal)", "ok": True,
                "empresa": {
                    "cnpj": d,
                    "razao_social": g(e, "razao_social"),
                    "nome_fantasia": g(e, "nome_fantasia") or est.get("nome_fantasia"),
                    "situacao": g(e, "descricao_situacao_cadastral") or est.get("situacao_cadastral"),
                    "abertura": g(e, "data_inicio_atividade") or est.get("data_inicio_atividade"),
                    "natureza_juridica": g(e, "natureza_juridica", "natureza_juridica.descricao"),
                    "porte": g(e, "porte", "porte.descricao"),
                    "capital_social": g(e, "capital_social"),
                    "cnae_principal": g(e, "cnae_fiscal_descricao", "estabelecimento.atividade_principal.descricao"),
                    "municipio_uf": f"{g(e, 'municipio', 'estabelecimento.cidade.nome')}/{g(e, 'uf', 'estabelecimento.estado.sigla')}",
                    "socios": [{"nome": s.get("nome_socio") or s.get("nome"),
                                "qualificacao": s.get("qualificacao_socio") or g(s, "qualificacao_socio.descricao")}
                               for s in (e.get("qsa") or e.get("socios") or [])],
                },
            }
        except Exception:
            continue
    return None


# ---------------------------------------------------------------- Querido Diário
# O domínio antigo (api.queridodiario.ok.org.br) foi desativado; o atual é api.queridodiario.org.br.
# Pode ser trocado pela variável QD_API_URL no .env se mudar de novo.
QD_URL = os.getenv("QD_API_URL", "https://api.queridodiario.org.br").rstrip("/") + "/gazettes"


async def querido_diario(client: httpx.AsyncClient, termos: list[str], tamanho: int = 20) -> dict:
    """Busca de texto em diários oficiais municipais. Junta resultados de vários termos."""
    vistos, itens, total, erros = set(), [], 0, []
    for termo in termos:
        if not termo:
            continue
        try:
            r = await client.get(QD_URL, headers=HEADERS, timeout=30, params={
                "querystring": f'"{termo}"', "excerpt_size": 400,
                "number_of_excerpts": 2, "size": tamanho,
            })
            r.raise_for_status()
            j = r.json()
            total += j.get("total_gazettes") or 0
            for gz in j.get("gazettes") or []:
                chave = gz.get("url") or (gz.get("territory_id"), gz.get("date"), gz.get("edition"))
                if chave in vistos:
                    continue
                vistos.add(chave)
                itens.append({
                    "titulo": f"{gz.get('territory_name')}/{gz.get('state_code')}",
                    "sub": f"Diário de {gz.get('date')}" + (f" · edição {gz['edition']}" if gz.get("edition") else ""),
                    "campos": {"Termo buscado": termo},
                    "texto": " … ".join(e.strip() for e in (gz.get("excerpts") or [])),
                    "link": gz.get("url"),
                    "data": gz.get("date"),
                })
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (502, 503, 504, 520, 522):
                return {"fonte": "Querido Diário (diários municipais)", "ok": False,
                        "erro": f"A API do Querido Diário está fora do ar (HTTP {e.response.status_code}). "
                                "Problema do lado deles; tente mais tarde."}
            erros.append(f"{termo}: HTTP {e.response.status_code}")
        except Exception as e:
            erros.append(f"{termo}: {_erro('Querido Diário', e)['erro']}")
            break  # erro de conexão/SSL vale para todos os termos
    if erros and not itens:
        return {"fonte": "Querido Diário (diários municipais)", "ok": False, "erro": "; ".join(erros)}
    itens.sort(key=lambda i: i.get("data") or "", reverse=True)
    return {"fonte": "Querido Diário (diários municipais)", "ok": True, "total": total, "itens": itens,
            "nota": "Menções em diários oficiais de municípios cobertos pelo projeto."}


# ---------------------------------------------------------------- Portal da Transparência (CGU)
PT_URL = "https://api.portaldatransparencia.gov.br/api-de-dados"
PT_CADASTRO = "https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email"


async def _pt_get(client, rota: str, params: dict, paginas: int = 3) -> list:
    chave = os.getenv("TRANSPARENCIA_API_KEY")
    itens = []
    for p in range(1, paginas + 1):
        r = await client.get(f"{PT_URL}/{rota}", params={**params, "pagina": p},
                             headers={**HEADERS, "chave-api-dados": chave, "Accept": "application/json"},
                             timeout=30)
        r.raise_for_status()
        lote = r.json() or []
        if not isinstance(lote, list):
            lote = [lote]
        itens.extend(lote)
        if len(lote) < 15:  # a API pagina de 15 em 15
            break
    return itens


def _sem_chave(fonte):
    return {"fonte": fonte, "ok": False,
            "erro": f"Falta a chave gratuita do Portal da Transparência. Cadastre-se em {PT_CADASTRO} "
                    "e coloque em TRANSPARENCIA_API_KEY no arquivo .env"}


def _filtrar_doc(itens, doc):
    """Garante que o registro é mesmo do documento buscado (filtro errado devolve a base toda)."""
    d = so_digitos(doc)
    saida = []
    for i in itens:
        doc_item = so_digitos(str(g(i, "sancionado.codigoFormatado", "pessoaJuridica.cnpjFormatado",
                                    "pessoa.cnpjFormatado", "pessoa.cpfFormatado", padrao="")))
        # CPFs vêm mascarados (***.123.456-**): compara só os dígitos visíveis do meio
        if not doc_item or d == doc_item or (len(d) == 11 and len(doc_item) == 6 and doc_item == d[3:9]):
            saida.append(i)
    return saida


async def transparencia_sancoes(client: httpx.AsyncClient, doc: str) -> list[dict]:
    """CEIS, CNEP (CPF ou CNPJ), CEPIM e Acordos de Leniência (só CNPJ)."""
    if not os.getenv("TRANSPARENCIA_API_KEY"):
        return [_sem_chave("Portal da Transparência — sanções (CEIS, CNEP, CEPIM, Leniência)")]
    d = so_digitos(doc)
    eh_cnpj = len(d) == 14
    tarefas = [("CEIS — Empresas/pessoas inidôneas e suspensas", "ceis", {"codigoSancionado": d}),
               ("CNEP — Empresas punidas (Lei Anticorrupção)", "cnep", {"codigoSancionado": d})]
    if eh_cnpj:
        tarefas += [("CEPIM — Entidades sem fins lucrativos impedidas", "cepim", {"cnpjSancionado": d}),
                    ("Acordos de Leniência (CGU)", "acordos-leniencia", {"cnpjSancionado": d})]
    saida = []
    for nome, rota, params in tarefas:
        fonte = f"Portal da Transparência · {nome}"
        try:
            brutos = await _pt_get(client, rota, params)
            if rota in ("ceis", "cnep", "cepim"):
                brutos = _filtrar_doc(brutos, d)
            saida.append({"fonte": fonte, "ok": True, "total": len(brutos),
                          "itens": [_norm_sancao(rota, i) for i in brutos],
                          "vazio": "Nada consta neste cadastro."})
        except Exception as e:
            saida.append(_erro(fonte, e))
    return saida


def _norm_sancao(rota, i):
    if rota == "acordos-leniencia":
        return {
            "titulo": g(i, "situacaoAcordo", padrao="Acordo de leniência"),
            "sub": g(i, "orgaoResponsavel"),
            "campos": limpar({
                "Início": g(i, "dataInicioAcordo"), "Fim": g(i, "dataFimAcordo"),
                "Empresas": ", ".join(filter(None, (g(s, "nomeInformadoOrgaoResponsavel", "razaoSocial")
                                                   for s in (i.get("sancoes") or [])))),
            }),
        }
    if rota == "cepim":
        return {
            "titulo": g(i, "motivo", padrao="Impedimento CEPIM"),
            "sub": g(i, "orgaoSuperior.nome"),
            "campos": limpar({"Entidade": g(i, "pessoaJuridica.nome"),
                              "Convênio": g(i, "convenio.numero"), "Objeto": g(i, "convenio.objeto"),
                              "Referência": g(i, "dataReferencia")}),
        }
    return {
        "titulo": g(i, "tipoSancao.descricaoResumida", "tipoSancao.descricaoPortal", padrao="Sanção"),
        "sub": g(i, "orgaoSancionador.nome"),
        "campos": limpar({
            "Sancionado": g(i, "sancionado.nome", "pessoa.nome"),
            "Início": g(i, "dataInicioSancao"), "Fim": g(i, "dataFimSancao"),
            "Processo": g(i, "numeroProcesso"),
            "Multa": moeda(g(i, "valorMulta")) if g(i, "valorMulta") else None,
            "Fundamentação": "; ".join(filter(None, (f.get("descricao") for f in (i.get("fundamentacao") or []))))
            if isinstance(i.get("fundamentacao"), list) else None,
            "Fonte": g(i, "fonteSancao.nomeExibicao"),
        }),
    }


async def transparencia_contratos(client: httpx.AsyncClient, cnpj: str) -> dict:
    fonte = "Portal da Transparência · Contratos com o Governo Federal"
    if not os.getenv("TRANSPARENCIA_API_KEY"):
        return _sem_chave(fonte)
    try:
        brutos = await _pt_get(client, "contratos/cpf-cnpj", {"cpfCnpj": so_digitos(cnpj)}, paginas=2)
        itens = [{
            "titulo": g(i, "objeto", padrao="Contrato")[:160],
            "sub": g(i, "unidadeGestora.orgaoVinculado.nome", "unidadeGestora.nome", "orgao.nome"),
            "campos": limpar({
                "Número": g(i, "numero"), "Assinatura": g(i, "dataAssinatura"),
                "Vigência": f"{g(i, 'dataInicioVigencia', padrao='?')} a {g(i, 'dataFimVigencia', padrao='?')}",
                "Valor inicial": moeda(g(i, "valorInicialCompra")),
                "Valor final": moeda(g(i, "valorFinalCompra")),
                "Modalidade": g(i, "modalidadeCompra"), "Situação": g(i, "situacaoContrato"),
            }),
            "data": g(i, "dataAssinatura"),
        } for i in brutos]
        return {"fonte": fonte, "ok": True, "total": len(itens), "itens": itens,
                "vazio": "Nenhum contrato federal encontrado."}
    except Exception as e:
        return _erro(fonte, e)


# ---------------------------------------------------------------- Escavador (opcional, pago)
ESC_URL = "https://api.escavador.com/api/v2/envolvido/processos"


async def escavador(client: httpx.AsyncClient, *, doc: str | None = None, nome: str | None = None) -> dict | None:
    """Só roda se ESCAVADOR_TOKEN estiver no .env. Cada consulta consome créditos."""
    token = os.getenv("ESCAVADOR_TOKEN")
    if not token:
        return None
    fonte = "Escavador (API paga)"
    params = {"cpf_cnpj": so_digitos(doc)} if doc else {"nome": nome}
    try:
        r = await client.get(ESC_URL, params=params, timeout=60, headers={
            **HEADERS, "Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json"})
        r.raise_for_status()
        j = r.json()
        env = j.get("envolvido_encontrado") or {}
        itens = [{
            "titulo": i.get("numero_cnj"),
            "sub": " × ".join(filter(None, [i.get("titulo_polo_ativo"), i.get("titulo_polo_passivo")])),
            "campos": limpar({
                "Início": i.get("ano_inicio") or i.get("data_inicio"),
                "Última movimentação": i.get("data_ultima_movimentacao"),
                "Tribunais": ", ".join(filter(None, (f.get("sigla") or f.get("nome") for f in (i.get("fontes") or [])))),
                "Valor da causa": moeda(g(i, "fontes.0.capa.valor_causa.valor")),
                "Classe": g(i, "fontes.0.capa.classe"),
            }),
            "processo": i.get("numero_cnj"),
        } for i in (j.get("items") or [])]
        return {"fonte": fonte, "ok": True, "itens": itens,
                "total": env.get("quantidade_processos") or len(itens),
                "nota": f"Envolvido: {env.get('nome')} ({env.get('tipo_pessoa')})" if env else None}
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 402:
            return {"fonte": fonte, "ok": False, "erro": "Sem saldo de créditos no Escavador"}
        return _erro(fonte, e)
    except Exception as e:
        return _erro(fonte, e)


# ---------------------------------------------------------------- Links para consulta manual
def links_manuais(tipo: str, valor: str, nome: str | None = None) -> dict:
    """Consultas oficiais que exigem CAPTCHA ou login: o programa não automatiza, só abre o caminho."""
    d = so_digitos(valor)
    q = quote_plus(nome or valor)
    L = []
    if tipo == "cnpj":
        L += [
            ("TCU — Consulta Consolidada de PJ (TCU, CNJ improbidade, CEIS, CNEP)", "https://certidoes-apf.apps.tcu.gov.br/"),
            ("Certidão de débitos federais (Receita/PGFN)", "https://servicos.receitafederal.gov.br/servico/certidoes/#/home/cnpj"),
            ("CNDT — Certidão de débitos trabalhistas (TST)", "https://cndt-certidao.tst.jus.br/inicio.faces"),
            ("Certificado de regularidade do FGTS (Caixa)", "https://consulta-crf.caixa.gov.br/consultacrf/pages/consultaEmpregador.jsf"),
        ]
    if tipo == "cpf":
        L += [
            ("Certidão de débitos federais (Receita/PGFN)", "https://servicos.receitafederal.gov.br/servico/certidoes/#/home/cpf"),
            ("CNDT — Certidão de débitos trabalhistas (TST)", "https://cndt-certidao.tst.jus.br/inicio.faces"),
            ("BNMP — Mandados de prisão (CNJ)", "https://portalbnmp.cnj.jus.br/"),
        ]
    if tipo in ("cnpj", "cpf", "nome"):
        L += [
            ("CNIA — Condenações por improbidade (CNJ)", "https://www.cnj.jus.br/improbidade_adm/consultar_requerido.php"),
            ("Busca no Escavador", f"https://www.escavador.com/busca?q={q}"),
            ("Busca no Jusbrasil", f"https://www.jusbrasil.com.br/busca?q={q}"),
            ("Publicações no DJEN (comunica.pje.jus.br)", "https://comunica.pje.jus.br/"),
        ]
    if tipo == "cnj":
        L += [
            ("Publicações no DJEN", "https://comunica.pje.jus.br/"),
            ("Busca no Jusbrasil", f"https://www.jusbrasil.com.br/busca?q={quote_plus(valor)}"),
            ("Busca no Escavador", f"https://www.escavador.com/busca?q={quote_plus(valor)}"),
        ]
    doc_txt = fmt_cnpj(d) if tipo == "cnpj" else fmt_cpf(d) if tipo == "cpf" else valor
    return {"fonte": "Consultas manuais (exigem CAPTCHA ou login)", "ok": True, "links": [
        {"titulo": t, "url": u} for t, u in L], "nota": f"Tenha em mãos: {doc_txt}"}
