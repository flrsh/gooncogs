import asyncio
import discord
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from copy import copy
import re
from typing import Optional, Union
from redbot.core.utils.chat_formatting import pagify


class EditablePosts(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=85215643217426)
        self.config.init_custom("editable_posts", 1)
        self.config.register_custom("editable_posts", editable=False)
        self.config.register_custom("editable_posts", channel=None)

    @commands.group(aliases=["editableposts"])
    @checks.admin()
    async def editable_posts(self, ctx: commands.Context):
        """Group command for creating posts you can edit later."""
        pass

    async def valid_message(self, message: discord.Message):
        msg_id = message.id
        return await self.config.custom("editable_posts", msg_id).editable()

    @editable_posts.command()
    @checks.admin()
    async def create(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        *,
        title: Optional[str]
    ):
        """Creates a new editable post in a given channel.
        Use the edit dubcommand to change its text.
        """
        embed = discord.Embed(
            title=title or "[reserved post]", color=await ctx.embed_color()
        )
        msg = await channel.send(embed=embed)
        await self.config.custom("editable_posts", msg.id).editable.set(True)
        await self.config.custom("editable_posts", msg.id).channel.set(msg.channel.id)
        await ctx.message.add_reaction("\N{WHITE HEAVY CHECK MARK}")

    @editable_posts.command()
    @checks.admin()
    async def title(
        self, ctx: commands.Context, message: discord.Message, *, title: str
    ):
        """Changes the title of an editable post."""
        if not await self.valid_message(message):
            return
        embed = message.embeds[0]
        embed.title = title
        await message.edit(embed=embed)
        await ctx.message.add_reaction("\N{WHITE HEAVY CHECK MARK}")

    @editable_posts.command()
    @checks.admin()
    async def edit(self, ctx: commands.Context, message: discord.Message, *, text: str):
        """Edits an editable post with new text."""
        if not await self.valid_message(message):
            return
        embed = message.embeds[0]
        embed.description = text
        await message.edit(embed=embed)
        await ctx.message.add_reaction("\N{WHITE HEAVY CHECK MARK}")

    @editable_posts.command()
    @checks.admin()
    async def remove(self, ctx: commands.Context, message: discord.Message):
        """Deletes an editable post."""
        if not await self.valid_message(message):
            return
        await message.delete()
        await ctx.message.add_reaction("\N{WHITE HEAVY CHECK MARK}")

    @editable_posts.command()
    @checks.admin()
    async def list(self, ctx: commands.Context):
        """Lists all editable posts on this server."""
        async with ctx.typing():
            messages = []
            lines = []
            for msg_id, data in (
                await self.config.custom("editable_posts").all()
            ).items():
                channel = self.bot.get_channel(data["channel"])
                if channel is None or channel.guild != ctx.guild or not data["editable"]:
                    continue
                message = None
                try:
                    message = await channel.fetch_message(msg_id)
                except discord.errors.NotFound:
                    await self.config.custom("editable_posts", msg_id).editable.set(
                        False
                    )
                    continue
                except discord.errors.Forbidden:
                    lines.append(f"***ERROR***: Can't access channel {channel.mention}")
                    continue
                messages.append(message)
            for message in messages:
                # TODO store this in the config so it isn't terribly slow
                msg_text = message.embeds[0].title + " " + message.jump_url
                lines.append(msg_text)
            if lines:
                for page in pagify("\n".join(lines)):
                    await ctx.send(page)
            else:
                await ctx.send("No editable posts made.")
