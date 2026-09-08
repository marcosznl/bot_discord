import os

from dotenv import load_dotenv

load_dotenv()

# Raiz do projeto (onde main.py mora) - usada pelos cogs para achar seus
# arquivos de dados (json/sqlite) independente de onde o cog em si esteja.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Pasta com os bancos sqlite (xp.db).
DATA_DIR = os.path.join(BASE_DIR, "data")

TOKEN = os.getenv("DISCORD_TOKEN")
GIPHY_API_KEY = os.getenv("GIPHY_API_KEY")
MC_BOOT_TIMEOUT = 300
MC_BOOT_CHECK_INTERVAL = 10
MC_SHUTDOWN_TIMEOUT = 60
# Tempo sem nenhum jogador conectado ate um servidor de Minecraft ser desligado
# automaticamente (evita deixar a maquina rodando um mundo vazio a toa).
MC_AUTO_DESLIGAR_SEGUNDOS = 180
MC_CHAT_CHANNEL_ID = int(os.getenv("MC_CHAT_CHANNEL_ID", "0") or 0)
# IP/host dinamico para conexao (edite no .env quando o IP mudar).
MC_HOST = os.getenv("MC_HOST", "")

# Endereco da API local do LM Studio (compativel com o formato da OpenAI).
LM_STUDIO_URL = os.getenv("LM_STUDIO_URL", "http://localhost:1234/v1/chat/completions")
LLM_PROMPT_SISTEMA = os.getenv(
    "LLM_PROMPT_SISTEMA",
    "Voce e um assistente respondendo em um servidor do Discord. Seja conciso. "
    "Responda sempre em portugues do Brasil (pt-br).",
)
LMS_CLI = os.path.expanduser("~/.lmstudio/bin/lms")
LLM_MODELO = os.getenv("LLM_MODELO", "google/gemma-4-e4b")
# Tempo sem nenhuma interacao ate a LLM local ser desligada automaticamente.
LLM_AUTO_DESLIGAR_SEGUNDOS = 180
# Quantas mensagens (usuario + assistente) ficam guardadas por conversa.
LLM_HISTORICO_MAX_MENSAGENS = 50

# Pasta que contem as subpastas de cada servidor de Minecraft (uma por
# servidor, com o nome usado em "dir" abaixo). Editavel via .env para migrar
# o bot para outra maquina/usuario sem mexer no codigo.
MC_SERVERS_DIR = os.path.expanduser(os.getenv("MC_SERVERS_DIR", "~/server_minecraft"))

MC_RCON_SENHA = os.getenv("MC_RCON_SENHA", "")

# Intervalo entre checagens de novos videos dos canais do YouTube acompanhados.
YOUTUBE_VERIFICAR_INTERVALO_MINUTOS = 60
# Pasta onde os videos sao baixados (tanto /baixarvideo quanto o aviso
# automatico de video novo) - baixados direto em Python (yt-dlp), sem depender
# de nenhum atalho do macOS.
YOUTUBE_DOWNLOAD_DIR = os.path.expanduser(
    os.getenv(
        "YOUTUBE_DOWNLOAD_DIR",
        "~/Library/Mobile Documents/com~apple~CloudDocs/Youtube Download",
    )
)

# IDs do Discord autorizados a usar os comandos pessoais do bot (youtube,
# baixarvideo, baixaraudio, mc, llm) tanto por DM quanto no servidor - lista
# editavel no .env (separada por virgula) sem precisar mexer no codigo.
USUARIOS_AUTORIZADOS = {
    int(uid) for uid in os.getenv("USUARIOS_AUTORIZADOS", "139432275984121856,494115635681558528").split(",") if uid.strip()
}
