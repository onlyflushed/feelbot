import datetime
import pprint
import asyncio
import traceback
from functools import partial
from random import shuffle

import discord
from discord.ext import commands

from youtube_dl import YoutubeDL
import re

URL_REG = re.compile(r'https?://(?:www\.)?.+')
YOUTUBE_VIDEO_REG = re.compile(r"(https?://)?(www\.)?youtube\.(com|nl)/watch\?v=([-\w]+)")

filters = {
    'nightcore': 'aresample=48000,asetrate=48000*1.25'
}


def utc_time():
    return datetime.datetime.now(datetime.timezone.utc)


YDL_OPTIONS = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'retries': 5,
    # 'default_search': 'auto',
    'extract_flat': True,
    'source_address': '0.0.0.0',
}

FFMPEG_OPTIONS = {
    'before_options': '-nostdin'
                      ' -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}


def fix_characters(text: str):
    replaces = [
        ('&quot;', '"'),
        ('&amp;', '&'),
        ('(', '\u0028'),
        (')', '\u0029'),
        ('[', '【'),
        (']', '】'),
        ("  ", " "),
        ("*", '"'),
        ("_", ' '),
        ("{", "\u0028"),
        ("}", "\u0029"),
    ]
    for r in replaces:
        text = text.replace(r[0], r[1])

    return text


ytdl = YoutubeDL(YDL_OPTIONS)


def is_requester():
    def predicate(ctx):
        player = ctx.bot.players.get(ctx.guild.id)
        if not player:
            return True
        if ctx.author.guild_permissions.manage_channels:
            return True
        if ctx.author.voice and not any(
                m for m in ctx.author.voice.channel.members if not m.bot and m.guild_permissions.manage_channels):
            return True
        if player.current['requester'] == ctx.author:
            return True

    return commands.check(predicate)


class MusicPlayer:

    def __init__(self, ctx: commands.Context):
        self.ctx = ctx
        self.bot = ctx.bot
        self.queue = []
        self.current = None
        self.event = asyncio.Event()
        self.now_playing = None
        self.timeout_task = None
        self.channel: discord.VoiceChannel = None
        self.disconnect_timeout = 180
        self.loop = False
        self.exiting = False
        self.nightcore = False
        self.fx = []
        self.no_message = False
        self.locked = False

    async def player_timeout(self):
        await asyncio.sleep(self.disconnect_timeout)
        self.exiting = True
        self.bot.loop.create_task(self.ctx.cog.destroy_player(self.ctx))

    async def process_next(self):

        self.event.clear()

        if self.locked:
            return

        if self.exiting:
            return

        try:
            self.timeout_task.cancel()
        except:
            pass

        if not self.queue:
            self.timeout_task = self.bot.loop.create_task(self.player_timeout())

            remaining = int((utc_time() + datetime.timedelta(seconds=self.disconnect_timeout)).timestamp())

            embed = discord.Embed(
                description=f"A fila está vazia...\nIrei desligar o player <t:{remaining}:R> caso não seja adicionada novas músicas.",
                color=discord.Colour.red())
            await self.ctx.send(embed=embed)
            return

        await self.start_play()

    async def renew_url(self):

        info = self.queue.pop(0)

        self.current = info

        try:
            url = info['webpage_url']
        except KeyError:
            url = info['url']

        if (yt_url := YOUTUBE_VIDEO_REG.match(url)):
            url = yt_url.group()

        to_run = partial(ytdl.extract_info, url=url, download=False)
        info = await self.bot.loop.run_in_executor(None, to_run)

        return info

    def ffmpeg_after(self, e):

        if e:
            print(f"ffmpeg error: {e}")

        self.event.set()

    async def start_play(self):

        await self.bot.wait_until_ready()

        if self.exiting:
            return

        self.event.clear()

        try:
            info = await self.renew_url()
        except Exception as e:
            traceback.print_exc()
            try:
                await self.ctx.send(embed=discord.Embed(
                    description=f"**Ocorreu um erro durante a reprodução da música:\n[{self.current['title']}]({self.current['webpage_url']})** ```css\n{e}\n```",
                    color=discord.Colour.red()))
            except:
                pass
            self.locked = True
            await asyncio.sleep(6)
            self.locked = False
            await self.process_next()
            return


        url = ""
        for format in info['formats']:
            if format['ext'] == 'm4a':
                url = format['url']
                break
        if not url:
            url = info['formats'][0]['url']

        ffmpg_opts = dict(FFMPEG_OPTIONS)

        self.fx = []

        if self.nightcore:
            self.fx.append(filters['nightcore'])

        if self.fx:
            ffmpg_opts['options'] += (f" -af \"" + ", ".join(self.fx) + "\"")

        try:
            if self.channel != self.ctx.me.voice.channel:
                self.channel = self.ctx.me.voice.channel
                await self.ctx.voice_client.move_to(self.channel)
        except AttributeError:
            print("teste: Bot desconectado após obter download da info.")
            return

        source = discord.FFmpegPCMAudio(url, **ffmpg_opts)

        self.ctx.voice_client.play(source, after=lambda e: self.ffmpeg_after(e))

        if self.no_message:
            self.no_message = False
        else:
            try:
                embed = discord.Embed(
                    description=f"**Tocando agora:**\n[**{info['title']}**]({info['webpage_url']})\n\n**Duração:** `{datetime.timedelta(seconds=info['duration'])}`",
                    color=self.ctx.me.colour,
                )

                thumb = info.get('thumbnail')

                if self.loop:
                    embed.description += " **| Repetição:** `ativada`"

                if self.nightcore:
                    embed.description += " **| Nightcore:** `Ativado`"

                if thumb:
                    embed.set_thumbnail(url=thumb)

                self.now_playing = await self.ctx.send(embed=embed)

            except Exception:
                traceback.print_exc()

        await self.event.wait()

        source.cleanup()

        if self.loop:
            self.queue.insert(0, self.current)
            self.no_message = True

        self.current = None

        await self.process_next()


class music(commands.Cog):
    def __init__(self, bot):

        if not hasattr(bot, 'players'):
            bot.players = {}

        self.bot = bot

    def get_player(self, ctx):
        try:
            player = ctx.bot.players[ctx.guild.id]
        except KeyError:
            player = MusicPlayer(ctx)
            self.bot.players[ctx.guild.id] = player

        return player

    async def destroy_player(self, ctx):

        ctx.player.exiting = True
        ctx.player.loop = False

        try:
            ctx.player.timeout_task.cancel()
        except:
            pass

        del self.bot.players[ctx.guild.id]

        if ctx.me.voice:
            await ctx.voice_client.disconnect()
        elif ctx.voice_client:
            ctx.voice_client.cleanup()

    # searching the item on youtube
    async def search_yt(self, item):

        if (yt_url := YOUTUBE_VIDEO_REG.match(item)):
            item = yt_url.group()

        elif not URL_REG.match(item):
            item = f"ytsearch:{item}"

        to_run = partial(ytdl.extract_info, url=item, download=False)
        info = await self.bot.loop.run_in_executor(None, to_run)

        try:
            entries = info["entries"]
        except KeyError:
            entries = [info]

        if info["extractor_key"] == "YoutubeSearch":
            entries = entries[:1]

        tracks = []

        for t in entries:

            if not (duration:=t.get('duration')):
                continue

            url = t.get('webpage_url') or t['url']

            if not URL_REG.match(url):
                url = f"https://www.youtube.com/watch?v={url}"

            tracks.append(
                {
                    'url': url,
                    'title': fix_characters(t['title']),
                    'uploader': t['uploader'],
                    'duration': duration
                }
            )

        return tracks

    @commands.command(name="help", alisases=['ajuda'], help="Comando de ajuda")
    async def help(self, ctx):
        helptxt = ''
        for command in self.bot.commands:
            helptxt += f'**{command}** - {command.help}\n'
        embedhelp = discord.Embed(
            colour=1646116,  # grey
            title=f'Comandos do {self.bot.user.name}',
            description=helptxt + '\n[Crie seu próprio Bot de Música](https://youtu.be/YGx0xNHzjgE)'
        )
        embedhelp.set_thumbnail(url=self.bot.user.avatar_url)
        await ctx.send(embed=embedhelp)

    @commands.command(name="play", help="Toca uma música do YouTube", aliases=['p', 'tocar'])
    async def p(self, ctx, *, query: str = "Rick Astley - Never Gonna Give You Up "):

        if not ctx.author.voice:
            # if voice_channel is None:
            # you need to be connected so that the bot knows where to go
            embedvc = discord.Embed(
                colour=1646116,  # grey
                description='Para tocar uma música, primeiro se conecte a um canal de voz.'
            )
            await ctx.send(embed=embedvc)
            return

        query = query.strip("<>")

        try:
            async with ctx.typing():
                songs = await self.search_yt(query)
        except Exception as e:
            traceback.print_exc()
            embedvc = discord.Embed(
                colour=12255232,  # red
                description=f'**Algo deu errado ao processar sua busca:**\n```css\n{repr(e)}```'
            )
            await ctx.send(embed=embedvc)
            return

        if not songs:
            embedvc = discord.Embed(
                colour=12255232,  # red
                description=f'Não houve resultados para sua busca: **{query}**'
            )
            await ctx.send(embed=embedvc)
            return

        if not ctx.player:
            ctx.player = self.get_player(ctx)

        player = ctx.player

        vc_channel = ctx.author.voice.channel

        if (size := len(songs)) > 1:
            txt = f"Você adicionou **{size} músicas** na fila!"
        else:
            txt = f"Você adicionou a música **{songs[0]['title']}** à fila!"

        for song in songs:
            song['requester'] = ctx.author
            player.queue.append(song)

        embedvc = discord.Embed(
            colour=32768,  # green
            description=f"{txt}\n\n[Crie seu próprio Bot de Música](https://youtu.be/YGx0xNHzjgE)"
        )
        await ctx.send(embed=embedvc)

        if not ctx.voice_client or not ctx.voice_client.is_connected():
            player.channel = vc_channel
            await vc_channel.connect(timeout=None, reconnect=False)

        if not ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
            await player.process_next()

    @commands.command(name="queue", help="Mostra as atuais músicas da fila.", aliases=['q', 'fila'])
    async def q(self, ctx):

        player = ctx.player

        if not player:
            await ctx.reply("Não há players ativo no momento...")
            return

        if not player.queue:
            embedvc = discord.Embed(
                colour=1646116,
                description='Não existe músicas na fila no momento.'
            )
            await ctx.send(embed=embedvc)
            return

        retval = ""

        def limit(text):
            if len(text) > 30:
                return text[:28] + "..."
            return text

        for n, i in enumerate(player.queue[:20]):
            retval += f'**{n + 1} | `{datetime.timedelta(seconds=i["duration"])}` - ** [{limit(i["title"])}]({i["url"]}) | {i["requester"].mention}\n'

        if (qsize := len(player.queue)) > 20:
            retval += f"\nE mais **{qsize - 20}** música(s)"

        embedvc = discord.Embed(
            colour=12255232,
            description=f"{retval}"
        )
        await ctx.send(embed=embedvc)

    @is_requester()
    @commands.command(name="skip", help="Pula a música atual que está tocando.", aliases=['pular', 's'])
    async def skip(self, ctx):

        player = ctx.player

        if not player:
            await ctx.reply("Não há players ativo no momento...")
            return

        if not ctx.voice_client or not ctx.voice_client.is_playing():
            await ctx.reply("Não estou tocando algo...")
            return

        await ctx.message.add_reaction('👍')
        player.loop = False
        ctx.voice_client.stop()

    @skip.error  # Erros para kick
    async def skip_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            embedvc = discord.Embed(
                colour=12255232,
                description=f"Você deve ser dono da música adicionada ou ter a permissão de **Gerenciar canais** para pular músicas."
            )
            await ctx.send(embed=embedvc)
        else:
            raise error

    @commands.command(name="shuffle", aliases=["misturar"], help="Misturar as músicas da fila")
    async def shuffle_(self, ctx):

        player = ctx.player

        embed = discord.Embed(color=discord.Colour.red())

        if not player:
            embed.description = "Não estou tocando algo no momento."
            await ctx.send(embed=embed)

        if len(player.queue) < 3:
            embed.description = "A fila tem que ter no mínimo 3 músicas para ser misturada."
            await ctx.send(embed=embed)
            return

        shuffle(player.queue)

        embed.description = f"**Você misturou as músicas da fila.**"
        embed.colour = discord.Colour.green()
        await ctx.send(embed=embed)

    @commands.command(aliases=["loop", "repetir"], help="Ativar/Desativar a repetição da música atual")
    async def repeat(self, ctx):

        player = ctx.player

        embed = discord.Embed(color=discord.Colour.red())

        if not player:
            embed.description = "Não estou tocando algo no momento"
            await ctx.send(embed=embed)

        player.loop = not player.loop

        embed.colour = discord.Colour.green()
        embed.description = f"**Repetição {'ativada para a música atual' if player.loop else 'desativada'}.**"

        await ctx.send(embed=embed)

    @commands.command(aliases=["nc"], help="Ativar/Desativar o efeito nightcore (Música acelerada com tom mais agudo.)")
    async def nightcore(self, ctx):

        player = ctx.player

        embed = discord.Embed(color=discord.Colour.red())

        if not player:
            embed.description = "Não estou tocando algo no momento"
            await ctx.send(embed=embed)

        player.nightcore = not player.nightcore
        player.queue.insert(0, player.current)
        player.no_message = True

        ctx.voice_client.stop()

        embed.description = f"**Efeito nightcore {'ativado' if player.nightcore else 'desativado'}.**"
        embed.colour = discord.Colour.green()

        await ctx.send(embed=embed)

    @commands.command(aliases=["parar", "sair", "leave", "l"], help="Parar o player e me desconectar do canal de voz.")
    async def stop(self, ctx):

        embedvc = discord.Embed(colour=12255232)

        player = ctx.player

        if not player:
            embedvc.description = "Não há player ativo no momento..."
            await ctx.reply(embed=embedvc)
            return

        if not ctx.me.voice:
            embedvc.description = "Não estou conectado em um canal de voz."
            await ctx.reply(embed=embedvc)
            return

        if not ctx.author.voice or ctx.author.voice.channel != ctx.me.voice.channel:
            embedvc.description = "Você precisa estar no meu canal de voz atual para usar esse comando."
            await ctx.reply(embed=embedvc)
            return

        if any(m for m in ctx.me.voice.channel.members if
               not m.bot and m.guild_permissions.manage_channels) and not ctx.author.guild_permissions.manage_channels:
            embedvc.description = "No momento você não tem permissão para usar esse comando."
            await ctx.reply(embed=embedvc)
            return

        await self.destroy_player(ctx)

        embedvc.colour = 1646116
        embedvc.description = "Você parou o player"
        await ctx.reply(embed=embedvc)


    @commands.Cog.listener("on_voice_state_update")
    async def player_vc_disconnect(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):

        if member.id != self.bot.user.id:
            return

        if after.channel:
            return

        player: MusicPlayer = self.bot.players.get(member.guild.id)

        if not player:
            return

        if player.exiting:
            return

        embed = discord.Embed(description="**Desligando player por desconexão do canal.**", color=member.color)

        await player.ctx.channel.send(embed=embed)

        await self.destroy_player(player.ctx)


    async def cog_before_invoke(self, ctx):

        ctx.player = self.bot.players.get(ctx.guild.id)

        return True


def setup(client):
    client.add_cog(music(client))