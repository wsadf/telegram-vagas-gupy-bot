import os
import re
import time
import sqlite3
import requests
import unicodedata
import xml.etree.ElementTree as ET
import html as html_lib
from datetime import datetime, timedelta

try:
    from bs4 import BeautifulSoup
    BS4_DISPONIVEL = True
except ImportError:
    BS4_DISPONIVEL = False
    print("⚠️  beautifulsoup4 não instalado — LinkedIn desativado. Rode: pip install beautifulsoup4")

# --- 1. CONFIG ---
DIRETORIO_ATUAL = os.path.dirname(os.path.abspath(__file__))
CAMINHO_BANCO   = os.getenv("BANCO_VAGAS") or os.path.join(DIRETORIO_ATUAL, 'vagas_gupy.db')

def carregar_env():
    caminho = os.path.join(DIRETORIO_ATUAL, '.env')
    if not os.path.exists(caminho):
        return
    with open(caminho) as f:
        for linha in f:
            linha = linha.strip()
            if linha and not linha.startswith('#') and '=' in linha:
                chave, valor = linha.split('=', 1)
                os.environ.setdefault(chave.strip(), valor.strip())

carregar_env()

TOKEN   = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID_GRUPO")

# Modo de teste: DRY_RUN=1 imprime os alertas no terminal em vez de enviar ao Telegram.
# Use junto com BANCO_VAGAS=<arquivo temporário> para não alterar o banco real.
DRY_RUN = os.getenv("DRY_RUN") == "1"

# ════════════════════════════════════════════════════════════════════════════════
# 2. CONFIGURAÇÕES DO USUÁRIO
# Tudo que você precisa alterar para adaptar o bot ao seu perfil está aqui.
# ════════════════════════════════════════════════════════════════════════════════

# ──────────────────────────────────────────────────────────────────────────────
# A. BUSCAS: o que procurar em cada plataforma (perfil: Assistente Social, BH)
# ──────────────────────────────────────────────────────────────────────────────
# "nome" é o label exibido no alerta do Telegram.

# Gupy (API atual do portal): busca por cargo (jobName) filtrando por estado (state).
# A cidade é filtrada depois, por CIDADES_PERMITIDAS. 'limit' deve ficar em 10.
# Para filtrar modalidade, acrescente 'workplaceType': 'on-site' | 'hybrid' | 'remote'.
TERMOS_GUPY = [
    "assistente social", "analista social", "serviço social", "responsabilidade social",
    # perícia, coordenação e reassentamento
    "perícia social", "perito", "coordenador social", "coordenador de projetos sociais",
    "reassentamento", "trabalho técnico social", "técnico social",
]
# Presencial e híbrido: Minas Gerais (sem filtro de modalidade, então vem tudo que está em MG).
FILTROS_GUPY = [
    {"nome": f"{termo.upper()} · MG", "params": {'jobName': termo, 'state': 'Minas Gerais', 'limit': 10}}
    for termo in TERMOS_GUPY
]

# Remoto: vagas de qualquer estado do Brasil (o filtro de cidade e estado não vale para elas).
# Desligue com ACEITAR_REMOTO = False para receber só vagas da região.
ACEITAR_REMOTO = True
TERMOS_GUPY_REMOTO = ["assistente social", "analista social", "serviço social", "responsabilidade social"]
if ACEITAR_REMOTO:
    FILTROS_GUPY += [
        {"nome": f"{termo.upper()} · REMOTO", "params": {'jobName': termo, 'workplaceType': 'remote', 'limit': 10}}
        for termo in TERMOS_GUPY_REMOTO
    ]

# ProgramaThor é um site só de tecnologia: desativado.
FILTROS_PROGRAMATHOR = []

# LinkedIn (API guest): keywords + localização + período.
# Sem f_WT, para trazer presencial, híbrido e remoto.
# f_TPR=r259200 → últimos 3 dias | r604800 → 7 dias
# O LinkedIn agrupa a região metropolitana: buscar "Contagem" ou "Betim" devolve as mesmas
# vagas de "Belo Horizonte" (testado em 16/11/2026), então uma cidade basta.
# Para outra região (ex.: "Juiz de Fora"), acrescente o nome em CIDADES_LINKEDIN.
CIDADES_LINKEDIN = ["Belo Horizonte"]
TERMOS_LINKEDIN  = ["assistente social", "analista social", "serviço social"]
# Termos extras (perícia, coordenação, reassentamento): só em BH, para limitar o número de buscas.
TERMOS_LINKEDIN_EXTRA = ["reassentamento", "trabalho técnico social", "coordenador de projetos sociais", "perícia social"]
FILTROS_LINKEDIN = [
    {
        "nome": f"{termo.upper()} · {cidade.upper()}",
        "params": {"keywords": f'"{termo}"', "location": f"{cidade}, Minas Gerais, Brasil", "f_TPR": "r604800", "start": 0},
    }
    for cidade in CIDADES_LINKEDIN
    for termo in TERMOS_LINKEDIN
] + [
    {
        "nome": f"{termo.upper()} · BELO HORIZONTE",
        "params": {"keywords": f'"{termo}"', "location": "Belo Horizonte, Minas Gerais, Brasil", "f_TPR": "r604800", "start": 0},
    }
    for termo in TERMOS_LINKEDIN_EXTRA
]

# Inhire: as empresas monitoradas eram de tecnologia. Desativado.
# Para ativar, adicione subdomínios de organizações sociais em EMPRESAS_INHIRE.
FILTROS_INHIRE = [
    {"nome": "ASSISTENTE SOCIAL", "termo": "social", "local_filtro": "presencial"},
]
EMPRESAS_INHIRE = []

