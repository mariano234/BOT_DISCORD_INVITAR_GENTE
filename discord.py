import discord
from discord import app_commands
from discord.ext import commands

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync() # Sincroniza los Slash Commands con Discord

bot = MyBot()

@bot.tree.command(name="invite", description="Genera una invitación de un solo uso por privado")
async def invite(interaction: discord.Interaction):
    # 1. Asegurarse de que el comando se ejecuta dentro de un servidor
    if interaction.guild is None:
        await interaction.response.send_message("Este comando solo funciona dentro de un servidor.", ephemeral=True)
        return

    try:
        # 2. Crear la invitación de un solo uso (max_uses=1) y que expire en 24 horas (max_age=86400)
        invite = await interaction.channel.create_invite(
            max_uses=1, 
            max_age=86400, 
            unique=True, 
            reason=f"Invitación solicitada por {interaction.user}"
        )
        
        # 3. Enviar la invitación por Mensaje Privado (DM)
        await interaction.user.send(f"Aquí tienes tu invitación exclusiva para **{interaction.guild.name}**: {invite.url}\n*Nota: Solo tiene un uso y expirará en 24 horas.*")
        
        # 4. Avisar al usuario en el canal de forma privada (efímera)
        await interaction.response.send_message("¡Listo! Revisa tus mensajes privados, te he enviado la invitación.", ephemeral=True)
        
    except discord.Forbidden:
        # Por si el bot no tiene permisos en el canal o el usuario tiene los DMs cerrados
        await interaction.response.send_message("No pude enviarte el mensaje. Por favor, asegúrate de tener los mensajes privados permitidos para este servidor.", ephemeral=True)

bot.run('TU_BOT_TOKEN_AQUÍ')
