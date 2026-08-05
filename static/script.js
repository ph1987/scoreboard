const INTERVALO_MS = 30000;
const CHAVE_COOKIE_ALERTAS = "scoreboard_alertas_ativos";
const DURACAO_TOAST_MS = 60000;

const ICONE_EVENTO = {
  gol: "⚽",
  cartao_vermelho: "🟥",
};

const LABEL_STATUS = {
  ao_vivo: "Ao vivo",
  encerrado: "Encerrado",
  agendado: "A começar",
};

let apenasAoVivo = false;
let alertasAtivos = lerPreferenciaAlertas();
let placaresVistos = null; // null = ainda não carregou nenhum dado (evita alertar na primeira carga)
let dadosAtuais = null;

function lerCookie(nome) {
  const match = document.cookie.match(new RegExp("(?:^|; )" + nome + "=([^;]*)"));
  return match ? decodeURIComponent(match[1]) : null;
}

function escreverCookie(nome, valor, dias) {
  const expira = new Date(Date.now() + dias * 24 * 60 * 60 * 1000).toUTCString();
  document.cookie = `${nome}=${encodeURIComponent(valor)}; expires=${expira}; path=/; SameSite=Lax`;
}

function lerPreferenciaAlertas() {
  // desligado por padrão: o navegador só libera áudio depois de uma interação,
  // então o alerta precisa nascer de um clique para realmente tocar
  const valor = lerCookie(CHAVE_COOKIE_ALERTAS);
  return valor === "1";
}

async function atualizarPlacar() {
  try {
    const resp = await fetch("/api/partidas");
    const dados = await resp.json();
    notificarNovidades(dados);

    // renderizar() recria os cards do zero; sem isso, a página voltaria pro topo a cada ciclo
    const posicaoScroll = window.scrollY;
    renderizar(dados);
    window.scrollTo(0, posicaoScroll);
  } catch (e) {
    console.error("Erro ao buscar partidas:", e);
  }
}

function chavePartida(competicao, partida) {
  // o mesmo confronto pode acontecer em duas competições (ex: Fluminense x Vasco
  // no Brasileirão e na Copa do Brasil), então a chave precisa da competição
  return [competicao.id, partida.time_casa, partida.time_fora].join("|");
}

function notificarNovidades(dados) {
  const placaresAtuais = new Map();
  const golsNovos = [];

  for (const competicao of dados.competicoes ?? []) {
    for (const partida of competicao.partidas ?? []) {
      const chave = chavePartida(competicao, partida);
      const casa = partida.placar_casa ?? 0;
      const fora = partida.placar_fora ?? 0;
      placaresAtuais.set(chave, { casa, fora });

      // o placar sobe assim que qualquer uma das duas fontes registra o gol,
      // enquanto o lance a lance (com jogador e minuto) costuma demorar mais.
      // Notificar pelo placar deixa o alerta bem mais rápido.
      const anterior = placaresVistos?.get(chave);
      if (!anterior) continue;

      // gol anulado pelo VAR faz o placar cair; só subida vira notificação
      if (casa > anterior.casa) {
        golsNovos.push({ partida, time: partida.time_casa, escudo: partida.escudo_casa });
      }
      if (fora > anterior.fora) {
        golsNovos.push({ partida, time: partida.time_fora, escudo: partida.escudo_fora });
      }
    }
  }

  if (golsNovos.length > 0 && alertasAtivos) {
    tocarAlerta();
    for (const gol of golsNovos) {
      mostrarToastGol(gol.partida, gol.time, gol.escudo);
    }
  }

  placaresVistos = placaresAtuais;
}

