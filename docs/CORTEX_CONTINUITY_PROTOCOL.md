# Cortex Research — Protocolo de Continuidade e Retomada

**Objetivo:** tornar a execução científica recuperável após timeout, perda de conexão, reinício do chat ou interrupção do operador, sem duplicar seeds, alterar o runtime sob teste ou depender da UI como fonte de verdade.

## 1. Princípio

A continuidade é derivada de artefatos persistentes no host. O chat não é o estado da pesquisa.

As fontes canônicas, em ordem, são:

1. configs/cortex_baseline_v1.json para seeds e regras congeladas;
2. baseline_runs/<protocol>/<mode>/<seed>/manifest.json para lifecycle da seed;
3. result.json e generation_reports para resultado congelado;
4. runs/cortex_phase_state.json para resumo reconstruível da fase;
5. BUILD_INFO.json do runtime científico e do dashboard;
6. docs/CORTEX_HANDOFF.md e docs/CORTEX_RESEARCH_PROGRAM.md para decisão e checklist;
7. /api/context apenas como projeção de observabilidade, nunca como autoridade de execução.

## 2. Runtime científico vs dashboard

O runtime científico F1-B permanece fixo em:

    95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac

O dashboard possui runtime próprio e pode avançar independentemente.

Uma mudança de frontend, documentação, launcher ou ferramenta de análise não muda o sistema sob teste enquanto o runner continua apontando explicitamente para a release científica congelada.

## 3. Estado legível por máquina

O script:

    scripts/cortex_phase_state.py --write

reconstrói o estado da fase a partir dos manifests e results e grava:

    runs/cortex_phase_state.json

Campos críticos:

- baseline_release_commits;
- baseline_release_consistent;
- global_champion_exists;
- modes.exploratory.valid_completed;
- modes.exploratory.running;
- modes.exploratory.pending;
- modes.exploratory.next_seed;
- resume.action;
- resume.do_not_start_another_seed.

Se uma seed estiver running, next_seed é null e do_not_start_another_seed=true.

## 4. Launcher detached

O script:

    scripts/launch_corrected_baseline_seed.py

inicia uma seed em nova sessão de processo, com stdin desligado e stdout/stderr persistidos em runs/launchers. Assim, a execução não depende da conexão SentinelX nem da vida do processo do chat.

O launcher exige explicitamente:

- seed;
- mode;
- release-root;
- expected-commit.

Ele recusa:

- release dirty;
- SHA diferente do esperado;
- SHA ou release-root diferentes do pin declarado no protocolo;
- seed fora do protocolo congelado;
- qualquer outra seed baseline ainda marcada como running;
- sandbox já contendo evidência;
- champion global reaparecido durante F1-B;
- factorio-ai-evolution ativo;
- runner ausente.
- armazenamento insuficiente em /var (mínimo padrão: 1 GiB livre).

Antes de criar o processo, o launcher grava automaticamente:

    runs/audits/baseline_<seed>_global_isolation_before.json

Esse snapshot contém SHA-256, tamanho e mtime dos artefatos globais críticos e confirma que
evolution_champion.json continua ausente. A validação pós-run compara contra esse snapshot.

O launcher também grava storage_preflight no record persistente e recusa a seed antes da
execução quando /var não possui o headroom mínimo.

Exemplo F1-B:

    python scripts/launch_corrected_baseline_seed.py \
      --seed 20261003 \
      --mode exploratory \
      --release-root /srv/factorio-ai-runtime/releases/95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac \
      --expected-commit 95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac

## 5. Algoritmo de retomada

Após qualquer interrupção:

1. não iniciar nova seed;
2. executar git status no source checkout;
3. ler BUILD_INFO do runtime científico;
4. ler BUILD_INFO do dashboard;
5. verificar factorio-ai-evolution;
6. executar scripts/cortex_phase_state.py --write;
7. se running não estiver vazio, monitorar apenas essa seed;
8. se running estiver vazio, validar o último result antes de usar next_seed;
9. conferir /api/context contra o machine-readable phase state;
10. atualizar docs/checklists somente após VALIDITY=PASS e ISOLATION=PASS;
11. iniciar no máximo uma próxima seed.

## 6. Regra anti-duplicação

Nunca interpretar timeout de uma chamada remota como falha do processo científico.

Antes de repetir qualquer comando de execução, verificar:

- manifest da seed;
- processo curriculum_runner;
- launcher record;
- runtime_heartbeat;
- result.json.

Se manifest=running ou o processo existir, a única ação permitida é observar.

## 7. Regra de snapshot vs mundo vivo

result.json e generation report são snapshots científicos congelados.

/api/world e o mapa são o mundo Factorio vivo. Depois que uma seed termina, o jogo continua tickando; combustível, buffers, produção e status de máquinas podem mudar sem que o resultado experimental mude.

Por isso a UI deve sempre distinguir:

    EVIDENCE = frozen seed evidence
    WORLD = live RCON

Uma divergência temporal entre ambos não é automaticamente corrupção.

## 8. Checkpoint mínimo antes de autorizar próxima seed

- [ ] manifest completed;
- [ ] returncode = 0;
- [ ] code_revision.commit = frozen release;
- [ ] code_revision.dirty = false;
- [ ] measurement_protocol presente;
- [ ] unclassified_rate_keys vazio;
- [ ] global isolation hashes sem alteração;
- [ ] global champion ausente;
- [ ] diagnóstico do bottleneck registrado;
- [ ] analyzer agregado reexecutado;
- [ ] seed marcada no protocolo;
- [ ] README, research program e handoff atualizados;
- [ ] phase state regenerado;
- [ ] commit e push do checkpoint;
- [ ] dashboard deployado somente se houve mudança de observabilidade.

## 9. Estado atual

Seeds exploratórias válidas: 20261001, 20261002 e 20261003.

Próxima seed autorizável após fechamento deste checkpoint: 20261004.

As três seeds concluídas falharam em Logistic science com logistic_science_output=0 e closed_loop_autonomy=false. O padrão estrutural se repete; a severidade de fuel starvation varia entre seeds.

Storage hardening: docs/CORTEX_F1_STORAGE_HARDENING.md.
