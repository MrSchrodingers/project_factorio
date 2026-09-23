# Mapas distintos para a validação de robustez

Este documento separa em três blocos: (1) o que foi **medido** nesta máquina,
(2) o que já está **implementado e testado** no repositório, (3) a **receita**
para gerar N mundos distintos, que ainda não foi executada porque derruba o
container do Factorio.

---

## 1. Medido: a seed nunca chega ao gerador de mapa

### 1.1 O cenário do laboratório não gera mapa nenhum

`default_lab_scenario` embarca um mapa pronto. O arquivo `blueprint.zip` do
cenário contém `blueprint/blueprint.dat` com 3.652.547 bytes, e é esse arquivo
que o servidor carrega como `level.dat` a cada boot:

```
$ unzip -l .venv-fle/.../fle/cluster/scenarios/default_lab_scenario/blueprint.zip
  3652547  blueprint/blueprint.dat
        5  blueprint/info.json

$ docker logs fle-local-factorio_0-1 | grep -n 'Factorio 2.0.73\|level.dat\|Map version\|CreatingGame)'
1:        2026-09-21 17:24:11; Factorio 2.0.73 (build 84377, linux64, headless)
31:  0.702 Loading level.dat: 3652547 bytes.
32:  0.702 Info Scenario.cpp:154: Map version 1.1.110-0
45:  1.282 Info ...: updateTick(360199) changing state from(CreatingGame) to(InGame)
529165:   2026-09-21 20:45:32; Factorio 2.0.73 (build 84377, linux64, headless)
529195:  0.382 Loading level.dat: 3652547 bytes.
529196:  0.382 Info Scenario.cpp:154: Map version 1.1.110-0
529209:  0.677 Info ...: updateTick(360199) changing state from(CreatingGame) to(InGame)
```

Dois boots, o mesmo `level.dat`, o mesmo tick inicial (360199) e uma versão de
mapa (1.1.110) anterior ao binário em uso. Consequência direta: **recriar o
container não muda o terreno**, e `--map-gen-settings` / `--map-gen-seed` são
inertes enquanto o cenário for `default_lab_scenario`.

### 1.2 O mundo vivo não corresponde ao arquivo de map-gen

Leitura por RCON (somente leitura) do mundo que está rodando:

```
$ .venv-fle/bin/python -c "..."   # /silent-command rcon.print(... map_gen_settings ...)
{"seed":2859378883,"surface":"nauvis","tick":6734761,"width":1000,"height":1000}
```

`fle/cluster/config/map-gen-settings.json` declara `"seed": null`, `"width": 0`
e `"height": 0` (infinito). O mundo vivo tem seed 2859378883 e é fechado em
1000x1000 — ou seja, veio do mapa embarcado, não daquele arquivo.

### 1.3 A seed do experimento morre no `reset`

- `fle/env/gym_env/environment.py:534-541` — `def reset(self, options=None, seed=None)`
  com a docstring `seed: Not used`.
- `src/factorio_ai_lab/integrations/fle.py:179-188` — repassa `seed` para esse
  `reset`, que limpa entidades e não toca em terreno.
- `fle/cluster/run_envs.py:117-118` — `--map-gen-seed` só é adicionado ao
  comando quando `scenario == "open_world"`, e com o valor fixo de classe
  `map_gen_seed = 44340` (linha 63), igual para todas as instâncias.

### 1.4 Assinatura do terreno vivo

```
$ PYTHONPATH=src .venv-fle/bin/python scripts/world_signature.py --port 27000
{
  "world_signature": "2e946aaac861f5e85460",
  "map_seed": 2859378883, "width": 1000, "height": 1000,
  "resources": {
    "coal":       {"cells": 4, "min_x":  15.5, "max_x":  38.5, "min_y": -3.5, "max_y": 20.5},
    "copper-ore": {"cells": 2, "min_x": -70.5, "max_x": -46.5, "min_y": 70.5, "max_y": 95.5},
    "iron-ore":   {"cells": 2, "min_x":  15.5, "max_x":  38.5, "min_y": 70.5, "max_y": 95.5},
    "stone":      {"cells": 4, "min_x": -70.5, "max_x": -46.5, "min_y": -3.5, "max_y": 20.5},
    "crude-oil":  {"cells": 4, "min_x": -69.5, "max_x":  38.5, "min_y": 40.5, "max_y": 49.5}
  }
}
```

Os centros conferem com os patches relatados nas oito gerações: ferro
(27, 83), cobre (-58.5, 83), carvão (27, 8.5). O instrumento acha o caso
positivo conhecido.

---

## 2. Implementado e testado (sem tocar no container)

### 2.1 O gate deixou de contar seeds

`src/factorio_ai_lab/learning/robustness.py` passa a qualificar por **mundos
distintos verificados**, não por seeds:

