import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

# Raiz do projeto (onde main.py mora) - usada pelos cogs para achar seus
# arquivos de dados (json/sqlite) independente de onde o cog em si esteja.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Pasta com os bancos sqlite (xp.db, moderacao.db, comunidade.db). sqlite nao
# cria a pasta sozinho, so o arquivo.
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

TOKEN = os.getenv("DISCORD_TOKEN")
GIPHY_API_KEY = os.getenv("GIPHY_API_KEY")

# Fuso usado para interpretar datas digitadas (/evento, /lembrete) - os
# usuarios escrevem "25/12 20:00" pensando no horario local, nao em UTC.
FUSO = ZoneInfo(os.getenv("FUSO_HORARIO", "America/Sao_Paulo"))
