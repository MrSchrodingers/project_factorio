# Cortex Research — F2-E Controlled Transactional Execution

**Phase:** F2
**Checkpoint:** F2-E2 concluído — controlled execution validada; structural option rejeitada por no_fuel
**Ambient authority:** proibida
**Scientific F1 runtime:** 95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac
**Confirmatory holdout:** 20261101–20261110 congelado e não utilizado

## 1. Pergunta de pesquisa

F2-D produziu PreparedStructuralAction inerte. F2-E testa o primeiro edge executável entre o
control plane tipado do Cortex e Factorio:

> Uma hipótese estrutural preparada pode ser executada preservando authority explícita, medição
> causal, rollback exato e distinção entre falha de engine, falha de instrumento e falsificação
> da própria hipótese?

O objetivo não é conceder autonomia contínua. O objetivo é provar um caminho seguro, auditável e
falsificável de hipótese para ação física.

## 2. Authority e transaction boundary

Toda mutação exige ActionAuthority.EXECUTE explicitamente em cada chamada. SHADOW e PROPOSAL são
recusados antes de TransactionalFLEExecutor.execute.

O adapter não possui scheduler, loop, serviço, grant persistente nem capacidade de promover a
própria authority.

Stack:

PreparedStructuralAction
-> compile_structural_action
-> CompiledStructuralAction
-> StructuralTransactionalAdapter
-> TransactionalFLEExecutor
-> candidate Factorio state
-> measurement
-> hard-condition evaluation
-> commit OU rollback
-> ActionResult

Rollback permanece exclusivamente em TransactionalFLEExecutor.

## 3. Compiler semântico

F2-E1 compila ensure_item, place_processor, adopt_processor, configure_processing, direct-inserter
connect_delivery e verify_postconditions.

Prototype names são resolvidos contra fle.env.game_types.Prototype. Não há heurística de
capitalização. Prototype inexistente gera structural_execute_prototype_unresolved.

Furnaces não recebem assembler-style set_entity_recipe para smelting convencional. Delivery por
belt ainda é refusal: F2-E1 autoriza apenas o menor caminho já validado, direct inserter.

A transação contém observation window limitada: default 8 s, máximo 60 s. O valor faz parte de
CompiledStructuralAction e do ActionResult.

## 4. Hard postconditions

A hard prediction herdada da F2-D é:

- physical_factory_graph.producers_reaching_processor INCREASE.

F2-E adiciona dois execution guards obrigatórios:

- processor_exists EQUALS true;
- processor_output INCREASE.

O primeiro usa igualdade, portanto um valor medido false nunca satisfaz o gate. O segundo impede
falso positivo topológico: máquina/inserter podem existir e o factory graph pode mostrar uma rota
sem que qualquer processamento funcional tenha ocorrido.

Topologia válida não equivale a capacidade funcional.

## 5. Acceptance e rollback

Candidate state só é commitado quando simultaneamente:

1. FLE não reporta error_occurred;
2. candidate_game_state existe;
3. measurement pós-ação funciona;
4. todas as hard postconditions preparadas passam;
5. processor_exists == true;
6. processor_output aumentou.

Caso contrário TransactionalFLEExecutor restaura checkpoint_before.

As causas são separadas:

- structural_transaction_failed — engine/candidate-state failure;
- structural_measurement_failed — instrumento científico falhou;
- structural_postcondition_failed — execução ocorreu, medida funcionou, hipótese não se confirmou.

Um resultado científico negativo não é classificado como falha de infraestrutura.

Accepted implica status=accepted, authority=execute, changed_world=true e hard postconditions
satisfied. Rejected implica changed_world=false, refusal nomeada e rollback.

O purpose continua infrastructure, preservando a distinção entre construir automação e carregar
a fábrica manualmente.

## 6. F2-E1 replay gate

tests/test_cortex_structural_execute.py usa o TransactionalFLEExecutor real sobre ambiente fake
determinístico. O commit/rollback não é mockado.

O gate prova:

- resolução no Prototype enum real;
- compilação determinística;
- belt ainda recusado;
- SHADOW não chama executor;
- EXECUTE commitado apenas após efeitos funcionais;
- rollback exato por postcondition failure;
- rollback por measurement failure;
- engine failure separado de hypothesis failure;
- processor_exists=false não passa por mera existência do campo.

