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

# IDs do Discord autorizados a usar os comandos pessoais do bot (status),
# so por DM - lista editavel no .env (separada por virgula) sem precisar
# mexer no codigo.
USUARIOS_AUTORIZADOS = {
    int(uid) for uid in os.getenv("USUARIOS_AUTORIZADOS", "").split(",") if uid.strip()
}