function criarToastConteudo(partida, time, escudo) {
  const conteudo = document.createElement("div");
  conteudo.className = "toast-gol";

  const placar = document.createElement("div");
  placar.className = "toast-gol-placar";
  placar.textContent = `${partida.time_casa} ${partida.placar_casa ?? 0} x ${partida.placar_fora ?? 0} ${partida.time_fora}`;

  // sem jogador nem minuto de propósito: eles só chegam com o lance a lance,
  // e esperar por eles atrasaria o alerta
  const linha = document.createElement("div");
  linha.className = "toast-gol-linha";

  const icone = document.createElement("span");
  icone.className = "evento-icone";
  icone.textContent = ICONE_EVENTO.gol;

  const texto = document.createElement("span");
  texto.className = "toast-gol-texto";
  texto.textContent = `Gol do ${time}`;

  const img = criarEscudo(escudo, time, "evento-escudo");

  linha.append(icone, texto, ...(img ? [img] : []));
  conteudo.append(placar, linha);
  return conteudo;
}

function deslocamentoToast() {
  // o toast tem que cair logo abaixo do cabeçalho, que muda de altura conforme
  // a largura da tela (no celular os botões quebram para uma segunda linha).
  // O Toastify posiciona em top:15px e aplica o offset por cima disso.
  const header = document.querySelector(".scoreboard-header");
  if (!header) return 0;
  return Math.max(0, header.getBoundingClientRect().height + 24 - 15);
}

function mostrarToastGol(partida, time, escudo) {
  Toastify({
    node: criarToastConteudo(partida, time, escudo),
    duration: DURACAO_TOAST_MS,
    close: true,
    gravity: "top",
    position: "center",
    stopOnFocus: true,
    className: "toast-retro",
    offset: { x: 0, y: deslocamentoToast() },
  }).showToast();
}

function tocarAlerta() {
  const audio = document.getElementById("som-alerta");
  audio.currentTime = 0;
  audio.play().catch(() => {
    // navegador pode bloquear autoplay antes de interação do usuário
  });
}

function liberarAudio() {
  // o navegador só autoriza play() programático depois de uma interação do
  // usuário. Tocar mudo dentro do próprio clique registra essa autorização,
  // senão o primeiro gol tentaria tocar e seria bloqueado silenciosamente.
  const audio = document.getElementById("som-alerta");
  audio.muted = true;
  audio
    .play()
    .then(() => {
      audio.pause();
      audio.currentTime = 0;
    })
    .catch(() => {})
    .finally(() => {
      audio.muted = false;
    });
}

function criarEscudo(url, alt, className) {
  if (!url) return null;
  const img = document.createElement("img");
  img.className = className;
  img.src = url;
  img.alt = alt ?? "";
  img.loading = "lazy";
  return img;
}

function criarEventoItem(evento) {
  const li = document.createElement("li");
  li.className = evento.tipo === "gol" ? "gol" : "cartao-vermelho";

  const icone = document.createElement("span");
  icone.className = "evento-icone";
  icone.textContent = ICONE_EVENTO[evento.tipo] ?? "";

  const jogador = document.createElement("span");
  jogador.className = "evento-jogador";
  jogador.textContent = evento.contra ? `${evento.jogador ?? ""} (contra)` : evento.jogador ?? "";

  const escudo = criarEscudo(evento.escudo_time, evento.time, "evento-escudo");

  const minuto = document.createElement("span");
  minuto.className = "evento-minuto";
  minuto.textContent = evento.minuto ?? "";

  li.append(icone, jogador, ...(escudo ? [escudo] : []), minuto);
  return li;
}

function faltaLanceDoGol(partida) {
  // o placar vem de duas fontes e sobe antes do lance a lance publicar o gol;
  // enquanto houver gol no placar sem evento correspondente, a partida está
  // esperando o detalhe (jogador e minuto) chegar
  if (partida.status !== "ao_vivo") return false;
  const golsNoPlacar = (partida.placar_casa ?? 0) + (partida.placar_fora ?? 0);
  const golsNarrados = (partida.eventos ?? []).filter((e) => e.tipo === "gol").length;
  return golsNarrados < golsNoPlacar;
}

