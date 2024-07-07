import asyncio
import aiohttp
import discord
from redbot.core import commands, Config, checks
import discord.errors
from redbot.core.bot import Red
from typing import *
import logging
import datetime
import re
from redbot.core.utils.chat_formatting import pagify, box, quote
import markdownify

log = logging.getLogger("red.goon.mybbnotif")


class MybbNotif(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, 856623215587)
        self.config.init_custom("subforums", 1)
        self.config.register_custom(
            "subforums", channel_ids={}, prefix=None, last_timestamp=None
        )
        self.config.register_global(forum_url=None, period=180)
        self.session = aiohttp.ClientSession()
        self.main_loop_task = None

        self.debug_data = {}

    def cog_unload(self):
        self.running = False
        self.main_loop_task.cancel()
        asyncio.create_task(self.session.close())

    async def run(self):
        self.running = True
        self.main_loop_task = asyncio.create_task(self.main_loop())

    async def main_loop(self):
        while self.running:
            try:
                await self.check_forum()
            except asyncio.CancelledError:
                log.exception("MybbNotif main loop cancelled mid-action")
                break
            except:
                log.exception("Error in MybbNotif main loop")
            try:
                await asyncio.sleep(await self.config.period())
            except asyncio.CancelledError:
                break

    def parse_post_key(self, text):
        return re.search(r'var my_post_key = "([0-9a-f]*)";', text).groups()[0]

    async def get_post_key(self):
        tokens = (await self.bot.get_shared_api_tokens("mybb"))
        async with self.session.get(tokens.get('url')) as res:
            return self.parse_post_key(await res.text())

    async def login(self):
        tokens = (await self.bot.get_shared_api_tokens("mybb"))
        login_data = {
            "action": "do_login",
            "url": tokens.get('url') + "member.php",
            "quick_login": "1",
            "my_post_key": await self.get_post_key(),
            "quick_username": tokens.get('username'),
            "quick_password": tokens.get('password'),
            "quick_remember": "yes",
            "submit": "Login"
        }
        async with self.session.post(login_data['url'], data=login_data) as res:
            self.debug_data['login'] = await res.text()
            return res.status == 200

    async def check_subforum(
        self,
        url: str,
        prefix: str,
        channels: List[discord.TextChannel],
        last_timestamp: Optional[float],
    ):
        async with self.session.get(url) as res:
            data = await res.json(content_type=None)
            self.debug_data[url] = data
            if last_timestamp is not None:
                for item in data.get("items", []):
                    timestamp = datetime.datetime.fromisoformat(
                        item["date_published"]
                    ).timestamp()
                    if timestamp <= last_timestamp:
                        break
                    message = f"[{prefix}] __{item['title']}__ by {item['author']['name']}\n<{item['url']}>\n"
                    forum_post_text = re.sub("\n+", "\n", markdownify.markdownify(item['content_html']))
                    message += quote(list(pagify(forum_post_text, page_length=500))[0].strip())
                    for channel in channels:
                        await channel.send(message)
            if not data.get("items"):
                return None
            isotime = data["items"][0]["date_published"]
            return datetime.datetime.fromisoformat(isotime).timestamp()

    async def check_subforum_raw(self, base_url: str, forum_id: int, data: Dict):
        prefix = data.get("prefix") or str(forum_id)
        channels = [
            self.bot.get_channel(int(chid))
            for chid in data.get("channel_ids", {}).keys()
        ]
        url = f"{base_url}?fid={forum_id}&type=json&limit=30"
        last_timestamp = data.get("last_timestamp")
        new_last_timestamp = await self.check_subforum(
            url, prefix, channels, last_timestamp
        )
        if new_last_timestamp is not None:
            await self.config.custom("subforums", forum_id).last_timestamp.set(
                new_last_timestamp
            )

    async def check_forum(self):
        tasks = []
        await self.login()
        tokens = await self.bot.get_shared_api_tokens("mybb")
        forum_url = tokens.get('url') + "syndication.php"
        for forum_id, data in (await self.config.custom("subforums").all()).items():
            tasks.append(self.check_subforum_raw(forum_url, forum_id, data))
        await asyncio.gather(*tasks)

    async def channels_of_subforum(self, forum_id: int):
        channel_ids = await self.config.custom("subforums", forum_id).channel_ids()
        return [self.bot.get_channel(int(chid)) for chid in channel_ids.keys()]

    @commands.group()
    @checks.admin()
    async def mybbnotif(self, ctx: commands.Context):
        """Manage RSS feed from a mybb forum."""
        pass

    @mybbnotif.command()
    async def manualcheck(self, ctx: commands.Context):
        await self.check_forum()
        await ctx.send("Completed.")

    @mybbnotif.command()
    async def period(self, ctx: commands.Context, seconds: Optional[int]):
        """Gets or sets the forum check time period in seconds."""
        old = await self.config.period()
        if seconds is not None and seconds > 0:
            await self.config.period.set(seconds)
            await ctx.send(
                f"Previously checking the forum every {old} seconds, now changed to {seconds}."
            )
        else:
            await ctx.send(f"Currently checking the forum every {old} seconds.")

    @mybbnotif.command()
    async def setprefix(self, ctx: commands.Context, forum_id: int, prefix: str):
        """Sets announcement prefix for a given subforum id."""
        await self.config.custom("subforums", forum_id).prefix.set(prefix)
        await ctx.send(f"Subforum {forum_id} will now be denoted by prefix `{prefix}`.")

    @mybbnotif.command()
    async def addchannel(
        self,
        ctx: commands.Context,
        forum_id: int,
        channel: Optional[discord.TextChannel],
    ):
        if channel is None:
            channel = ctx.channel
        async with self.config.custom(
            "subforums", forum_id
        ).channel_ids() as channel_ids:
            channel_ids[str(channel.id)] = None
        await ctx.send(
            f"Channel {channel.mention} will now receive notifications from subforum number {forum_id}."
        )

    @mybbnotif.command()
    async def removechannel(
        self,
        ctx: commands.Context,
        forum_id: int,
        channel: Optional[discord.TextChannel],
    ):
        if channel is None:
            channel = ctx.channel
        async with self.config.custom(
            "subforums", forum_id
        ).channel_ids() as channel_ids:
            del channel_ids[str(channel.id)]
        await ctx.send(
            f"Channel {channel.mention} will no longer receive notifications from subforum number {forum_id}."
        )

    @mybbnotif.command()
    async def checkchannels(self, ctx: commands.Context, forum_id: int):
        channel_ids = await self.config.custom("subforums", forum_id).channel_ids()
        if not channel_ids:
            await ctx.send("No channels.")
        else:
            await ctx.send(
                "\n".join(
                    ch.mention for ch in await self.channels_of_subforum(forum_id)
                )
            )
