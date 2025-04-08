import discord
from discord.ext import commands
from discord.ui import Button, View

class MessageLinkCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="embed", description="指定されたメッセージリンクの内容を埋め込み表示します。")
    async def embed(self, ctx: commands.Context, link: str):
        if "https://discord.com/channels/" not in link:
            await ctx.send("有効なDiscordメッセージリンクを入力してください。")
            return

        try:
            link_parts = link.split("https://discord.com/channels/")[1].split("/")
            if len(link_parts) != 3:
                await ctx.send("無効なメッセージリンク形式です。")
                return
            guild_id, channel_id, message_id = map(int, link_parts)

            target_guild = self.bot.get_guild(guild_id)
            if not target_guild:
                await ctx.send("指定されたサーバーが見つかりません。")
                return

            target_channel = target_guild.get_channel(channel_id)
            if not target_channel or not isinstance(target_channel, discord.abc.Messageable):
                await ctx.send("指定されたチャンネルが見つかりません。")
                return

            target_message = await target_channel.fetch_message(message_id)

            content = target_message.content
            author = target_message.author
            name = author.name
            icon_url = author.avatar.url if author.avatar else author.default_avatar.url
            timestamp = target_message.created_at
            target_message_link = f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"

            embed = discord.Embed(description=content, color=0x00bfff, timestamp=timestamp)
            embed.set_author(name=name, icon_url=icon_url)
            embed.set_footer(text=f"From #{target_message.channel}")

            if target_message.attachments:
                attachment = target_message.attachments[0]
                if any(attachment.filename.lower().endswith(image_ext) for image_ext in ['png', 'jpg', 'jpeg', 'gif', 'webp']):
                    embed.set_image(url=attachment.url)
                else:
                    embed.add_field(name="添付ファイル", value=f"[{attachment.filename}]({attachment.url})")

            view = View(timeout=None)
            view.add_item(Button(label="元のメッセージへ", style=discord.ButtonStyle.link, url=target_message_link))

            await ctx.send(embed=embed, view=view)

            for original_embed in target_message.embeds:
                await ctx.send(embed=original_embed, view=view)

        except Exception as e:
            print(f"エラーが発生しました: {e}")
            await ctx.send("メッセージの埋め込みに失敗しました。")

    async def setup(bot):
        await bot.add_cog(MessageLinkCog(bot))