function criarItemAguardando() {
  const li = document.createElement("li");
  li.className = "evento-aguardando";
  // sem rótulo: o giro já comunica que falta o lance chegar
  li.setAttribute("aria-label", "aguardando o lance do gol");

  const spinner = document.createElement("span");
  spinner.className = "spinner-pixel";

  li.appendChild(spinner);
  return li;
}

function criarOddItem(rotulo, valor) {
  const item = document.createElement("span");
  item.className = "partida-odds-item";

  const spanRotulo = document.createElement("span");
  spanRotulo.className = "partida-odds-rotulo";
  spanRotulo.textContent = rotulo;

  const spanValor = document.createElement("span");
  spanValor.className = "partida-odds-valor";
  spanValor.textContent = typeof valor === "number" ? valor.toFixed(2) : "-";

  item.append(spanRotulo, spanValor);
  return item;
}

function criarOddsLinha(odds) {
  const container = document.createElement("div");
  container.className = "partida-odds";

  const casaDeApostas = document.createElement("span");
  casaDeApostas.className = "partida-odds-casa";
  casaDeApostas.textContent = odds.casa_de_apostas;

  container.append(
    casaDeApostas,
    criarOddItem("1", odds.casa),
    criarOddItem("X", odds.empate),
    criarOddItem("2", odds.fora)
  );

  return container;
}

function criarPartidaCard(partida) {
  const card = document.createElement("div");
  card.className = `partida partida--${partida.status}`;

  const linha = document.createElement("div");
  linha.className = "partida-linha";

  const escudoCasa = criarEscudo(partida.escudo_casa, partida.time_casa, "partida-escudo");

  const timeCasa = document.createElement("span");
  timeCasa.className = "partida-time partida-time--casa";
  timeCasa.textContent = partida.time_casa;

  const placarCasa = document.createElement("span");
  placarCasa.className = "partida-placar";
  placarCasa.textContent = partida.placar_casa ?? "-";

  const versus = document.createElement("span");
  versus.className = "partida-versus";
  versus.textContent = "x";

  const placarFora = document.createElement("span");
  placarFora.className = "partida-placar";
  placarFora.textContent = partida.placar_fora ?? "-";

  const timeFora = document.createElement("span");
  timeFora.className = "partida-time partida-time--fora";
  timeFora.textContent = partida.time_fora;

  const escudoFora = criarEscudo(partida.escudo_fora, partida.time_fora, "partida-escudo");

  linha.append(
    ...(escudoCasa ? [escudoCasa] : []),
    timeCasa,
    placarCasa,
    versus,
    placarFora,
    timeFora,
    ...(escudoFora ? [escudoFora] : [])
  );
  card.appendChild(linha);

  const status = document.createElement("div");
  status.className = "partida-status";
  status.textContent =
    partida.status === "agendado" && partida.data_hora
      ? partida.data_hora
      : LABEL_STATUS[partida.status] ?? partida.status;
  card.appendChild(status);

  const eventos = partida.eventos ?? [];
  const aguardandoLance = faltaLanceDoGol(partida);

  if (eventos.length > 0 || aguardandoLance) {
    const listaEventos = document.createElement("ul");
    listaEventos.className = "partida-eventos";
    for (const evento of eventos) {
      listaEventos.appendChild(criarEventoItem(evento));
    }
    if (aguardandoLance) {
      listaEventos.appendChild(criarItemAguardando());
    }
    card.appendChild(listaEventos);
  }

  if (partida.odds && partida.odds.length > 0 && (partida.status === "agendado" || partida.status === "ao_vivo")) {
    const listaOdds = document.createElement("div");
    listaOdds.className = "partida-odds-lista";
    for (const odds of partida.odds) {
      listaOdds.appendChild(criarOddsLinha(odds));
    }
    card.appendChild(listaOdds);
  }

  return card;
}