# Solides: DESATIVADO. A API responde HTTP 403 a qualquer acesso automático (testado em 16/11/2026).
# Para tentar de novo, use o formato: {"nome": "ASSISTENTE SOCIAL", "params": {'title': 'assistente social', 'take': 14}}
FILTROS_SOLIDES = []

# ──────────────────────────────────────────────────────────────────────────────
# B. PERFIL: local, termos obrigatórios e bloqueios
# ──────────────────────────────────────────────────────────────────────────────

# Período máximo de publicação aceito. Na primeira execução, aumente
# (ex: 30 dias) para preencher o histórico e depois volte ao padrão.
DIAS_BUSCA_GUPY    = 120  # a Gupy só lista vagas abertas; 120 dias traz as já publicadas na 1ª execução
DIAS_BUSCA_SOLIDES = 20

# Local: Gupy e Solides só enviam vagas dessas cidades (região metropolitana de BH).
# Deixe a lista vazia para aceitar qualquer cidade. Sem acento e em minúsculas.
CIDADES_PERMITIDAS = [
    "belo horizonte", "contagem", "betim", "nova lima", "sabara", "santa luzia",
    "ribeirao das neves", "vespasiano", "lagoa santa", "ibirite", "sarzedo",
    "brumadinho", "esmeraldas", "pedro leopoldo", "mario campos", "sao jose da lapa",
]
ESTADOS_PERMITIDOS = ["minas gerais", "mg"]   # lista vazia = qualquer estado

# O título da vaga precisa conter pelo menos um destes termos (evita
# "assistente administrativo", que a busca da Gupy costuma trazer junto).
TERMOS_OBRIGATORIOS = [
    "social", "cras", "creas", "suas", "reassentamento", "habita",
    "perit", "tts", "socioeduc", "socioambiental", "comunit", "terceiro setor",
]

# Qualquer termo abaixo encontrado no título da vaga a elimina da lista.
GAPS_ELIMINATORIOS = [
    "estágio", "estagio", "estagiário", "estagiario", "jovem aprendiz", "aprendiz",
    "técnico em enfermagem", "tecnico em enfermagem", "vendas", "telemarketing",
    "motorista", "recepcionista", "marketing", "social media", "mídias sociais",
    "midias sociais", "redes sociais", "social selling", "tráfego", "trafego",
    "voluntári", "voluntari",
]

# Vagas dessas empresas são ignoradas em todas as fontes (busca parcial).
EMPRESAS_IGNORADAS = [
    "Jobgether",
    # Adicione outras empresas que deseja ignorar aqui
]

# ──────────────────────────────────────────────────────────────────────────────
# C. MINHA STACK: termos do perfil usados para calcular o match
# ──────────────────────────────────────────────────────────────────────────────
# Níveis: 🔴 Baixo 0 | 🔵 Padrão 1 | 🟡 Médio 2 | 🟢 Alto 3 ou mais
# Atenção: Gupy, LinkedIn e Inhire analisam apenas o título da vaga;
# Solides usa título + descrição completa.
MINHA_STACK = [
    "assistente social", "analista social", "serviço social", "servico social",
    "habitação", "habitacao", "reassentamento", "trabalho técnico social",
    "assistência social", "assistencia social", "suas", "cras", "creas", "scfv",
    "convivência", "vínculos", "famílias", "familias", "vulnerabilidade",
    "políticas públicas", "politicas publicas", "projetos sociais", "programas sociais",
    "estudo social", "parecer", "laudo", "perícia", "pericia", "relatório técnico",
    "gestão de projetos", "coordenador", "supervisor", "saúde", "saude",
    "cress", "cadúnico", "cadunico", "escuta qualificada", "mediação de conflitos",
    "responsabilidade social", "relacionamento comunitário", "socioeducativo",
]

# ──────────────────────────────────────────────────────────────────────────────
# D. UTILITÁRIOS DE LOCAL
# ──────────────────────────────────────────────────────────────────────────────
def _norm(s):
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode('utf-8')
    return s.strip().lower()

def local_permitido(estado, cidade=""):
    """True se o estado e a cidade estão nas listas permitidas (ou se a lista está vazia).
    Campo vazio na fonte não bloqueia a vaga."""
    e, c = _norm(estado), _norm(cidade)
    if ESTADOS_PERMITIDOS and e and e not in ESTADOS_PERMITIDOS:
        return False
    if CIDADES_PERMITIDAS and c and c not in CIDADES_PERMITIDAS:
        return False
    return True

# Set em memória para evitar duplicatas na mesma execução (mesma vaga, fontes/buscas diferentes)
_enviados_sessao: set = set()

def _chave_sessao(titulo: str, empresa: str) -> str:
    normalizar = lambda s: re.sub(r'[^a-z0-9]', '', s.lower())
    return normalizar(titulo)[:60] + "|" + normalizar(empresa)[:30]

def tem_gap_eliminatorio(titulo):
    t = titulo.lower()
    return any(g in t for g in GAPS_ELIMINATORIOS)

