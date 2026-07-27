const INTERVALO_MS = 30000;
const CHAVE_COOKIE_ALERTAS = "scoreboard_alertas_ativos";

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
let eventosVistos = null; // null = ainda não carregou nenhum dado (evita alertar na primeira carga)
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
  const valor = lerCookie(CHAVE_COOKIE_ALERTAS);
  return valor === null ? true : valor === "1";
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

function chaveEvento(partida, evento) {
  return [partida.time_casa, partida.time_fora, evento.tipo, evento.time, evento.minuto, evento.jogador].join("|");
}

function notificarNovidades(dados) {
  const chavesAtuais = new Set();
  let houveGolNovo = false;

  for (const partida of dados.partidas ?? []) {
    for (const evento of partida.eventos ?? []) {
      const chave = chaveEvento(partida, evento);
      chavesAtuais.add(chave);
      // cartão vermelho continua aparecendo na partida, mas só gol dispara o alerta sonoro
      if (eventosVistos !== null && !eventosVistos.has(chave) && evento.tipo === "gol") {
        houveGolNovo = true;
      }
    }
  }

  if (houveGolNovo && alertasAtivos) {
    tocarAlerta();
  }

  eventosVistos = chavesAtuais;
}

function tocarAlerta() {
  const audio = document.getElementById("som-alerta");
  audio.currentTime = 0;
  audio.play().catch(() => {
    // navegador pode bloquear autoplay antes de interação do usuário
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

  if (partida.eventos && partida.eventos.length > 0) {
    const listaEventos = document.createElement("ul");
    listaEventos.className = "partida-eventos";
    for (const evento of partida.eventos) {
      listaEventos.appendChild(criarEventoItem(evento));
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

function renderizarGrid(partidas) {
  const grid = document.getElementById("grid-partidas");
  grid.replaceChildren();

  const partidasFiltradas = apenasAoVivo
    ? partidas.filter((p) => p.status === "ao_vivo")
    : partidas;

  if (partidasFiltradas.length === 0) {
    const vazio = document.createElement("p");
    vazio.className = "grid-vazio";
    vazio.textContent = apenasAoVivo
      ? "Nenhuma partida ao vivo no momento"
      : "Nenhuma partida encontrada";
    grid.appendChild(vazio);
    return;
  }

  for (const partida of partidasFiltradas) {
    const card = criarPartidaCard(partida);
    grid.appendChild(card);

    // com o card já no DOM (scrollHeight correto), rola a lista de eventos
    // pro final, priorizando mostrar os lances mais recentes quando não couber tudo
    const listaEventos = card.querySelector(".partida-eventos");
    if (listaEventos) {
      listaEventos.scrollTop = listaEventos.scrollHeight;
    }
  }
}

function renderizar(dados) {
  dadosAtuais = dados;
  document.getElementById("rodada-atual").textContent = dados.rodada ? `${dados.rodada}ª RODADA` : "";
  renderizarGrid(dados.partidas ?? []);
}

function atualizarBotaoFiltro(btn) {
  btn.classList.toggle("ativo", apenasAoVivo);
  btn.title = apenasAoVivo ? "Exibir todas as partidas" : "Exibir apenas partidas ao vivo";
}

function atualizarBotaoAlertas(btn) {
  btn.textContent = `${alertasAtivos ? "🔊" : "🔇"} ALERTAS`;
  btn.classList.toggle("ativo", alertasAtivos);
}

function inicializarControles() {
  const btnFiltro = document.getElementById("btn-filtro-ao-vivo");
  const btnAlertas = document.getElementById("btn-alertas");

  atualizarBotaoFiltro(btnFiltro);
  atualizarBotaoAlertas(btnAlertas);

  btnFiltro.addEventListener("click", () => {
    apenasAoVivo = !apenasAoVivo;
    atualizarBotaoFiltro(btnFiltro);
    if (dadosAtuais) renderizarGrid(dadosAtuais.partidas ?? []);
  });

  btnAlertas.addEventListener("click", () => {
    alertasAtivos = !alertasAtivos;
    escreverCookie(CHAVE_COOKIE_ALERTAS, alertasAtivos ? "1" : "0", 365);
    atualizarBotaoAlertas(btnAlertas);
  });
}

inicializarControles();
atualizarPlacar();
setInterval(atualizarPlacar, INTERVALO_MS);
