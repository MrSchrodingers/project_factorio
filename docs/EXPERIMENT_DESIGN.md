# Protocolo quantitativo de experimentos

## Objetivo científico

O projeto deve distinguir melhoria real de overfitting a seeds, reward hacking ou aumento
bruto de compute. Cada mudança de agente será avaliada em tarefas pareadas e seeds congeladas.

## Vetor de objetivos

Para episódio e, mantemos um vetor de métricas:

J(e) = [sucesso, throughput, custo_material, area, energia, comprimento_rotas,
        curvas, falhas, rollbacks, deadlocks, latencia, compute]

O score escalar usado por um algoritmo de treino nunca substitui esse vetor na avaliação.
Quando objetivos competirem, relatamos fronteira de Pareto e hypervolume.

## Métricas primárias

- task success rate;
- throughput do item-alvo por minuto;
- throughput por custo de entidade;
- footprint ocupado e bounding-box;
- material e energia por unidade produzida;
- comprimento total de belts e número de curvas;
- número de entidades, underground belts e cruzamentos;
- ações inválidas, exceções, rollbacks e deadlocks;
- tempo simulado até o objetivo;
- wall-clock por decisão e por episódio;
- para LLM: tokens de entrada/saída, tokens/s, contexto e memória residente.

## Protocolo estatístico

1. Geradores procedurais recebem seeds explícitas.
2. Conjuntos train, validation e test não compartilham seeds.
3. Comparações de algoritmos usam a mesma seed sempre que possível.
4. Baseline padrão: pelo menos 30 seeds de teste para experimentos rápidos.
5. Resultados finais usam média, mediana, desvio e IC 95% por bootstrap.
6. Para diferença entre versões, usar distribuição pareada delta por seed.
7. Hiperparâmetros são escolhidos somente no conjunto de validação.
8. O conjunto de teste fica congelado até a comparação final.

## Hiperparâmetros por subsistema

### Roteamento

- alpha: custo por tile;
- beta: penalidade por curva;
- gamma: underground belt;
- delta: cruzamento/interferência;
- epsilon: risco de bloquear expansão;
- zeta: proximidade a infraestrutura compartilhada;
- peso heurístico, quando deliberadamente usarmos Weighted A*.

O benchmark inicial varre beta em mapas com obstáculos e registra comprimento, curvas,
nós expandidos, sucesso e wall-clock.

### Otimização de produção

- margem de capacidade;
- penalidade por máquina;
- penalidade por energia;
- reserva de belt;
- valor de espaço futuro;
- integrality gap e timeout do solver.

### Política neural

- arquitetura: CNN, CNN + attention, GNN;
- embedding dimension;
- número de heads/camadas;
- learning rate e scheduler;
- batch size;
- entropy coefficient;
- PPO clip;
- gamma e GAE lambda;
- número de passos por rollout;
- curriculum threshold.

### LLM local

- modelo e hash;
- quantização;
- context length;
- temperature/top-p/top-k;
- max tokens;
- número de tentativas;
- budget de self-critique;
- limite de wall-clock;
- tamanho da memória recuperada.

O LLM não recebe liberdade para alterar esses valores durante um benchmark. Auto-tuning é um
experimento separado.

## Sistemas dinâmicos e controle

Inventários e buffers podem ser modelados por estado discreto:

x[t+1] = x[t] + Delta t (S r[t] + B u[t] - d[t])

onde S é a matriz estequiométrica, r as taxas internas, u os controles e d a demanda.
Isso permite medir estabilidade de buffers, saturação, starvation e aplicar MPC quando o
problema for realmente temporal.

RNN/GRU/RSSM só entram para modelar resíduos ou informação parcialmente observável. Não há
vantagem científica em reaprender por rede neural uma dinâmica determinística que já sabemos
escrever exatamente.
