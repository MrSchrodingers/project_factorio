# Arquitetura experimental

## 1. Princípio

Factorio é majoritariamente determinístico e possui estado fortemente estruturado. Portanto,
aprender um world model neural completo desde o início desperdiça informação conhecida e
dificulta atribuição causal.

A ordem proposta é:

1. modelo explícito das regras conhecidas;
2. modelo residual aprendido apenas para diferenças entre abstração e Factorio real;
3. política neural para decisões onde busca exata fica cara;
4. LLM para decomposição semântica e síntese de programas.

## 2. Camadas

### Orchestrator

Converte metas como "automatizar green circuits" em DAG de subobjetivos, requisitos e
invariantes. O LLM local pode sugerir o DAG, mas um verificador tipado valida entidades,
receitas, dependências e precondições antes da execução.

### Production optimizer

Para receitas j e itens i, definimos matriz estequiométrica S_ij. A variável x_j representa
taxa de execução da receita. O solver deve maximizar produção ou minimizar custo sujeito a
capacidades de belts, inserters e máquinas. Começamos com LP/HiGHS e migramos a MILP quando
placement discreto entrar no modelo.

### Spatial planner

Baseline: A* ponderado em grade. O custo alvo é

C = a L + b N_turn + c N_underground + d N_cross + e R_block + f C_expansion

onde R_block estima risco de bloquear corredores futuros.

### Transactional executor

Toda ação composta segue:

checkpoint -> propose -> validate -> execute -> verify -> commit

Em falha:

rollback -> classify failure -> repair proposal -> retry

Isso explora o resultado do FLE v0.2: backtracking a partir do estado pré-erro evita cadeias
longas de correções em estados parcialmente quebrados.

### Learned policy

Subproblemas graduais:

1. roteamento;
2. recipe/machine placement;
3. conexão multi-input;
4. compactação;
5. reparo local.

Baseline neural: CNN local + self-attention global + heads fatorizadas. Depois comparamos GNN
e transformer sobre grafo de entidades.

### World model

Inicialmente, W_explicit(s,a) -> s' é determinístico. Depois treinamos um residual de erro contra
FLE/Factorio. RNN/GRU ou RSSM só entra se existir dependência temporal não capturada pelo estado.

## 3. Observabilidade

Cada episódio registra:

- seed e versão do ambiente;
- commit SHA;
- modelo, quantização e parâmetros;
- planner/solver e hiperparâmetros;
- ações propostas/aceitas/rejeitadas;
- checkpoints/rollbacks;
- latência por componente;
- tokens e tokens/s;
- vetor completo de objetivos;
- score escalar de treino;
- layout e replay.

SQLite/Parquet bastam inicialmente; MLflow local entra nos sweeps.

## 4. Separação treino/ground truth

O simulador rápido serve para gerar dados e treinar. FLE/Factorio serve como ground truth.
Melhoria no simulador só é aceita após replay/parity test no jogo.
