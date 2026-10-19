"""Traducao dos comandos para quem usa o Discord em ingles: nomes e
descricoes de comandos, grupos e menus de clique direito. Parametros e
respostas continuam em portugues."""

import discord
from discord import app_commands

IDIOMAS = {discord.Locale.american_english, discord.Locale.british_english}

NOMES = {
    # comandos e grupos
    "ajuda": "help", "ping": "ping", "usuario": "user", "avatar": "avatar", "servidor": "server",
    "mod": "mod", "logs": "logs", "automod": "automod", "verificacao": "verification",
    "enquete": "poll", "sugerir": "suggest", "sorteio": "giveaway", "cargos": "roles",
    "tickets": "tickets", "candidaturas": "applications", "evento": "event", "lembrete": "reminder",
    "aniversario": "birthday", "anuncio": "announcement", "musica": "music", "som": "sound",
    "sala": "room", "palco": "stage", "xp": "xp", "interagir": "interact", "gif": "gif",
    "emoji": "emoji", "cargo_visual": "role_style", "configurar": "settings", "boasvindas": "welcome",
    # subcomandos
    "silenciar": "timeout", "dessilenciar": "untimeout", "expulsar": "kick", "banir": "ban",
    "desbanir": "unban", "advertir": "warn", "advertencias": "warnings", "perdoar": "pardon",
    "limpar": "clear", "lento": "slowmode", "trancar": "lock", "destrancar": "unlock", "raid": "raid",
    "tocar": "play", "pular": "skip", "anterior": "previous", "parar": "stop", "fila": "queue",
    "painel": "panel", "criar": "create", "listar": "list", "cancelar": "cancel", "encerrar": "end",
    "ressortear": "reroll", "renomear": "rename", "limite": "limit", "transferir": "transfer",
    "adicionar": "add", "copiar": "copy", "figurinha": "sticker", "abrir": "open", "fechar": "close",
    "palestrante": "speaker", "plateia": "audience", "definir": "set", "remover": "remove",
    "proximos": "upcoming", "agendados": "scheduled", "sugestoes": "suggestions",
    "aniversarios": "birthdays", "destaques": "starboard", "contagem": "counting", "salas": "rooms",
    "xp_voz": "voice_xp", "antiraid": "antiraid", "eventos": "events", "auto_thread": "auto_thread",
    "tela_boas_vindas": "welcome_screen", "integracao_pergunta": "onboarding_question", "ver": "view",
    # menus de clique direito
    "Ver perfil": "View profile", "Ver avatar": "View avatar", "Ver rank XP": "View XP rank",
    "Denunciar mensagem": "Report message", "Fixar ou desafixar": "Pin or unpin",
    "Adicionar emojis ao servidor": "Add emojis to server",
}

