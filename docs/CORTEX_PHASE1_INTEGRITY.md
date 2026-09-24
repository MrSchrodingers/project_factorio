# Cortex Research — Fase 1A: Integridade, Provenance e Isolamento Operacional

**Status:** PASS para F1-A.
**Fase maior:** F1 — Instrumentação, isolamento e baseline corrigida.
**Escopo:** pré-condições científicas antes de retirar G37 e iniciar a baseline corrigida multi-seed.

## 1. Motivação

A arquitetura pré-Cortex possuía boa telemetria e vários gates, mas /srv/factorio-ai-lab acumulava três papéis incompatíveis: checkout de desenvolvimento, origem dos módulos executados pelos serviços e estado mutável dos experimentos.

Isso permitia que uma geração começasse sob um estado de código e terminasse depois de alterações locais. O defeito é científico: sem identidade imutável do executável, uma melhora não pode ser atribuída com rigor ao agente, ao algoritmo ou ao programador.

A F1-A redefine o runtime para que cada geração tenha uma identidade de código imutável e um estado mutável explicitamente separado.

## 2. Linha de base histórica preservada

Antes de qualquer limpeza, factorio-ai-evolution foi interrompido deliberadamente e foi criado um snapshot consistente em:

    /srv/factorio-ai-lab/backups/cortex-f1-pre-20260924T031901Z

O backup inclui runs, documentação relevante, manifest, hashes e a working tree pré-existente.

As alterações logísticas anteriores ao Cortex foram qualificadas separadamente:

- 80 testes direcionados: PASS;
- gate core/FLE completo: 1261 passed, 1 skipped;
- gate PyTorch: 2 passed;
- Ruff: PASS;
- compileall: PASS.

Elas foram commitadas separadamente como a3a50b514cdcc7f01025774638817f6215977680, com a mensagem fix: fecha distribuição física de minério e troncos logísticos. Portanto, não são atribuídas causalmente ao Cortex.

## 3. Isolamento CODE_ROOT x STATE_ROOT

O sistema agora distingue:

    CODE_ROOT  = código imutável da release
    STATE_ROOT = runs, datasets, checkpoints, modelos e estado persistente

A implementação central está em src/factorio_ai_lab/paths.py.

Em desenvolvimento, os dois roots podem coincidir. Em produção, FACTORIO_AI_STATE_ROOT aponta para /srv/factorio-ai-lab, enquanto os módulos são importados da release ativa em /srv/factorio-ai-runtime/current.

### 3.1 Releases por SHA

scripts/deploy_runtime.sh:

1. recusa deploy se git status --porcelain não estiver vazio;
2. resolve um commit exato;
3. usa git archive para produzir uma árvore sem .git;
4. grava BUILD_INFO.json;
5. marca dirty=false somente porque a origem limpa foi verificada;
6. troca o symlink current atomicamente;
7. grava runs/deployment.json com SHA, branch, release_path e timestamp.

A release implantada durante esta fase foi:

    /srv/factorio-ai-runtime/releases/e5cd102a39b6bf0f8268c845ea0e59b7118a265c

BUILD_INFO.json registra commit e5cd102a39b6bf0f8268c845ea0e59b7118a265c, branch research/cortex-v1, dirty=false e schema factorio_ai_build_v1.

O commit f8437d5 implementou state root/provenance. O commit e5cd102 implementou deploy imutável e contrato systemd.

## 4. Bloqueio de promoção sem provenance

code_revision() consulta BUILD_INFO.json em releases e Git em checkout fonte.

revision_is_promotable() exige simultaneamente commit conhecido e dirty exatamente false.

Os serviços evolution/curriculum usam FACTORIO_AI_REQUIRE_CLEAN_PROMOTION=1. Uma decisão que seria promovida é convertida em retenção do incumbent quando a revisão não puder provar identidade limpa, registrando clean_code_revision_required.

Unknown nunca é convertido silenciosamente em clean.

## 5. Revisão por geração

Candidate records e generation reports carregam code_revision. Em release, a revisão vem de BUILD_INFO.json e continua atribuível mesmo sem .git.

Isso fecha o defeito histórico de G37: a geração 37 foi promovida sob código não commitado e seu registro original não carregava essa provenance.

## 6. Semântica epistemológica de medidas

Foi introduzido src/factorio_ai_lab/evidence.py.

EvidenceStatus possui cinco estados: observed, derived, estimated, missing e invalid.

EvidenceValue impede por construção que missing ou invalid carreguem um número plausível. Estimated pode carregar confiança explícita; require_observed() só aceita evidência observada.

A intenção é criar o contrato para todas as novas superfícies Cortex e migrar gradualmente métricas herdadas.

## 7. Auditoria de defaults numéricos

scripts/audit_numeric_defaults.py executa análise AST em src e enumera Mapping.get com fallback numérico e expressões or com fallback numérico.

A auditoria F1 após as correções críticas registrou:

- 487 ocorrências totais;
- 141 candidatos de maior risco.

Relatório versionado:

    docs/audits/NUMERIC_DEFAULT_AUDIT_F1.md

JSON exaustivo:

    runs/audits/numeric_defaults_f1.json

High-risk é triagem, não acusação automática de bug. Counters definidos como zero podem ser legítimos; medições não executadas ou inválidas não podem ser convertidas em zero.

