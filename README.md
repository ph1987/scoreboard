# retroplacar.com

![print-v0](print-v0.png)

Placar ao vivo do futebol brasileiro, com visual retrô inspirado na tela de resultados do Elifoot 98. Pensado para ser usado como página pública ou como overlay/Browser Source no OBS.

## Competições

- Brasileirão Série A (pontos corridos — exibe a rodada atual)
- Copa do Brasil (mata-mata — exibe a fase atual)
- Libertadores (mata-mata — exibe a fase atual)
- Sul-Americana (mata-mata — exibe a fase atual)

## Quando cada partida aparece

O board mostra só o que é relevante agora:

- partidas em andamento vêm sempre no topo
- partidas agendadas entram quando falta no máximo **1 dia** para o início
- partidas encerradas saem **1 hora** depois do apito final

Como as odds só existem para o Brasileirão, elas ficam invisíveis entre rodadas — voltam quando a próxima rodada entra na janela. As constantes ficam em `state.py`.

## Funcionalidades

- Placar e eventos (gols e cartões vermelhos, com jogador e minuto) de todas as partidas em cartaz
- Escudos dos times, baixados uma vez e servidos localmente
- Atualização automática a cada 30 segundos
- Filtro para mostrar apenas partidas em andamento
- Alerta sonoro + toast com o lance (escudo, jogador e minuto) ao sair um gol; o toast fecha no X ou sozinho em 1 minuto, e o botão ALERTAS liga/desliga os dois (preferência salva em cookie)
- Odds da Betano e da Betnacional para os próximos jogos e partidas em andamento do Brasileirão

## Como rodar localmente

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Depois acesse http://127.0.0.1:8000

## Estrutura

```
scoreboard/
├── main.py             # FastAPI: inicia o scraper em background + serve API e estáticos
├── scraper.py          # busca e normaliza os dados do ge.globo.com (as 3 competições)
├── odds.py             # busca odds na betano.bet.br e na betnacional.bet.br, casando os times com os do ge.globo
├── parsing.py          # helper compartilhado p/ extrair JSON embutido em HTML
├── state.py            # estado compartilhado em memória (snapshot das competições)
├── deploy/             # setup.sh, unit do systemd e Caddyfile p/ subir numa VM
├── static/             # frontend (HTML/CSS/JS puro)
│   └── vendor/         # Toastify (MIT), servido localmente em vez de CDN
└── uploads/            # arquivos servidos publicamente (áudio do alerta, escudos, fonte)
```

A fonte do título é a Press Start 2P (OFL) e o toast usa o Toastify (MIT); ambos são servidos do próprio projeto, sem depender de CDN para a página renderizar.

Os escudos em `uploads/escudos/` são baixados automaticamente pelo scraper na primeira vez que cada time aparece, por isso não fazem parte do repositório.

## Fonte de dados

Placar e eventos vêm do ge.globo: [Brasileirão](https://ge.globo.com/futebol/brasileirao-serie-a/), [Copa do Brasil](https://ge.globo.com/futebol/copa-do-brasil/) e [Libertadores](https://ge.globo.com/futebol/libertadores/). As odds vêm do [betano.bet.br](https://www.betano.bet.br/sport/futebol/brasil/brasileirao-serie-a-betano/10016/) e do [betnacional.bet.br](https://betnacional.bet.br/apostas-brasileirao-serie-a). Todas sem autenticação ou chave de API.

A fonte trata os dois formatos de competição de formas diferentes: pontos corridos entregam os jogos numa lista plana (`lista_jogos`), enquanto o mata-mata os espalha em `secao → chave → jogos` e traz data e horário em campos separados. O scraper normaliza os dois no mesmo formato.

## Deploy

Pensado para hospedagem gratuita/hobby em serviços como Railway ou Render — processo único, sem banco de dados, leitura pública sem autenticação. Para subir numa VM própria (EC2, Vultr etc.), os scripts em `deploy/` provisionam a máquina com systemd e Caddy (HTTPS automático).
