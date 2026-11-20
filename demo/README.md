# Demo: correr os cenários S1–S5

Os scripts só falam com o **simulador** e leem o backend; nunca escrevem na casa Altice real.
A IA (LLM) e a app são reais: nenhuma decisão é pré-gravada.

## 1. Terminais

Cada processo no seu terminal, e **todos ficam abertos** (o `run_scenario.py` precisa do
simulador e do backend a correr):

| Terminal | Comando |
|---|---|
| 1 · Simulador | `cd simulator && uv run python server.py` (porta 8100) |
| 2 · Backend | `cd backend && <variáveis do cenário> DB_PATH=demo.db uv run uvicorn app.main:app` (porta 8000) |
| 3 · Frontend | `cd frontend && npm run dev` → http://localhost:5173 |
| 4 · Cenário | `cd simulator && uv run python ../demo/run_scenario.py <sN>` |

- `DB_PATH=demo.db` mantém a demo separada da `home.db` do dia-a-dia. Apagar `backend/demo.db*`
  (com o backend parado) dá uma casa sem histórico nem aprendizagem.
- As variáveis do cenário (`NOW_OVERRIDE`, `EMPTY_AFTER_MINUTES`) só são lidas no arranque:
  **reinicia o backend** quando mudas para um cenário que precisa de outras.
- As divisões vêm do simulador (Sala, Cozinha, Corredor, Entrada) e são criadas sozinhas. O S4
  marca a Entrada como entrada da casa; noutros casos faz-se no **Gestor** ("É a entrada da casa").
- As linhas `POST /mcp 200 OK` no simulador são o poller do backend (a cada 2 s): é normal.

## 2. Opções do `run_scenario.py`

| Opção | Efeito |
|---|---|
| *(nenhuma)* | Repõe o estado inicial, toca os passos e **espera** pelo resultado → `== PASSOU` / `== FALHOU` |
| `--no-check` | Só toca os passos e termina logo. A casa reage **depois** (segundos a minutos): para apresentar |
| `--load-only` | Só carrega o estado inicial (antes de arrancar o backend, ver S3) |
| `--skip-load` | Não repõe o estado: só toca os passos |
| `--speed N` | Divide esperas e tempos limite por N |

Código de saída: 0 passou, 1 uma expectativa falhou, 2 o cenário não correu (ex.: simulador desligado).

> Cada cenário **repõe o simulador**: dispositivos simulados adicionados à mão desaparecem.

## 3. Onde ver

- **Casa**: planta com as divisões a acender/apagar e "O que acontece em casa" (ícones: pessoa / IA / reflexo, este com um raio).
- **Atividade**: cada decisão com a explicação, Anular / Confirmar / Rejeitar.
- **Aprendizagem**: crença atual, insights, reflexos e horários propostos (para aprovar).

## 4. Cenários

| # | Backend | O que acontece | Tempo de reação |
|---|---|---|---|
| S1 | `NOW_OVERRIDE=2026-11-19T21:00:00` | Ao serão liga-se a TV → a luz da sala baixa | ~5–15 s |
| S2 | — | Pessoa passa da cozinha para o corredor → acende o corredor, apaga a cozinha | ~5–15 s |
| S3 | `NOW_OVERRIDE=2026-11-20T07:20:00` + histórico | A máquina de café liga sozinha antes das 07:30 | até ~10 min |
| S4 | `EMPTY_AFTER_MINUTES=0.5` | Porta abre e fecha, ninguém se mexe → apaga tudo, sugere armar o alarme | ~30–45 s após fechar |
| S5 | — | Aparece uma fechadura (tipo desconhecido) → configurar no Gestor | ~5 s |

### S1 · TV ao serão
```
cd backend && NOW_OVERRIDE=2026-11-19T21:00:00 DB_PATH=demo.db uv run uvicorn app.main:app
cd simulator && uv run python ../demo/run_scenario.py s1
```

### S2 · A luz acompanha a pessoa (e aprende)
```
cd backend && DB_PATH=demo.db uv run uvicorn app.main:app
cd simulator && uv run python ../demo/run_scenario.py s2
```
Corre 2–3 vezes até o agente propor um reflexo; aprova-o em **Aprendizagem** e corre outra vez:
fica instantâneo e sem LLM (ícone de raio na Casa). Apagar a luz da cozinha depende da qualidade do modelo
rápido (`AGENT_MODEL`): o flash-lite às vezes não o faz.

### S3 · Rotina do café (a ordem importa)
O agente descobre a rotina na run de arranque, por isso o estado tem de estar carregado **antes**
de o backend arrancar (um reset depois desfazia a ação e contava como override do utilizador).
```
# backend parado
python demo/seed_history.py                                   # uma vez: gera demo/seed/history.jsonl
cd backend && uv run python -m app.storage.import_seed ../demo/seed/history.jsonl --align-to-today --db demo.db
cd simulator && uv run python ../demo/run_scenario.py s3 --load-only
cd backend && NOW_OVERRIDE=2026-11-20T07:20:00 DB_PATH=demo.db uv run uvicorn app.main:app
cd simulator && uv run python ../demo/run_scenario.py s3 --skip-load
```
O relógio começa às 07:20 e avança em tempo real: a máquina liga uns minutos antes das 07:30.
O horário diário proposto confirma-se em **Aprendizagem**.

### S4 · Casa vazia
```
cd backend && EMPTY_AFTER_MINUTES=0.5 DB_PATH=demo.db uv run uvicorn app.main:app
cd simulator && uv run python ../demo/run_scenario.py s4
``` 
A porta tem de acabar **fechada** e não pode haver movimento durante 30 s. Armar o alarme é
sempre só sugestão (e só existe no gateway real).

**Ao vivo com o sensor de porta real**: põe o sensor de contacto real na **Entrada** (Gestor),
acende luzes simuladas, abre e fecha a porta real e espera ~30 s sem passar à frente da câmara.

### S5 · Dispositivo desconhecido
```
cd backend && DB_PATH=demo.db uv run uvicorn app.main:app
cd simulator && uv run python ../demo/run_scenario.py s5
```
No **Gestor**, a fechadura aparece por configurar: **Básico** (aceitar a proposta do agente) ou
**Avançado** (editar). Fica em `suggest` até ser confirmada.

## 5. Problemas comuns

| Sintoma | Causa |
|---|---|
| `ConnectError: All connection attempts failed` | O simulador não está a correr (terminal 1) |
| "Não aconteceu nada" com `--no-check` | O guião termina antes da reação: espera, ou corre sem `--no-check` |
| S3 parado | Backend sem `NOW_OVERRIDE`, histórico não importado para `demo.db`, ou backend arrancado antes do `--load-only` |
| S4 não apaga | Backend sem `EMPTY_AFTER_MINUTES=0.5`, porta ficou aberta, ou houve movimento |
| Agente lento / sem resposta | Quota do modelo esgotada (429 no log do backend): cada mudança de estado gasta um pedido |
| Dispositivo real esbatido "Offline" | Não comunica com a Altice há mais de 1 h |
