import discord
from discord.ext import commands
import youtube_dl
import asyncio
from collections import deque
import requests
from io import BytesIO

# Configurações
TOKEN = 'SEU_TOKEN_DO_BOT'  # Substitua pelo token real!
PREFIX = '!'  # Prefixo de comandos
BOT_OWNER_MESSAGE = "Quem programou isso foi o Erik e nem o Nata nem o Gabriel ajudaram nem com o token!"
MR_ROBOT_AVATAR_URL = "https://i.imgur.com/JL3GXQj.jpg"  # Imagem do Mr. Robot

# Intents (permissões)
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# Configuração do youtube_dl (com fallback para yt-dlp)
try:
    import yt_dlp as youtube_dl
except ImportError:
    import youtube_dl

youtube_dl.utils.bug_reports_message = lambda: ''
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'extract_flat': 'in_playlist'
}
ffmpeg_options = {
    'options': '-vn -af loudnorm=I=-16:LRA=11:TP=-1.5',
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

# Estruturas de dados para cada servidor
class GuildMusicState:
    def __init__(self):
        self.queue = deque()
        self.current_song = None
        self.volume = 0.5
        self.loop = False
        self.skip_votes = set()

# Dicionário para armazenar estados por servidor
guild_states = {}

def get_guild_state(guild_id):
    if guild_id not in guild_states:
        guild_states[guild_id] = GuildMusicState()
    return guild_states[guild_id]

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.duration = data.get('duration')
        self.thumbnail = data.get('thumbnail') or data.get('thumbnails', [{}])[0].get('url')
        self.uploader = data.get('uploader')
        self.views = data.get('view_count')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        
        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
            
            if 'entries' in data:
                data = data['entries'][0]
            
            filename = data['url'] if stream else ytdl.prepare_filename(data)
            return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)
        except Exception as e:
            raise Exception(f"Erro ao processar o vídeo: {str(e)}")

async def play_next(ctx):
    guild_state = get_guild_state(ctx.guild.id)
    
    if guild_state.loop and guild_state.current_song:
        player = await YTDLSource.from_url(guild_state.current_song.url, loop=bot.loop, stream=True)
        player.volume = guild_state.volume
    elif guild_state.queue:
        player = guild_state.queue.popleft()
        player.volume = guild_state.volume
        guild_state.current_song = player
        guild_state.skip_votes.clear()
    else:
        guild_state.current_song = None
        await ctx.send("🎵 Fila de reprodução vazia. Use `!play` para adicionar mais músicas!")
        return

    voice_client = ctx.voice_client
    
    def after_playing(error):
        if error:
            print(f'Erro: {error}')
        coro = play_next(ctx)
        asyncio.run_coroutine_threadsafe(coro, bot.loop)
    
    voice_client.play(player, after=after_playing)
    
    embed = discord.Embed(
        title="🎶 Tocando agora",
        description=f"[{player.title}]({player.url})",
        color=discord.Color.blue()
    )
    
    if player.thumbnail:
        embed.set_thumbnail(url=player.thumbnail)
    
    info_fields = []
    
    if player.duration:
        minutes, seconds = divmod(player.duration, 60)
        info_fields.append(f"⏳ Duração: {minutes}:{seconds:02d}")
    
    if player.uploader:
        info_fields.append(f"🎤 Artista: {player.uploader}")
    
    if player.views:
        info_fields.append(f"👀 Visualizações: {player.views:,}")
    
    if info_fields:
        embed.add_field(name="Informações", value="\n".join(info_fields), inline=False)
    
    embed.set_footer(
        text=f"Volume: {int(guild_state.volume * 100)}% | Loop: {'✅' if guild_state.loop else '❌'} | {BOT_OWNER_MESSAGE}"
    )
    
    await ctx.send(embed=embed)

