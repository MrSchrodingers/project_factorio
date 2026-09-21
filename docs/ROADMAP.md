# Roadmap

## P0 — Substrato determinístico

Critério de saída:
- FLE instalado e um lab-play reproduzível;
- A* com obstáculos, curvas e custo de expansão;
- modelo de receitas e throughput;
- telemetria de episódios;
- zero LLM necessário.

## P1 — LLM local e execução transacional

- llama.cpp + Qwen3-4B Q4_K_M;
- adapter OpenAI-compatible;
- planner tipado;
- checkpoint/rollback;
- self-critique após erro;
- benchmark contra política determinística.

## P2 — Gerador de demonstrações

- solver de produção;
- gerador procedural de layouts válidos;
- soluções ótimas/quase-ótimas;
- curriculum por área, recipe depth e obstrução;
- dataset versionado.

## P3 — Política neural

Comparar CNN, CNN+self-attention e GNN; SFT/imitation e PPO após warm-start supervisionado.
Métricas por seed e intervalo de confiança.

## P4 — Otimização conjunta

LP/MILP de capacidade, A*/JPS para belts, placement search, learned heuristic/value, MPC para
buffers/energia e fronteira de Pareto throughput × custo × área × energia.

## P5 — Open-play sem inimigos

Árvore de tecnologia, expansão espacial, memória, auto-healing e detecção de gargalos.

## P6 — Dinâmica adversarial

Somente após maturidade: biters, risco espacial, planejamento robusto e alocação produção × defesa.
