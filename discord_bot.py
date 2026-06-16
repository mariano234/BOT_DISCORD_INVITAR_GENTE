import os
import discord
from discord import app_commands
from discord.ext import commands

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())

    async def setup_hook(self):
        # Registramos el comando de menú de contexto
        self.tree.add_command(invite_context_menu)
        await self.tree.sync()

bot = MyBot()

# Creamos un menú de contexto que aparece al hacer clic derecho en un MENSAJE
@app_commands.context_menu(name="Enviar Invitación")
@app_commands.checks.has_permissions(administrator=True) # Solo para admins
async def invite_context_menu(interaction: discord.Interaction, message: discord.Message):
    # El usuario objetivo es el autor del mensaje al que le estamos haciendo clic derecho
    usuario = message.author

    if interaction.guild is None:
        await interaction.response.send_message("Este comando solo funciona dentro de un servidor.", ephemeral=True)
        return

    # 1. Leer el nombre del canal desde las variables de entorno (por defecto buscará '👋bienvenida')
    nombre_canal_configurado = os.environ.get('CANAL_BIENVENIDA', '👋bienvenida')
    
    # Buscar el canal por su nombre exacto
    target_channel = discord.utils.get(interaction.guild.text_channels, name=nombre_canal_configurado)
    
    # Si no encuentra el canal configurado, usa el canal donde se usó la app
    if target_channel is None:
        target_channel = interaction.channel

    try:
        # 2. Crear la invitación de 1 solo uso
        invite = await target_channel.create_invite(
            max_uses=1, 
            max_age=259200, 
            unique=True, 
            reason=f"Invitación creada por Admin {interaction.user} mediante menú de contexto para {usuario}"
        )
        
        # 3. Enviar el DM al usuario
        await usuario.send(
            f"¡Hola! Has sido invitado a **{interaction.guild.name}**.\n"
            f"Aquí tienes tu enlace de invitación: {invite.url}\n"
            f"*Nota: Solo tiene un uso y caduca a los 3 días.*"
        )
        
        # 4. Respuesta oculta de confirmación para el admin
        await interaction.response.send_message(
            f"✅ ¡Invitación generada para #{target_channel.name} y enviada por privado a **{usuario.name}**!", 
            ephemeral=True
        )
        
    except discord.Forbidden:
        await interaction.response.send_message(
            f"❌ No pude enviarle el mensaje a {usuario.mention}. Asegúrate de que tenga los DMs abiertos.", 
            ephemeral=True
        )

# Manejador de errores si un no-admin intenta usarlo
@invite_context_menu.error
async def invite_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("❌ No tienes permisos de Administrador para usar esta función.", ephemeral=True)

# Ejecutar el Bot
token = os.environ.get('DISCORD_TOKEN')
if token:
    bot.run(token)
else:
    print("Error: No se encontró la variable de entorno DISCORD_TOKEN")
