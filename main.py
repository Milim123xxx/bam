import asyncio
import json
import logging
import os
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from myserver import server_on


TOKEN = os.getenv("TOKEN") or os.getenv("DISCORD_TOKEN")
GUILD_ID_TEXT = os.getenv("GUILD_ID")

if not TOKEN:
    raise RuntimeError("ไม่พบ TOKEN หรือ DISCORD_TOKEN ใน Environment Variables")
if not GUILD_ID_TEXT:
    raise RuntimeError("ไม่พบ GUILD_ID ใน Environment Variables")
try:
    GUILD_ID = int(GUILD_ID_TEXT)
except ValueError as exc:
    raise RuntimeError("GUILD_ID ต้องเป็นตัวเลข Server ID ของ Discord") from exc

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("afk-bot")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# guild_id -> configured voice channel id
afk_channels: dict[int, int] = {}
voice_clients: dict[int, discord.VoiceClient] = {}
reconnect_tasks: dict[int, asyncio.Task] = {}
# guild_id -> (source chat channel id, reply room id)
chat_configs: dict[int, tuple[int, int]] = {}
# forwarded message id -> (source channel id, original author id, reply room id)
forwarded_messages: dict[int, tuple[int, int, int]] = {}
CONFIG_FILE = os.getenv("CHAT_CONFIG_FILE", "chat_config.json")
synced = False


def load_chat_configs() -> None:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as file:
            saved = json.load(file)
        for guild_id, values in saved.items():
            if isinstance(values, list) and len(values) == 2:
                chat_configs[int(guild_id)] = (int(values[0]), int(values[1]))
        logger.info("โหลดการตั้งค่าแชทแล้ว %s เซิร์ฟเวอร์", len(chat_configs))
    except FileNotFoundError:
        logger.info("ยังไม่มีไฟล์ตั้งค่าแชท เริ่มต้นด้วยการใช้ /chat")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        logger.exception("อ่านไฟล์ตั้งค่าแชทไม่สำเร็จ จะเริ่มด้วยการตั้งค่าเปล่า")


def save_chat_configs() -> None:
    data = {str(guild_id): list(values) for guild_id, values in chat_configs.items()}
    temporary = f"{CONFIG_FILE}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(data, file)
        os.replace(temporary, CONFIG_FILE)
    except OSError:
        logger.exception("บันทึกการตั้งค่าแชทไม่สำเร็จ")


async def disconnect_voice(guild_id: int) -> None:
    vc = voice_clients.pop(guild_id, None)
    if vc is not None:
        try:
            await vc.disconnect(force=True)
        except discord.DiscordException:
            logger.debug("Voice disconnect failed", exc_info=True)


async def connect_afk(guild: discord.Guild, channel: discord.VoiceChannel) -> None:
    await disconnect_voice(guild.id)
    vc = await channel.connect(reconnect=True, self_deaf=True, self_mute=True)
    voice_clients[guild.id] = vc
    afk_channels[guild.id] = channel.id
    logger.info("AFK enabled: guild=%s channel=%s", guild.id, channel.id)


async def reconnect_afk(guild_id: int) -> None:
    if guild_id in reconnect_tasks:
        return

    async def retry() -> None:
        delay = 5
        try:
            while guild_id in afk_channels:
                guild = bot.get_guild(guild_id)
                if guild is None:
                    await asyncio.sleep(delay)
                    continue

                channel = guild.get_channel(afk_channels[guild_id])
                if not isinstance(channel, discord.VoiceChannel):
                    logger.warning("Configured voice channel was not found: %s", afk_channels[guild_id])
                    await asyncio.sleep(30)
                    continue

                current = voice_clients.get(guild_id)
                if current and current.is_connected():
                    return

                try:
                    vc = await channel.connect(reconnect=True, self_deaf=True, self_mute=True)
                    voice_clients[guild_id] = vc
                    logger.info("AFK reconnected: guild=%s channel=%s", guild_id, channel.id)
                    return
                except (discord.ClientException, discord.DiscordException, OSError) as exc:
                    logger.warning("Reconnect failed: %s; retrying in %ss", exc, delay)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 60)
        except asyncio.CancelledError:
            raise
        finally:
            reconnect_tasks.pop(guild_id, None)

    reconnect_tasks[guild_id] = asyncio.create_task(retry())