- `record(..., world_signature=...)` — argumento novo e opcional; a chamada
  atual de `open_play_runner.py:5560`, que não passa assinatura, continua
  válida e registra a passagem como **não verificada**.
- `distinct_pass_worlds` / `distinct_pass_world_count` — mundos provados.
- `seed_world_collisions` — seeds diferentes que caíram no mesmo mundo.
- `world_status` — `no_pass`, `unverified`, `partially_verified`,
  `in_progress`, `collapsed`, `verified`.
- `blocking_reason` — texto explicando por que a qualificação não avança.
- `distinct_pass_seed_count` — chave legada lida pelo dashboard
  (`static/app.js:1582`) e pelo runner (`open_play_runner.py:5633`) como
  numerador do progresso; agora carrega o número de mundos distintos. O fato
  bruto das seeds continua em `distinct_pass_seeds` e `pass_seed_count`.
- `pending_for(...)` retorna `False` quando há `blocking_reason`: repetir a
  corrida num terreno já provado idêntico não compra evidência, só congela o
  incumbente em `evolution_loop.py:891-905`.

Regras de bloqueio (só com evidência conclusiva):

| Situação | `world_status` | Bloqueia? |
|---|---|---|
| 2+ seeds na mesma assinatura | `collapsed` | sim |
| passagens >= `required_passes` e alguma sem assinatura | `unverified` | sim |
| menos passagens que o exigido, sem assinatura | `unverified` | não |
| mundos distintos já observados, ainda insuficientes | `in_progress` | não |

### 2.2 Assinatura de terreno

`src/factorio_ai_lab/learning/map_suite.py`:

- `world_signature_command(radius, cell_size)` — comando RCON **somente
  leitura** (`find_entities_filtered{type="resource"}` + `map_gen_settings`).
- `world_signature(payload)` — sha256 de `map_seed` + `width`/`height` +
  pegada dos patches em células de 32 tiles. A contagem de minério fica **fora**
  do hash: o agente minera enquanto joga, e uma identidade que muda durante a
  corrida não serviria para comparar corridas.
- `world_fingerprint(payload)` — assinatura mais a evidência legível.
- `scripts/world_signature.py` — transporte RCON, `--out`, `--expect`,
  `--quiet`. Não toma o lease do mundo; pode rodar com experimento ativo.

### 2.3 Avaliação sobre conjunto fixo de mapas

Ainda em `map_suite.py`, pronto para ser ligado quando existirem N mundos:

- `MapSuite` — conjunto imutável e ordenado; **recusa** dois mapas com a mesma
  assinatura de mundo ou mapa sem assinatura.
- `aggregate_fitness(suite, scores)` — `mean`, `worst`, `best`, `worst_map`,
  `missing_maps`, `complete`, `world_evidence`.
- `FixedMapSetEvaluator` — roda o genoma em todos os mapas do conjunto e
  levanta `WorldMismatchError` se o mundo observado não for o do `MapSpec`.
- `rank_genomes(results)` — ordena por pior caso primeiro, média depois, e
  recusa comparar conjuntos diferentes ou cobertura parcial.

Testes: `tests/test_map_suite.py`, `tests/test_robustness_worlds.py`,
`tests/test_cluster_map_seeds.py`.

### 2.4 Compose com uma seed por instância

`scripts/fle_cluster_local.py` ganhou `--map-gen-seeds`, que reescreve o
comando de cada serviço com um `--map-gen-seed` próprio, exige seeds distintas
e **recusa** o cenário de mapa embarcado.

---

## 3. Receita para os N mundos (não executada)

Pré-condição: nenhum experimento rodando. O compose atual não tem volume de
saves, então o mundo vive só na memória do container — `docker compose up -d`
com configuração nova **destrói o mundo atual** (hoje com tick ~6,9M).

### Passo 1 — parar o que está em curso

```bash
cd /srv/factorio-ai-lab
cat runs/factorio_world_lease.json      # confirmar que não há dono ativo
pkill -f factorio_ai_lab.experiments    # ou esperar o loop terminar
```

### Passo 2 — subir N mundos gerados, um por seed

```bash
cd /srv/factorio-ai-lab
PYTHONPATH=src .venv-fle/bin/python scripts/fle_cluster_local.py stop
PYTHONPATH=src .venv-fle/bin/python scripts/fle_cluster_local.py start \
  -n 3 -s open_world --map-gen-seeds 20260931 20260932 20260933
```

Portas resultantes (`fle/cluster/run_envs.py:15-16,236-238`):
`factorio_0` → RCON 27000 / jogo 34197, `factorio_1` → 27001 / 34198,
`factorio_2` → 27002 / 34199.

### Passo 3 — validar que os terrenos são de fato diferentes