def calcular_match(titulo):
    """
    Calcula o nível de compatibilidade da vaga com o perfil do candidato.

    O parâmetro `titulo` pode conter mais do que apenas o título da vaga —
    cada fonte passa textos diferentes:
      - Gupy:        apenas o título da vaga
      - LinkedIn:    apenas o título da vaga
      - Inhire:      apenas o título da vaga
      - ProgramaThor: título + tags de tecnologia exibidas no card
      - Solides:     título + descrição completa da vaga (HTML limpo)

    Para Gupy, LinkedIn e Inhire, tecnologias mencionadas somente na descrição
    NÃO são detectadas, podendo resultar em nível Baixo para vagas relevantes.

    Níveis de match (baseado na contagem de itens de MINHA_STACK encontrados):
      🔴 Baixo  — 0 itens compatíveis
      🔵 Padrão — 1 item compatível
      🟡 Médio  — 2 itens compatíveis
      🟢 Alto   — 3 ou mais itens compatíveis
    """
    t = titulo.lower()
    techs = [s for s in MINHA_STACK if s in t]
    score = len(techs)
    if score >= 3:
        nivel = "🟢 Alto"
    elif score == 2:
        nivel = "🟡 Médio"
    elif score == 1:
        nivel = "🔵 Padrão"
    else:
        nivel = "🔴 Baixo"
    return nivel, techs

# --- 3. BANCO E TELEGRAM ---

TRADUCAO_MODELO = {
    "on-site": "Presencial",
    "hybrid":  "Híbrido",
    "remote":  "Remoto",
}

TRADUCAO_TIPO_VAGA = {
    "vacancy_type_effective":   "Efetivo",
    "vacancy_type_apprentice":  "Jovem Aprendiz",
    "vacancy_type_internship":  "Estágio",
    "vacancy_type_temporary":   "Temporário",
    "vacancy_type_freelancer":  "Freelancer",
    "vacancy_type_talent_pool": "Banco de Talentos (sem vaga específica)",
}