@bot.event
async def on_ready() -> None:
    global synced
    logger.info("ออนไลน์แล้ว: %s | servers=%s", bot.user, len(bot.guilds))
    if synced:
        return
    load_chat_configs()
    guild = discord.Object(id=GUILD_ID)
    try:
        bot.tree.copy_global_to(guild=guild)
        commands_synced = await bot.tree.sync(guild=guild)
        synced = True
        logger.info("ซิงก์คำสั่งสำเร็จ: %s คำสั่ง", len(commands_synced))
    except discord.DiscordException:
        logger.exception("ซิงก์ Slash Commands ไม่สำเร็จ")


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
) -> None:
    if bot.user and member.id == bot.user.id and after.channel is None:
        if member.guild.id in afk_channels:
            logger.warning("บอตหลุดจากห้องโทร กำลังเชื่อมต่อกลับ")
            await reconnect_afk(member.guild.id)


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return

    if message.guild is not None and isinstance(message.channel, discord.TextChannel):
        config = chat_configs.get(message.guild.id)
        if config:
            source_id, reply_id = config
            # A reply in the staff room is sent back to the source room as the bot.
            if message.channel.id == reply_id and message.reference:
                forwarded = forwarded_messages.get(message.reference.message_id)
                if forwarded and forwarded[2] == reply_id:
                    source_channel_id, author_id, _ = forwarded
                    source = message.guild.get_channel(source_channel_id)
                    if isinstance(source, discord.TextChannel):
                        await source.send(f"<@{author_id}> {message.content}")
                        await message.reply("ส่งคำตอบกลับห้องต้นทางแล้ว", mention_author=False)
                    return

            # A normal message in the source room is copied to the staff room.
            if message.channel.id == source_id:
                reply_room = message.guild.get_channel(reply_id)
                if isinstance(reply_room, discord.TextChannel):
                    text = message.content or "[ข้อความไม่มีเนื้อหา]"
                    if len(text) > 4000:
                        text = text[:3997] + "..."
                    embed = discord.Embed(title="ข้อความใหม่จากห้องแชท", description=text, color=0x5865F2)
                    embed.set_footer(text=f"ผู้ส่ง: {message.author} | ID: {message.author.id}")
                    sent = await reply_room.send(embed=embed)
                    forwarded_messages[sent.id] = (source_id, message.author.id, reply_id)
                return

    await bot.process_commands(message)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    logger.error(
        "Slash command error: %s",
        error,
        exc_info=(type(error), error, error.__traceback__),
    )
    text = "เกิดข้อผิดพลาดในการทำงาน กรุณาตรวจสิทธิ์บอตและ Render Logs"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.DiscordException:
        logger.exception("ส่งข้อความแจ้ง error ไม่สำเร็จ")