O conjunto focado F2-E1 + regressões Cortex/FLE passou 45 testes após o reboot de 2026-09-24.

## 7. One-shot Factorio canary — F2-E2

scripts/run_cortex_structural_canary.py define o experimento real. Ele é fail-closed:

- --execute é obrigatório;
- sem --execute retorna world_mutation=false;
- seeds confirmatórias 20261101–20261110 são recusadas antes de abrir ambiente;
- seed default 424242 está fora do baseline/holdout;
- árvore Git precisa estar clean/committed;
- FactorioWorldLease é obrigatório;
- continuous_authority=false.

O canário substitui apenas o mundo vivo do laboratório por um mundo determinístico de teste.
Nenhum artefato F1 congelado é modificado.

A infraestrutura de fixture cria somente um precursor mínimo: um burner mining drill de ferro,
fuel e wooden chest downstream. Depois disso, target detection, ResourceSurvey, runtime catalog,
structural planning, preparation e execução passam pelo caminho Cortex.

Antes de EXECUTE o runner serializa um checkpoint explícito.

## 8. Evidence artifact

Default:

runs/audits/cortex_f2e_structural_canary.json

Registra schema, seed, SHA/dirty, authority explícita, continuous_authority=false, bootstrap,
factory graph before, targets, plan, PreparedStructuralAction, measurements before/candidate/final,
ActionResult, intervention accounting, transaction_committed e rollback_observed.

Experiment status e transaction status são campos diferentes. Um canário concluído pode resultar
em transação rejeitada.

## 9. Valor científico de um reject

O primeiro canário pode falhar legitimamente. Por exemplo, furnace e inserter podem ser colocados
mas processor_output permanecer zero por ausência de fuel dependency.

Nesse caso o resultado correto é:

- structural_postcondition_failed;
- rollback;
- counterexample preservado;
- próxima ação modela a dependência ausente.

O gate de output não deve ser relaxado para fabricar sucesso.

## 10. Non-goals

F2-E1/E2 não autorizam:

- continuous autonomous execution;
- scheduler de mutações;
- handler específico de green science;
- uso das confirmatory seeds;
- mutação dos artefatos F1;
- promoção do Cortex a policy principal;
- bypass de TransactionalFLEExecutor;
- aceitação de topologia sem output funcional.

## 11. Exit criteria F2-E

F2-E só pode ser PASS quando:

- adapter e regressões passarem;
- full repository gate passar;
- implementação estiver commitada/pushada;
- canário Factorio rodar a partir de commit clean;
- accepted action tiver hard effects confirmados OU rejected action provar rollback;
- artifact do canário for analisado;
- docs/README/handoff/phase-state/frontend refletirem o resultado;
- F1 runtime permanecer imutável;
- confirmatory seeds permanecerem intactas;
- continuous autonomous authority continuar desabilitada.

Enquanto isso F2-E permanece ativa e F3 não está autorizada.

## 13. F2-E2 attempt 1 — instrumentation counterexample

O primeiro canário real foi executado na seed 424242 a partir do commit limpo
564cfbb54489ce2c6690adafcc629ec036bd4720.

Resultado:

- run_id: cortex-f2e-20260924T185807Z;
- continuous_authority=false;
- status do experimento: failed;
- ActionResult: não criado;
- transaction_committed: não alcançado;
- structural EXECUTE: não alcançado;
- failure: canary expected exactly one ready branch, got 0.

O artifact original foi preservado em:

runs/audits/cortex_f2e_structural_canary_attempt1.json

A falha ocorreu antes de TransactionalFLEExecutor receber a ação estrutural. Portanto não é
evidência contra a hipótese de execução transacional nem requer rollback estrutural.

### Causa

O bootstrap mediu iron ore no chest via inspect_inventory, mas o canário alimentava o planner com
_save_entity_state.

Esse instrumento retorna:

- chest inventory em inventories.chest;
- zero resource rows no cenário observado.

Já o planner F2-D consome o schema científico canônico usado no shadow audit:

- container contents em contents;
- resource tiles via ResourceSurvey.

Consequentemente o planner recebeu evidence incompleta e respondeu corretamente com
structural_buffer_contents_unobserved.

### Correção

A boundary F2-E2 passa a usar:

- FactorioObserver.snapshot para entidades/contents;
- FactorioObserver.resource_overview para ResourceSurvey;
- FactorioObserver.game_knowledge para RuntimeFactorioCatalog;
- _save_entity_state apenas para o inventário on-hand do character e checkpoint FLE.

