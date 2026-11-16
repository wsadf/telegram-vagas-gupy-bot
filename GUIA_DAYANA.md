# Bot de vagas de Assistente Social (Dayana)

Adaptação do bot [telegram-vagas-gupy-bot](https://github.com/carlosandrercouto/telegram-vagas-gupy-bot).
Ele busca vagas de Assistente Social e Analista Social e avisa no Telegram.

## O que foi alterado
- Buscas: "assistente social", "analista social" e "serviço social" na Gupy, LinkedIn (Belo Horizonte) e Solides.
- Local: Gupy e Solides só enviam vagas de Minas Gerais e da região metropolitana de BH (lista `CIDADES_PERMITIDAS` no `main.py`).
- Título: só passam títulos com "social", "CRAS", "CREAS", "SUAS", "reassentamento", "habitação", "perícia" e termos parecidos. Isso tira vagas como "assistente administrativo".
- Bloqueios: estágio, aprendiz, vendas, marketing, "social media", "redes sociais" e similares.
- Match: o nível (Alto, Médio, Padrão, Baixo) usa termos do perfil dela, como habitação, reassentamento, SUAS e perícia.
- ProgramaThor e InHire foram desativados, porque só têm vagas de tecnologia.
- Banco `vagas_gupy.db` do projeto original foi apagado. Será criado vazio na primeira execução.
- Novo modo de teste: `DRY_RUN=1` mostra os alertas no terminal sem enviar nada.
- `rodar_gupy.bat` agora roda na própria pasta.

## Prefeitura de Belo Horizonte (fonte nova)
O bot lê duas páginas da PBH:
- **Oportunidades de trabalho** (editais novos e em andamento): abre cada edital, lê o "período da inscrição" e avisa só os que **ainda estão abertos** e citam Assistente Social, Serviço Social ou Analista Social. A lista da PBH marca como "em andamento" editais antigos já encerrados, por isso o bot confere a data.
- **Saúde, processos seletivos:** avisa os editais de Assistente Social da seção "em andamento" que ainda não têm resultado. O prazo está no PDF do edital. Seleções de Referência Técnica podem ser restritas a servidores, então confirme no edital.

Para desligar, use `PBH_ATIVO = False` no `main.py`.

## O que o bot NÃO cobre
Instituto Elo, Quatre, URBEL, concursos e tribunais não passam pelo bot. Continue conferindo:
- https://www.parceriaspublicas.institutoelo.org.br/trabalhe-conosco
- https://quatre.com.br/oportunidades/assistente-social-bh/

## Passo a passo

### 1. Pré-requisito
Python 3.8 ou superior (python.org). Marque "Add Python to PATH" na instalação.

### 2. Criar o bot no Telegram
1. No Telegram, fale com `@BotFather`, envie `/newbot` e guarde o token.
2. Crie um grupo só dela, adicione o bot e envie uma mensagem qualquer.
3. Abra no navegador `https://api.telegram.org/bot<TOKEN>/getUpdates` e copie o número em `"chat"` → `"id"`.
4. Não compartilhe o token nem essa página.

### 3. Testar sem enviar nada
No terminal, dentro desta pasta:
```bash
pip install -r requirements.txt
set DRY_RUN=1
set BANCO_VAGAS=teste.db
python main.py
```
Os alertas aparecem no terminal. Apague `teste.db` depois.

### 4. Usar no computador
Copie `.env.example` para `.env` e preencha:
```
TELEGRAM_TOKEN=...
CHAT_ID_GRUPO=...
```
Rode `python main.py` ou dê dois cliques em `rodar_gupy.bat`. Para automatizar no Windows, agende o `.bat` no Agendador de Tarefas.

### 5. Usar sem deixar o computador ligado (GitHub Actions)
1. Crie um repositório **privado** no GitHub e envie esta pasta (o banco de vagas é salvo nele).
2. Em Settings, Secrets and variables, Actions, crie `TELEGRAM_TOKEN` e `CHAT_ID_GRUPO`.
3. Na aba Actions, ative os workflows e rode "Vagas Bot" uma vez em "Run workflow".
4. Horários: segunda a sexta às 8h, 10h, 12h, 14h, 16h, 18h e 20h (Brasília), e fim de semana às 10h, 14h e 18h.

## Ajustes comuns (seção "Configurações do usuário" do `main.py`)
- Mais cidades: edite `CIDADES_PERMITIDAS`. Lista vazia aceita qualquer cidade.
- Vagas remotas: adicione na `FILTROS_GUPY` um item com `'workplaceTypes': 'remote'` e o nome contendo "REMOTO".
- Perícia e gestão: adicione termos em `FILTROS_GUPY`, `FILTROS_LINKEDIN` e `FILTROS_SOLIDES` (ex.: "coordenador de projetos sociais", "responsabilidade social").
- Primeira execução: aumente `DIAS_BUSCA_GUPY` e `DIAS_BUSCA_SOLIDES` para 30 e depois volte.
- Empresa que não interessa: adicione em `EMPRESAS_IGNORADAS`.

## Limitações
- Gupy, LinkedIn e Solides são lidos por endereços não documentados. Podem mudar e quebrar o bot sem aviso.
- Na Gupy e no LinkedIn o match olha só o título.
- O filtro de cidade depende do campo preenchido pela empresa. Campo vazio não bloqueia a vaga.
