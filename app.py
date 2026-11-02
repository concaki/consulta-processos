"""Consulta de processos e dados públicos — servidor web.

Rode com:  python -m uvicorn app:app --reload   e abra http://localhost:8000
"""
import asyncio
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse

BASE = Path(__file__).parent


def carregar_env():
    """Lê o arquivo .env (CHAVE=valor) da pasta do programa, sem dependências extras."""
    arq = BASE / ".env"
    if arq.exists():
        for linha in arq.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, v = linha.split("=", 1)
                if v.strip():
                    os.environ.setdefault(k.strip(), v.strip().strip('"'))


carregar_env()

import fontes  # noqa: E402
import fontes_extra as fx  # noqa: E402
from identificadores import detectar_tipo, formatar_cnj, so_digitos  # noqa: E402

app = FastAPI(title="Consulta de Processos")


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/status")
def status():
    return {"transparencia": bool(os.getenv("TRANSPARENCIA_API_KEY")),
            "escavador": bool(os.getenv("ESCAVADOR_TOKEN"))}


def achatar(lista):
    out = []
    for x in lista:
        if x is None:
            continue
        out.extend(x if isinstance(x, list) else [x])
    return out


@app.get("/api/buscar")
async def buscar(q: str = Query(..., min_length=3, max_length=150)):
    q = q.strip()
    tipo = detectar_tipo(q)
    resp = {"consulta": q, "tipo": tipo, "resultados": [], "avisos": []}
    limites = httpx.Limits(max_connections=20)

    async with httpx.AsyncClient(follow_redirects=True, limits=limites) as client:
        if tipo == "cnj":
            num = formatar_cnj(q)
            resp["consulta"] = num
            resp["resultados"] = achatar(await asyncio.gather(
                fontes.datajud_por_numero(client, q),
                fontes.djen(client, numero=q),
                fx.querido_diario(client, [num]),
            ))
            resp["resultados"].append(fx.links_manuais("cnj", num))
            resp["avisos"].append("O DataJud não informa as partes; nomes e advogados vêm das publicações do DJEN.")

        elif tipo == "cnpj":
            receita = await fontes.cnpj_receita(client, q)
            if not receita.get("ok"):
                receita = await fx.cnpj_fallback(client, q) or receita
            resp["resultados"].append(receita)
            emp = receita.get("empresa") or {}
            razao = emp.get("razao_social")

            tarefas = [
                fx.querido_diario(client, [fx.fmt_cnpj(q), razao]),
                fx.transparencia_sancoes(client, q),
                fx.transparencia_contratos(client, q),
                fx.escavador(client, doc=q),
            ]
            if razao:
                tarefas.insert(0, fontes.djen(client, nome_parte=razao))
            resp["resultados"] += achatar(await asyncio.gather(*tarefas))
            resp["resultados"].append(fx.links_manuais("cnpj", q, razao))
            if razao:
                resp["avisos"].append(
                    f"Processos do DJEN localizados pelo nome '{razao}': pode haver homônimos e "
                    "processos sem publicação não aparecem."
                )

        elif tipo == "cpf":
            resp["resultados"] = achatar(await asyncio.gather(
                fx.transparencia_sancoes(client, q),
                fx.escavador(client, doc=q),
            ))
            resp["resultados"].append(fx.links_manuais("cpf", q))
            if not os.getenv("ESCAVADOR_TOKEN"):
                resp["avisos"].append(
                    "Processos por CPF só com API paga (configure ESCAVADOR_TOKEN no .env) "
                    "ou pesquisando pelo nome completo da pessoa."
                )

        elif tipo == "nome":
            resp["resultados"] = achatar(await asyncio.gather(
                fontes.djen(client, nome_parte=q),
                fx.querido_diario(client, [q]),
                fx.escavador(client, nome=q),
            ))
            resp["resultados"].append(fx.links_manuais("nome", q))
            resp["avisos"].append("Busca por nome pode trazer homônimos — confira número do processo e tribunal.")

        else:
            resp["avisos"].append("Não reconheci a entrada. Use um número CNJ, CNPJ, CPF válido ou um nome.")

    return resp