```bash
mkdir -p runs/world_suite
for port in 27000 27001 27002; do
  PYTHONPATH=src .venv-fle/bin/python scripts/world_signature.py \
    --port "$port" --label "map_$port" --out "runs/world_suite/map_$port.json" --quiet
done | sort | uniq -c
```

Três linhas com contagem 1 = três mundos. Qualquer contagem > 1 significa que
a seed **não** chegou ao gerador: não prossiga, o conjunto seria um mundo com
três nomes. Comparação direta dos patches:

```bash
jq -r '.world_signature, (.resources | to_entries[] | "\(.key) \(.value.min_x) \(.value.min_y)")' \
  runs/world_suite/map_*.json
```

### Passo 4 — registrar o conjunto e ligar a avaliação

```python
from factorio_ai_lab.learning.map_suite import MapSuite

suite = MapSuite.from_dicts(
    "open-play-3",
    [
        {"map_id": "map_27000", "world_signature": "...", "seed": 20260931,
         "endpoint": "127.0.0.1:27000"},
        {"map_id": "map_27001", "world_signature": "...", "seed": 20260932,
         "endpoint": "127.0.0.1:27001"},
        {"map_id": "map_27002", "world_signature": "...", "seed": 20260933,
         "endpoint": "127.0.0.1:27002"},
    ],
)
```

`MapSuite` rejeita o conjunto se duas assinaturas forem iguais — a validação
do passo 3 fica embutida no próprio mecanismo.

### Passo 5 — alimentar o gate com a assinatura (dono: `open_play_runner.py`)

Arquivo fora do escopo desta entrega. A ligação é de três linhas, logo após o
`reset` e **antes** de o agente minerar:

```python
from factorio_ai_lab.learning.map_suite import world_fingerprint, world_signature_command

payload = json.loads(instance.rcon_client.send_command(world_signature_command()))
world = world_fingerprint(payload)["world_signature"]
...
OpenPlayRobustnessGate(...).record(..., world_signature=world)
```

Enquanto isso não existir, o gate registra passagens não verificadas e, a
partir da terceira, publica `blocking_reason = world_signature_missing`.

---

## 4. Custo e o que quebra

| Item | Valor | Origem |
|---|---|---|
| CPU/RAM por instância | 1 vCPU, 1024 MB | `deploy.resources.limits` no compose |
| Host | 16 cores, 15846 MB | `System info` no log do Factorio |
| 3 mundos | 3 vCPU, ~3 GB | derivado dos dois anteriores |
| Boot até `InGame` (mapa embarcado) | 0,68 s e 1,28 s | dois boots no `docker logs` |
| Boot até `InGame` (mapa gerado) | **não medido** | exige subir container |
| Mundo atual | perdido na recriação | não há volume de saves no compose |

Rupturas esperadas ao trocar `default_lab_scenario` por `open_world`:

1. O `open_world/control.lua` tem uma linha (`util = require("util")`); não há
   `freeplay.lua` nem `paperclips.lua`. O ferramental do agente é injetado pelo
   FLE por RCON, mas o setup de cenário deixa de existir.
2. O terreno passa a ser infinito e gerado: água, cliffs e `peaceful_mode:
   false` com `enemy-base` ativo em `map-gen-settings.json`. Ajustar isso exige
   trocar o config compartilhado, que hoje é um bind mount de
   `.venv-fle/.../fle/cluster/config` (fora do versionamento) — o caminho limpo
   é copiar o diretório para dentro do repositório e remontar.
3. Toda posição fixa que o campeão decorou (ferro em (27, 83) etc.) deixa de
   valer. Queda de fitness na primeira geração é o resultado esperado, não uma
   regressão.
4. **Não verificado nesta máquina**: que `--map-gen-seed` combinado com
   `--start-server-load-scenario open_world` produza terrenos distintos. É o
   caminho que o próprio FLE usa para esse cenário, mas a prova é o passo 3.

## 5. Alternativa: saves pré-gerados

Mantém o mundo entre reinícios e dispensa confiar no `--map-gen-seed` em
tempo de boot:

```bash
docker run --rm -v "$PWD/.fle-local/saves:/opt/factorio/saves" \
  factoriotools/factorio:2.0.73 \
  /opt/factorio/bin/x64/factorio --create /opt/factorio/saves/world_a.zip \
  --map-gen-settings /opt/factorio/config/map-gen-settings.json \
  --map-gen-seed 20260931
```

Depois, `ComposeGenerator(save_file=...)` troca o comando para
`--start-server <save>` e adiciona o volume de saves
(`run_envs.py:100-104,189-194`). Custo extra: o compose local precisa passar a
montar `.fle-local/saves`, hoje ausente. Tempo de geração por save: **não
medido**.