Post-action measurement também usa o observer canônico. processor_output passa a ser medido em
craft_output da máquina observada, não por variável transitória do namespace.

O canário também grava plan/targets/instruments antes de exigir branch ready, de forma que futuras
recusas pré-EXECUTE preservem evidência completa.

## 14. F2-E2 attempt 2 — functional counterexample with verified rollback

O retry foi executado na seed 424242 a partir do commit limpo:

4a0558e3a4c6b7795d618f4cdcda3a22e53074d8

Artifact canônico:

runs/audits/cortex_f2e_structural_canary.json

Cópia imutável do retry:

runs/audits/cortex_f2e_structural_canary_attempt2.json

SHA-256 de ambos no momento da preservação:

2783803e228cf59b2048aeac9a4c5c28a27c0b8ac51eb5a89cea5d97d2d4770e

### Resultado

- experiment status: completed;
- ActionResult: rejected;
- authority: execute;
- changed_world: false;
- transaction_committed: false;
- rollback_observed: true;
- refusal: structural_postcondition_failed;
- continuous_authority: false.

### Medição before

- producers_reaching_processor = 0;
- physical_processing_coverage = 0.0;
- processor_exists = false;
- processor_output = 0.0.

### Candidate state

- producers_reaching_processor = 1;
- physical_processing_coverage = 1.0;
- processor_exists = true;
- processor_status = no_fuel;
- processor_output = 0.0.

As duas postconditions topológicas foram satisfeitas:

- producers_reaching_processor INCREASE = satisfied;
- processor_exists == true = satisfied.

A hard postcondition funcional falhou:

- processor_output INCREASE = unsatisfied.

### Estado após rollback

A medição final retornou ao estado pré-transação:

- producers_reaching_processor = 0;
- physical_processing_coverage = 0.0;
- processor_exists = false;
- processor_output = 0.0.

Portanto o rollback transacional foi observado empiricamente no Factorio real.

## 15. Interpretação causal

O retry separa três proposições:

1. o Cortex consegue compilar e enviar a structural action ao executor transacional;
2. placement + delivery alteram corretamente a topologia física;
3. a opção estrutural ainda é causalmente incompleta porque não satisfaz a dependência de fuel do stone-furnace.

O resultado não falsifica TransactionalFLEExecutor nem o structural planner de placement/delivery.
Ele falsifica a hipótese mais forte de que o branch atual, sozinho, seja suficiente para produzir.

Esse é exatamente o tipo de counterexample que o programa Cortex deve preservar.

O gate processor_output não será relaxado.

## 16. Decision F2-E

**PASS para a camada de execução transacional controlada.**

Foi demonstrado em Factorio real que:

- EXECUTE é explícito;
- a candidate transaction pode alterar o mundo;
- hard postconditions são medidas antes do commit;
- uma hipótese funcional falsa é rejeitada;
- rollback restaura o checkpoint;
- o ActionResult preserva a causa da rejeição.

**FAIL para suficiência funcional da opção estrutural atual.**

F2 permanece aberta. F3 não está autorizada.

## 17. Gate de fechamento F2-E2

- 1368 core/FLE tests: PASS;
- 2 PyTorch tests: PASS;
- continuity/outcome tests: PASS;
- Ruff/static checks: PASS;
- frontend TypeScript/Vite build: PASS;
- node --check: PASS;
- scientific runtime F1: unchanged / dirty=false;
- factorio-ai-evolution: inactive + disabled;
- confirmatory seeds: unspent;
- rollback exactness: PASS.

## 18. Próximo checkpoint — F2-F

F2-F deve tornar a opção estrutural dependency-complete.

Para stone-furnace, isso significa modelar explicitamente fuel/energy como parte da capability,
compondo planners existentes em vez de inserir carvão por special-case no executor.

Requisitos:

1. detectar energy/fuel requirement do processor a partir de catálogo/observação;
2. representar a dependência como precondition/child option tipada;
3. reutilizar resupply/fuel planning já existente;
4. preparar processor + delivery + fuel dependency como uma opção composta;
5. manter rollback único em TransactionalFLEExecutor;
6. exigir processor_output > 0 no canário seguinte;
7. continuar sem scheduler/continuous authority;
8. não usar confirmatory seeds.