def iniciar_banco():
    conn   = sqlite3.connect(CAMINHO_BANCO)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vagas_enviadas (
            link TEXT PRIMARY KEY,
            data_publicacao TEXT,
            titulo TEXT
        )
    ''')
    conn.commit()
    return conn, cursor

def ja_enviada(cursor, link):
    cursor.execute('SELECT 1 FROM vagas_enviadas WHERE link = ?', (link,))
    return cursor.fetchone() is not None

def enviar_telegram(mensagem):
    if DRY_RUN:
        print("   [DRY_RUN] " + re.sub(r'<[^>]+>', '', mensagem).replace("\n\n", "\n"))
        return
    # CHAT_ID_GRUPO aceita vários destinos separados por vírgula (ex.: "123456789,-1001234567890").
    # Cada destino recebe a mensagem; a falha em um não impede o envio aos outros.
    for chat_id in [c.strip() for c in (CHAT_ID or "").split(",") if c.strip()]:
        payload = {
            "chat_id":                  chat_id,
            "text":                     mensagem,
            "parse_mode":               "HTML",
            "disable_web_page_preview": True,
        }
        try:
            r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json=payload, timeout=10)
            if r.status_code != 200:
                print(f"⚠️  Telegram recusou (chat {chat_id}): {r.text}")
        except Exception as e:
            print(f"❌ Erro Telegram (chat {chat_id}): {e}")

def registrar_e_enviar(conn, cursor, link, titulo, empresa, data_f, mensagem, fonte, nivel_match):
    chave = _chave_sessao(titulo, empresa)
    if chave in _enviados_sessao:
        print(f"   🔁 Duplicata (sessão): {titulo[:50]}")
        return
    _enviados_sessao.add(chave)
    cursor.execute('INSERT OR IGNORE INTO vagas_enviadas VALUES (?, ?, ?)', (link, data_f, titulo))
    conn.commit()
    enviar_telegram(mensagem)
    print(f"   ✅ [{nivel_match}] {titulo[:50]}...")
    time.sleep(2)

def filtros_basicos(titulo, empresa=None):
    """Retorna (bloqueada, motivo) com os filtros de perfil."""
    if TERMOS_OBRIGATORIOS and not any(t in titulo.lower() for t in TERMOS_OBRIGATORIOS):
        return True, f"🚫 Fora do perfil: {titulo[:55]}"
    if tem_gap_eliminatorio(titulo):
        return True, f"🚫 Gap: {titulo[:55]}"
    if empresa:
        emp = empresa.lower()
        for emp_ignorada in EMPRESAS_IGNORADAS:
            if emp_ignorada.lower() in emp:
                return True, f"🚫 Empresa ignorada: {empresa[:50]}"
    return False, ""

# --- 4. GUPY ---

def buscar_vagas_gupy(conn, cursor):
    print("\n🟣 GUPY — iniciando varredura...")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept':     'application/json, text/plain, */*',
        'Origin':     'https://portal.gupy.io',
    }
    url_api = "https://portal.gupy.io/api/job-search/jobs"

    for filtro in FILTROS_GUPY:
        print(f"\n   🔎 {filtro['nome']}...")
        vagas_velhas  = 0
        LIMITE_VELHAS = 20

        for pagina in range(1, 36):
            params = filtro['params'].copy()
            params['offset'] = (pagina - 1) * 10

            try:
                resp = requests.get(url_api, headers=headers, params=params, timeout=15)
                if resp.status_code != 200:
                    print(f"   🛑 HTTP {resp.status_code}")
                    break

                dados = resp.json().get('data', [])
                if not dados:
                    print("   🔚 Sem mais vagas.")
                    break

                for vaga in dados:
                    link   = vaga.get('jobUrl', '')
                    if not link:
                        continue

                    titulo  = vaga.get('name', 'Título Indisponível')
                    empresa = vaga.get('careerPageName') or 'Empresa não informada'
                    if _norm(empresa) in ('pagina de carreira', 'carreira', 'carreiras', 'vagas'):
                        sub = re.match(r'https?://([^.]+)\.gupy\.io', link)
                        empresa = sub.group(1).capitalize() if sub else empresa
                    local   = "Qualquer lugar (Remoto)" if 'REMOTO' in filtro['nome'] else f"{vaga.get('city', 'Não informado')} - {vaga.get('state', 'Não informado')}"
                    modelo  = TRADUCAO_MODELO.get(vaga.get('workplaceType', ''), "Não informado")
                    tipo    = TRADUCAO_TIPO_VAGA.get(vaga.get('type', ''), "Outros")
                    pcd     = "Sim" if vaga.get('disabilities') else "Não informado"

                    pais    = vaga.get('country', '')
                    if pais and pais.lower() not in ['brasil', 'brazil', 'br']:
                        continue

                    # Vaga remota vale de qualquer cidade; as demais respeitam as cidades permitidas.
                    if vaga.get('workplaceType') != 'remote' and not local_permitido(vaga.get('state'), vaga.get('city')):
                        continue

                    data_iso = vaga.get('publishedDate', '')
                    try:
                        data_utc = datetime.strptime(data_iso.split('.')[0], "%Y-%m-%dT%H:%M:%S")
                        data_brt = data_utc - timedelta(hours=3)
                        data_f   = data_brt.strftime("%d/%m/%Y")
                        hora_f   = data_brt.strftime("%H:%M")
                        if datetime.now() - data_brt > timedelta(days=DIAS_BUSCA_GUPY):
                            print(f"   📅 Vaga antiga ({data_f}). Encerrando busca.")
                            vagas_velhas = LIMITE_VELHAS
                            break
                    except Exception:
                        data_f, hora_f = "Sem data", "--:--"

                    bloqueada, motivo = filtros_basicos(titulo, empresa)
                    if bloqueada:
                        print(f"   {motivo}")
                        continue

                    if ja_enviada(cursor, link):
                        vagas_velhas += 1
                        if vagas_velhas >= LIMITE_VELHAS:
                            break
                        continue

                    vagas_velhas = 0
                    nivel_match, techs = calcular_match(titulo)
                    techs_str = " · ".join(t.upper() for t in techs[:4]) if techs else "Verificar descrição"

                    mensagem = (
                        f"🟣 <b>GUPY — {filtro['nome']}</b>\n\n"
                        f"💼 <b>Vaga:</b> {titulo}\n"
                        f"🏢 <b>Empresa:</b> {empresa}\n"
                        f"📍 <b>Local:</b> {local}\n"
                        f"💻 <b>Modelo:</b> {modelo}\n"
                        f"📄 <b>Tipo:</b> {tipo}\n"
                        f"♿ <b>PCD:</b> {pcd}\n"
                        f"📅 <b>Data:</b> {data_f} às {hora_f}\n"
                        f"📊 <b>Match:</b> {nivel_match} · <i>{techs_str}</i>\n\n"
                        f"🔗 <a href='{link}'>Aplicar na Gupy</a>"
                    )
                    registrar_e_enviar(conn, cursor, link, titulo, empresa, data_f, mensagem, "GUPY", nivel_match)

                if vagas_velhas >= LIMITE_VELHAS:
                    print("   🛑 Encerrando paginação.")
                    break

            except Exception as e:
                print(f"   ⚠️  Erro: {e}")
                break

# --- 5. PROGRAMATHOR ---

def buscar_vagas_programathor(conn, cursor):
    if not BS4_DISPONIVEL:
        print("\n⚠️  ProgramaThor desativado: instale beautifulsoup4")
        return

    print("\n🟤 PROGRAMATHOR — iniciando varredura...")

    headers = {
        'User-Agent':      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'pt-BR,pt;q=0.9',
    }

    for filtro in FILTROS_PROGRAMATHOR:
        print(f"\n   🔎 {filtro['nome']}...")

        # Monta URL base: /jobs-{termo}/{local_filtro} ou /jobs-{termo} se sem local
        termo        = filtro["termo"].lower()
        local_filtro = filtro.get("local_filtro", "")
        base_url     = f"https://programathor.com.br/jobs-{termo}/{local_filtro}" if local_filtro else f"https://programathor.com.br/jobs-{termo}"

        for pagina in range(1, 6):
            params = {} if pagina == 1 else {"page": pagina}

            try:
                resp = requests.get(base_url, params=params, headers=headers, timeout=15)
                if resp.status_code != 200:
                    print(f"   🛑 HTTP {resp.status_code}")
                    break

                soup  = BeautifulSoup(resp.text, 'html.parser')
                cards = soup.find_all('div', class_='cell-list')

                if not cards:
                    print("   🔚 Sem mais vagas.")
                    break

                novos_na_pagina = 0

                for card in cards:
                    link_el = card.find('a', href=lambda h: h and '/jobs/' in h)
                    if not link_el:
                        continue

                    link = "https://programathor.com.br" + link_el['href']

                    titulo_el = card.find('h3')
                    titulo_raw = titulo_el.get_text(strip=True) if titulo_el else ""
                    if 'Vencida' in titulo_raw or 'vencida' in titulo_raw:
                        continue
                    titulo = titulo_raw.replace('NOVA', '').strip() or "Título Indisponível"

                    spans   = card.select('.cell-list-content-icon span')
                    empresa = spans[0].get_text(strip=True) if len(spans) > 0 else "Empresa não informada"
                    local   = spans[1].get_text(strip=True) if len(spans) > 1 else ""
                    salario = spans[3].get_text(strip=True) if len(spans) > 3 else ""
                    nivel   = spans[4].get_text(strip=True) if len(spans) > 4 else ""
                    tipo    = spans[5].get_text(strip=True) if len(spans) > 5 else ""
                    tags    = [t.get_text(strip=True) for t in card.select('span.tag-list')]

                    # Filtros básicos (gap no título ou empresa ignorada)
                    bloqueada, motivo = filtros_basicos(titulo, empresa)
                    if bloqueada:
                        print(f"   {motivo}")
                        continue

                    # Gaps nos tags de tecnologia
                    if any(tem_gap_eliminatorio(t) for t in tags):
                        print(f"   🚫 Gap na tag: {titulo[:55]}")
                        continue

                    if ja_enviada(cursor, link):
                        continue

                    novos_na_pagina += 1

                    # Match scoring usa título + stack explícita do card
                    texto_match = titulo + " " + " ".join(tags)
                    nivel_match, techs = calcular_match(texto_match)
                    techs_str = " · ".join(t.upper() for t in techs[:4]) if techs else (", ".join(tags[:4]) or "Verificar descrição")
                    tags_str  = ", ".join(tags[:6]) if tags else ""

                    mensagem = (
                        f"🟤 <b>PROGRAMATHOR — {filtro['nome']}</b>\n\n"
                        f"💼 <b>Vaga:</b> {titulo}\n"
                        f"🏢 <b>Empresa:</b> {empresa}\n"
                        f"📍 <b>Local:</b> {local}\n"
                        f"📄 <b>Nível:</b> {nivel}"
                        + (f" · {tipo}" if tipo else "") + "\n"
                        + (f"💰 <b>Salário:</b> {salario}\n" if salario else "")
                        + (f"🛠️  <b>Stack:</b> <i>{tags_str}</i>\n" if tags_str else "")
                        + f"📊 <b>Match:</b> {nivel_match} · <i>{techs_str}</i>\n\n"
                        f"🔗 <a href='{link}'>Aplicar no ProgramaThor</a>"
                    )
                    registrar_e_enviar(conn, cursor, link, titulo, empresa, datetime.now().strftime("%d/%m/%Y"), mensagem, "PROGRAMATHOR", nivel_match)

                if novos_na_pagina == 0:
                    break

                time.sleep(1)

            except Exception as e:
                print(f"   ⚠️  Erro: {e}")
                break

# --- 6. LINKEDIN ---

def buscar_vagas_linkedin(conn, cursor):
    if not BS4_DISPONIVEL:
        print("\n⚠️  LinkedIn desativado: instale beautifulsoup4")
        return

    print("\n🔷 LINKEDIN — iniciando varredura...")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    }
    url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

    for filtro in FILTROS_LINKEDIN:
        print(f"\n   🔎 {filtro['nome']}...")
        try:
            resp = requests.get(url, params=filtro["params"], headers=headers, timeout=15)
            if resp.status_code != 200:
                print(f"   🛑 HTTP {resp.status_code}")
                continue

            soup  = BeautifulSoup(resp.text, 'html.parser')
            cards = soup.find_all('div', class_='base-card')

            if not cards:
                print("   🔚 Nenhuma vaga ou resposta bloqueada.")
                continue

            for card in cards:
                titulo_el  = card.find(class_=lambda c: c and 'title' in c)
                empresa_el = card.find(class_=lambda c: c and 'subtitle' in c)
                link_el    = card.find('a', href=True)
                data_el    = card.find('time')

                titulo  = titulo_el.get_text(strip=True)  if titulo_el  else "Título Indisponível"
                empresa = empresa_el.get_text(strip=True) if empresa_el else "Empresa não informada"
                link    = link_el['href'].split('?')[0]   if link_el    else ''

                if not link:
                    continue

                try:
                    data_iso = data_el.get('datetime', '') if data_el else ''
                    data_pub = datetime.strptime(data_iso, "%Y-%m-%d")
                    data_f   = data_pub.strftime("%d/%m/%Y")
                    hora_f   = "--:--"
                except Exception:
                    data_f, hora_f = "Sem data", "--:--"

                bloqueada, motivo = filtros_basicos(titulo, empresa)
                if bloqueada:
                    print(f"   {motivo}")
                    continue

                if ja_enviada(cursor, link):
                    continue

                nivel_match, techs = calcular_match(titulo)
                techs_str = " · ".join(t.upper() for t in techs[:4]) if techs else "Verificar descrição"

                mensagem = (
                    f"🔷 <b>LINKEDIN — {filtro['nome']}</b>\n\n"
                    f"💼 <b>Vaga:</b> {titulo}\n"
                    f"🏢 <b>Empresa:</b> {empresa}\n"
                    f"📅 <b>Data:</b> {data_f}\n"
                    f"📊 <b>Match:</b> {nivel_match} · <i>{techs_str}</i>\n\n"
                    f"🔗 <a href='{link}'>Aplicar no LinkedIn</a>"
                )
                registrar_e_enviar(conn, cursor, link, titulo, empresa, data_f, mensagem, "LINKEDIN", nivel_match)

        except Exception as e:
            print(f"   ⚠️  Erro: {e}")

# --- 7. INHIRE ---

def buscar_vagas_inhire(conn, cursor):
    print("\n🟣 INHIRE — iniciando varredura...")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    }
    url_base = "https://api.inhire.app/job-posts/public/pages"

    for empresa_slug in EMPRESAS_INHIRE:
        print(f"\n   🏢 {empresa_slug.upper()}...")
        headers['X-Tenant'] = empresa_slug
        
        try:
            resp = requests.get(url_base, headers=headers, timeout=15)
            if resp.status_code != 200:
                print(f"   🛑 HTTP {resp.status_code}")
                continue
                
            dados = resp.json()
            jobs = dados.get('jobsPage', [])
            nome_empresa = dados.get('tenantName', empresa_slug.capitalize())
            
            if not jobs:
                print("   🔚 Nenhuma vaga encontrada.")
                continue

            for filtro in FILTROS_INHIRE:
                # Vamos buscar vagas para cada filtro
                for job in jobs:
                    if job.get('status') != 'published':
                        continue
                        
                    titulo = job.get('displayName', 'Título Indisponível')
                    titulo_lower = titulo.lower()
                    
                    if filtro['termo'] not in titulo_lower:
                        continue
                        
                    modelo_api = job.get('workplaceType', '').lower()
                    modelo = TRADUCAO_MODELO.get(modelo_api, "Não informado")
                    
                    if filtro['local_filtro'] == 'remoto' and modelo_api != 'remote':
                        continue
                        
                    job_id = job.get('jobId')
                    titulo_slug = unicodedata.normalize('NFKD', titulo).encode('ascii', 'ignore').decode('utf-8')
                    titulo_slug = re.sub(r'[^\w\s-]', '', titulo_slug).strip().lower()
                    titulo_slug = re.sub(r'[-\s]+', '-', titulo_slug)
                    link = f"https://{empresa_slug}.inhire.app/vagas/{job_id}/{titulo_slug}"
                    
                    bloqueada, motivo = filtros_basicos(titulo, nome_empresa)
                    if bloqueada:
                        print(f"   {motivo}")
                        continue
                        
                    if ja_enviada(cursor, link):
                        continue
                        
                    local = job.get('location', 'Não informado')
                    nivel_match, techs = calcular_match(titulo)
                    techs_str = " · ".join(t.upper() for t in techs[:4]) if techs else "Verificar descrição"
                    data_f = datetime.now().strftime("%d/%m/%Y")
                    
                    mensagem = (
                        f"🟣 <b>INHIRE — {filtro['nome']}</b>\n\n"
                        f"💼 <b>Vaga:</b> {titulo}\n"
                        f"🏢 <b>Empresa:</b> {nome_empresa}\n"
                        f"📍 <b>Local:</b> {local}\n"
                        f"💻 <b>Modelo:</b> {modelo}\n"
                        f"📅 <b>Data (Descoberta):</b> {data_f}\n"
                        f"📊 <b>Match:</b> {nivel_match} · <i>{techs_str}</i>\n\n"
                        f"🔗 <a href='{link}'>Aplicar na Inhire</a>"
                    )
                    registrar_e_enviar(conn, cursor, link, titulo, nome_empresa, data_f, mensagem, "INHIRE", nivel_match)
                    
        except Exception as e:
            print(f"   ⚠️  Erro ao buscar {empresa_slug}: {e}")

# --- 8. SOLIDES ---

def buscar_vagas_solides(conn, cursor):
    print("\n🟢 SOLIDES — iniciando varredura...")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    }
    url_base = "https://apigw.solides.com.br/jobs/v3/portal-vacancies-new"

    for filtro in FILTROS_SOLIDES:
        print(f"\n   🔎 {filtro['nome']}...")
        
        for pagina in range(1, 10):
            params = filtro['params'].copy()
            params['page'] = pagina
            
            try:
                resp = requests.get(url_base, headers=headers, params=params, timeout=15)
                if resp.status_code != 200:
                    print(f"   🛑 HTTP {resp.status_code}")
                    break
                    
                json_data = resp.json()
                if not json_data.get('success'):
                    print("   🛑 Erro na resposta da Solides")
                    break
                    
                dados = json_data.get('data', {})
                vagas = dados.get('data', [])
                total_pages = dados.get('totalPages', 1)
                
                if not vagas:
                    print("   🔚 Sem mais vagas.")
                    break
                    
                for vaga in vagas:
                    titulo = vaga.get('title', 'Título Indisponível')
                    
                    # Verificação de modalidade remota (se o filtro exigir)
                    modelo_api = vaga.get('jobType', '').lower()
                    if 'remoto' in filtro['nome'].lower() and modelo_api != 'remoto':
                        continue
                        
                    link = vaga.get('redirectLink', '')
                    if not link:
                        continue

                    # Correção da URL da vaga Solides
                    match_url = re.search(r'https://([^.]+)\.solides\.jobs/vacancies/(\d+)', link)
                    if match_url:
                        company_slug = match_url.group(1)
                        vacancy_id = match_url.group(2)
                        link = f"https://{company_slug}.vagas.solides.com.br/vaga/{vacancy_id}"

                    empresa = vaga.get('companyName', 'Empresa não informada')
                    bloqueada, motivo = filtros_basicos(titulo, empresa)
                    if bloqueada:
                        print(f"   {motivo}")
                        continue
                        
                    if ja_enviada(cursor, link):
                        continue
                    
                    data_iso = vaga.get('createdAt', '')
                    if data_iso:
                        try:
                            # Tentar extrair "YYYY-MM-DD"
                            data_pub = datetime.strptime(data_iso[:10], "%Y-%m-%d")
                            data_f = data_pub.strftime("%d/%m/%Y")
                            if datetime.now() - data_pub > timedelta(days=DIAS_BUSCA_SOLIDES):
                                print(f"   📅 Vaga antiga ({data_f}). Pulando.")
                                continue
                        except Exception:
                            data_f = data_iso
                    else:
                        data_f = "Não informado"
                        
                    # Tratamento do texto descritivo para enriquecer o match
                    description_raw = vaga.get('description', '')
                    if BS4_DISPONIVEL and description_raw:
                        description_limpa = BeautifulSoup(description_raw, 'html.parser').get_text(separator=' ')
                    else:
                        description_limpa = re.sub(r'<[^>]+>', ' ', description_raw)
                        
                    texto_para_match = f"{titulo} {description_limpa}"
                    nivel_match, techs = calcular_match(texto_para_match)
                    techs_str = " · ".join(t.upper() for t in techs[:4]) if techs else "Verificar descrição"
                    
                    cidade_info = vaga.get('city') or {}
                    estado_info = vaga.get('state') or {}
                    if not local_permitido(estado_info.get('code') or estado_info.get('name'), cidade_info.get('name')):
                        continue
                    local = f"{cidade_info.get('name', '')} - {estado_info.get('code', '')}".strip(" -")
                    if not local:
                        local = "Brasil"
                        
                    modelo = modelo_api.capitalize() if modelo_api else "Não informado"
                    
                    mensagem = (
                        f"🟢 <b>SOLIDES — {filtro['nome']}</b>\n\n"
                        f"💼 <b>Vaga:</b> {titulo}\n"
                        f"🏢 <b>Empresa:</b> {empresa}\n"
                        f"📍 <b>Local:</b> {local}\n"
                        f"💻 <b>Modelo:</b> {modelo}\n"
                        f"📅 <b>Data:</b> {data_f}\n"
                        f"📊 <b>Match:</b> {nivel_match} · <i>{techs_str}</i>\n\n"
                        f"🔗 <a href='{link}'>Aplicar na Solides</a>"
                    )
                    registrar_e_enviar(conn, cursor, link, titulo, empresa, data_f, mensagem, "SOLIDES", nivel_match)
                    
                if pagina >= total_pages:
                    break
                    
            except Exception as e:
                print(f"   ⚠️  Erro: {e}")
                break

# --- 9. PREFEITURA DE BELO HORIZONTE ---

PBH_BASE      = "https://prefeitura.pbh.gov.br"
PBH_URL_LISTA = PBH_BASE + "/oportunidades-de-trabalho"
PBH_URL_SAUDE = PBH_BASE + "/saude/informacoes/gestao-de-pessoas/processos-seletivos"
PBH_ATIVO     = True   # False desliga as duas buscas da PBH

# Cargos de Serviço Social nos editais da PBH.
PBH_CARGO_SOCIAL = re.compile(r"assistente social|servi[cç]o social|analista social", re.I)

def _pbh_get(url, params=None):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept-Language': 'pt-BR,pt;q=0.9',
    }
    resp = requests.get(url, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text

def _pbh_texto(pagina):
    pagina = re.sub(r'(?is)<(script|style).*?</\1>', ' ', pagina)
    texto = re.sub(r'<[^>]+>', ' ', pagina)
    texto = html_lib.unescape(texto).replace('\xa0', ' ')
    return re.sub(r'\s+', ' ', texto)

def _pbh_periodo(texto):
    """Lê 'PERÍODO DA INSCRIÇÃO: dd/mm/aaaa até dd/mm/aaaa'. Retorna (inicio, fim) ou (None, None)."""
    m = re.search(r'PER[IÍ]ODO DA INSCRI[CÇ][AÃ]O:\s*(\d{2}/\d{2}/\d{4})\s*(?:at[eé]|a)\s*(\d{2}/\d{2}/\d{4})', texto, re.I)
    if not m:
        return None, None
    try:
        return (datetime.strptime(m.group(1), "%d/%m/%Y"), datetime.strptime(m.group(2), "%d/%m/%Y"))
    except ValueError:
        return None, None

def _pbh_marcar_visto(conn, cursor, link, data_f, titulo):
    """Guarda o edital no banco para não reabrir a página a cada execução."""
    cursor.execute('INSERT OR IGNORE INTO vagas_enviadas VALUES (?, ?, ?)', (link, data_f, titulo))
    conn.commit()

def buscar_vagas_pbh_editais(conn, cursor):
    """Lista geral de oportunidades da PBH (novos e em andamento).
    Abre cada edital, confere o prazo de inscrição e avisa os que estão abertos e têm Serviço Social."""
    print("\n🏛️  PBH · EDITAIS GERAIS — iniciando varredura...")
    vistos = set()
    hoje = datetime.now().date()

    for situacao, rotulo in (("1", "novos"), ("2", "em andamento")):
        print(f"\n   🔎 Editais {rotulo}...")
        try:
            lista = _pbh_get(PBH_URL_LISTA, {"field_situacao_processo_seletivo_value": situacao})
        except Exception as e:
            print(f"   ⚠️  Erro ao abrir a lista: {e}")
            continue

        itens = re.findall(r'href="(/[^"#?]*/oportunidades-de-trabalho/[^"#?]+)"[^>]*>([^<]*)<', lista)
        if not itens:
            print("   🔚 Nenhum edital encontrado (a página pode ter mudado).")
            continue

        for caminho, titulo_raw in itens:
            link = PBH_BASE + caminho
            if link in vistos:
                continue
            vistos.add(link)
            if ja_enviada(cursor, link):
                continue

            titulo = html_lib.unescape(titulo_raw).replace('\xa0', ' ').strip() or "Edital PBH"
            try:
                texto = _pbh_texto(_pbh_get(link))
                time.sleep(0.5)   # pausa entre páginas, para não sobrecarregar o site da PBH
            except Exception as e:
                print(f"   ⚠️  Erro ao abrir {titulo[:50]}: {e}")
                continue

            inicio, fim = _pbh_periodo(texto)
            if fim is None:
                print(f"   ❔ Sem período de inscrição: {titulo[:60]}")
                continue
            if fim.date() < hoje:
                _pbh_marcar_visto(conn, cursor, link, fim.strftime("%d/%m/%Y"), "[PBH encerrado] " + titulo[:120])
                continue

            achou = PBH_CARGO_SOCIAL.search(texto)
            if not achou:
                _pbh_marcar_visto(conn, cursor, link, fim.strftime("%d/%m/%Y"), "[PBH sem Serviço Social] " + titulo[:120])
                print(f"   🚫 Aberto, sem Serviço Social: {titulo[:60]}")
                continue

            trecho = texto[max(0, achou.start() - 40): achou.end() + 90].strip()
            orgao = titulo.split(" -Edital")[0].split(" - Edital")[0].strip()
            nivel_match, techs = calcular_match(titulo + " " + achou.group(0))
            techs_str = " · ".join(t.upper() for t in techs[:4]) if techs else "Verificar edital"

            mensagem = (
                f"🏛️ <b>PREFEITURA DE BH — {html_lib.escape(rotulo.upper())}</b>\n\n"
                f"💼 <b>Edital:</b> {html_lib.escape(titulo)}\n"
                f"🏢 <b>Órgão:</b> {html_lib.escape(orgao)}\n"
                f"🧾 <b>Trecho do cargo:</b> <i>{html_lib.escape(trecho)}</i>\n"
                f"📅 <b>Inscrição:</b> {inicio.strftime('%d/%m/%Y') if inicio else '?'} até <b>{fim.strftime('%d/%m/%Y')}</b>\n"
                f"📊 <b>Match:</b> {nivel_match} · <i>{techs_str}</i>\n\n"
                f"🔗 <a href='{link}'>Abrir o edital</a>"
            )
            registrar_e_enviar(conn, cursor, link, titulo, orgao, fim.strftime("%d/%m/%Y"), mensagem, "PBH", nivel_match)

def buscar_vagas_pbh_saude(conn, cursor):
    """Página de processos seletivos da Saúde (Referência Técnica, TSS etc.).
    Lê a seção 'EM ANDAMENTO' e avisa os editais de Assistente Social ainda sem resultado."""
    print("\n🏥 PBH · SAÚDE — iniciando varredura...")
    try:
        pagina = _pbh_get(PBH_URL_SAUDE)
    except Exception as e:
        print(f"   ⚠️  Erro ao abrir a página: {e}")
        return

    corpo = pagina[pagina.find('<body'):]
    ini = corpo.find('EM ANDAMENTO')
    fim = corpo.find('FINALIZADOS', ini) if ini >= 0 else -1
    if ini < 0 or fim < 0:
        print("   🔚 Seção 'em andamento' não encontrada (a página pode ter mudado).")
        return

    secao = corpo[ini:fim]
    total = achados = 0
    for m in re.finditer(r'<p><strong>(SELE[^<]*)</strong></p>\s*<p>(.*?)</p>', secao, re.S):
        total += 1
        titulo = html_lib.unescape(re.sub(r'<[^>]+>', '', m.group(1))).replace('\xa0', ' ').strip()
        bloco = m.group(2)
        if not PBH_CARGO_SOCIAL.search(titulo):
            continue
        if 'resultado' in bloco.lower():
            continue   # já tem resultado publicado

        a = re.search(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', bloco, re.S)
        if not a:
            continue
        link = PBH_BASE + a.group(1) if a.group(1).startswith('/') else a.group(1)
        edital = html_lib.unescape(re.sub(r'<[^>]+>', '', a.group(2))).replace('\xa0', ' ').strip()
        if ja_enviada(cursor, link):
            continue

        achados += 1
        pub = re.search(r'/(\d{2})-(\d{2})-(\d{4})_', link)
        data_f = f"{pub.group(1)}/{pub.group(2)}/{pub.group(3)}" if pub else datetime.now().strftime("%d/%m/%Y")
        nivel_match, techs = calcular_match(titulo)
        techs_str = " · ".join(t.upper() for t in techs[:4]) if techs else "Verificar edital"

        mensagem = (
            f"🏥 <b>PREFEITURA DE BH · SAÚDE</b>\n\n"
            f"💼 <b>Seleção:</b> {html_lib.escape(titulo)}\n"
            f"📄 <b>Edital:</b> {html_lib.escape(edital)}\n"
            f"📅 <b>Publicado em:</b> {data_f}\n"
            f"⚠️ <i>O prazo de inscrição está no edital. Confira se a seleção aceita candidatos "
            f"externos, pois seleções de Referência Técnica podem ser restritas a servidores.</i>\n"
            f"📊 <b>Match:</b> {nivel_match} · <i>{techs_str}</i>\n\n"
            f"🔗 <a href='{link}'>Abrir o edital (PDF)</a>"
        )
        registrar_e_enviar(conn, cursor, link, titulo, "Secretaria Municipal de Saúde (PBH)", data_f, mensagem, "PBH-SAUDE", nivel_match)

    print(f"   📋 {total} seleções em andamento lidas, {achados} de Serviço Social novas.")

# --- MAIN ---

def main():
    if not DRY_RUN and (not TOKEN or not CHAT_ID):
        print("❌ ERRO: Token do Telegram ou Chat ID não encontrados no arquivo .env!")
        return

    conn, cursor = iniciar_banco()

    buscar_vagas_gupy(conn, cursor)
    buscar_vagas_programathor(conn, cursor)
    buscar_vagas_linkedin(conn, cursor)
    buscar_vagas_inhire(conn, cursor)
    buscar_vagas_solides(conn, cursor)

    if PBH_ATIVO:
        for busca in (buscar_vagas_pbh_editais, buscar_vagas_pbh_saude):
            try:
                busca(conn, cursor)
            except Exception as e:   # uma falha na PBH não pode derrubar as outras fontes
                print(f"   ⚠️  Erro inesperado em {busca.__name__}: {e}")

    conn.close()
    print("\n✅ Varredura completa de todas as fontes!")

if __name__ == '__main__':
    main()
