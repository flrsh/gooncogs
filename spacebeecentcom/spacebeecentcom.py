import asyncio
import urllib
from collections import OrderedDict
import struct
import discord
from redbot.core import commands, Config, checks
import discord.errors
from redbot.core.bot import Red
from typing import *
from fastapi import Request, Depends, HTTPException
from fastapi.responses import JSONResponse
from github import Github
import re
import time
import functools
import inspect
import collections
import secrets

PLAYER_ROLE_ID = 182284445837950977


class SpacebeeCentcom(commands.Cog):
    AHELP_COLOUR = discord.Colour.from_rgb(184, 46, 0)
    ASAY_COLOUR = discord.Colour.from_rgb(174, 80, 186)
    MHELP_COLOUR = discord.Colour.from_rgb(123, 0, 255)
    SUCCESS_REPLY = {"status": "ok"}
    default_user_settings = {
        "linked_ckey": None,
        "link_verification": None,
    }

    def __init__(self, bot: Red):
        self.bot = bot
        self.asay_uses_embed = False
        self.config = Config.get_conf(self, identifier=5525879512398)
        self.config.init_custom("ckey", 1)
        self.config.register_user(**self.default_user_settings)
        self.config.register_custom("ckey", discord_id=None)
        self.gh = None

    async def init(self):
        self.gh = Github((await self.bot.get_shared_api_tokens("github")).get("token"))

    class SpacebeeError(Exception):
        def __init__(self, message: str, status_code: int, error_code: int = 0):
            self.message = message
            self.status_code = status_code
            self.error_code = error_code

    def make_message_embed(
        self,
        colour,
        from_key,
        from_name,
        message,
        embed_tag,
        server_name,
        to_key=None,
        to_name=None,
        url=None,
    ):
        embed = discord.Embed()
        embed.title = f"{from_key}/{from_name}"
        if to_key is not None:
            embed.title += f" \N{RIGHTWARDS ARROW} {to_key}/{to_name}"
        if url is not None:
            embed.url = url
        embed_tag = embed_tag
        embed.description = message
        embed.colour = colour
        embed.set_footer(text=f"{server_name} {embed_tag}")
        return embed

    async def discord_broadcast(self, channels, *args, exception=None, **kwargs):
        tasks = [
            self.bot.get_channel(ch).send(*args, **kwargs)
            for ch in channels
            if ch != exception
        ]
        await asyncio.gather(*tasks)

    async def discord_broadcast_ahelp(
        self,
        channels,
        server_name,
        from_key,
        from_name,
        msg,
        to_key=None,
        to_name=None,
        exception=None,
        url=None,
    ):
        embed_tag = "ADMINPM" if to_key is not None else "ADMINHELP"
        embed = self.make_message_embed(
            self.AHELP_COLOUR,
            from_key,
            from_name,
            msg,
            embed_tag,
            server_name,
            to_key,
            to_name,
            url,
        )
        if hasattr(channels, "channels"):
            channels = channels.channels["ahelp"]
        await self.discord_broadcast(channels, embed=embed, exception=exception)

    async def discord_broadcast_mhelp(
        self,
        channels,
        server_name,
        from_key,
        from_name,
        msg,
        to_key=None,
        to_name=None,
        exception=None,
    ):
        embed_tag = "MENTORPM" if to_key is not None else "MENTORHELP"
        embed = self.make_message_embed(
            self.MHELP_COLOUR,
            from_key,
            from_name,
            msg,
            embed_tag,
            server_name,
            to_key,
            to_name,
        )
        if hasattr(channels, "channels"):
            channels = channels.channels["mhelp"]
        await self.discord_broadcast(channels, embed=embed, exception=exception)

    async def discord_broadcast_asay(
        self, channels, server_name, from_key, from_name, source, msg, exception=None
    ):
        if hasattr(channels, "channels"):
            channels = channels.channels["asay"]
        if self.asay_uses_embed:
            embed = self.make_message_embed(
                self.ASAY_COLOUR, from_key, from_name, msg, "ASAY", server_name
            )
            await self.discord_broadcast(channels, embed=embed, exception=exception)
        else:
            out_msg = f"\N{LARGE PURPLE SQUARE} [{source}] __{from_key}__: {msg}"
            await self.discord_broadcast(channels, out_msg, exception=exception)

    async def game_broadcast_asay(
        self, servers, from_key, from_name, source, msg, exception=None
    ):
        goonservers = self.bot.get_cog("GoonServers")
        send_data = {
            "type": "asay",
            "nick": f"[{source}] {from_key}" if source is not None else from_key,
            "msg": msg,
        }
        await goonservers.send_to_servers(servers, send_data, exception=exception)

    async def discord_broadcast_alert(
        self, channels, server_name, from_key, from_name, msg, exception=None
    ):
        if hasattr(channels, "channels"):
            channels = channels.channels["alert"]
        out_msg = f"\N{RED EXCLAMATION MARK} [{server_name}] {from_name} ({from_key}) {msg}"
        messages = await server.subtype.channel_broadcast(self.bot, "alert", out_msg)
        for message in messages:
            await message.add_reaction("\N{BLUE SQUARE}")
            await message.add_reaction("\N{YELLOW SQUARE}")
            await message.add_reaction("\N{RED SQUARE}")

    async def server_dep(self, server: str, server_name: str, api_key: str):
        if api_key != (await self.bot.get_shared_api_tokens("spacebee"))["api_key"]:
            raise self.SpacebeeError("Invalid API key.", 403)
        server = self.get_server(server_name) or self.get_server(server)
        if server is None:
            raise self.SpacebeeError("Unknown server.", 404)
        return server

    def register_to_general_api(self, app):
        @app.exception_handler(self.SpacebeeError)
        async def invalid_api_key_error_handler(
            request: Request, exc: self.SpacebeeError
        ):
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "status": "error",
                    "errormsg": exc.message,
                    "error": exc.error_code,
                },
            )

        @app.get("/asay")
        async def adminsay(
            key: str, name: str, msg: str, server=Depends(self.server_dep)
        ):
            await self.discord_broadcast_asay(
                server.subtype, server.full_name, key, name, server.short_name, msg
            )
            await self.game_broadcast_asay(
                server.subtype.servers,
                key,
                name,
                server.short_name,
                msg,
                exception=server,
            )
            return self.SUCCESS_REPLY

        @app.get("/ban")
        async def ban(
            key: str,
            key2: str,
            msg: str,
            time: str,
            timestamp: Optional[float],
            server=Depends(self.server_dep),
        ):
            embed = discord.Embed()
            embed.title = f"{key} banned {key2}"
            embed.description = msg
            if timestamp is None:
                embed.add_field(name="expires", value=f"in {time}")
            if timestamp > 0:
                timestamp = (
                    int(timestamp) * 60 + 946684800
                )  # timestamp is send in minutes since 2000-01-01 00:00 GMT
                embed.add_field(
                    name="expires", value=f"<t:{timestamp}:F>\n(<t:{timestamp}:R>)"
                )
            elif timestamp == 0:
                embed.add_field(name="expires", value="permanent")
            else:
                embed.add_field(name="expires", value="until appeal")
            embed.colour = discord.Colour.red()
            embed.set_footer(text=f"{server.full_name} BAN")
            for channel_id in server.subtype.channels["ban"]:
                await self.bot.get_channel(channel_id).send(embed=embed)
            return self.SUCCESS_REPLY

        @app.get("/help")
        async def adminhelp(
            key: str,
            name: str,
            msg: str,
            log_link: Optional[str] = None,
            server=Depends(self.server_dep),
        ):
            await self.discord_broadcast_ahelp(
                server.subtype, server.full_name, key, name, msg, url=log_link
            )
            return self.SUCCESS_REPLY

        @app.get("/pm")
        async def adminpm(
            key: str,
            name: str,
            key2: str,
            name2: str,
            msg: str,
            server=Depends(self.server_dep),
        ):
            await self.discord_broadcast_ahelp(
                server.subtype, server.full_name, key, name, msg, key2, name2
            )
            return self.SUCCESS_REPLY

        @app.get("/mentorhelp")
        async def mentorhelp(
            key: str, name: str, msg: str, server=Depends(self.server_dep)
        ):
            await self.discord_broadcast_mhelp(
                server.subtype, server.full_name, key, name, msg
            )
            return self.SUCCESS_REPLY

        @app.get("/mentorpm")
        async def mentorpm(
            key: str,
            name: str,
            key2: str,
            name2: str,
            msg: str,
            server=Depends(self.server_dep),
        ):
            await self.discord_broadcast_mhelp(
                server.subtype, server.full_name, key, name, msg, key2, name2
            )
            return self.SUCCESS_REPLY

        @app.get("/admin")
        async def admin(
            msg: str, key: str = "", name: str = "", server=Depends(self.server_dep)
        ):
            out = f"[{server.full_name}] "
            if key or name:
                out += f"{name} ({key}) "
            out += msg
            await server.subtype.channel_broadcast(self.bot, "admin_misc", out)
            return self.SUCCESS_REPLY

        @app.get("/alert")
        async def alert(
            msg: str, key: str = "", name: str = "", server=Depends(self.server_dep)
        ):
            await self.discord_broadcast_alert(
                server.subtype, server.full_name, key, name, msg
            )
            return self.SUCCESS_REPLY

        @app.get("/admin_debug")
        async def admin_debug(
            msg: str, key: str = "", name: str = "", server=Depends(self.server_dep)
        ):
            out = f"[{server.full_name}] "
            if key or name:
                out += f"{name} ({key}) "
            out += msg
            await server.subtype.channel_broadcast(self.bot, "debug", out)
            return self.SUCCESS_REPLY

        @app.get("/issue")
        async def admin_debug(
            title: str, body: str, secret: bool, server=Depends(self.server_dep)
        ):
            repo_name = (
                "goonstation/goonstation-secret"
                if secret
                else "goonstation/goonstation"
            )
            repo = self.gh.get_repo(repo_name)
            repo.create_issue(title, body)
            return self.SUCCESS_REPLY

        @app.get("/link")
        async def link(key: str, ckey: str, code: str, server=Depends(self.server_dep)):
            if "-" not in code:
                return None
            user_id, verification = code.split("-")
            user_id = int(user_id)
            user = self.bot.get_user(user_id)
            target_verif = await self.config.user(user).link_verification()
            if target_verif != verification:
                return None
            ckeys_linked_account = await self.config.custom("ckey", ckey).discord_id()
            if ckeys_linked_account:
                try:
                    await ctx.send(
                        f"Ckey `{ckey}` is already linked to {'your' if user_id == ckeys_linked_account else 'another'} account."
                    )
                except:
                    pass
                return
            await self.config.user(user).link_verification.set(None)
            await self.config.user(user).linked_ckey.set(ckey)
            await self.config.custom("ckey", ckey).discord_id.set(user_id)
            try:
                await user.send(f"Account successfully linked to ckey `{ckey}`.")
            except:
                pass
            guild = self.bot.get_guild(182249960895545344)
            member = guild.get_member(user_id)
            if member is not None:
                await member.add_roles(guild.get_role(PLAYER_ROLE_ID))
            return self.SUCCESS_REPLY

    def ckeyify(self, text):
        return "".join(c.lower() for c in text if c.isalnum())

    @commands.command()
    async def link(self, ctx: commands.Context):
        """Links your Discord account with your BYOND username."""
        current_ckey = await self.config.user(ctx.author).linked_ckey()
        if current_ckey:
            await ctx.send(
                f"You are already linked to ckey `{current_ckey}`. If you wish to unlink please contact an administrator (ideally using the ]report command)."
            )
            return
        verif = secrets.token_hex(8)
        full_verif = f"{ctx.author.id}-{verif}"
        await self.config.user(ctx.author).link_verification.set(verif)
        try:
            msg = f"Login into one of Goonstation servers and use the Link Discord verb in the Commands tab on the right. Enter code `{full_verif}` when prompted."
            await ctx.author.send(msg)
        except:
            await ctx.send("You need DMs enabled to link your account.")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.guild.id != 182249960895545344:
            return
        current_ckey = await self.config.user(member).linked_ckey()
        if current_ckey:
            rolestuff_cog = self.bot.get_cog("RoleStuff")
            player_added = False
            if rolestuff_cog:
                roles_added, _ = await rolestuff_cog.restore_roles_internal(member)
                if any(r.id == PLAYER_ROLE_ID for r in roles_added):
                    player_added = True
            if not player_added:
                await member.add_roles(guild.get_role(PLAYER_ROLE_ID))

    @commands.command()
    @checks.admin()
    async def unlinkother(self, ctx: commands.Context, target: discord.User):
        """Unlinks a Discord user from their ckey."""
        current_ckey = await self.config.user(target).linked_ckey()
        if current_ckey:
            await self.config.user(target).linked_ckey.set(None)
            await self.config.custom("ckey", current_ckey).discord_id.set(None)
            await ctx.send(f"Unlinked ckey `{current_ckey}` from {target.mention}")
        else:
            await ctx.send("They had no linked ckey")

    @commands.command()
    @checks.admin()
    async def linkother(
        self, ctx: commands.Context, target: discord.User, *, ckey: str
    ):
        """Directly links a Discord user to a BYOND ckey."""
        ckey = self.ckeyify(ckey)
        current_ckey = await self.config.user(target).linked_ckey()
        if current_ckey:
            await ctx.send(
                f"That user is already linked to a ckey `{current_ckey}`. Unlink it first."
            )
            return
        ckeys_linked_account = await self.config.custom("ckey", ckey).discord_id()
        if ckeys_linked_account:
            await ctx.send(
                f"That ckey is already linked to user <@{ckeys_linked_account}>."
            )
            return
        await self.config.user(target).linked_ckey.set(ckey)
        await self.config.custom("ckey", ckey).discord_id.set(target.id)
        msg = f"Linked ckey `{ckey}` to {target.mention}"
        if current_ckey:
            msg += f" (Their previous ckey was `{current_ckey}`)"
        await ctx.send(msg)

    async def user_to_ckey(self, user):
        return await self.config.user(user).linked_ckey()

    @commands.command()
    @checks.admin()
    async def checklink(self, ctx: commands.Context, target: Union[discord.User, str]):
        """Checks linked account of a Discord user."""
        if not isinstance(target, str):
            current_ckey = await self.config.user(target).linked_ckey()
            if current_ckey:
                await ctx.send(f"{target.mention}'s ckey is `{current_ckey}`")
            else:
                await ctx.send(f"{target.mention} has not linked their account")
        else:
            ckey = self.ckeyify(target)
            user_id = await self.config.custom("ckey", ckey).discord_id()
            if user_id:
                await ctx.send(
                    f"`{ckey}`'s Discord account is {self.bot.get_user(user_id).mention}"
                )
            else:
                await ctx.send(f"Ckey `{ckey}` has not lonked their Discord account")

    def get_server(self, server_id):
        goonservers_cog = self.bot.get_cog("GoonServers")
        return goonservers_cog.resolve_server(server_id)

    async def check_and_send_message(
        self, type, message: discord.Message, server_id, data
    ):
        goonservers = self.bot.get_cog("GoonServers")
        server = goonservers.resolve_server(server_id)
        if not server:
            await message.reply("Unknown server.")
            return False
        if message.channel.id not in server.subtype.channels[type]:
            await message.reply("Wrong channel.")
            return False
        response = await goonservers.send_to_server_safe(
            server, data, message, to_dict=True
        )
        if response == 0.0:
            await message.reply("Could not find that person.")
            return False
        elif isinstance(response, dict):
            await message.add_reaction("\N{WHITE HEAVY CHECK MARK}")
            if type == "ahelp":
                await self.discord_broadcast_ahelp(
                    server.subtype,
                    server.full_name,
                    response["key"],
                    "Discord",
                    response["msg"],
                    response["key2"],
                    response["name2"],
                    exception=message.channel.id,
                )
            elif type == "mhelp":
                await self.discord_broadcast_mhelp(
                    server.subtype,
                    server.full_name,
                    response["key"],
                    "Discord",
                    response["msg"],
                    response["key2"],
                    response["name2"],
                    exception=message.channel.id,
                )
            elif type == "asay":
                await self.discord_broadcast_asay(
                    server.subtype,
                    server.full_name,
                    response["key"],
                    "Discord",
                    "Discord",
                    response["msg"],
                    exception=message.channel.id,
                )
            return True
        return False

    @commands.command()
    async def pm(
        self, ctx: commands.Context, server_id: str, target: str, *, message: str
    ):
        """Sends an admin PM to a given Goonstation server to a given ckey.

        You can also do this by using Discord replies on incoming adminhelps."""
        await self.check_and_send_message(
            "ahelp",
            ctx.message,
            server_id,
            {
                "type": "pm",
                "nick": ctx.message.author.name,
                "msg": message,
                "target": target,
            },
        )

    @commands.command()
    async def asay(self, ctx: commands.Context, server_id: str, *, message: str):
        """Sends an adminsay message to a given Goonstation server.

        You can also do this by using Discord replies on incoming asays."""
        await self.check_and_send_message(
            "asay",
            ctx.message,
            server_id,
            {
                "type": "asay",
                "nick": ctx.message.author.name,
                "msg": message,
            },
        )

    @commands.command(aliases=["mpm"])
    async def mentorpm(
        self, ctx: commands.Context, server_id: str, target: str, *, message: str
    ):
        """Sends a mentor PM to a given Goonstation server to a given ckey.

        You can also do this by using Discord replies on incoming mentorhelps."""
        await self.check_and_send_message(
            "mhelp",
            ctx.message,
            server_id,
            {
                "type": "mentorpm",
                "nick": ctx.message.author.name,
                "msg": message,
                "target": target,
            },
        )

    async def process_semicolon_asay(self, message: discord.Message):
        goonservers = self.bot.get_cog("GoonServers")
        if message.clean_content[0] != ";":
            return False
        msg = message.clean_content[1:].strip()
        asay_servers = goonservers.channel_to_servers(message.channel.id, "asay")
        target_channels = set()
        for server in asay_servers:
            target_channels |= set(server.subtype.channels["asay"])

        data = {"type": "asay", "nick": message.author.name, "msg": msg}

        await goonservers.send_to_servers(asay_servers, data)
        await self.discord_broadcast_asay(
            target_channels,
            "Discord",
            message.author.name,
            "Discord",
            "Discord",
            msg,
            exception=message.channel.id,
        )
        return True

    async def process_discord_replies(self, message: discord.Message):
        reference = message.reference
        if reference is None:
            return
        replied_to_msg = reference.resolved
        if not isinstance(replied_to_msg, discord.Message):
            return
        if replied_to_msg.author.id != self.bot.user.id:
            return
        target = None
        server_id = None
        reply_type = None
        channel_type = None
        if len(replied_to_msg.embeds) > 0:
            embed = replied_to_msg.embeds[0]
            target = embed.title.split("/")[0]
            if not isinstance(embed.footer.text, str):
                return
            msg_type = embed.footer.text.split()[-1]
            server_id = embed.footer.text[: -len(msg_type) - 1]
            reply_type = None
            if msg_type in ["ADMINHELP", "ADMINPM"]:
                reply_type = "pm"
                channel_type = "ahelp"
            elif msg_type in ["MENTORHELP", "MENTORPM"]:
                reply_type = "mentorpm"
                channel_type = "mhelp"
            elif msg_type == "ASAY":
                reply_type = "asay"
                channel_type = "asay"
            else:
                return
        elif replied_to_msg.content[0] == "\N{LARGE PURPLE SQUARE}":
            match = re.match(
                "\N{LARGE PURPLE SQUARE} " + r"\[(.*?)\] ([^:]*): .*",
                replied_to_msg.content,
            )
            if match:
                server_id, target = match.groups()
                reply_type = "asay"
                channel_type = "asay"

        if reply_type is None:
            return
        await self.check_and_send_message(
            channel_type,
            message,
            server_id,
            {
                "type": reply_type,
                "nick": message.author.name,
                "msg": message.content,
                "target": target,
            },
        )

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        goonservers = self.bot.get_cog("GoonServers")
        if not goonservers:
            return
        if message.channel.id not in goonservers.valid_channels:
            return
        if message.guild is None or self.bot.user == message.author:
            return
        if await self.bot.cog_disabled_in_guild(self, message.guild):
            return
        valid_user = (
            isinstance(message.author, discord.Member) and not message.author.bot
        )
        if not valid_user:
            return
        if not message.clean_content:
            return

        try:
            await self.process_semicolon_asay(message)
            await self.process_discord_replies(message)
        except:
            import traceback

            await self.bot.send_to_owners(traceback.format_exc())

    @commands.Cog.listener()
    async def on_reaction_add(reaction, user):
        _id = payload.message_id
        reaction_msg = reaction.message 
        message

        if reaction_msg.author.id != self.bot.user.id:
            return
        if reaction.me is False:
            return

        if str(reaction.emoji) == "\N{BLUE SQUARE}":
            message = "notes"
        elif str(reaction.emoji == "\N{YELLOW SQUARE}"):
            message = "pm"
        elif str(reaction.emoji == "\N{RED SQUARE}"):
            message = "ban"
            
        await self.check_and_send_message(
            channel_type,
            message,
            server_id,
            {
                "type": reply_type,
                "nick": message.author.name,
                "msg": message.content,
                "target": target,
            },
        )