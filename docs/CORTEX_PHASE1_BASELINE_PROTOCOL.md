# Cortex Research — F1-B Baseline Corrigida

**Status:** PRE-REGISTERED / READY TO RUN.
**Pré-condição:** F1-A PASS e G37 aposentada de forma reversível.
**Objetivo:** medir a arquitetura pré-Cortex corrigida sob execução limpa, sem herança entre seeds.

## 1. Pergunta experimental

Qual é o desempenho da arquitetura herdada depois de corrigidos os principais defeitos de instrumentação e isolamento, quando cada seed começa sem champion, sem lifelong checkpoint e sem estado seletivo herdado de outra seed?

Esse resultado será a referência causal para as fases Cortex seguintes.

## 2. O que esta baseline mede

A baseline inclui o runner mecanicista e as ferramentas herdadas atuais, porque elas são precisamente o sistema que será substituído gradualmente.

Ela NÃO inclui:

- champion G37;
- lifelong state de G37;
- niche archive herdado;
- open-play strategy herdada;
- evolução serial entre seeds;
- working tree suja;
- release sem BUILD_INFO.

Histories, telemetry e knowledge históricos permanecem preservados no STATE_ROOT principal, mas cada seed da baseline usa STATE_ROOT próprio e frio. Portanto, um run não consulta a memória produzida por outro run da suite.

## 3. Release e provenance

Cada seed só pode rodar quando BUILD_INFO.json declarar commit conhecido e dirty=false.

O runner é scripts/run_corrected_baseline_seed.py.

Ele configura para o processo filho:

    PYTHONPATH = <release>/src
    FACTORIO_AI_STATE_ROOT = <sandbox da seed>
    FACTORIO_AI_REQUIRE_CLEAN_PROMOTION = 1
    FACTORIO_SERVER_ADDRESS = 127.0.0.1
    FACTORIO_SERVER_PORT = 27000

O evolution service global precisa permanecer inativo durante cada run.

## 4. Independência estatística

O sandbox tem a forma:

    baseline_runs/cortex_baseline_protocol_v1/<mode>/<seed>/

Dentro dele, o currículo cria seu próprio runs/, champion local, reports, knowledge e demais artefatos.

Consequências:

- seed A não vira incumbent da seed B;
- knowledge de A não entra em B;
- archive de A não entra em B;
- selection state de A não entra em B;
- a única infraestrutura compartilhada é o Factorio/FLE, que é resetado pelo runner e protegido pelo world lease.

## 5. Seeds congeladas

Fonte canônica: configs/cortex_baseline_v1.json.

### Exploratory

- [ ] 20261001
- [ ] 20261002
- [ ] 20261003
- [ ] 20261004
- [ ] 20261005

As seeds exploratórias podem revelar defeitos no protocolo. Mudanças metodológicas exigem nova versão do protocolo e não podem ser retroativamente tratadas como se fossem o mesmo experimento.

### Confirmatory

- [ ] 20261101
- [ ] 20261102
- [ ] 20261103
- [ ] 20261104
- [ ] 20261105
- [ ] 20261106
- [ ] 20261107
- [ ] 20261108
- [ ] 20261109
- [ ] 20261110

Depois que a primeira seed confirmatória for executada, a lista não pode ser alterada para esta versão do protocolo.

## 6. G37

G37 foi aposentada em 2026-09-24 antes da primeira seed desta baseline.

Registro:

    runs/baseline_reset.json

Retirement:

    /srv/factorio-ai-lab/backups/selection-reset-20260924T040637Z

Champion aposentada:

- generation: 37;
- run_id: curriculum-20260923T035414Z;
- selected_at: 2026-09-23T04:02:22.978028+00:00.

O retirement moveu oito artefatos seletivos e preservou histories, telemetry, knowledge, counterexamples, repairs, datasets e models.

## 7. Métricas pré-registradas

Métricas primárias:

- completed_stage_count;
- failed stage / bottleneck;
- closed_loop_autonomy;
- autonomy_score;
- manual_logistics_calls;
- physical_processing_coverage;
- fuel_starved_entities;
- power_starved_entities;
- external_dependencies.

Métricas produtivas:

- endogenous_rate_per_s;
- intervention_rate_per_s;
- rates_per_s por commodity;
- productive_runtime_s;
- route_cost;
- route_turns.

Métricas de integridade:

- code_revision.commit;
- code_revision.dirty;
- measurement_protocol;
- unclassified_rate_keys;
- retorno do processo;
- existência de generation report.

## 8. Estatística

Após as cinco seeds exploratórias:

- reportar cada seed individualmente;
- mediana;
- média apenas como complemento;
- min/max;
- taxa de conclusão por stage;
- distribuição de halt causes;
- nenhum melhor-run isolado como conclusão.

Para a série confirmatória:

- IQM;
- bootstrap confidence interval;
- mediana;
- taxa de sucesso por capability;
- distribuição completa;
- comparação pareada quando uma futura arquitetura Cortex usar as mesmas seeds.

Nenhuma seed falha pode ser descartada silenciosamente.

## 9. Critérios de invalidação

Uma seed é inválida, não failed, se:

- build não é clean;
- release commit diverge do manifest;
- world lease foi violado;
- outro evolution/curriculum concorrente estava ativo;
- generation report não possui code_revision;
- infraestrutura externa interrompeu o run de modo não relacionado à política.

Uma seed failed permanece na amostra quando a falha é comportamento do agente/sistema sob o protocolo.

## 10. Regra de tuning

Exploratory pode produzir hipótese de correção de protocolo.

Confirmatory não pode alimentar tuning dentro de cortex_baseline_protocol_v1.

Se uma mudança de código for necessária após iniciar confirmatory:

1. encerrar o protocolo atual;
2. registrar o motivo;
3. criar cortex_baseline_protocol_v2;
4. congelar nova release;
5. reiniciar toda a série confirmatória.

## 11. Execução unitária

Dry-run:

    python scripts/run_corrected_baseline_seed.py --seed 20261001 --mode exploratory --dry-run

Execução:

    python scripts/run_corrected_baseline_seed.py --seed 20261001 --mode exploratory

O runner recusa automaticamente repetir um sandbox que já contenha evidência.

## 12. Checklist de encerramento F1-B

- [x] G37 aposentada com backup;
- [x] selection state herdado removido do namespace ativo;
- [x] seeds pré-registradas;
- [x] runner independente por seed testado;
- [x] dry-run da primeira seed validado;
- [ ] cinco seeds exploratórias concluídas;
- [ ] relatório estatístico exploratório produzido;
- [ ] decisão explícita sobre executar ou não confirmatory antes da F2;
- [ ] documentação/README/handoff atualizados com os resultados;
- [ ] tag de fechamento F1 criada.

F2 continua bloqueada enquanto os itens de execução/relatório permanecerem abertos.