function criarCompeticaoTitulo(competicao) {
  const titulo = document.createElement("h2");
  titulo.className = "competicao-titulo";

  const nome = document.createElement("span");
  nome.className = "competicao-nome";
  nome.textContent = competicao.nome;
  titulo.appendChild(nome);

  // pontos corridos trazem a rodada; mata-mata traz a fase (ex: "Oitavas de final")
  if (competicao.subtitulo) {
    const fase = document.createElement("span");
    fase.className = "competicao-fase";
    fase.textContent = competicao.subtitulo;
    titulo.appendChild(fase);
  }

  return titulo;
}

function criarCompeticaoSecao(competicao, partidas) {
  const secao = document.createElement("section");
  secao.className = "competicao";
  secao.appendChild(criarCompeticaoTitulo(competicao));

  const grid = document.createElement("div");
  grid.className = "grid-partidas";

  for (const partida of partidas) {
    grid.appendChild(criarPartidaCard(partida));
  }

  secao.appendChild(grid);
  return secao;
}

function renderizarCompeticoes(competicoes) {
  const container = document.getElementById("competicoes");
  container.replaceChildren();

  let algumaPartida = false;

  for (const competicao of competicoes) {
    const partidas = apenasAoVivo
      ? (competicao.partidas ?? []).filter((p) => p.status === "ao_vivo")
      : competicao.partidas ?? [];

    // com o filtro ligado, competição sem jogo ao vivo some inteira em vez de
    // deixar um cabeçalho órfão
    if (partidas.length === 0) continue;

    algumaPartida = true;
    container.appendChild(criarCompeticaoSecao(competicao, partidas));
  }

  if (!algumaPartida) {
    const vazio = document.createElement("p");
    vazio.className = "grid-vazio";
    vazio.textContent = apenasAoVivo
      ? "Nenhuma partida ao vivo no momento"
      : "Nenhuma partida encontrada";
    container.appendChild(vazio);
    return;
  }

  // com os cards já no DOM (scrollHeight correto), rola cada lista de eventos
  // pro final, priorizando os lances mais recentes quando não couber tudo
  for (const lista of container.querySelectorAll(".partida-eventos")) {
    lista.scrollTop = lista.scrollHeight;
  }
}

function renderizar(dados) {
  dadosAtuais = dados;
  renderizarCompeticoes(dados.competicoes ?? []);
}

function atualizarBotaoFiltro(btn) {
  btn.classList.toggle("ativo", apenasAoVivo);
  btn.title = apenasAoVivo ? "Exibir todas as partidas" : "Exibir apenas partidas ao vivo";
}

function atualizarBotaoAlertas(btn) {
  btn.textContent = `${alertasAtivos ? "🔊" : "🔇"} ALERTAS`;
  btn.classList.toggle("ativo", alertasAtivos);
  btn.title = alertasAtivos
    ? "Desativar aviso de gol (som e notificação na tela)"
    : "Ativar aviso de gol (som e notificação na tela)";
}

function inicializarControles() {
  const btnFiltro = document.getElementById("btn-filtro-ao-vivo");
  const btnAlertas = document.getElementById("btn-alertas");

  atualizarBotaoFiltro(btnFiltro);
  atualizarBotaoAlertas(btnAlertas);

  btnFiltro.addEventListener("click", () => {
    apenasAoVivo = !apenasAoVivo;
    atualizarBotaoFiltro(btnFiltro);
    if (dadosAtuais) renderizarCompeticoes(dadosAtuais.competicoes ?? []);
  });

  btnAlertas.addEventListener("click", () => {
    alertasAtivos = !alertasAtivos;
    escreverCookie(CHAVE_COOKIE_ALERTAS, alertasAtivos ? "1" : "0", 365);
    atualizarBotaoAlertas(btnAlertas);
    // precisa ser aqui dentro: a autorização vale para o gesto do clique
    if (alertasAtivos) liberarAudio();
  });
}

inicializarControles();
atualizarPlacar();
setInterval(atualizarPlacar, INTERVALO_MS);