@bot.tree.command(name="chat", description="ตั้งค่าห้องแชทต้นทางและห้องตอบกลับผ่านบอต")
@app_commands.describe(
    source="ห้องแชทที่สมาชิกใช้พิมพ์ข้อความ",
    reply_room="ห้องที่ใช้พิมพ์ตอบกลับผ่านบอต",
)
@app_commands.default_permissions(manage_guild=True)
async def chat_command(
    interaction: discord.Interaction,
    source: discord.TextChannel,
    reply_room: discord.TextChannel,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("คำสั่งนี้ใช้ในเซิร์ฟเวอร์เท่านั้น", ephemeral=True)
        return
    if source.id == reply_room.id:
        await interaction.response.send_message("กรุณาเลือกคนละห้องสำหรับรับข้อความและตอบกลับ", ephemeral=True)
        return

    me = interaction.guild.me
    if me is not None:
        for room in (source, reply_room):
            permissions = room.permissions_for(me)
            if not permissions.view_channel or not permissions.send_messages or not permissions.embed_links:
                await interaction.response.send_message(
                    f"บอตต้องมี View Channel, Send Messages และ Embed Links ใน {room.mention}",
                    ephemeral=True,
                )
                return

    chat_configs[interaction.guild.id] = (source.id, reply_room.id)
    save_chat_configs()
    await interaction.response.send_message(
        f"เปิดระบบแชทแล้ว\nห้องต้นทาง: {source.mention}\nห้องตอบกลับ: {reply_room.mention}\n\n"
        "ข้อความจากห้องต้นทางจะถูกส่งไปห้องตอบกลับ ให้กด Reply ที่ข้อความนั้นเพื่อส่งคำตอบกลับไปห้องต้นทางในชื่อบอต",
        ephemeral=True,
    )
    logger.info("Chat configured: guild=%s source=%s reply=%s", interaction.guild.id, source.id, reply_room.id)


@bot.tree.command(name="chat_off", description="ปิดระบบแชทผ่านบอต")
@app_commands.default_permissions(manage_guild=True)
async def chat_off_command(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("คำสั่งนี้ใช้ในเซิร์ฟเวอร์เท่านั้น", ephemeral=True)
        return
    chat_configs.pop(interaction.guild.id, None)
    save_chat_configs()
    await interaction.response.send_message("ปิดระบบแชทแล้ว", ephemeral=True)


@bot.tree.command(name="afk", description="ให้บอตเข้าไปอยู่ในห้องโทรแบบ AFK")
@app_commands.describe(channel="เลือกห้องโทร หรือไม่เลือกเพื่อใช้ห้องที่คุณอยู่")
async def afk_command(
    interaction: discord.Interaction,
    channel: Optional[discord.VoiceChannel] = None,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("คำสั่งนี้ใช้ในเซิร์ฟเวอร์เท่านั้น", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    if channel is None:
        user_voice = getattr(interaction.user, "voice", None)
        channel = user_voice.channel if user_voice else None
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.followup.send(
                "กรุณาเข้าห้องโทรก่อน แล้วใช้ `/afk` อีกครั้ง หรือเลือกห้องในช่อง channel",
                ephemeral=True,
            )
            return
    me = interaction.guild.me
    if me is not None:
        permissions = channel.permissions_for(me)
        if not permissions.view_channel or not permissions.connect:
            await interaction.followup.send(
                "บอตไม่มีสิทธิ์ View Channel หรือ Connect ในห้องนี้", ephemeral=True
            )
            return

    try:
        await connect_afk(interaction.guild, channel)
        await interaction.followup.send(
            f"เปิด AFK แล้ว: {channel.mention}\nบอตจะปิดไมค์ ปิดเสียง และพยายามเชื่อมต่อกลับเมื่อหลุด",
            ephemeral=True,
        )
    except (discord.ClientException, discord.DiscordException, OSError):
        logger.exception("เข้า Voice Channel ไม่สำเร็จ")
        await interaction.followup.send(
            "เข้า Voice ไม่สำเร็จ โปรดตรวจสิทธิ์ View Channel และ Connect", ephemeral=True
        )


@bot.tree.command(name="off", description="ให้บอตออกจากห้องโทรและปิด AFK")
async def off_command(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("คำสั่งนี้ใช้ในเซิร์ฟเวอร์เท่านั้น", ephemeral=True)
        return

    guild_id = interaction.guild.id
    if guild_id not in afk_channels:
        await interaction.response.send_message("บอตไม่ได้เปิด AFK อยู่", ephemeral=True)
        return

    afk_channels.pop(guild_id, None)
    task = reconnect_tasks.pop(guild_id, None)
    if task is not None:
        task.cancel()
    await disconnect_voice(guild_id)
    await interaction.response.send_message("ปิด AFK และออกจากห้องโทรแล้ว", ephemeral=True)
    logger.info("AFK disabled: guild=%s", guild_id)


server_on()
bot.run(TOKEN)
