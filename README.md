# bot_discord

Bot de Discord em Python ([discord.py](https://github.com/Rapptz/discord.py) 2.7) com moderação, comunidade, níveis (XP), música, voz e diversão. Só usa slash commands e menus de clique direito, e os painéis usam o layout novo do Discord (Components V2). Todos os botões de painel (cargos, tickets, sorteios, sugestões, verificação, candidaturas) continuam funcionando depois que o bot reinicia.

## Comandos

### Geral
| Comando | O que faz |
| --- | --- |
| `/ping` | Mostra se o bot está online e a latência |
| `/ajuda` | Lista os comandos, com um menu para escolher a categoria |
| `/usuario [usuario]` | Cartão de perfil: datas de criação e entrada, cargos, banner |
| `/avatar [usuario]` | Avatar em tamanho grande (com o do servidor, se houver) e links para baixar |
| `/servidor` | Informações do servidor: dono, membros, canais, impulsos, banner |

### Moderação
| Comando | O que faz |
| --- | --- |
| `/mod silenciar` / `dessilenciar` | Castigo do Discord por 1 min a 28 dias |
| `/mod expulsar` | Expulsa e avisa a pessoa por DM |
| `/mod banir` | Bane (também por ID, mesmo de quem já saiu) e pode apagar mensagens recentes |
| `/mod desbanir` | Desbane, com a lista de banidos no autocomplete |
| `/mod advertir` | Registra uma advertência, avisa por DM e aplica a punição automática se atingir o limite |
| `/mod advertencias` | Histórico de advertências de alguém (ativas e expiradas) |
| `/mod perdoar` | Apaga as advertências de alguém |
| `/mod limpar` | Apaga até 200 mensagens, com filtro opcional por pessoa |
| `/mod lento` | Liga ou desliga o modo lento do canal |
| `/mod trancar` / `destrancar` | Impede ou libera mensagens de quem não tem cargo especial |
| `/logs` | Escolhe o canal que recebe os logs da moderação |
| `/automod` | Painel das regras nativas do AutoMod (palavrões, spam, spam de menções, palavras proibidas) |
| `/verificacao` | Painel com botão "Verificar" que dá o cargo de membro, com idade mínima de conta opcional |

O canal de `/logs` recebe:
- mensagens apagadas e editadas
- entradas e saídas do servidor e das calls
- banimentos, expulsões, castigos, mudanças de cargo e de apelido, com quem fez e o motivo (feitos pelo bot ou não)
- convites criados
- advertências, denúncias, verificações recusadas e alertas do AutoMod
- a conversa completa de cada ticket fechado

Hierarquia: ninguém consegue moderar alguém com cargo igual ou acima do seu (ou do bot), nem o dono do servidor.

### Comunidade
| Comando | O que faz |
| --- | --- |
| `/enquete` | Enquete nativa do Discord, com 2 a 10 opções, duração e múltipla escolha |
| `/sugerir` | Manda uma sugestão para votação (👍/👎). A equipe aprova ou recusa com resposta, e o autor recebe por DM. Funciona em canal de texto (com thread para discutir) ou em fórum (com etiqueta de status) |
| `/sorteio criar` / `encerrar` / `ressortear` | Sorteio com botão de participar, vários ganhadores, cargo obrigatório opcional e encerramento automático |
| `/cargos` | Painel com até 20 botões: clica para pegar o cargo, clica de novo para largar |
| `/tickets` | Painel que abre uma thread privada com a equipe. O ticket tem botões para fechar e baixar a conversa; ao fechar, a conversa vai para os logs e para quem abriu |
| `/candidaturas` | Painel com formulário (área, motivo, disponibilidade, até 3 arquivos, aceite das regras). A equipe aprova ou recusa; quem for aprovado pode ganhar um cargo |
| `/evento` | Cria um evento agendado (em canal de voz/palco ou externo), com capa opcional |
| `/lembrete criar` / `listar` / `cancelar` | Lembrete por tempo (`10m`, `1h30m`) ou data (`25/12 20:00`, `amanha 9h`) |
| `/aniversario definir` / `remover` / `proximos` | Aniversários do servidor, com parabéns às 9h e cargo de aniversariante do dia |

### Voz e música
| Comando | O que faz |
| --- | --- |
| `/musica tocar` | Toca ou põe na fila (link do YouTube ou busca) |
| `/musica pular` / `anterior` / `parar` | Controles da reprodução |
| `/musica fila` / `limpar` | Ver ou limpar a fila |
| `/musica painel` | Painel "tocando agora" com capa, barra de progresso, próximas da fila e todos os controles em botões |
| `/som` | Toca um som do soundboard (do servidor ou padrão do Discord) na sua call |
| `/sala renomear` / `limite` / `trancar` / `destrancar` / `expulsar` / `transferir` | Controles da sua sala de voz temporária |

A fila de música é salva: se o bot reiniciar e ainda tiver gente na call, ele volta e continua tocando. O bot sai sozinho da call depois de 5 minutos sem tocar nada.

Salas temporárias: quem entra no canal configurado em `/configurar salas` ganha uma sala própria, que é apagada quando fica vazia.

### Níveis e jogos
| Comando | O que faz |
| --- | --- |
| `/xp` | Menu com seu rank, o ranking do servidor, como funciona e as configurações (admin) |
| `/interagir` | 25 interações com gif de anime (abraçar, beijar, cafuné, toca aqui, guerra de travesseiro…). Quem recebe pode clicar em **Retribuir** |
| `/gif` | Busca um gif no Giphy |

XP:
- Mensagens dão de 15 a 25 XP (no máximo uma vez a cada 20 s).
- Ficar em call com pelo menos mais uma pessoa dá 5 XP por minuto. Não conta quem está com o fone desligado nem no canal AFK.
- Quem tem Gerenciar Servidor pode ligar ou desligar o sistema, zerar o XP do servidor, dar ou tirar XP e configurar cargos automáticos por nível.

Também tem o **jogo de contagem**: num canal configurado, cada pessoa manda o próximo número; se alguém errar ou contar duas vezes seguidas, recomeça do 1. O bot guarda o recorde.

O **mural de destaques** manda para um canal as mensagens que juntam X reações ⭐ (ou outro emoji) e atualiza o contador.

### Configuração
| Comando | O que faz |
| --- | --- |
| `/configurar ver` | Mostra tudo o que está ligado no servidor |
| `/configurar sugestoes` | Canal (texto ou fórum) das sugestões |
| `/configurar aniversarios` | Canal dos parabéns e cargo de aniversariante |
| `/configurar destaques` | Canal do mural, quantidade mínima e emoji |
| `/configurar contagem` | Canal do jogo de contagem |
| `/configurar salas` | Canal de voz que cria as salas temporárias |
| `/configurar advertencias` | Limite de advertências, punição automática (silenciar, expulsar ou banir) e validade |
| `/configurar xp_voz` | Liga ou desliga o XP em call |
| `/boasvindas` | Canal e texto de boas-vindas para novos membros |

Para desligar um recurso, use o `/configurar` dele sem escolher canal.

### Menus de clique direito (Apps)
- **Em uma pessoa:** Ver perfil, Ver avatar, Ver rank XP
- **Em uma mensagem:** Denunciar mensagem (vai para o canal de logs), Fixar ou desafixar (precisa de Gerenciar Mensagens)

`/ping`, `/ajuda`, `/usuario`, `/avatar`, `/gif`, `/enquete`, `/lembrete` e os menus Ver perfil e Ver avatar também funcionam em DM e se o bot for instalado na sua conta ("Adicionar aos meus apps").

## Instalação

Requer Python 3.10 ou mais novo e o `ffmpeg` no PATH (para música).

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

Crie um `.env` na raiz:

```
DISCORD_TOKEN=token-do-bot
GIPHY_API_KEY=chave-da-api-do-giphy
# Opcional, padrão America/Sao_Paulo - fuso das datas digitadas e dos parabéns
FUSO_HORARIO=America/Sao_Paulo
```

No [Developer Portal](https://discord.com/developers/applications), em **Bot > Privileged Gateway Intents**, ligue **Server Members Intent** e **Message Content Intent**. Sem isso o bot não conecta.

Para convidar o bot com todas as permissões que ele usa:

```
https://discord.com/oauth2/authorize?client_id=SEU_CLIENT_ID&scope=bot+applications.commands&permissions=58678131944694
```

O cargo do bot precisa ficar **acima** dos cargos que ele vai dar (cargos por nível, painel de cargos, verificação, aniversário, candidatura) e das pessoas que ele vai moderar.

Para rodar:

```bash
venv/bin/python main.py
```

## Permissões dos comandos

Os comandos de administração já vêm escondidos para quem não tem a permissão correspondente:

| Permissão | Comandos |
| --- | --- |
| Moderar Membros | `/mod` |
| Gerenciar Servidor | `/logs`, `/automod`, `/boasvindas`, `/tickets`, `/candidaturas`, `/sorteio`, `/configurar` |
| Gerenciar Cargos | `/cargos`, `/verificacao` |
| Gerenciar Eventos | `/evento` |

Para mudar quem vê cada um, use **Configurações do servidor > Integrações > nome do bot**. Cada subcomando de `/mod` ainda confere a permissão específica dele (por exemplo, Banir Membros no `/mod banir`).

Cargos com permissões de moderação ou administração nunca são entregues por botão (painel de cargos, verificação, candidatura aprovada).

## Dados

Tudo fica em `data/` (fora do git):

| Arquivo | O que guarda |
| --- | --- |
| `xp.db` | XP e cargos por nível |
| `moderacao.db` | Advertências e canal de logs |
| `comunidade.db` | Lembretes |
| `bot.db` | Sorteios, sugestões, aniversários, mural, contagem, salas, candidaturas e o `/configurar` |
| `musica_fila.json` | Fila de música |

A configuração de boas-vindas fica em `boas_vindas.json`. As regras do AutoMod ficam no próprio Discord, com o prefixo `[Bot]`.

## Testes

```bash
venv/bin/pip install -r requirements-dev.txt
venv/bin/pytest
```

Além das funções puras e dos bancos de cada recurso, os testes carregam o bot inteiro sem conectar e serializam todos os comandos, painéis e formulários. Isso pega nome inválido, limite de componentes e erro de import antes de subir o bot de verdade.
