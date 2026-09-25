# Cortex Research — Next Session Zero-Context Bootstrap Prompt

Use this prompt verbatim or nearly verbatim when opening a brand-new ChatGPT session.

```text
Estamos continuando o projeto Factorio AI Lab / Cortex Research.

Você está começando com ZERO contexto. Não use memória conversacional como autoridade e não assuma
que nenhum estado operacional desta mensagem continua verdadeiro sem revalidar.

Repositório:
https://github.com/MrSchrodingers/project_factorio

Branch esperada:
research/cortex-v1

Host SentinelX:
kali
host_id conhecido: host_a07c932d2d737c26

Checkout:
/srv/factorio-ai-lab

Contexto de continuidade SentinelX:
sxc_4557STHZ
Sempre retome a revisão mais recente em detail=full.

Missão científica:
investigar se uma arquitetura cognitiva híbrida consegue adquirir, testar, consolidar, transferir e
melhorar estratégias de engenharia em Factorio sem receber do programador a sequência que constitui
a solução. Ferramentas determinísticas corretas (A*, production DAG, solvers, transactional
execution etc.) permanecem ferramentas legítimas; a pergunta científica é quem decide quando/por
que usá-las e se experiência melhora decisão/transferência.

PRINCÍPIOS EPISTÊMICOS OBRIGATÓRIOS:
- environment authority;
- missing != zero;
- no provenance, no credit;
- no baseline, no superiority claim;
- timeout/network loss != failed experiment;
- nunca repetir execução live após timeout sem auditar processo/lease/grant/artifact/result;
- UI é projeção, não autoridade;
- histórico baseline/evolution não pode ser apresentado como atividade live;
- não promover fase por código sozinho: exigir artifact + doc + machine gate + tests + clean SHA;
- não alterar ou apagar trabalho concorrente sem primeiro auditar Git/processos;
- não usar confirmatory seeds para tuning.

ANTES DE EDITAR OU EXECUTAR QUALQUER EXPERIMENTO:

1. Use SentinelX para retomar sxc_4557STHZ em detail=full.
2. Revalide o host e /srv/factorio-ai-lab.
3. Verifique git status/HEAD/origin e os últimos commits/tags.
4. Regenere:
   sudo -u ti env PYTHONPATH=src .venv-fle/bin/python scripts/cortex_phase_state.py --write
5. Leia runs/cortex_phase_state.json.
6. Leia BUILD_INFO do runtime científico e do dashboard.
7. Verifique dashboard/LLM/evolution active+enabled.
8. Verifique processos curriculum_runner/open_play_runner/evolution_loop/run_cortex*.
9. Consulte /api/context, /api/world, /api/factory-graph, /api/evolution, /api/research e /ws/live.
10. Use GitHub para confirmar o estado publicado do branch/tag correspondente ao host.

LEIA NESTA ORDEM:
1. docs/CORTEX_ZERO_CONTEXT_ROADMAP_HANDOFF.md
2. docs/CORTEX_RESEARCH_PROGRAM.md
3. docs/CORTEX_HANDOFF.md
4. docs/CORTEX_PHASE4_MEMORY_SUBSTRATE.md
5. docs/CORTEX_PHASE4_MEMORY_RETRIEVAL.md
6. docs/CORTEX_PHASE4_CAUSAL_ABLATION_PROTOCOL.md
7. docs/CORTEX_DASHBOARD_EVIDENCE_SCOPE.md
8. docs/CORTEX_CONTINUITY_PROTOCOL.md
9. F3 docs se precisar reconstruir a cadeia executiva.
10. F2 authority docs somente antes de qualquer discussão futura de live execution.

ESTADO ESPERADO DO HANDOFF, QUE DEVE SER REVALIDADO:
- F0 PASS;
- F1 PASS;
- F2 COMPLETE / G5;
- F3 COMPLETE / F3-C;
- F4 ACTIVE / F4-B SHADOW;
- F4-A typed memory substrate PASS;
- F4-B hybrid retrieval + consolidation + non-destructive decay PASS;
- F4 Exit Gate ainda NÃO satisfeito;
- next checkpoint = F4-C;
- F4-C bloqueado até congelar benchmark de transferência não-confirmatório, diverso e held-out;
- continuous autonomous authority OFF;
- factorio-ai-evolution inactive+disabled;
- confirmatory seeds 20261101–20261110 intactas/pending;
- resume.do_not_start_another_seed=true;
- última mutação live Cortex continua F2-G4B e NÃO deve ser repetida;
- WORLD live pode estar conectado com 0 entidades; isso é estado físico observado;
- G97/curriculum/UCB/model metrics são evidência histórica congelada se não houver runner ativo.

F4-B EVIDENCE ESPERADA:
- implementation: a4ff558eccd9df0898fef4138a75fa1b55976a8d
- closure: 9a23b661c86ebd2eddb37eec2e1f3d8198c8b9c3
- artifact: runs/audits/cortex_f4b_memory_retrieval.json
- artifact SHA-256: 5fc37b0cee5f121c5ff6b6054fc45f4b0a09bad851e34793958bdd3dc1c5a801
- DB: 222 items / 759 occurrences / quick_check=ok
- DB manifest before=after:
  e6aa69816fe992f6b2a6afc8aff529fa5f1572106ca0939cee830af5a6cf3399
- full gate F4-B: 1520 core/FLE + 2 PyTorch PASS.

UI/STATUS HARDENING ESPERADO:
- implementation: 0c94090f91ad43c1a3a47a6bec25f377f9e56e00
- UI deve mostrar CORTEX SHADOW / idle intentional quando nenhum runner existe;
- evolution/G97/curriculum/UCB/timeline devem estar HISTÓRICO/FROZEN;
- mapa WORLD LIVE vazio deve dizer que 0 entidades não é renderer travado;
- nunca preencher o mapa live com fábrica histórica sintética.

F4-C / RQ1:
A pergunta agora é causal: memória melhora transferência entre seeds/worlds?

Auditoria preliminar:
- corrected exploratory seeds 20261001–20261005 são insuficientemente diversos;
- repairs repetem quase somente dois symptom/action families;
- cada seed tem uma única spatial demo;
- todas têm mesmo start/goal e route_cost=8.25;
- corpus global: 85 counterexamples / 33 signatures / 52 recorrências posteriores;
- 85 runs não equivalem a 85 independent seeds.

PRÓXIMO TRABALHO AUTORIZADO:
F4-C protocol design / preregistration.

Você deve:
1. auditar novamente a elegibilidade dos dados;
2. definir source-memory vs held-out evaluation seeds/worlds;
3. definir pares MEMORY ON vs MEMORY ABLATED no mesmo world/checkpoint;
4. bloquear leakage temporal/cross-seed;
5. manter tools/action surface/compute budgets equivalentes;
6. congelar task families e critérios de inclusão/exclusão;
7. definir primary endpoint J, effect size, CI e paired statistical test antes de avaliar;
8. definir pilot não-confirmatório se necessário para variância/sample size;
9. gerar protocol manifest imutável e testes;
10. somente depois considerar novos non-confirmatory evaluation seeds.

NÃO:
- habilite evolution;
- use 20261101–20261110 para tuning/pilot;
- conceda continuous authority;
- rode live Cortex canary só para movimentar a UI;
- trate lookup repetido como cross-seed transfer;
- trate 85 runs como 85 independent seeds;
- converta missing/unmeasured em zero;
- declare F4 COMPLETE sem paired causal evidence.

NO PRIMEIRO TURNO:
entregue reconstrução curta porém rigorosa de Git/runtime/services/phase/artifacts/confirmatory/WORLD
vs CURRENT vs HISTORICAL, divergências, riscos e plano F4-C. Se tudo estiver consistente, prossiga no
mesmo trabalho para preparar o protocolo F4-C sem pedir confirmações desnecessárias. Pare antes de
executar seeds caso o protocolo ainda não esteja formalmente congelado e elegível.

Ao fechar checkpoint:
focused tests -> full core/FLE -> PyTorch -> Ruff -> compileall -> JS -> TS/Vite -> whitespace ->
clean SHA -> canonical artifact -> phase-state -> docs -> push/tag -> dashboard deploy se aplicável ->
API/WebSocket verification -> evolution/confirmatory recheck -> SentinelX context save.
```
