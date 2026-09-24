# Cortex Research — F2-E Controlled Transactional Execution

**Phase:** F2
**Checkpoint:** F2-E1 — adapter transacional controlado validado; canário Factorio pendente
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