### 7.1 Dois casos críticos corrigidos

A revisão encontrou dois casos no caminho decisório:

1. learning/autonomy.py tratava taxa ausente como taxa observada igual a zero;
2. learning/survival.py tratava taxa ausente no challenger como 0.0 ao testar retenção.

Agora:

- autonomia usa lógica tri-state; taxa ausente vira unknown quando nenhuma falha física já determina False;
- survival impede promoção quando a retenção de uma taxa positiva do incumbent não pode ser demonstrada por falta de medição.

Falha observada continua vencendo unknown. Unknown não vira sucesso e não vira uma medição inventada.

## 8. Perfis de teste reproduzíveis

O primeiro gate integral falhou na coleta porque .venv-fle não possui torch, enquanto dois testes PyTorch fazem parte da suíte. O ambiente .venv possui torch 2.14.0+cpu e dependências ML.

O contrato agora está codificado em scripts/test_profiles.sh.

Core/FLE:

    1283 passed

ML/PyTorch:

    2 passed

Ruff e compileall também passaram.

A diferença ambiental deixou de ser implícita e passou a ser parte do protocolo reprodutível.

## 9. Hardening do LLM local

factorio-ai-llm foi reiniciado com:

- contexto 4096 em vez de 8192;
- MemoryHigh=6G;
- MemoryMax=9G;
- MemorySwapMax=512M;
- TasksMax=64;
- Restart=on-failure.

Antes do hardening, o processo apresentava VmRSS ~8.29 GB, RssAnon ~7.72 GB e swap ~245 MB.

Após restart pela release imutável, a memória inicial observada ficou em ~4.63 GiB e os limites de cgroup passaram a ser efetivos.

### 9.1 Soak

Primeiro bloco, 8 chamadas: 4.631 GiB -> 4.664 GiB. O aumento inicial não foi declarado estabilidade.

Segundo bloco, 24 chamadas adicionais:

- início: 4774.94 MiB;
- fim: 4780.39 MiB;
- delta: 5.46 MiB;
- inclinação linear: 0.027 MiB/request;
- latência média: 0.645 s.

Interpretação: houve alocação inicial e depois forte redução da inclinação. O teste não prova ausência de retenção em horizontes longos, mas demonstra redução substancial do footprint e contenção por cgroup.

Artefatos:

    runs/audits/llm_memory_soak_f1.json
    runs/audits/llm_memory_soak_f1_extended.json

## 10. Restart e limites do Factorio

O container fle-local-factorio_0-1 já possuía RestartPolicy.Name=unless-stopped. Nenhuma alteração artificial foi feita apenas para marcar o checklist.

Também foram verificados image factoriotools/factorio:2.0.73, Memory=1 GiB, MemorySwap=2 GiB, NanoCpus=1 CPU e RCON em 127.0.0.1:27000.

## 11. Backup e reset versionados

Foram introduzidos scripts/backup_research_state.py e scripts/reset_selection_state.py.

O backup recusa cópia confirmatória enquanto factorio-ai-evolution estiver ativo, salvo override explícito. Ele produz manifest com SHA-256 e code_revision.

O reset é dry-run por padrão. Com --apply ele exige evolution inativo, move os artefatos de seleção para retirement, preserva histories/research/telemetry/knowledge/counterexamples/repairs/datasets/models e grava runs/baseline_reset.json.

O dry-run F1 detectou champion, lifelong sidecars, robustness/strategy, niche archive e loop state. Nenhum deles foi removido em F1-A.

## 12. Seeds congeladas da baseline

configs/cortex_baseline_v1.json define 5 seeds exploratórias (20261001–20261005) e 10 confirmatórias (20261101–20261110).

Seeds confirmatórias não podem ser substituídas depois do primeiro uso e não podem ser usadas para tuning. Release precisa ser limpa e G37 é excluída da baseline confirmatória.

## 13. Estado de G37

G37 ainda existe em runs/evolution_champion.json ao final de F1-A.

Isso é intencional. Ela não foi apagada antes de backup, mecanismo de retirement, seeds congeladas, release imutável e gates de provenance. F1-B fará o retirement controlado; o original permanecerá no backup.

## 14. Estado operacional ao fechar F1-A

- factorio-ai-evolution: inactive deliberadamente;
- factorio-ai-dashboard: active pela release imutável;
- factorio-ai-llm: active pela release imutável e cgroup hardening;
- Factorio container: running, restart unless-stopped;
- branch: research/cortex-v1.

## 15. Exit Gate F1-A

F1-A é PASS: lineage, isolamento, promoção fail-closed, schema epistemológico, auditoria de defaults, hardening do LLM, restart do Factorio, backup/reset e seeds estão definidos e testados.

F1 ainda NÃO terminou.

## 16. Próxima etapa: F1-B

A ordem obrigatória é:

1. commit/push/deploy desta instrumentação;
2. repetir reset dry-run;
3. aplicar retirement controlado de G37;
4. iniciar baseline usando release imutável;
5. executar seeds exploratórias do protocolo;
6. produzir relatório estatístico inicial;
7. decidir PASS/FAIL da F1 completa.

F2 permanece bloqueada até o Exit Gate completo de F1.
