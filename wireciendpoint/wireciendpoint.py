import asyncio
import urllib
from collections import OrderedDict
import struct
import discord
from redbot.core import commands, Config, checks
from redbot.core.data_manager import cog_data_path, bundled_data_path
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu
import discord.errors
from redbot.core.bot import Red
from typing import *
from fastapi import Request, Depends, HTTPException
from fastapi.responses import JSONResponse
import re
import time
import functools
import inspect
from redbot.core.utils.chat_formatting import pagify
import collections
from pydantic import BaseModel
import datetime
import random
from bisect import bisect
from itertools import accumulate
import aiohttp
from github import Github
from github.GithubException import GithubException

TM_LABEL = "S-Testmerged"

EMOJI_RANGES_UNICODE = {
    6: [
        ("\U0001F300", "\U0001F320"),
        ("\U0001F330", "\U0001F335"),
        ("\U0001F337", "\U0001F37C"),
        ("\U0001F380", "\U0001F393"),
        ("\U0001F3A0", "\U0001F3C4"),
        ("\U0001F3C6", "\U0001F3CA"),
        ("\U0001F3E0", "\U0001F3F0"),
        ("\U0001F400", "\U0001F43E"),
        ("\U0001F440",),
        ("\U0001F442", "\U0001F4F7"),
        ("\U0001F4F9", "\U0001F4FC"),
        ("\U0001F500", "\U0001F53C"),
        ("\U0001F540", "\U0001F543"),
        ("\U0001F550", "\U0001F567"),
        ("\U0001F5FB", "\U0001F5FF"),
    ],
    7: [
        ("\U0001F300", "\U0001F32C"),
        ("\U0001F330", "\U0001F37D"),
        ("\U0001F380", "\U0001F3CE"),
        ("\U0001F3D4", "\U0001F3F7"),
        ("\U0001F400", "\U0001F4FE"),
        ("\U0001F500", "\U0001F54A"),
        ("\U0001F550", "\U0001F579"),
        ("\U0001F57B", "\U0001F5A3"),
        ("\U0001F5A5", "\U0001F5FF"),
    ],
    8: [
        ("\U0001F300", "\U0001F579"),
        ("\U0001F57B", "\U0001F5A3"),
        ("\U0001F5A5", "\U0001F5FF"),
    ],
}


def random_emoji(unicode_version=8, rnd=random):
    if unicode_version in EMOJI_RANGES_UNICODE:
        emoji_ranges = EMOJI_RANGES_UNICODE[unicode_version]
    else:
        emoji_ranges = EMOJI_RANGES_UNICODE[-1]

    # Weighted distribution
    count = [ord(r[-1]) - ord(r[0]) + 1 for r in emoji_ranges]
    weight_distr = list(accumulate(count))

    # Get one point in the multiple ranges
    point = rnd.randrange(weight_distr[-1])

    # Select the correct range
    emoji_range_idx = bisect(weight_distr, point)
    emoji_range = emoji_ranges[emoji_range_idx]

    # Calculate the index in the selected range
    point_in_range = point
    if emoji_range_idx != 0:
        point_in_range = point - weight_distr[emoji_range_idx - 1]

    # Emoji 😄
    emoji = chr(ord(emoji_range[0]) + point_in_range)
    emoji_codepoint = "U+{}".format(hex(ord(emoji))[2:].upper())

    return (emoji, emoji_codepoint)


