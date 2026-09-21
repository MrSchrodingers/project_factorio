# FLE runtime local

## Versões validadas

- Factorio Learning Environment: 0.4.3
- Factorio headless: 2.0.73
- Python do FLE: 3.12
- a2a-sdk: >=0.3.26,<1

A release FLE 0.4.3 declara a2a-sdk sem limite superior. A linha 1.x removeu tipos
usados pela release, incluindo TextPart; por isso o laboratório fixa a família 0.3.x.

## Isolamento

O FLE roda em .venv-fle, separado do ambiente do laboratório. O setup usa .tmp/
como diretório temporário porque o host possui uma partição /tmp pequena.

Comando:

    ./scripts/setup_fle.sh

## Cluster local seguro

O launcher do projeto usa o gerador de Compose do próprio FLE e altera apenas o bind
das portas para loopback:

- RCON: 127.0.0.1:27000 -> 27015/tcp
- Factorio: 127.0.0.1:34197 -> 34197/udp

Isso é importante porque o FLE 0.4.3 usa a senha RCON padrão factorio.

Comandos:

    .venv-fle/bin/python scripts/fle_cluster_local.py start -n 1
    .venv-fle/bin/python scripts/fle_cluster_local.py status
    .venv-fle/bin/python scripts/fle_cluster_local.py stop

## Smoke transacional

    PYTHONPATH=src .venv-fle/bin/python scripts/smoke_fle.py

O smoke executa uma ação aceita, guarda output_game_state, executa uma ação
deliberadamente rejeitada e confirma que o executor restaurou o último checkpoint
aceito.

## Warnings upstream conhecidos

FLE 0.4.3 ainda usa gym==0.26.2, que emite aviso com NumPy 2.x e avisos de
observation-space. Não promovemos isso a erro enquanto o contrato reset/step continuar
passando nos testes de integração. Visão fica desativada até que sprites sejam necessários.

## Autenticação e token do Factorio

O laboratório usa o pacote Linux headless disponibilizado para servidores. Para execução local,
RCON e tarefas FLE não é necessário token da conta pessoal do Factorio. O container atual não
possui username, password ou token de conta configurados.

Credenciais de serviço passam a ser relevantes em operações online específicas, por exemplo
publicação no matchmaking público ou sincronização/download autenticado de mods. Esses recursos
não fazem parte do baseline e nenhum token pessoal deve ser versionado no repositório.

## Compatibilidade de movimento fast

No conjunto FLE 0.4.3 + Factorio 2.0.73, request_path/get_path apresentou timeout mesmo para
movimentos curtos. O projeto mantém um compatibility shim limitado ao modo experimental fast:
ele reposiciona storage.agent_characters[N] diretamente e registra o deslocamento como custo
experimental. Validação geométrica continua sendo responsabilidade do planner/validator.

