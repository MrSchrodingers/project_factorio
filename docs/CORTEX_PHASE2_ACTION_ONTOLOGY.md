# Cortex Research — F2 Action/Option Ontology and Universal Executor

**Phase:** F2 — Action/Option Ontology e Universal Executor
**Checkpoint:** F2-A — typed ontology + universal shadow facade
**Authority:** SHADOW / NO LIVE-WORLD MUTATION
**Baseline holdout:** 20261101–20261110 frozen and unspent
**Scientific baseline runtime:** 95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac

## 1. Motivação causal

A F1 caracterizou a arquitetura pré-Cortex em cinco seeds independentes. Em 5/5 execuções, o sistema concluiu 14 stages e parou em Logistic science; green science permaneceu zero e closed-loop autonomy permaneceu falsa.

O counterexample mais importante foi estrutural. O repair loop detectou producer_output_unprocessed, propôs placement:place_processing_for_buffered_output e falhou em executar porque o runner não possuía binding para esse intent: no_runner_binding_for_intent.

Esse resultado separa planejamento de autoridade. O repositório já possui planners úteis e um executor transacional robusto. A limitação é que a autoridade está fragmentada em branches e strings de código do curriculum_runner/open_play_runner.

F2 cria uma camada de controle genérica entre cognição e Factorio.

## 2. Hipótese de pesquisa

Se toda intervenção candidata for representada por um request tipado com provenance, evidence, preconditions, predicted postconditions e authority explícita, então planners heterogêneos podem ser compostos por um executive genérico sem codificar a solução como sequência de stages.

Isso é pré-condição para F3. Um cognitive loop não pode escolher entre alternativas se apenas branches específicos do runner são executáveis.

## 3. Escopo de F2-A

F2-A NÃO concede autoridade live ao Cortex.

Não altera o runtime científico F1, não executa seeds confirmatórias, não remove curriculum_runner, não substitui TransactionalFLEExecutor e não conecta LLM diretamente a código FLE.

F2-A estabelece semântica, evidence contract e authority boundary.

## 4. Componentes legados mapeados

| Family | Componente existente | Contrato atual | Estado F2-A |
|---|---|---|---|
| placement | planning/placement.py | PlacementPlan: adopt/build/refuse | shadow facade |
| delivery | planning/delivery.py | DeliveryLink: inserter/belt/refuse | shadow facade |
| resupply | planning/resupply.py | SupplyPlan + named refusals | shadow facade |
| rebuild | planning/rebuild.py | RebuildProposal + regression guards | shadow facade |
| craft | FLE craft_item embutido nos runners | raw transactional code | shadow facade, adapter pendente |
| research | FLE set_research embutido nos runners | raw transactional code | shadow facade, adapter pendente |
| dependency_plan | planning/dependency_plan.py | PlanStep/Blocker graph | shadow facade |

A nova camada não duplica esses planners. Ela padroniza a passagem de um plano para uma ação candidata e aplica authority.

## 5. ActionFamily

O namespace inicial contém placement, delivery, resupply, rebuild, craft, research e dependency_plan.

Family e intent são separados. Placement, por exemplo, pode representar place_processing_for_buffered_output, extend_power_supply_to_machine, attach_machine_to_live_network e restore_water_path_to_generator.

Isso permite aprender escolha de intents sem substituir o tool family.

## 6. ActionRequest

ActionRequest é a unidade canônica de decisão. Contém:

- action_id;
- family;
- intent;
- arguments;
- targets;
- requires;
- provides;
- EvidenceRef;
- preconditions;
- postconditions;
- ActionProvenance.

Não existe no Cortex uma API semântica do tipo "execute esta string".

## 7. ActionResult

ActionResult distingue shadowed, proposed, refused, accepted, rejected e failed.

Um resultado não aceito não pode afirmar changed_world=true.

Um resultado ACCEPTED só é permitido sob EXECUTE authority e apenas quando todas as hard postconditions presentes no resultado estão SATISFIED.

Retorno sem exception não equivale a sucesso cognitivo.

## 8. EvidenceRef

EvidenceRef aponta para a evidência que licenciou ou mediu a ação. Registra source, path, EvidenceStatus, reason quando missing/invalid e digest opcional.

A regra epistemológica da F1 permanece:

missing != zero.

Evidence missing/invalid precisa de motivo explícito.

## 9. Refusal

Refusal é saída de primeira classe.

Os códigos universais iniciais são:

- authority_shadow_only;
- authority_proposal_only;
- no_action_binding;
- binding_has_no_execute_handler;
- hard_precondition_not_satisfied;
- action_handler_exception.

Refusals de planners de domínio devem ser preservadas pelos adapters futuros.

## 10. Authority model

A autoridade é explícita e monotônica:

SHADOW -> PROPOSAL -> EXECUTE.

SHADOW permite representar, resolver binding e auditar, mas proíbe chamar handler mutante.

PROPOSAL permite emitir/rankear ação, porém continua sem mutação.

EXECUTE permite adapter concreto, mas somente após gates F2 e verificação pós-ação.

F2-A opera apenas em SHADOW.

## 11. Preconditions e postconditions

ActionCondition contém name, operator, state, expected, hard/soft e evidence refs.

