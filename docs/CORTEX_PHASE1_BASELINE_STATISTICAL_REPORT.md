# F1 — Relatório Estatístico da Baseline Exploratória Corrigida

**Protocol:** cortex_baseline_protocol_v1
**Scientific runtime:** 95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac
**Sample:** 5 exploratory seeds, frozen before execution
**Validity:** 5 valid / 0 invalid
**Inference class:** descriptive exploratory baseline; not a confirmatory Cortex comparison.

## 1. Pergunta

Qual é o comportamento da arquitetura pré-Cortex corrigida quando executada em cinco seeds independentes, cold-start, sem champion G37, sem memória seletiva herdada e sob o mesmo runtime imutável?

## 2. Integridade experimental

Todas as cinco observações satisfizeram:

- manifest completed e returncode 0;
- code_revision.commit igual à release congelada;
- dirty=false;
- measurement_protocol=cell_attributed_v3;
- unclassified_rate_keys vazio;
- global isolation hash PASS;
- evolution_champion global ausente;
- factorio-ai-evolution inativo;
- cada seed em STATE_ROOT próprio.

Nenhuma seed foi descartada por comportamento desfavorável.

## 3. Resultados por seed

| Seed | Stages | Bottleneck | Closed loop | Green science | Autonomy | Physical coverage | Manual logistics | Fuel-starved |
|---:|---:|---|---|---:|---:|---:|---:|---:|
| 20261001 | 14 | Logistic science | false | 0 | 0.5 | 0.333 | 53 |  |
| 20261002 | 14 | Logistic science | false | 0 | 0.375 | 0.5 | 53 | 1 |
| 20261003 | 14 | Logistic science | false | 0 | 0.375 | 0.5 | 55 | 5 |
| 20261004 | 14 | Logistic science | false | 0 | 0.5 | 0.5 | 55 |  |
| 20261005 | 14 | Logistic science | false | 0 | 0.5 | 0.5 | 55 |  |

## 4. Estatística descritiva pré-registrada

| Metric | n | Mean | Median | Sample SD | Q1 | Q3 | IQR | Min | Max |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| autonomy_score | 5 | 0.45 | 0.5 | 0.068465 | 0.375 | 0.5 | 0.125 | 0.375 | 0.5 |
| manual_logistics_calls | 5 | 54.2 | 55 | 1.095445 | 53 | 55 | 2 | 53 | 55 |
| physical_processing_coverage | 5 | 0.466667 | 0.5 | 0.074536 | 0.5 | 0.5 | 0 | 0.333333 | 0.5 |
| fuel_starved_entities | 5 | 1.2 | 0 | 2.167948 | 0 | 1 | 1 | 0 | 5 |
| power_starved_entities | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| external_dependencies | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| endogenous_rate_per_s | 5 | 0.606853 | 0.606284 | 0.001273 | 0.606284 | 0.606284 | 0 | 0.606284 | 0.60913 |
| intervention_rate_per_s | 5 | 0.647391 | 0.647644 | 0.000561 | 0.647004 | 0.647807 | 0.000803 | 0.646606 | 0.647897 |
| productive_runtime_s | 5 | 668.216667 | 680.216667 | 26.832816 | 680.216667 | 680.216667 | 0 | 620.216667 | 680.216667 |
| route_cost | 5 | 8.25 | 8.25 | 0 | 8.25 | 8.25 | 0 | 8.25 | 8.25 |
| route_turns | 5 | 1 | 1 | 0 | 1 | 1 | 0 | 1 | 1 |

## 5. Frequências e invariantes

- Logistic science foi o bottleneck em **5/5 seeds (100%)**.
- Logistic science foi o único failed stage registrado em **5/5 seeds (100%)**.
- Closed-loop autonomy ocorreu em **0/5 (0%)**.
- Green-science output > 0 ocorreu em **0/5 (0%)**.
- Power starvation final: mediana 0; máximo 0.
- External dependencies: mediana 0; máximo 0.
- Halt causes: {'fuel_starvation': 2, 'none_observed': 3}.

Todos os 14 stages anteriores à Logistic science foram concluídos em 5/5 seeds:

