import os
import discord
from discord import app_commands
from discord.ext import commands

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()

bot = MyBot()

@bot.tree.command(name="invite", description="Genera una invitación de un solo uso por privado")
async def invite(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("Este comando solo funciona dentro de un servidor.", ephemeral=True)
        return

    try:
        # Genera invitación de 1 uso, válida por 24 horas
        invite = await interaction.channel.create_invite(
            max_uses=1, 
            max_age=86400, 
            unique=True, 
            reason=f"Solicitada por {interaction.user}"
        )
        
        await interaction.user.send(f"Aquí tienes tu invitación exclusiva para **{interaction.guild.name}**: {invite.url}\n*Nota: Solo tiene un uso y expirará en 24 horas.*")
        await interaction.response.send_message("¡Listo! Revisa tus mensajes privados.", ephemeral=True)
        
    except discord.Forbidden:
        await interaction.response.send_message("No pude enviarte el mensaje. Asegúrate de tener los DMs abiertos.", ephemeral=True)

# Railway leerá la variable de entorno que configuraremos más adelante
token = os.environ.get('DISCORD_TOKEN')
if token:
    bot.run(token)
else:
    print("Error: No se encontró la variable de entorno DISCORD_TOKEN")
