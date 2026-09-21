# Research baseline

## FLE

O Factorio Learning Environment fornece interface Gym, ações por Python sintetizado, observações
estruturadas, lab-play, open-play, checkpoints, histórico, multi-agent e MCP.

Uso planejado: benchmark e fonte de verdade.

Referências:
- https://github.com/JackHopkins/factorio-learning-environment
- https://proceedings.neurips.cc/paper_files/paper/2025/hash/75f3a62e55187bc3e5e5961fa2a46bcf-Abstract-Datasets_and_Benchmarks_Track.html

## Factorion

Em 2026 o Factorion combina simulador Rust rápido, lessons procedurais, SFT, PPO, CNN +
self-attention e action masking. O limite atual é central para nossa hipótese: reconstruir
demonstrações está muito à frente de criar fábricas do zero. Isso motiva planner simbólico +
demonstrações procedurais + RL, não RL esparso puro.

Referência:
- https://github.com/beyarkay/factorion

## Inferência local no host atual

Hardware observado em 2026-09-21:

- Intel Core i7-10700, 8C/16T;
- 15 GiB RAM;
- AVX2, sem AVX-512;
- sem GPU NVIDIA moderna utilizável;
- Docker disponível.

Baseline:

1. llama.cpp;
2. Qwen3-4B GGUF Q4_K_M;
3. contexto 8k-16k para controle e memória externa estruturada;
4. geração estruturada por schema quando possível;
5. Nemotron Nano 9B v2 quantizado apenas como comparação.

vLLM CPU suporta x86, mas neste host sem AVX-512 não é a escolha inicial.

## Router

Há duas camadas:

1. task router: A*, LP/MILP, política neural ou LLM;
2. model router: seleciona modelo apenas dentro das tarefas LLM.

O task router determinístico é o baseline. LiteLLM ou vLLM Semantic Router pode entrar depois.
