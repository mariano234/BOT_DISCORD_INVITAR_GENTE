import os
import random
import threading
import sqlite3
import asyncio
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

import discord
from discord import app_commands
from discord.ext import commands

# ==========================================
# 1. SERVIDOR WEB INTERNO PARA RENDER
# ==========================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot OK")

    def log_message(self, format, *args):
        return

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# ==========================================
# 2. BASE DE DATOS LOCAL (SQLITE)
# ==========================================
conn = sqlite3.connect('bot_database.db', check_same_thread=False)
cursor = conn.cursor()

# Registro de actividad de usuarios
cursor.execute('''
    CREATE TABLE IF NOT EXISTS ultima_actividad (
        user_id INTEGER PRIMARY KEY,
        last_seen TEXT
    )
''')

# Seguimiento de hilos de verificación pendientes
cursor.execute('''
    CREATE TABLE IF NOT EXISTS hilos_verificacion (
        thread_id INTEGER PRIMARY KEY,
        user_id INTEGER,
        resultado INTEGER
    )
''')
conn.commit()

def registrar_actividad(user_id: int):
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO ultima_actividad (user_id, last_seen)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET last_seen=excluded.last_seen
    ''', (user_id, now))
    conn.commit()

# ==========================================
# 3. BOT DE DISCORD Y EVENTOS
# ==========================================
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True          # Para detectar la entrada de miembros
        intents.message_content = True  # Para leer respuestas de verificación y mensajes
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.tree.add_command(invite_context_menu)
        await self.tree.sync()

bot = MyBot()

# --- EVENTO AUTOMÁTICO AL ENTRAR UN NUEVO MIEMBRO ---
@bot.event
async def on_member_join(member: discord.Member):
    # 1. Anuncio en el canal de bienvenida (opcional)
    nombre_bienvenida = os.environ.get('CANAL_BIENVENIDA', '👋bienvenida')
    canal_bienvenida = discord.utils.get(member.guild.text_channels, name=nombre_bienvenida)
    
    if canal_bienvenida:
        embed_bienvenida = discord.Embed(
            title=f"¡Bienvenido/a a {member.guild.name}! 👋",
            description=f"Hola {member.mention}, por favor dirígete al canal de autenticación para verificar tu cuenta.",
            color=discord.Color.blue()
        )
        embed_bienvenida.set_thumbnail(url=member.display_avatar.url)
        await canal_bienvenida.send(embed=embed_bienvenida)

    # 2. Búsqueda del canal de autenticación
    nombre_autenticacion = os.environ.get('CANAL_AUTENTICACION', '🛑autenticación')
    canal_autenticacion = discord.utils.get(member.guild.text_channels, name=nombre_autenticacion)

    if not canal_autenticacion:
        print(f"Error: No se encontró el canal '{nombre_autenticacion}' en {member.guild.name}.")
        return

    # Generar números aleatorios para la verificación
    num1 = random.randint(1, 10)
    num2 = random.randint(1, 10)
    resultado = num1 + num2

    try:
        # Crear un Hilo Privado en el canal de autenticación exclusivo para el nuevo usuario
        thread = await canal_autenticacion.create_thread(
            name=f"🔒-verificacion-{member.name}",
            type=discord.ChannelType.private_thread,
            invitable=False
        )
        
        # Unir al usuario al hilo
        await thread.add_user(member)

        # Guardar registro temporal en la base de datos
        cursor.execute(
            "INSERT OR REPLACE INTO hilos_verificacion (thread_id, user_id, resultado) VALUES (?, ?, ?)",
            (thread.id, member.id, resultado)
        )
        conn.commit()

        # Enviar el mensaje con la pregunta matemática
        embed_pregunta = discord.Embed(
            title="🔒 Verificación de Seguridad",
            description=(
                f"¡Hola {member.mention}! Para acceder al resto de canales y obtener tu rol, "
                f"responde a este mensaje escribiendo únicamente el resultado de la siguiente suma:\n\n"
                f"👉 **¿Cuánto es {num1} + {num2}?**"
            ),
            color=discord.Color.green()
        )
        await thread.send(embed=embed_pregunta)

    except discord.Forbidden:
        print(f"Error: El bot carece de permisos para crear hilos privados en #{canal_autenticacion.name}.")

# --- LECTURA DE MENSAJES Y RESPUESTAS ---
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Registrar última actividad de mensajes del servidor
    if message.guild:
        registrar_actividad(message.author.id)

    # Procesar verificación si el mensaje proviene de un hilo privado
    if isinstance(message.channel, discord.Thread):
        cursor.execute("SELECT user_id, resultado FROM hilos_verificacion WHERE thread_id = ?", (message.channel.id,))
        fila = cursor.fetchone()

        if fila:
            user_id, resultado_esperado = fila

            # Comprobar que solo el usuario al que se evalúa pueda responder
            if message.author.id == user_id:
                respuesta = message.content.strip()

                if respuesta == str(resultado_esperado):
                    nombre_rol = os.environ.get('NOMBRE_ROL_AUTENTICADO', 'Autenticado')
                    rol = discord.utils.get(message.guild.roles, name=nombre_rol)

                    if rol:
                        try:
                            await message.author.add_roles(rol)
                            await message.channel.send(f"🎉 ¡Respuesta correcta! Se te ha asignado el rol **{rol.name}**. Eliminando este canal...")
                        except discord.Forbidden:
                            await message.channel.send("⚠️ El bot no tiene permisos suficientes para asignarte el rol. Verifica la jerarquía de roles.")
                    else:
                        await message.channel.send(f"⚠️ El rol **{nombre_rol}** no existe en el servidor.")

                    # Limpiar registro en la base de datos
                    cursor.execute("DELETE FROM hilos_verificacion WHERE thread_id = ?", (message.channel.id,))
                    conn.commit()

                    # Esperar 2 segundos y eliminar el hilo para no dejar residuos
                    await asyncio.sleep(2)
                    await message.channel.delete()

                else:
                    await message.channel.send("❌ Respuesta incorrecta. Escribe de nuevo el resultado correcto.")

    await bot.process_commands(message)

# ==========================================
# 4. COMANDOS SLASH Y MENÚ CONTEXTUAL
# ==========================================

# --- Comando Admin: Consultar Actividad / Inactividad ---
@bot.tree.command(name="inactivos", description="Muestra miembros con registro de actividad")
@app_commands.checks.has_permissions(administrator=True)
async def inactivos(interaction: discord.Interaction):
    cursor.execute("SELECT user_id, last_seen FROM ultima_actividad ORDER BY last_seen ASC LIMIT 10")
    registros = cursor.fetchall()

    if not registros:
        await interaction.response.send_message("No hay registros de actividad aún.", ephemeral=True)
        return

    texto = "**Últimas actividades registradas:**\n"
    for uid, fecha in registros:
        user = interaction.guild.get_member(uid)
        nombre = user.name if user else f"ID {uid}"
        texto += f"• **{nombre}**: {fecha} UTC\n"

    await interaction.response.send_message(texto, ephemeral=True)

# --- Menú Contextual: Enviar Invitación ---
@app_commands.context_menu(name="Enviar Invitación")
@app_commands.checks.has_permissions(administrator=True)
async def invite_context_menu(interaction: discord.Interaction, message: discord.Message):
    usuario = message.author
    if interaction.guild is None:
        await interaction.response.send_message("Este comando solo funciona dentro de un servidor.", ephemeral=True)
        return

    nombre_canal_configurado = os.environ.get('CANAL_BIENVENIDA', '👋bienvenida')
    target_channel = discord.utils.get(interaction.guild.text_channels, name=nombre_canal_configurado) or interaction.channel

    try:
        invite = await target_channel.create_invite(max_uses=1, max_age=259200, unique=True, reason=f"Invitación de {interaction.user}")
        await usuario.send(f"¡Hola! Has sido invitado a **{interaction.guild.name}**: {invite.url}")
        await interaction.response.send_message(f"✅ Invitación enviada a **{usuario.name}**.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ No se pudo enviar el mensaje privado al usuario.", ephemeral=True)

# ==========================================
# 5. INICIO DE SESIÓN
# ==========================================
token = os.environ.get('DISCORD_TOKEN')
if token:
    bot.run(token)
else:
    print("Error: No se encontró la variable de entorno DISCORD_TOKEN")
