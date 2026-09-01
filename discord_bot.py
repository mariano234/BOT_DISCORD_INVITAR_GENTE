import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from discord import app_commands
from discord.ext import commands

# ==========================================
# 1. SERVIDOR WEB INTERNO (Engaña a Render para no suspender el bot)
# ==========================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot OK")

    def log_message(self, format, *args):
        # Desactiva los logs repetitivos de peticiones HTTP en la consola
        return

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

# Inicia el servidor HTTP en un hilo secundario independiente
threading.Thread(target=run_web_server, daemon=True).start()


# ==========================================
# 2. CÓDIGO DEL BOT DE DISCORD
# ==========================================
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.tree.add_command(invite_context_menu)
        await self.tree.sync()

bot = MyBot()

@app_commands.context_menu(name="Enviar Invitación")
@app_commands.checks.has_permissions(administrator=True)
async def invite_context_menu(interaction: discord.Interaction, message: discord.Message):
    usuario = message.author

    if interaction.guild is None:
        await interaction.response.send_message("Este comando solo funciona dentro de un servidor.", ephemeral=True)
        return

    # Obtiene el canal configurado en la variable de entorno o usa '👋bienvenida' por defecto
    nombre_canal_configurado = os.environ.get('CANAL_BIENVENIDA', '👋bienvenida')
    target_channel = discord.utils.get(interaction.guild.text_channels, name=nombre_canal_configurado)
    
    # Si el canal no existe con ese nombre, se usará el canal actual
    if target_channel is None:
        target_channel = interaction.channel

    try:
        # Genera la invitación única (caduca en 3 días / 1 solo uso)
        invite = await target_channel.create_invite(
            max_uses=1, 
            max_age=259200, 
            unique=True, 
            reason=f"Invitación creada por Admin {interaction.user} para {usuario}"
        )
        
        # Envía el mensaje privado al usuario objetivo
        await usuario.send(
            f"¡Hola! Has sido invitado a **{interaction.guild.name}**.\n"
            f"Aquí tienes tu enlace de invitación: {invite.url}\n"
            f"*Nota: Solo tiene un uso y caduca a los 3 días.*"
        )
        
        await interaction.response.send_message(
            f"✅ ¡Invitación generada para #{target_channel.name} y enviada por privado a **{usuario.name}**!", 
            ephemeral=True
        )
        
    except discord.Forbidden:
        await interaction.response.send_message(
            f"❌ No pude enviarle el mensaje a {usuario.mention}. Asegúrate de que tenga los mensajes privados (DMs) abiertos.", 
            ephemeral=True
        )

@invite_context_menu.error
async def invite_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("❌ No tienes permisos de Administrador para usar esta función.", ephemeral=True)

# ==========================================
# 3. INICIO DE SESIÓN
# ==========================================
token = os.environ.get('DISCORD_TOKEN')
if token:
    bot.run(token)
else:
    print("Error: No se encontró la variable de entorno DISCORD_TOKEN")
