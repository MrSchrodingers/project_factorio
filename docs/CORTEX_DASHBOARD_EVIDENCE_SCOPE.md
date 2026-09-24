# F1-B — Dashboard Evidence Scope e Verdade do Mundo

**Status:** PASS — dashboard corrigido, versionado e deployado separadamente do runtime científico.

## 1. Incidente observado

Durante a primeira baseline corrigida, o dashboard apresentava simultaneamente mapa físico com 117 entidades, geração global G97, champion global G37 e memória global, embora a seed F1-B já tivesse sido executada em um STATE_ROOT isolado.

Essa combinação era epistemicamente inválida. O dashboard lia o mundo via RCON ao vivo, mas research state, evolution history, champion, knowledge e generation reports pelo RUNS_DIR global.

## 2. Auditoria do mapa

Leitura direta de /api/world e RCON após a seed 20261001 confirmou:

- 117 entidades;
- 49 transport-belts;
- 19 pipes;
- 12 wooden-chests;
- 12 burner-inserters;
- 8 medium-electric-poles;
- 6 burner-mining-drills;
- 4 assembling-machine-2;
- 3 stone-furnaces;
- 1 offshore-pump;
- 1 boiler;
- 1 steam-engine.

Receitas observadas: iron-gear-wheel, automation-science-pack, copper-cable, electronic-circuit e iron-plate. Não existe assembler com receita logistic-science-pack no mundo final da seed.

Portanto, o mapa não estava exibindo uma fábrica antiga em cache. Ele estava exibindo o mundo físico real deixado pela seed 20261001. A aparência mecanicista é um resultado científico: a baseline herdada ainda constrói células com burner miners, chest buffers e integração parcial.

## 3. Ciência verde

A seed 20261001 terminou em partial_success, bottleneck Logistic science.

Medidas:

- logistic_science_output = 0;
- logistic_science_rate_per_s = 0;
- producer_count = 6;
- producers_reaching_buffer = 5;
- producers_reaching_processor = 2;
- physical_processing_coverage = 33.3%.

A ausência de ciência verde no mapa é consistente com a evidência experimental. A UI deve mostrar explicitamente que WORLD vem de live RCON e EVIDENCE vem do sandbox da seed.

## 4. Causa do mix de arenas

O mundo é lido por FactorioObserver.snapshot() via RCON. Antes deste hardening, active_run_data(), research_data(), evolution_data(), knowledge_data(), survival e diagnostics usavam artefatos do RUNS_DIR global. Como F1-B executa cada seed com STATE_ROOT isolado, esses artefatos pertenciam ao antigo loop evolutivo e não à seed que tinha acabado de alterar o mundo.

## 5. Contrato de Evidence Scope

Foi introduzido src/factorio_ai_lab/dashboard/context.py.

O dashboard não tenta adivinhar arena por mtime. O systemd declara explicitamente:

    FACTORIO_AI_DASHBOARD_SCOPE=baseline:cortex_baseline_protocol_v1:exploratory:auto

O resolver procura manifests somente dentro do protocolo/modo declarado, prioriza seed running, seleciona a seed concluída mais recente quando não há execução ativa, resolve runs_dir para o sandbox e expõe release, seed, status e result summary.

Auto significa última seed desta arena, não qualquer arquivo mais recente no host.

## 6. Endpoints alinhados ao contexto

No contexto baseline:

- /api/run lê active_run.json da seed;
- /api/research lê research_state.json da seed;
- /api/evolution sintetiza independent_baseline_seed;
- champion = null;
- challenger = resultado/run da seed;
- promotion = decisão local da seed;
- /api/knowledge lê knowledge da seed;
- survival, discoveries e machine diagnostics usam reports/history do sandbox;
- /api/world continua sendo RCON ao vivo;
- /api/context declara a ligação entre as superfícies.

No escopo global, os paths e hooks de testes anteriores são preservados.

## 7. Sem champion falso

Uma baseline cold-start independente não tem incumbent herdado. O frontend não pode exibir Champion G37 e Challenger G97 quando F1-B está ativa.

A visão baseline passa a exibir seed 20261001, cold-start independente, no champion inherited, runtime 95c34a53 e partial_success / Logistic science.

## 8. Truth strip

O frontend ganhou uma faixa explícita CONTEXTO EXPERIMENTAL com WORLD · LIVE RCON e EVIDENCE · SEED <id>. Ela mostra seed, status, runtime da baseline, quantidade de entidades, failed stage e logistic science output quando medido.

O mapa em tela cheia também exibe um truth label independente, para que uma captura isolada do mapa continue carregando provenance visual.

## 9. Deploy desacoplado do runtime experimental

Foi criado scripts/deploy_dashboard.sh.

Destino:

    /srv/factorio-ai-dashboard-runtime/releases/<sha>
    /srv/factorio-ai-dashboard-runtime/current

O serviço factorio-ai-dashboard.service passa a executar desse runtime próprio. Source/docs/dashboard podem avançar sem alterar o runtime científico congelado da baseline em 95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac.

## 10. Validação de preview

Antes do deploy, o dashboard foi executado em porta isolada 8876.

Resultados:

- 54 testes dashboard/context: PASS;
- Ruff: PASS;
- compileall: PASS;
- node --check app.js: PASS;
- frontend TypeScript build: PASS;
- Vite build: PASS;
- cache stamps recalculados;
- /api/context: seed 20261001 / completed;
- /api/research: curriculum-20260924T043932Z / partial_success / Logistic science;
- /api/evolution: independent_baseline_seed / champion null;
- /api/world: 117 entities;
- /api/knowledge: isolated_seed / 9 lessons;
- HTML contém truth strip;
- map.html contém WORLD · LIVE RCON.

Screenshot headless: runs/audits/dashboard_f1b_preview.png.

## 11. Regra para próximas fases

Cada fase deve declarar explicitamente seu dashboard scope. F1-B usa baseline:cortex_baseline_protocol_v1:exploratory:auto. Uma futura fase Cortex não pode herdar esse valor silenciosamente.

## 12. Regra de interrupção

Após qualquer interrupção ou perda de conexão, verificar nesta ordem:

1. Git source HEAD e dirty state;
2. baseline runtime pin;
3. dashboard runtime pin;
4. factorio-ai-evolution deve permanecer inativo durante baseline;
5. /api/context;
6. manifest/result da última seed;
7. checkboxes do CORTEX_HANDOFF e CORTEX_RESEARCH_PROGRAM;
8. somente então executar a próxima seed.

A UI nunca é autoridade para decidir o que rodar; ela é uma projeção verificável dos artefatos canônicos.


## 13. Deployment efetivo

Dashboard release ativa:

    8ce05ba3a4eb48ed681fcaba1103913aa7505708

Runtime científico mantido sem alteração:

    95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac

Validação viva após restart:

- factorio-ai-dashboard: active;
- research runner: inactive;
- experiment context: seed 20261001;
- research status: partial_success;
- bottleneck: Logistic science;
- logistic science output: 0;
- champion: null;
- app.js stamp: d9f75008e955;
- bundle servido contém baseline seed completed;
- bundle servido substitui a frase herdada de incumbent por semântica cold-start.

O runtime científico não foi movido durante nenhuma dessas operações.