- A* belt logistics: 5/5 (100%).
- Automation science: 5/5 (100%).
- Baseline iron mining: 5/5 (100%).
- Belt-fed smelting: 5/5 (100%).
- Capability survival soak: 5/5 (100%).
- Coal self-sufficiency: 5/5 (100%).
- Copper expansion: 5/5 (100%).
- Copper smelting: 5/5 (100%).
- Electronic circuits: 5/5 (100%).
- Online placement learning: 5/5 (100%).
- Powered manufacturing: 5/5 (100%).
- Scale mining: 5/5 (100%).
- Smelting probe: 5/5 (100%).
- Steam power: 5/5 (100%).

## 6. Achado estrutural replicado

Nas cinco seeds, o sistema alcançou mineração, smelting, logística por belts, carvão, cobre, steam power, manufacturing elétrico, automation science e electronic circuits. Ainda assim, nenhuma seed materializou green science.

O repair system repetidamente conseguiu corrigir starvation de combustível em parte ou totalmente, porém o repair estrutural:

    placement:place_processing_for_buffered_output

foi diagnosticado mas não executado por:

    no_runner_binding_for_intent

Isso separa duas classes de capacidade:

1. **diagnóstico local e resupply:** existente e operacional;
2. **reestruturação industrial global:** reconhecida semanticamente, mas sem autoridade executável genérica.

Essa fronteira é precisamente consistente com a motivação da Fase 2: substituir authority fragmentada em stage handlers/bindings por uma ontology de ações e um Universal Executor.

## 7. Dependência de intervenção

A taxa endógena média foi **0.606853/s**, enquanto a taxa média atribuída a intervenção foi **0.647391/s**.

Nas cinco seeds, a taxa de intervenção permaneceu da mesma ordem ou superior à taxa endógena. O número de chamadas logísticas manuais também foi altamente estável: mediana 55, intervalo 53–55.

Portanto, a baseline não demonstra uma política fechada de fábrica autossustentável. Ela demonstra um pipeline instrumental competente, mas ainda dependente de sequência e intervenção codificadas.

## 8. Variabilidade

Há baixa variabilidade em várias métricas centrais:

- autonomy_score: SD=0.068465, range 0.375–0.5;
- manual_logistics_calls: SD=1.095445, range 53–55;
- route_cost: SD=0, valor 8.25 em toda a amostra;
- route_turns: SD=0, valor 1 em toda a amostra.

A maior variação qualitativa ocorreu em fuel starvation final, sem alterar o bottleneck terminal. Há seeds com starvation final zero e mesmo assim green science permanece zero. Isso enfraquece a hipótese de que combustível seja a causa suficiente do failure terminal.

## 9. Limitações

- n=5 é deliberadamente uma amostra exploratória pequena;
- as seeds foram usadas para caracterizar a baseline e podem orientar o desenho do Cortex;
- elas não devem ser reutilizadas como conjunto confirmatório para selecionar entre variantes Cortex;
- as 10 confirmatory seeds pré-registradas permanecem intactas;
- nenhuma estimativa de superioridade do futuro Cortex é feita neste relatório;
- não há claim de generalização para mapas/distribuições fora do protocolo atual.

## 10. Decisão F1

A baseline corrigida foi reproduzida em cinco seeds independentes e os artefatos necessários para comparação futura foram preservados. O resultado é suficientemente estável para cumprir o objetivo exploratório da F1.

**Conclusão descritiva:** a arquitetura herdada não falha por incapacidade de executar qualquer ação física; ela falha por não converter diagnóstico estrutural e objetivos de longo horizonte em autoridade genérica para reorganizar a fábrica. Esse é o contraste causal que F2/F3 devem atacar.

As seeds confirmatórias 20261101–20261110 permanecem congeladas e não executadas. Elas devem ser usadas posteriormente em avaliação pareada pré-Cortex vs Cortex, sob protocolo congelado, sem tuning nelas.

## 11. Artefatos

- docs/CORTEX_PHASE1_BASELINE_RESULTS.md;
- runs/audits/cortex_baseline_exploratory_summary.json;
- docs/CORTEX_PHASE1_BASELINE_SEED_20261001.md;
- docs/CORTEX_PHASE1_BASELINE_SEED_20261002.md;
- docs/CORTEX_PHASE1_BASELINE_SEED_20261003.md;
- docs/CORTEX_PHASE1_BASELINE_SEED_20261004.md;
- docs/CORTEX_PHASE1_BASELINE_SEED_20261005.md;
- runs/audits/baseline_<seed>_global_isolation_before.json.
