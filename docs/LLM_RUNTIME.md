# Runtime de LLM local

## Baseline

Modelo:

- Qwen3-4B
- GGUF Q4_K_M
- arquivo local: /home/ti/models/qwen3-4b/Qwen3-4B-Q4_K_M.gguf
- SHA-256: 7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5

Runtime:

- llama.cpp commit c641dfa83338b717a63f2f3371cac0acee18b53b
- CPU: Intel i7-10700
- endpoint: http://127.0.0.1:18081/v1
- contexto: 8192
- threads: 12
- parallel slots: 1

A porta 8080 não é usada porque já pertence ao Caddy do host.

## Benchmark CPU

Medições com 512 tokens de prompt e 128 tokens de geração:

| Threads | Prompt processing tok/s | Generation tok/s |
| ---: | ---: | ---: |
| 4 | 51.96 | 11.72 |
| 8 | 53.81 | 11.28 |
| 12 | 68.88 | 12.76 |
| 16 | 67.60 | 9.48 |

Doze threads foram escolhidas como baseline. O uso dos 16 threads lógicos reduziu o throughput
de geração, indicando oversubscription/contensão no host.

## Router

O router aceita apenas providers classificados como local ou free.

Ordem inicial:

1. local-qwen: Qwen3-4B em llama.cpp.
2. openrouter-free: openrouter/free, somente quando OPENROUTER_API_KEY existe.
3. Planners determinísticos não passam pelo LLM quando o tipo do problema é conhecido.

Providers classificados como paid são rejeitados pelo router.

Para tarefas operacionais curtas o Qwen3 é chamado com enable_thinking=false. O primeiro teste
com thinking habilitado consumiu o orçamento de 80 tokens sem produzir conteúdo final; em modo
non-thinking o mesmo tipo de roteamento retornou a decisão astar normalmente.

Reasoning explícito será tratado como uma classe de rota separada, com orçamento próprio, quando
houver evidência de ganho de qualidade.