@bot.event
async def on_ready():
    print(f'Bot {bot.user.name} está online!')
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening, 
        name=f"{PREFIX}help | {BOT_OWNER_MESSAGE}"
    ))
    
    # Configurar avatar do Mr. Robot
    try:
        response = requests.get(MR_ROBOT_AVATAR_URL)
        avatar = BytesIO(response.content)
        await bot.user.edit(avatar=avatar.read())
        print("Avatar atualizado com sucesso!")
    except Exception as e:
        print(f"Não foi possível atualizar o avatar: {e}")

@bot.command(name='play', aliases=['p'], help='Toca uma música do YouTube')
async def play(ctx, *, query):
    if not ctx.message.author.voice:
        await ctx.send("❌ Você precisa estar em um canal de voz!")
        return

    voice_client = ctx.voice_client
    
    if not voice_client:
        channel = ctx.message.author.voice.channel
        try:
            voice_client = await channel.connect()
        except discord.ClientException as e:
            await ctx.send(f"❌ Não consegui conectar ao canal: {e}")
            return
    
    guild_state = get_guild_state(ctx.guild.id)
    
    # Verifica se é URL ou termo de busca
    if not query.startswith(('http://', 'https://')):
        query = f"ytsearch:{query}"
    
    async with ctx.typing():
        try:
            player = await YTDLSource.from_url(query, loop=bot.loop, stream=True)
        except Exception as e:
            await ctx.send(f"❌ Erro ao buscar a música: {e}")
            return
    
    if voice_client.is_playing() or voice_client.is_paused():
        guild_state.queue.append(player)
        embed = discord.Embed(
            description=f"🎵 **{player.title}** adicionado à fila (posição #{len(guild_state.queue)})",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
    else:
        guild_state.current_song = player
        player.volume = guild_state.volume
        voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
        
        embed = discord.Embed(
            title="🎶 Tocando agora",
            description=f"[{player.title}]({player.url})",
            color=discord.Color.blue()
        )
        if player.thumbnail:
            embed.set_thumbnail(url=player.thumbnail)
        if player.duration:
            minutes, seconds = divmod(player.duration, 60)
            embed.add_field(name="Duração", value=f"{minutes}:{seconds:02d}", inline=True)
        embed.set_footer(text=f"Volume: {int(guild_state.volume * 100)}% | {BOT_OWNER_MESSAGE}")
        
        await ctx.send(embed=embed)

@bot.command(name='stop', help='Para a música e limpa a fila')
async def stop(ctx):
    voice_client = ctx.voice_client
    if not voice_client or not voice_client.is_connected():
        return await ctx.send("❌ Não estou conectado a um canal de voz!")
    
    guild_state = get_guild_state(ctx.guild.id)
    guild_state.queue.clear()
    guild_state.current_song = None
    guild_state.skip_votes.clear()
    voice_client.stop()
    await voice_client.disconnect()
    
    embed = discord.Embed(
        description="🛑 Música parada e fila limpa!",
        color=discord.Color.red()
    )
    embed.set_footer(text=BOT_OWNER_MESSAGE)
    await ctx.send(embed=embed)

@bot.command(name='pause', help='Pausa a música atual')
async def pause(ctx):
    voice_client = ctx.voice_client
    if not voice_client or not voice_client.is_playing():
        return await ctx.send("❌ Nada está tocando no momento!")
    
    if voice_client.is_paused():
        return await ctx.send("⏸ A música já está pausada!")
    
    voice_client.pause()
    
    embed = discord.Embed(
        description="⏸ Música pausada!",
        color=discord.Color.orange()
    )
    embed.set_footer(text=BOT_OWNER_MESSAGE)
    await ctx.send(embed=embed)

@bot.command(name='resume', help='Continua a música pausada')
async def resume(ctx):
    voice_client = ctx.voice_client
    if not voice_client or not voice_client.is_paused():
        return await ctx.send("❌ Nada está pausado no momento!")
    
    voice_client.resume()
    
    embed = discord.Embed(
        description="▶ Música continuada!",
        color=discord.Color.green()
    )
    embed.set_footer(text=BOT_OWNER_MESSAGE)
    await ctx.send(embed=embed)

@bot.command(name='skip', aliases=['s'], help='Pula a música atual')
async def skip(ctx):
    voice_client = ctx.voice_client
    if not voice_client or not voice_client.is_playing():
        return await ctx.send("❌ Nada está tocando no momento!")
    
    guild_state = get_guild_state(ctx.guild.id)
    required_votes = max(2, len(voice_client.channel.members) // 2)  # 50% dos usuários
    
    if ctx.author.guild_permissions.administrator or ctx.author == ctx.guild.owner:
        voice_client.stop()
        await ctx.send("⏭ Música pulada por um administrador!")
        return
    
    guild_state.skip_votes.add(ctx.author.id)
    
    if len(guild_state.skip_votes) >= required_votes:
        voice_client.stop()
        await ctx.send("⏭ Música pulada por votação!")
    else:
        await ctx.send(f"✋ Voto para pular registrado! ({len(guild_state.skip_votes)}/{required_votes} votos necessários)")

@bot.command(name='queue', aliases=['q', 'fila'], help='Mostra a fila de músicas')
async def queue(ctx):
    guild_state = get_guild_state(ctx.guild.id)
    
    if not guild_state.queue and not guild_state.current_song:
        return await ctx.send("❌ A fila está vazia!")
    
    embed = discord.Embed(title="🎵 Fila de Músicas", color=discord.Color.gold())
    
    if guild_state.current_song:
        current = guild_state.current_song
        embed.add_field(
            name="Tocando agora",
            value=f"[{current.title}]({current.url})",
            inline=False
        )
    
    if guild_state.queue:
        queue_list = "\n".join(
            f"{i+1}. [{song.title}]({song.url})" 
            for i, song in enumerate(guild_state.queue[:10])
        )
        embed.add_field(
            name=f"Próximas músicas (Total: {len(guild_state.queue)})",
            value=queue_list,
            inline=False
        )
    
    embed.set_footer(text=f"Loop: {'✅' if guild_state.loop else '❌'} | {BOT_OWNER_MESSAGE}")
    await ctx.send(embed=embed)

@bot.command(name='volume', aliases=['v', 'vol'], help='Ajusta o volume (0-100)')
async def volume(ctx, volume: int):
    if not 0 <= volume <= 100:
        return await ctx.send("❌ Por favor, insira um valor entre 0 e 100!")
    
    voice_client = ctx.voice_client
    if not voice_client or not voice_client.is_connected():
        return await ctx.send("❌ Não estou conectado a um canal de voz!")
    
    guild_state = get_guild_state(ctx.guild.id)
    guild_state.volume = volume / 100
    
    if voice_client.source:
        voice_client.source.volume = guild_state.volume
    
    embed = discord.Embed(
        description=f"🔊 Volume ajustado para {volume}%",
        color=discord.Color.blurple()
    )
    embed.set_footer(text=BOT_OWNER_MESSAGE)
    await ctx.send(embed=embed)

@bot.command(name='loop', help='Ativa/desativa o loop da música atual')
async def loop(ctx):
    guild_state = get_guild_state(ctx.guild.id)
    guild_state.loop = not guild_state.loop
    
    if guild_state.current_song:
        embed = discord.Embed(
            description=f"🔂 Loop {'ativado' if guild_state.loop else 'desativado'}!",
            color=discord.Color.purple()
        )
        embed.set_footer(text=BOT_OWNER_MESSAGE)
        await ctx.send(embed=embed)
    else:
        guild_state.loop = False
        await ctx.send("❌ Nenhuma música tocando para ativar o loop!")

@bot.command(name='nowplaying', aliases=['np', 'tocando'], help='Mostra a música atual')
async def nowplaying(ctx):
    guild_state = get_guild_state(ctx.guild.id)
    
    if not guild_state.current_song:
        return await ctx.send("❌ Nada está tocando no momento!")
    
    player = guild_state.current_song
    embed = discord.Embed(
        title="🎶 Tocando agora",
        description=f"[{player.title}]({player.url})",
        color=discord.Color.blue()
    )
    
    if player.thumbnail:
        embed.set_thumbnail(url=player.thumbnail)
    
    info_fields = []
    
    if player.duration:
        minutes, seconds = divmod(player.duration, 60)
        info_fields.append(f"⏳ Duração: {minutes}:{seconds:02d}")
    
    info_fields.append(f"🔊 Volume: {int(guild_state.volume * 100)}%")
    info_fields.append(f"🔂 Loop: {'✅' if guild_state.loop else '❌'}")
    
    if player.uploader:
        info_fields.append(f"🎤 Artista: {player.uploader}")
    
    embed.add_field(name="Informações", value="\n".join(info_fields), inline=False)
    embed.set_footer(text=BOT_OWNER_MESSAGE)
    
    await ctx.send(embed=embed)

@bot.command(name='clear', aliases=['limpar'], help='Limpa a fila de músicas')
async def clear(ctx):
    guild_state = get_guild_state(ctx.guild.id)
    guild_state.queue.clear()
    
    embed = discord.Embed(
        description="🧹 Fila de músicas limpa!",
        color=discord.Color.green()
    )
    embed.set_footer(text=BOT_OWNER_MESSAGE)
    await ctx.send(embed=embed)

@bot.command(name='disconnect', aliases=['dc', 'sair'], help='Desconecta o bot do canal de voz')
async def disconnect(ctx):
    voice_client = ctx.voice_client
    if not voice_client or not voice_client.is_connected():
        return await ctx.send("❌ Não estou conectado a um canal de voz!")
    
    guild_state = get_guild_state(ctx.guild.id)
    guild_state.queue.clear()
    guild_state.current_song = None
    guild_state.skip_votes.clear()
    await voice_client.disconnect()
    
    embed = discord.Embed(
        description="👋 Desconectado do canal de voz!",
        color=discord.Color.blue()
    )
    embed.set_footer(text=BOT_OWNER_MESSAGE)
    await ctx.send(embed=embed)

@bot.command(name='help', aliases=['ajuda'], help='Mostra todos os comandos disponíveis')
async def help_command(ctx):
    embed = discord.Embed(
        title="🎵 Nathanael - Comandos de Música",
        description=f"Prefixo: `{PREFIX}`\n\n{BOT_OWNER_MESSAGE}",
        color=discord.Color.purple()
    )
    
    commands_list = [
        ("`play [nome/url]`", "Toca uma música do YouTube"),
        ("`stop`", "Para a música e limpa a fila"),
        ("`pause`", "Pausa a música atual"),
        ("`resume`", "Continua a música pausada"),
        ("`skip`", "Pula a música atual (votação)"),
        ("`queue`", "Mostra a fila de músicas"),
        ("`volume [0-100]`", "Ajusta o volume"),
        ("`loop`", "Ativa/desativa o loop da música atual"),
        ("`nowplaying`", "Mostra a música atual"),
        ("`clear`", "Limpa a fila de músicas"),
        ("`disconnect`", "Desconecta o bot do canal de voz"),
        ("`help`", "Mostra esta mensagem de ajuda")
    ]
    
    for name, value in commands_list:
        embed.add_field(name=name, value=value, inline=False)
    
    embed.set_thumbnail(url=MR_ROBOT_AVATAR_URL)
    await ctx.send(embed=embed)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send(f"❌ Comando não encontrado. Digite `{PREFIX}help` para ver os comandos disponíveis.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Argumento faltando. Digite `{PREFIX}help` para ver como usar o comando.")
    else:
        await ctx.send(f"❌ Ocorreu um erro: {str(error)}")
    
    embed = discord.Embed(
        description=f"⚠ Erro: {str(error)}",
        color=discord.Color.red()
    )
    embed.set_footer(text=BOT_OWNER_MESSAGE)
    await ctx.send(embed=embed)

if __name__ == "__main__":
    bot.run(TOKEN)
