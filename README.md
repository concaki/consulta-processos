# Consulta de Processos e Dados Públicos

| Entrada | Fontes consultadas |
|---|---|
| **Número CNJ** | DataJud (capa e movimentações) · DJEN (publicações, partes, advogados) · Querido Diário |
| **CNPJ** | Receita (BrasilAPI → Minha Receita → CNPJ.ws) · DJEN pela razão social · Querido Diário · Portal da Transparência (CEIS, CNEP, CEPIM, Acordos de Leniência, contratos federais) · Escavador* |
| **CPF** | Portal da Transparência (CEIS, CNEP) · Escavador* |
| **Nome** | DJEN · Querido Diário · Escavador* |

\* Escavador só roda se você configurar o token (API paga).

Todas as buscas também mostram **links prontos** para consultas oficiais que exigem CAPTCHA
(TCU consolidada, certidões da Receita/PGFN, CNDT, FGTS, CNIA, BNMP). O programa não contorna CAPTCHA.

## Como rodar (Windows/PowerShell)

```
python -m pip install -r requirements.txt
python -m uvicorn app:app --reload
```
Abra http://localhost:8000 (precisa de internet com IP brasileiro: o DJEN bloqueia IPs do exterior).

## Chaves (opcional, mas recomendado)

Copie `.env.exemplo` para `.env` e preencha:

- `TRANSPARENCIA_API_KEY` — **gratuita**, cadastro por e-mail em
  https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email
- `ESCAVADOR_TOKEN` — paga, cada consulta gasta créditos.

Sem as chaves, essas fontes aparecem com aviso e o resto funciona.
Para conferir o que foi carregado: http://localhost:8000/api/status

## Limitações

- DataJud é lento (até ~1 min) e não traz nomes das partes.
- Busca por nome/razão social traz homônimos; confira sempre o número do processo.
- Processos em segredo de justiça não aparecem.
- APIs públicas mudam sem aviso. Se uma fonte falhar, o card dela mostra o erro e as demais continuam.

Use os dados com finalidade legítima e de acordo com a LGPD.