class WireCiEndpoint(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, 1482189223515)
        self.config.register_global(channels={}, repo=None, testmerge_channels={})
        self.rnd = random.Random()
        self.funny_messages = open(
            bundled_data_path(self) / "code_quality.txt"
        ).readlines()
        self.session = aiohttp.ClientSession()
        self.processed_successful_commits = {}
        self.processed_failed_commits = set()
        self.build_finished_lock = asyncio.Lock()

    def cog_unload(self):
        asyncio.create_task(self.session.close())

    async def run_with_github(self, github_fn, ctx = None):
        github_keys = await self.bot.get_shared_api_tokens("github")
        token = None
        if github_keys.get("token") is None:
            if ctx: await ctx.send("The GitHub token needs to be set!")
            return None
        token = github_keys.get("token")
        github = Github(token)
        return await asyncio.get_running_loop().run_in_executor(
            None, github_fn, github
        )

    def register_to_general_api(self, app):
        class BuildFinishedModel(BaseModel):
            api_key: str
            last_compile: Optional[str]
            branch: Optional[str]
            author: Optional[str]
            message: Optional[str]
            commit: Optional[str]
            server: str
            cancelled: Optional[bool]
            error: Union[bool,str]
            mapSwitch: Optional[int]
            mergeConflicts: Optional[list[dict]]

        @app.post("/wireci/build_finished")
        async def build_finished(data: BuildFinishedModel):
            async with self.build_finished_lock:
                if (
                    data.api_key
                    != (await self.bot.get_shared_api_tokens("wireciendpoint"))[
                        "incoming_api_key"
                    ]
                ):
                    return
                success = not bool(data.error)
                clean_success = success and not data.mergeConflicts
                channels = await self.config.channels()
                if not len(channels):
                    return
                data.server = data.server.strip()
                goonservers = self.bot.get_cog("GoonServers")
                server = goonservers.resolve_server(data.server)
                if data.message is None:
                    error_message = data.error
                    if error_message == True:
                        error_message = "unknown error"
                    message = f"**ERROR**: {server.short_name}\n```\n{error_message}\n```"
                    for channel_id in channels:
                        channel = self.bot.get_channel(int(channel_id))
                        msg = await channel.send(message)
                    return
                data.last_compile = data.last_compile.strip()
                data.branch = data.branch.strip()
                data.author = data.author.strip()
                data.message = data.message.strip()
                data.commit = data.commit.strip()
                repo = await self.config.repo()
                message = ""
                embed = None
                if data.cancelled:
                    commit_message = data.message
                    if "\n" in commit_message:
                        commit_message = commit_message.split("\n")[0]
                    message = f"**CANCELLED** __{data.branch}__ on {server.short_name} \N{cross mark} `{data.commit[:7]}` by {data.author}: `{commit_message}`"
                    success = False
                elif clean_success:
                    commit_message = data.message
                    if "\n" in commit_message:
                        commit_message = commit_message.split("\n")[0]
                    guild = self.bot.get_channel(int(next(iter(channels)))).guild
                    message_start = f"__{data.branch}__ on "
                    message_end = f"{server.short_name} \N{white heavy check mark} `{data.commit[:7]}` by {data.author}: `{commit_message}`"
                    message = message_start + message_end
                    if data.commit not in self.processed_successful_commits:
                        message += f"\nCode quality: {await self.funny_message(data.commit, guild)}"
                    elif all(
                        msg.channel.last_message_id == msg.id
                        for msg in self.processed_successful_commits[data.commit]
                    ):
                        new_processed_commits = []
                        for msg in self.processed_successful_commits[data.commit]:
                            first_part, second_part = msg.content.split(
                                "\N{WHITE HEAVY CHECK MARK}"
                            )
                            message = (
                                first_part[:-1]
                                + ", "
                                + server.short_name
                                + " \N{WHITE HEAVY CHECK MARK}"
                                + second_part
                            )
                            new_processed_commits.append(await msg.edit(content=message))
                        self.processed_successful_commits[data.commit] = new_processed_commits
                        return
                else:
                    embed = discord.Embed()
                    embed.title = f"`{data.branch}` on {server.short_name}: " + (
                        "succeeded" if success else "failed"
                    )
                    embed.colour = (
                        discord.Colour.from_rgb(60, 100, 45)
                        if success
                        else discord.Colour.from_rgb(150, 60, 45)
                    )
                    embed.description = f"```\n{data.last_compile}\n```"
                    if not success:
                        if data.error == True:
                            pass
                        elif "\n" in error_message.strip():
                            embed.description += f"\nError:\n```{data.error}```"
                        else:
                            embed.description += f"\nError: `{data.error.strip()}`"
                    embed.timestamp = datetime.datetime.utcnow()
                    embed.set_image(
                        url=f"https://opengraph.githubassets.com/1/{repo}/commit/{data.commit}"
                    )
                    embed.add_field(
                        name="commit",
                        value=f"[{data.commit[:7]}](https://github.com/{repo}/commit/{data.commit})",
                    )
                    embed.add_field(name="message", value=data.message)
                    embed.add_field(name="author", value=data.author)
                    if len(data.mergeConflicts) != 0:
                        merge_conflict_text = "\n".join(f" - [{c['prId']}](https://github.com/{repo}/pull/{c['prId']}): {c['files']}" for c in data.mergeConflicts)
                        embed.add_field(name="merge conflicts", value=merge_conflict_text)
                    embed.set_footer(
                        text="Code quality: " + await self.funny_message(data.commit)
                    )
                    if not success and data.commit not in self.processed_failed_commits:
                        author_discord_id = None
                        githubendpoint = self.bot.get_cog("GithubEndpoint")
                        if githubendpoint:
                            author_discord_id = await githubendpoint.config.custom(
                                "contributors", data.author
                            ).discord_id()
                        if author_discord_id is not None:
                            message = self.bot.get_user(author_discord_id).mention
                succ_messages = []
                if success:
                    self.processed_successful_commits[data.commit] = succ_messages
                else:
                    self.processed_failed_commits.add(data.commit)
                for channel_id in channels:
                    channel = self.bot.get_channel(int(channel_id))
                    msg = None
                    if embed:
                        msg = await channel.send(message, embed=embed)
                    else:
                        msg = await channel.send(message)
                    if success:
                        succ_messages.append(msg)

    async def funny_message(self, seed, guild=None):
        self.rnd.seed(seed)
        if self.rnd.randint(1, 30) == 1:
            if guild and self.rnd.randint(1, 2) == 1:
                return str(self.rnd.choice(guild.emojis))
            else:
                return random_emoji(rnd=self.rnd)[0]
        if self.rnd.randint(1, 1 + len(self.funny_messages)) == 1:
            return "Rolling a d20 for a quality check: " + str(self.rnd.randint(1, 20))
        if self.rnd.randint(1, 2 + len(self.funny_messages)) <= 2:
            githubendpoint = self.bot.get_cog("GithubEndpoint")
            if githubendpoint:
                person = self.rnd.choice(
                    list(
                        (
                            await githubendpoint.config.custom("contributors").all()
                        ).keys()
                    )
                )
                return self.rnd.choice(
                    [
                        f"Like a thing {person} wrote",
                        f"{person}-approved",
                    ]
                )
        return self.rnd.choice(self.funny_messages).strip()

    @commands.group(name="ci")
    @checks.admin()
    async def wireciendpoint(self, ctx: commands.Context):
        """Manage Wire's CI system."""
        pass

    @wireciendpoint.command()
    async def stats(self, ctx: commands.Context):
        """Check stats of CI builds."""
        {
            "successful_builds": 0,
            "cancelled_builds": 0,
            "failed_builds": 0,
            "map_switch_builds": 0,
            "average_build_duration": 0
        }
        def fmt_stat(key, value):
            readable_key = key.replace('_', ' ')
            readable_value = value
            if key == "average_build_duration":
                secs = value / 1000
                minutes = int(secs // 60)
                secs = secs % 60
                readable_value = f"{minutes}:{int(secs)}"
            return readable_key, readable_value
        tokens = await self.bot.get_shared_api_tokens("wireciendpoint")
        url = tokens.get("ci_path") + "/stats"
        api_key = tokens.get("outgoing_api_key")
        async with self.session.get(
            url,
            headers={
                "Api-Key": api_key,
            },
        ) as res:
            if res.status != 200:
                for page in pagify(
                    f"Server responded with an error code {res.status}: `{(await res.text())[:100]}`"
                ):
                    await ctx.send(page)
                return
            data = await res.json(content_type=None)
            embed = discord.Embed(title="CI Stats", color=await ctx.embed_colour())
            for key, value in data.items():
                rkey, rvalue = fmt_stat(key, value)
                embed.add_field(name=rkey, value=rvalue)
            await ctx.reply(embed=embed)


    @wireciendpoint.command(aliases=["check"])
    async def status(self, ctx: commands.Context):
        """Check status of CI builds."""
        tokens = await self.bot.get_shared_api_tokens("wireciendpoint")
        url = tokens.get("ci_path") + "/status"
        api_key = tokens.get("outgoing_api_key")
        async with self.session.get(
            url,
            headers={
                "Api-Key": api_key,
            },
        ) as res:
            if res.status != 200:
                for page in pagify(
                    f"Server responded with an error code {res.status}: `{(await res.text())[:100]}`"
                ):
                    await ctx.send(page)
                return
            data = await res.json(content_type=None)
            goonservers = self.bot.get_cog("GoonServers")
            message = [f"Max compile jobs: {data.get('maxCompileJobs', 'N/A')}"]
            current_jobs = data.get("currentCompileJobs", [])
            def servinfo(servdata):
                servid = servdata if isinstance(servdata, str) else servdata['serverId']
                server = goonservers.resolve_server(servid)
                servname = None
                if server:
                    servname = server.short_name
                else:
                    servname = "Unknown server " + servid
                return f"__{servname}__"# ({servdata['build']['currentBranch']})"
            if not current_jobs:
                message.append("No jobs currently running")
            else:
                message.append(
                    f"Currently compiling: "
                    + ", ".join(
                        servinfo(sid)
                        for sid in current_jobs if sid is not None
                    )
                )
            queued_jobs = data.get("queuedJobs", [])
            if not queued_jobs:
                message.append("No jobs queued")
            else:
                message.append(
                    f"Queued: "
                    + ", ".join(
                        servinfo(sid)
                        for sid in queued_jobs if sid is not None
                    )
                )
            await ctx.send("\n".join(message))

    @wireciendpoint.command()
    async def build(self, ctx: commands.Context, *, server_name: str):
        """Start a CI build."""
        tokens = await self.bot.get_shared_api_tokens("wireciendpoint")
        url = tokens.get("ci_path") + "/build"
        api_key = tokens.get("outgoing_api_key")
        goonservers = self.bot.get_cog("GoonServers")
        servers = set()
        for server_name in server_name.split():
            servers |= set(goonservers.resolve_server_or_category(server_name))
        if not servers:
            await ctx.send("Unknown server.")
            return
        success = True
        for server in servers:
            server_id = server.tgs
            async with self.session.post(
                url,
                headers={
                    "Api-Key": api_key,
                },
                json={"server": server_id},
            ) as res:
                if res.status != 200:
                    for page in pagify(
                        f"`{server_id}`: Server responded with an error code {res.status}: `{(await res.text())[:100]}`"
                    ):
                        await ctx.send(page)
                    success = False
                    continue
                data = await res.json(content_type=None)
                if not data.get("success"):
                    await ctx.send(
                        f"`{server_id}`: Idk what happened: `{(await res.text())[:100]}`"
                    )
                    success = False
        if success:
            await ctx.message.add_reaction("\N{WHITE HEAVY CHECK MARK}")

    @wireciendpoint.command()
    async def cancel(self, ctx: commands.Context, *, server_name: str):
        """Cancel a CI build."""
        tokens = await self.bot.get_shared_api_tokens("wireciendpoint")
        url = tokens.get("ci_path") + "/cancel"
        api_key = tokens.get("outgoing_api_key")
        goonservers = self.bot.get_cog("GoonServers")
        servers = set()
        for server_name in server_name.split():
            servers |= set(goonservers.resolve_server_or_category(server_name))
        if not servers:
            await ctx.send("Unknown server.")
            return
        success = True
        for server in servers:
            server_id = server.tgs
            async with self.session.post(
                url,
                headers={
                    "Api-Key": api_key,
                },
                json={"server": server_id},
            ) as res:
                if res.status == 404:
                    await ctx.send(f"`{server_id}`: Build not found")
                    success = False
                    continue
                if res.status != 200:
                    for page in pagify(
                        f"`{server_id}`: Server responded with an error code {res.status}: `{(await res.text())[:100]}`"
                    ):
                        await ctx.send(page)
                    success = False
                    continue
                data = await res.json(content_type=None)
                if not data.get("success"):
                    await ctx.send(
                        f"`{server_id}`: Idk what happened: `{(await res.text())[:100]}`"
                    )
                    success = False
        if success:
            await ctx.message.add_reaction("\N{WHITE HEAVY CHECK MARK}")

    @wireciendpoint.command()
    async def restart(self, ctx: commands.Context, server_name: str):
        """Restart a server managed by CI."""
        tokens = await self.bot.get_shared_api_tokens("wireciendpoint")
        url = tokens.get("ci_path") + "/restart"
        api_key = tokens.get("outgoing_api_key")
        goonservers = self.bot.get_cog("GoonServers")
        servers = goonservers.resolve_server_or_category(server_name)
        if not servers:
            await ctx.send("Unknown server.")
            return
        success = True
        for server in servers:
            server_id = server.tgs
            async with self.session.post(
                url,
                headers={
                    "Api-Key": api_key,
                },
                json={"server": server_id},
            ) as res:
                if res.status != 200:
                    for page in pagify(
                        f"`{server_id}`: Server responded with an error code {res.status}: `{(await res.text())[:100]}`"
                    ):
                        await ctx.send(page)
                    success = False
                    continue
                data = await res.json(content_type=None)
                if not data.get("success"):
                    await ctx.send(
                        f"`{server_id}`: Idk what happened: `{(await res.text())[:100]}`"
                    )
                    success = False
        if success:
            await ctx.message.add_reaction("\N{WHITE HEAVY CHECK MARK}")

    @wireciendpoint.command()
    async def setrepo(self, ctx: commands.Context, repo: str):
        """Set GitHub repo for commit link purposes."""
        await self.config.repo.set(repo)
        await ctx.send(f"Repo set to `{repo}`.")

    @wireciendpoint.command()
    async def addchannel(
        self, ctx: commands.Context, channel: Optional[discord.TextChannel]
    ):
        """Subscribe a channel to receive CI build updates."""
        if channel is None:
            channel = ctx.channel
        async with self.config.channels() as channels:
            channels[str(channel.id)] = None
        await ctx.send(
            f"Channel {channel.mention} will now receive notifications about builds."
        )

    @wireciendpoint.command()
    async def removechannel(
        self, ctx: commands.Context, channel: Optional[discord.TextChannel]
    ):
        """Unsubscribe a channel from CI build updates."""
        if channel is None:
            channel = ctx.channel
        async with self.config.channels() as channels:
            del channels[str(channel.id)]
        await ctx.send(
            f"Channel {channel.mention} will no longer receive notifications about builds."
        )

    @wireciendpoint.command()
    async def checkchannels(self, ctx: commands.Context):
        """Check channels subscribed to CI build updates."""
        channel_ids = await self.config.channels()
        if not channel_ids:
            await ctx.send("No channels.")
        else:
            await ctx.send(
                "\n".join(self.bot.get_channel(int(ch)).mention for ch in channel_ids)
            )

    @wireciendpoint.command()
    async def branch(self, ctx: commands.Context, server_name: str, new_branch: Optional[str]):
        """Gets or sets the branch for a given server or group of servers."""
        tokens = await self.bot.get_shared_api_tokens("wireciendpoint")
        get_url = tokens.get("ci_path") + "/branch/"
        set_url = tokens.get("ci_path") + "/switch-branch"
        api_key = tokens.get("outgoing_api_key")
        goonservers = self.bot.get_cog("GoonServers")
        servers = goonservers.resolve_server_or_category(server_name)
        if not servers:
            await ctx.send("Unknown server.")
            return
        msgs_per_server = DefaultDict(list)
        for server in servers:
            server_id = server.tgs
            old_branch = "unknown?"
            async with self.session.get(
                get_url + server_id,
                headers={
                    "Api-Key": api_key,
                },
            ) as res:
                if res.status != 200:
                    msgs_per_server[server].append(f"error code {res.status}: `{(await res.text())[:100]}`")
                    continue
                data = await res.json(content_type=None)
                old_branch = data.get('branch', "unknown?")
            if new_branch:
                async with self.session.post(
                    set_url,
                    headers={
                        "Api-Key": api_key,
                    },
                    json={
                        "server": server_id,
                        "branch": new_branch
                    }
                ) as res:
                    if res.status == 500:
                        data = await res.json(content_type=None)
                        msgs_per_server[server].append(data['error'])
                        continue
                    elif res.status != 200:
                        msgs_per_server[server].append(f"error code {res.status}: `{(await res.text())[:100]}`")
                        continue
                    data = await res.json(content_type=None)
                    msgs_per_server[server].append(f"branch changed from `{old_branch}` to `{new_branch}`")
            else:
                msgs_per_server[server].append(f"branch is `{old_branch}`")
        output = []
        for server, msgs in msgs_per_server.items():
            for msg in msgs:
                output.append(f"{server.short_name}: {msg}")
        if new_branch:
            output.append("Note that this does not retrigger a build. Consider using `]ci build`.")
        for page in pagify('\n'.join(output)):
            await ctx.send(page)

    @wireciendpoint.group(name="testmerge", aliases=["tm"])
    @checks.admin()
    async def testmerge(self, ctx: commands.Context):
        """Manage testmerges."""
        pass

    @testmerge.command()
    async def list(self, ctx: commands.Context, server_name: Optional[str]):
        """List active testmerges on a given server or globally."""
        await self._tm_list(ctx, server_name, verbose=False)

    @testmerge.command()
    async def listverbose(self, ctx: commands.Context, server_name: Optional[str]):
        """List active testmerges on a given server or globally but shows the PR embeds too."""
        await self._tm_list(ctx, server_name, verbose=True)

    async def _tm_list(self, ctx: commands.Context, server_name: Optional[str], verbose: bool):
        tokens = await self.bot.get_shared_api_tokens("wireciendpoint")
        api_key = tokens.get("outgoing_api_key")
        goonservers = self.bot.get_cog("GoonServers")
        server_id = ""
        server = None
        repo = await self.config.repo()
        if server_name:
            server = goonservers.resolve_server(server_name)
            if not server:
                await ctx.send("Unknown server.")
                return
            server_id = server.tgs
        url = tokens.get("ci_path") + f"/test-merges/{server_id}"
        async with self.session.get(
            url,
            headers={
                "api-key": api_key,
            },
        ) as res:
            if res.status != 200:
                for page in pagify(
                    f"Server responded with an error code {res.status}: `{(await res.text())[:100]}`"
                ):
                    await ctx.send(page)
            data = await res.json(content_type=None)
            embed_colour = await ctx.embed_colour()
            if not data:
                if server:
                    await ctx.send(f"No testmerges active on {server.short_name}")
                else:
                    await ctx.send("No testmerges active")
                return
            mod_data = []
            for pr_info in data:
                if pr_info['created_at']:
                    pr_info['created_at'] = datetime.datetime.fromisoformat(pr_info['created_at'])
                if pr_info['updated_at']:
                    pr_info['updated_at'] = datetime.datetime.fromisoformat(pr_info['updated_at'])
                def similar(a, b):
                    if isinstance(a, datetime.date) and isinstance(b, datetime.date):
                        return abs((a - b).total_seconds()) <= 60 * 30
                    else:
                        return a == b
                if mod_data and all(similar(mod_data[-1][key], pr_info[key]) for key in pr_info if key != 'server'):
                    mod_data[-1]['servers'].append(pr_info['server'])
                else:
                    mod_data.append(pr_info)
                    mod_data[-1]['servers'] = [pr_info['server']]
            data = mod_data
            current_embed = None
            current_embed_size = 0
            pages = []
            pr_links = set()
            for pr_info in data:
                text_to_add = ""
                pr_link = f"https://github.com/{repo}/pull/{pr_info['PR']}"
                pr_links.add(pr_link)
                text_to_add += f"[{pr_info['PR']}]({pr_link})"
                if pr_info['server']:
                    text_to_add += " on " + ", ".join(pr_info['servers'])
                else:
                    text_to_add += " on all servers"
                if pr_info['requester']:
                    text_to_add += f" by <{pr_info['requester']}>"
                if pr_info['created_at']:
                    text_to_add += f" on <t:{int(pr_info['created_at'].timestamp())}:f>"
                if pr_info['commit']:
                    text_to_add += f" [{pr_info['commit'][:7]}](https://github.com/{repo}/pull/{pr_info['PR']}/commits/{pr_info['commit']})"
                text_to_add += "\n"
                if pr_info['updater'] or pr_info['updated_at']:
                    text_to_add += "\N{No-Break Space}" * 5
                    text_to_add += "updated"
                    if pr_info['updater']:
                        text_to_add += f" by <{pr_info['updater']}>"
                    if pr_info['updated_at']:
                        text_to_add += f" on <t:{int(pr_info['updated_at'].timestamp())}:f>"
                    text_to_add += "\n"
                if current_embed_size + len(text_to_add) >= 4000:
                    pages.append(current_embed)
                    current_embed_size = 0
                    current_embed = None
                if current_embed is None:
                    current_embed = discord.Embed(
                            title = f"Testmerges of {server.short_name}" if server else "Testmerges",
                            color = embed_colour,
                            description = "",
                        )
                    current_embed_size += len(current_embed.title)
                current_embed_size += len(text_to_add)
                current_embed.description += text_to_add
            if current_embed:
                pages.append(current_embed)
            for i, page in enumerate(pages):
                page.set_footer(text=f"{i+1}/{len(pages)}")
            if not pages:
                await ctx.send("Something went wrong")
                return
            if len(pages) > 1:
                # TODO PR embeds
                await menu(ctx, pages, DEFAULT_CONTROLS, timeout=60.0)
            else:
                await ctx.send(embed=pages[0])
            if verbose:
                await ctx.send("\n".join(pr_links))

    @testmerge.command()
    async def bingus(self, ctx: commands.Context, pr: int):
        repo_name = await self.config.repo()
        def apply_label(github: Github):
            repo = github.get_repo(repo_name)
            pr_obj = repo.get_pull(pr)
            label = repo.get_label(TM_LABEL)
            pr_obj.add_to_labels(label)
        await self.run_with_github(apply_label, ctx)

    @testmerge.command()
    async def merge(self, ctx: commands.Context, pr: int, server_name: Optional[str], commit: Optional[str]):
        """Testmerges a given PR number at the latest or given GitHub commit to a given server or globally."""
        tokens = await self.bot.get_shared_api_tokens("wireciendpoint")
        api_key = tokens.get("outgoing_api_key")
        goonservers = self.bot.get_cog("GoonServers")
        if server_name is None:
            server_name = "standard"
        servers = goonservers.resolve_server_or_category(server_name)
        if not servers:
            await ctx.send("Unknown server.")
            return
        if commit and len(commit) != 40:
            await ctx.send("Error: That is not a full commit hash.")
            return
        all_success = True
        successful_servers = []
        for server in servers: 
            server_id = server.tgs
            url = tokens.get("ci_path") + f"/test-merges"
            send_data = {
                'pr': pr,
                'server': server_id,
                'requester': f"@{ctx.author.id}",
            }
            if commit:
                send_data['commit'] = commit
            async with self.session.post(
                url,
                headers={
                    "api-key": api_key,
                },
                json=send_data,
            ) as res:
                if res.status != 200:
                    for page in pagify(
                            f"{server.short_name}: Server responded with an error code {res.status}: `{(await res.text())[:100]}`"
                    ):
                        await ctx.send(page)
                        all_success = False
                else:
                    data = await res.json(content_type=None)
                    if data.get('success', None):
                        successful_servers.append(server)
                    else:
                        all_success = False
                        await ctx.send(f"{server.short_name}: Unknown response: `{data}`")
        if all_success:
            await ctx.message.add_reaction("\N{WHITE HEAVY CHECK MARK}")
            await ctx.send("Success - note that this does not retrigger a build. Consider using `]ci build`.")
        await self.testmerge_announce("\N{White Heavy Check Mark} **New** testmerge", pr=pr, servers=successful_servers, commit=commit)

        repo_name = await self.config.repo()
        def apply_label(github: Github):
            repo = github.get_repo(repo_name)
            pr_obj = repo.get_pull(pr)
            label = repo.get_label(TM_LABEL)
            pr_obj.add_to_labels(label)
        await self.run_with_github(apply_label, ctx)

    async def testmerge_announce(self, message: str, pr: int, servers: List[Any], commit: Optional[str] = None):
        channels = await self.config.testmerge_channels()
        if not len(channels):
            return
        repo = await self.config.repo()
        msg = message + "\n"
        msg += f"https://github.com/{repo}/pull/{pr}\n"
        if commit:
            msg += f"on commit https://github.com/{repo}/pull/{pr}/commits/{commit}"
        if len(servers):
            msg += "on servers "
            for server in servers:
                msg += server.short_name + " "
        for channel_id in channels:
            channel = self.bot.get_channel(int(channel_id))
            if channel:
                await channel.send(msg)

    @testmerge.command()
    async def update(self, ctx: commands.Context, pr: int, server_name: Optional[str], commit: Optional[str]):
        """Updates a given testmerge to the latest or given GitHub commit on a given server or globally."""
        tokens = await self.bot.get_shared_api_tokens("wireciendpoint")
        api_key = tokens.get("outgoing_api_key")
        goonservers = self.bot.get_cog("GoonServers")
        if server_name is None:
            server_name = "standard"
        servers = goonservers.resolve_server_or_category(server_name)
        if not servers:
            await ctx.send("Unknown server.")
            return
        if commit and len(commit) != 40:
            await ctx.send("Error: That is not a full commit hash.")
            return
        all_success = True
        successful_servers = []
        for server in servers: 
            server_id = server.tgs
            url = tokens.get("ci_path") + f"/test-merges"
            send_data = {
                'pr': pr,
                'server': server_id,
                'updater': f"@{ctx.author.id}"
            }
            if commit:
                send_data['commit'] = commit
            async with self.session.put(
                url,
                headers={
                    "api-key": api_key,
                },
                json=send_data,
            ) as res:
                if res.status != 200:
                    for page in pagify(
                            f"{server.short_name}: Server responded with an error code {res.status}: `{(await res.text())[:100]}`"
                    ):
                        await ctx.send(page)
                        all_success = False
                else:
                    data = await res.json(content_type=None)
                    if data.get('success', None):
                        successful_servers.append(server)
                    else:
                        all_success = False
                        await ctx.send(f"{server.short_name}: Unknown response: `{data}`")
        if all_success:
            await ctx.message.add_reaction("\N{WHITE HEAVY CHECK MARK}")
            await ctx.send("Success - note that this does not retrigger a build. Consider using `]ci build`.")
        await self.testmerge_announce("\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS} **Updated** testmerge", pr=pr, servers=successful_servers, commit=commit)

    @testmerge.command(name="cancel")
    async def tm_cancel(self, ctx: commands.Context, pr: int, server_name: Optional[str]):
        """Cancels a given testmerge on a given server or globally."""
        tokens = await self.bot.get_shared_api_tokens("wireciendpoint")
        api_key = tokens.get("outgoing_api_key")
        goonservers = self.bot.get_cog("GoonServers")
        if server_name is None:
            server_name = "standard"
        servers = goonservers.resolve_server_or_category(server_name)
        if not servers:
            await ctx.send("Unknown server.")
            return
        all_success = True
        successful_servers = []
        for server in servers: 
            server_id = server.tgs
            url = tokens.get("ci_path") + f"/test-merges"
            send_data = {
                'pr': pr,
                'server': server_id,
            }
            async with self.session.delete(
                url,
                headers={
                    "api-key": api_key,
                },
                json=send_data,
            ) as res:
                if res.status != 200:
                    for page in pagify(
                            f"{server.short_name}: Server responded with an error code {res.status}: `{(await res.text())[:100]}`"
                    ):
                        await ctx.send(page)
                        all_success = False
                else:
                    data = await res.json(content_type=None)
                    if data.get('success', None):
                        successful_servers.append(server)
                    else:
                        all_success = False
                        await ctx.send(f"{server.short_name}: Unknown response: `{data}`")
        if all_success:
            await ctx.message.add_reaction("\N{WHITE HEAVY CHECK MARK}")
            await ctx.send("Success - note that this does not retrigger a build. Consider using `]ci build`.")
        await self.testmerge_announce("\N{CROSS MARK} **Cancelled** testmerge", pr=pr, servers=successful_servers)

        repo_name = await self.config.repo()
        def remove_label(github: Github):
            repo = github.get_repo(repo_name)
            pr_obj = repo.get_pull(pr)
            label = repo.get_label(TM_LABEL)
            try:
                pr_obj.remove_from_labels(label)
            except GithubException as e:
                if e.data['message'] != "Label does not exist":
                    raise e
        await self.run_with_github(remove_label, ctx)

    @testmerge.command()
    async def addchannel(
        self, ctx: commands.Context, channel: Optional[discord.TextChannel]
    ):
        """Subscribe a channel to receive testmerge updates."""
        if channel is None:
            channel = ctx.channel
        async with self.config.testmerge_channels() as channels:
            channels[str(channel.id)] = None
        await ctx.send(
            f"Channel {channel.mention} will now receive notifications about testmerges."
        )

    @testmerge.command()
    async def removechannel(
        self, ctx: commands.Context, channel: Optional[discord.TextChannel]
    ):
        """Unsubscribe a channel from testmerge updates."""
        if channel is None:
            channel = ctx.channel
        async with self.config.testmerge_channels() as channels:
            del channels[str(channel.id)]
        await ctx.send(
            f"Channel {channel.mention} will no longer receive notifications about testmerges."
        )

    @testmerge.command()
    async def checkchannels(self, ctx: commands.Context):
        """Check channels subscribed to testmerge updates."""
        channel_ids = await self.config.testmerge_channels()
        if not channel_ids:
            await ctx.send("No channels.")
        else:
            await ctx.send(
                "\n".join(self.bot.get_channel(int(ch)).mention for ch in channel_ids)
            )