DESCRICOES = {
    "Mostra os comandos do bot que voce pode usar, por categoria": "Shows the bot commands you can use, by category",
    "Testa se o bot esta online e mostra a latencia": "Checks if the bot is online and shows the latency",
    "Mostra o perfil de alguem (ou o seu)": "Shows someone's profile (or yours)",
    "Mostra o avatar de alguem em tamanho grande": "Shows someone's avatar in full size",
    "Mostra informacoes deste servidor": "Shows information about this server",
    "Ferramentas de moderacao": "Moderation tools",
    "Silencia um membro por um tempo (castigo do Discord)": "Times out a member for a while",
    "Tira o silencio (castigo) de um membro": "Removes a member's timeout",
    "Expulsa um membro do servidor (ele pode voltar com convite)": "Kicks a member (they can rejoin with an invite)",
    "Bane alguem do servidor (funciona ate com quem ja saiu, pelo ID)": "Bans someone (works by ID even if they left)",
    "Remove o banimento de alguem": "Unbans someone",
    "Registra uma advertencia para um membro (ele recebe por DM)": "Warns a member (they get a DM)",
    "Lista as advertencias de um membro": "Lists a member's warnings",
    "Apaga todas as advertencias de um membro": "Clears all of a member's warnings",
    "Apaga mensagens recentes deste canal": "Deletes recent messages in this channel",
    "Liga ou desliga o modo lento deste canal": "Turns slowmode on or off in this channel",
    "Impede todo mundo (sem cargo especial) de falar neste canal": "Stops everyone without a special role from talking here",
    "Libera de novo o canal trancado": "Unlocks a locked channel",
    "Pausa ou libera convites e DMs entre membros (modo de seguranca do Discord)": "Pauses or resumes invites and member DMs (Discord safety mode)",
    "Configura o canal de logs da moderacao": "Sets the moderation log channel",
    "Liga/desliga as regras de moderacao automatica do Discord": "Turns Discord AutoMod rules on or off",
    "Publica um painel de verificacao que da o cargo de membro": "Posts a verification panel that gives the member role",
    "Cria uma enquete nativa do Discord": "Creates a native Discord poll",
    "Manda uma sugestao para o servidor votar": "Sends a suggestion for the server to vote on",
    "Sorteios com botao de participar": "Giveaways with a join button",
    "Cria um sorteio neste canal": "Creates a giveaway in this channel",
    "Encerra um sorteio agora e sorteia os ganhadores": "Ends a giveaway now and picks the winners",
    "Sorteia mais um ganhador de um sorteio ja encerrado": "Picks one more winner for an ended giveaway",
    "Publica um painel onde os membros pegam cargos com um clique": "Posts a panel where members pick roles with one click",
    "Publica um painel para membros abrirem tickets privados com a equipe": "Posts a panel for members to open private tickets",
    "Publica um painel para membros se candidatarem a equipe": "Posts a panel for members to apply to the staff",
    "Cria um evento agendado no servidor": "Creates a scheduled event",
    "Lembretes que o bot te manda na hora marcada": "Reminders the bot sends you on time",
    "Cria um lembrete": "Creates a reminder",
    "Mostra seus lembretes pendentes": "Shows your pending reminders",
    "Cancela um lembrete pendente": "Cancels a pending reminder",
    "Aniversarios do servidor": "Server birthdays",
    "Cadastra o seu aniversario": "Sets your birthday",
    "Tira o seu aniversario da lista": "Removes your birthday",
    "Mostra os proximos aniversarios do servidor": "Shows upcoming birthdays",
    "Anuncios no layout novo, na hora ou agendados": "Announcements, now or scheduled",
    "Escreve um anuncio (com imagem e botao) para agora ou para depois": "Writes an announcement (with image and button), now or later",
    "Lista os anuncios agendados": "Lists scheduled announcements",
    "Cancela um anuncio agendado": "Cancels a scheduled announcement",
    "Musica no canal de voz": "Music in the voice channel",
    "Toca uma musica (ou poe na fila) - link do YouTube ou busca": "Plays a song (or queues it) - YouTube link or search",
    "Pula para a proxima musica da fila": "Skips to the next song",
    "Volta para a musica anterior": "Goes back to the previous song",
    "Para a musica, limpa a fila e sai do canal de voz": "Stops the music, clears the queue and leaves",
    "Mostra a fila de musicas": "Shows the queue",
    "Limpa a fila (a musica atual continua tocando)": "Clears the queue (the current song keeps playing)",
    "Abre o painel com o que esta tocando e os botoes de controle": "Opens the now playing panel with controls",
    "Soundboard do servidor": "Server soundboard",
    "Toca um som do soundboard no seu canal de voz": "Plays a soundboard sound in your voice channel",
    "Adiciona um som ao soundboard do servidor": "Adds a sound to the server soundboard",
    "Controla a sua sala de voz temporaria": "Controls your temporary voice room",
    "Muda o nome da sua sala": "Renames your room",
    "Limita quantas pessoas cabem na sua sala": "Limits how many people fit in your room",
    "Ninguem novo entra na sua sala (quem ja esta fica)": "Nobody new can join your room",
    "Libera a entrada na sua sala": "Lets people join your room again",
    "Tira alguem da sua sala": "Removes someone from your room",
    "Passa a sua sala para outra pessoa": "Gives your room to someone else",
    "Abre e organiza palcos (canais de palco)": "Opens and manages stage channels",
    "Abre um palco com um tema": "Opens a stage with a topic",
    "Encerra o palco": "Ends the stage",
    "Sobe alguem da plateia para falar no palco": "Invites someone from the audience to speak",
    "Desce alguem para a plateia": "Moves someone to the audience",
    "Ve seu rank, o leaderboard do servidor ou configura o sistema de XP": "Your rank, the leaderboard and XP settings",
    "Interage com alguem usando um gif de anime": "Interacts with someone using an anime gif",
    "Busca um gif pela palavra-chave": "Searches for a gif",
    "Adiciona emojis, figurinhas e sons ao servidor": "Adds emojis, stickers and sounds to the server",
    "Cria um emoji a partir de uma imagem": "Creates an emoji from an image",
    "Copia um emoji de outro servidor (cole o emoji aqui)": "Copies an emoji from another server (paste it here)",
    "Cria uma figurinha a partir de uma imagem": "Creates a sticker from an image",
    "Muda cor (com degrade), icone ou emoji de um cargo": "Changes a role's color (gradient), icon or emoji",
    "Liga e configura os recursos do bot neste servidor": "Turns on and configures the bot's features",
    "Canal (de texto ou forum) que recebe o /sugerir": "Text or forum channel that gets /suggest",
    "Canal dos parabens e cargo de aniversariante do dia": "Birthday channel and birthday role",
    "Mural: mensagens com varias reacoes vao para um canal": "Starboard: messages with many reactions go to a channel",
    "Canal do jogo de contagem (1, 2, 3...)": "Counting game channel (1, 2, 3...)",
    "Canal de voz que cria uma sala temporaria para quem entrar": "Voice channel that creates a temporary room",
    "Validade das advertencias e punicao automatica": "Warning expiry and automatic punishment",
    "Liga ou desliga o XP por tempo em call": "Turns voice XP on or off",
    "Alerta (e pausa convites) quando muita gente entra de uma vez": "Alerts (and pauses invites) on mass joins",
    "Aviso antes dos eventos e cargo para quem marcou interesse": "Event reminders and an interested role",
    "Liga ou desliga a thread automatica em cada mensagem de um canal": "Turns automatic threads on or off for a channel",
    "Tela de boas-vindas do Discord (servidores da Comunidade)": "Discord welcome screen (Community servers)",
    "Adiciona uma pergunta de cargos na integracao inicial do Discord": "Adds a role question to Discord onboarding",
    "Mostra como o bot esta configurado neste servidor": "Shows the bot's settings for this server",
    "Configura a mensagem de boas-vindas para novos membros": "Sets the welcome message for new members",
}

_LOCAIS_NOME = {
    app_commands.TranslationContextLocation.command_name,
    app_commands.TranslationContextLocation.group_name,
}
_LOCAIS_DESCRICAO = {
    app_commands.TranslationContextLocation.command_description,
    app_commands.TranslationContextLocation.group_description,
}


class TradutorIngles(app_commands.Translator):
    async def translate(
        self, string: app_commands.locale_str, locale: discord.Locale, context: app_commands.TranslationContextTypes
    ) -> str | None:
        if locale not in IDIOMAS:
            return None
        if context.location in _LOCAIS_NOME:
            return NOMES.get(string.message)
        if context.location in _LOCAIS_DESCRICAO:
            return DESCRICOES.get(string.message)
        return None