ConditionState pode ser satisfied, unsatisfied ou unknown.

Hard precondition UNKNOWN ou UNSATISFIED bloqueia antes do handler.

Hard postcondition UNKNOWN impede status ACCEPTED.

Operadores iniciais: exists, equals, at_least, at_most, increase, decrease e unchanged.

Isso impede unread world -> assume safe -> execute -> engine rejection.

## 12. Provenance

Toda ActionRequest contém requested_by, source_component, code_revision, run_id, generation, parent_action_id e policy_version quando disponíveis.

Esse lineage permite responder qual policy propôs, qual SHA produziu, qual run executou, qual opção pai originou e se o resultado é replayable.

## 13. UniversalExecutor

UniversalExecutor é o único authority gate.

Responsabilidades:

1. resolver family/intent binding;
2. recusar binding ausente;
3. bloquear hard preconditions inválidas;
4. aplicar SHADOW/PROPOSAL/EXECUTE;
5. chamar handler somente em EXECUTE;
6. validar identidade do resultado;
7. retornar ActionResult auditável.

Ele não é planner nem policy.

## 14. Legacy shadow facade

F2-A declara bindings shadow para:

placement -> planning.placement
delivery -> planning.delivery
resupply -> planning.resupply
rebuild -> planning.rebuild
craft -> legacy FLE craft_item
research -> legacy FLE set_research
dependency_plan -> planning.dependency_plan

Esses bindings não carregam execute handler em F2-A. Resolver binding demonstra cobertura sem conceder autoridade.

## 15. Ponte RepairAction -> ActionRequest

repair_loop já carrega tool, intent, prediction, requires, provides, targets, removes e arguments.

request_from_repair_action promove essa representação ao schema universal.

A prediction vira hard postcondition com state UNKNOWN até ser medida.

Para o counterexample F1:

tool = placement
intent = place_processing_for_buffered_output
prediction = producers_reaching_processor increases

F2-A agora consegue representar e routear essa intenção em shadow mode. Ainda não a executa.

## 16. Relação com TransactionalFLEExecutor

TransactionalFLEExecutor continua responsável por checkpoint, candidate state, acceptance predicate, rollback, accounting e runtime telemetry.

F2 não reimplementará rollback.

Stack alvo:

cognitive candidate
-> ActionRequest
-> UniversalExecutor
-> adapter
-> TransactionalFLEExecutor
-> Factorio/FLE
-> measured postconditions
-> ActionResult

F2-A termina antes do adapter transacional.

## 17. Craft e research

Craft e research ainda aparecem como craft_item e set_research em strings dos runners.

F2-B/C deve tipar target item/technology, quantity, recipe/technology availability, material prerequisites, machine/lab requirements, energy/time budget e expected inventory/research delta.

A string FLE será detalhe de implementação, não ação cognitiva.

## 18. Invariantes de segurança

I1 — request válido não implica autoridade.

I2 — hard postcondition não medida nunca pode produzir ACCEPTED.

I3 — missing/unknown não satisfaz hard precondition.

I4 — rollback continua abaixo do Cortex.

I5 — runtime F1 permanece imutável.

I6 — seeds 20261101–20261110 permanecem holdout.

I7 — curriculum_runner permanece disponível como baseline até Exit Gate F2.

## 19. Verificação F2-A

tests/test_cortex_actions.py verifica:

- evidence missing/invalid;
- serialization e provenance;
- cobertura shadow das seis action families requeridas;
- zero world mutation sob SHADOW;
- hard UNKNOWN precondition refusal;
- named no-binding refusal;
- ACCEPTED exige EXECUTE;
- ACCEPTED exige hard postconditions SATISFIED;
- RepairAction estrutural vira ActionRequest e permanece shadowed.

O regression gate inclui repair_loop, TransactionalFLEExecutor e evidence schema.

## 20. Decisão F2-A

F2-A estabelece a vocabulary tipada e o authority boundary. Não fecha F2.

Concluído:

- primitive action schemas;
- ActionRequest / ActionResult / Refusal / EvidenceRef;
- shadow facade das seis families requeridas;
- contratos pre/postcondition;
- provenance;
- refusals universais nomeadas.

Pendente:

- adapters transacionais concretos;
- live execution genérica;
- options iniciais;
- functional chain sem curriculum_runner;
- runner legado apenas como baseline.

## 21. Próximo checkpoint F2-B

Implementar adapters transacionais nesta ordem:

1. resupply;
2. placement power-tap;
3. placement processing-cell construction;
4. delivery;
5. craft;
6. research;
7. rebuild.

Resupply e power-tap já possuem handlers legados validados. São o caminho de menor risco para testar parity.

Depois vem place_processing_for_buffered_output, exatamente o counterexample estrutural replicado em F1.

F2-B começa com parity/shadow tests. Nenhuma autonomous live authority será ativada nesse primeiro bloco.

## 22. Checkpoint versionado

Implementation commit: 6c50e3bc90505bb27d74431a8f01ac06d42ed9e5

Gate: 68 focused tests PASS; 1318 core/FLE PASS; 2 PyTorch PASS; Ruff/static/frontend build PASS.

Decision: F2-A PASS parcial. F2 permanece aberta e sem nova live authority.
