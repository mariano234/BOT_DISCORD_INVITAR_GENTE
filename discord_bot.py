import os
import random
import threading
import sqlite3
import asyncio
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

import discord
from discord import app_commands
from discord.ext import commands, tasks

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

# Registro ampliado de actividad de usuarios
cursor.execute('''
    CREATE TABLE IF NOT EXISTS ultima_actividad (
        user_id INTEGER PRIMARY KEY,
        last_seen TEXT,
        total_mensajes INTEGER DEFAULT 1
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
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO ultima_actividad (user_id, last_seen, total_mensajes)
        VALUES (?, ?, 1)
        ON CONFLICT(user_id) DO UPDATE SET 
            last_seen = excluded.last_seen,
            total_mensajes = total_mensajes + 1
    ''', (user_id, now))
    conn.commit()

# ==========================================
# 3. BOT DE DISCORD Y TAREAS PROGRAMADAS
# ==========================================
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True          # Para auditoría de miembros y roles
        intents.message_content = True  # Para registrar actividad
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.tree.add_command(invite_context_menu)
        await self.tree.sync()
        # Iniciar la tarea automática en segundo plano
        comprobar_inactividad_task.start(self)

bot = MyBot()

# --- TAREA PROGRAMADA: Auditoría automática cada 24 horas ---
@tasks.loop(hours=24)
async def comprobar_inactividad_task(bot_instance):
    await bot_instance.wait_until_ready()
    dias_limite = int(os.environ.get('DIAS_INACTIVIDAD', 30))
    nombre_rol_inactivo = os.environ.get('NOMBRE_ROL_INACTIVO', 'Inactivo')

    now = datetime.now(timezone.utc)

    for guild in bot_instance.guilds:
        rol_inactivo = discord.utils.get(guild.roles, name=nombre_rol_inactivo)
        if not rol_inactivo:
            continue

        for member in guild.members:
            if member.bot:
                continue

            cursor.execute("SELECT last_seen FROM ultima_actividad WHERE user_id = ?", (member.id,))
            fila = cursor.fetchone()

            if fila and fila[0]:
                last_seen_dt = datetime.strptime(fila[0], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
            else:
                # Si no hay registro en DB, usar su fecha de entrada al servidor
                last_seen_dt = member.joined_at or now

            dias_inactivo = (now - last_seen_dt).days

            # Asignar rol si supera el límite de días y no lo tiene aún
            if dias_inactivo >= dias_limite and rol_inactivo not in member.roles:
                try:
                    await member.add_roles(rol_inactivo, reason=f"Inactividad superior a {dias_limite} días.")
                except discord.Forbidden:
                    pass

# --- EVENTO AL ENTRAR UN NUEVO MIEMBRO ---
@bot.event
async def on_member_join(member: discord.Member):
    # Registrar su fecha de llegada en la BD
    registrar_actividad(member.id)

    # 1. Bienvenida
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

    # 2. Hilo privado de autenticación
    nombre_autenticacion = os.environ.get('CANAL_AUTENTICACION', '🛑autenticación')
    canal_autenticacion = discord.utils.get(member.guild.text_channels, name=nombre_autenticacion)

    if not canal_autenticacion:
        return

    try:
        await canal_autenticacion.set_permissions(
            member,
            view_channel=True,
            read_message_history=True,
            send_messages=False,
            send_messages_in_threads=True
        )
    except discord.Forbidden:
        pass

    num1 = random.randint(1, 10)
    num2 = random.randint(1, 10)
    resultado = num1 + num2

    try:
        thread = await canal_autenticacion.create_thread(
            name=f"🔒-verificacion-{member.name}",
            type=discord.ChannelType.private_thread,
            invitable=False
        )
        await thread.add_user(member)

        cursor.execute(
            "INSERT OR REPLACE INTO hilos_verificacion (thread_id, user_id, resultado) VALUES (?, ?, ?)",
            (thread.id, member.id, resultado)
        )
        conn.commit()

        embed_pregunta = discord.Embed(
            title="🔒 Verificación de Seguridad",
            description=(
                f"¡Hola {member.mention}! Para acceder al resto de canales, "
                f"responde a este mensaje escribiendo únicamente el resultado de la siguiente suma:\n\n"
                f"👉 **¿Cuánto es {num1} + {num2}?**"
            ),
            color=discord.Color.green()
        )
        await thread.send(embed=embed_pregunta)

    except discord.Forbidden:
        pass

# --- LECTURA DE MENSAJES Y RECUPERACIÓN AUTOMÁTICA ---
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if message.guild:
        # 1. Actualizar registro de actividad
        registrar_actividad(message.author.id)

        # 2. RECUPERACIÓN AUTOMÁTICA: Retirar el rol Inactivo al volver a hablar
        nombre_rol_inactivo = os.environ.get('NOMBRE_ROL_INACTIVO', 'Inactivo')
        rol_inactivo = discord.utils.get(message.guild.roles, name=nombre_rol_inactivo)

        if rol_inactivo and rol_inactivo in message.author.roles:
            try:
                await message.author.remove_roles(rol_inactivo, reason="El usuario ha vuelto a interactuar en el servidor.")
            except discord.Forbidden:
                pass

    # 3. Procesar verificación en hilos
    if isinstance(message.channel, discord.Thread):
        cursor.execute("SELECT user_id, resultado FROM hilos_verificacion WHERE thread_id = ?", (message.channel.id,))
        fila = cursor.fetchone()

        if fila and message.author.id == fila[0]:
            if message.content.strip() == str(fila[1]):
                nombre_rol = os.environ.get('NOMBRE_ROL_AUTENTICADO', 'Autenticado')
                rol = discord.utils.get(message.guild.roles, name=nombre_rol)

                if rol:
                    try:
                        await message.author.add_roles(rol)
                        await message.channel.send(f"🎉 ¡Respuesta correcta! Se te ha asignado el rol **{rol.name}**. Eliminando canal...")
                    except discord.Forbidden:
                        await message.channel.send("⚠️ El bot no tiene permisos suficientes para asignarte el rol.")

                cursor.execute("DELETE FROM hilos_verificacion WHERE thread_id = ?", (message.channel.id,))
                conn.commit()

                if message.channel.parent:
                    try:
                        await message.channel.parent.set_permissions(message.author, overwrite=None)
                    except Exception:
                        pass

                await asyncio.sleep(2)
                await message.channel.delete()
            else:
                await message.channel.send("❌ Respuesta incorrecta. Inténtalo de nuevo.")

    await bot.process_commands(message)

# ==========================================
# 4. COMANDOS SLASH DE GESTIÓN Y ACTIVIDAD
# ==========================================

# --- Comando 1: Consulta de ficha individual ---
@bot.tree.command(name="actividad_usuario", description="Muestra información detallada sobre la actividad de un miembro")
@app_commands.describe(usuario="El usuario del que deseas consultar información")
async def actividad_usuario(interaction: discord.Interaction, usuario: discord.Member):
    cursor.execute("SELECT last_seen, total_mensajes FROM ultima_actividad WHERE user_id = ?", (usuario.id,))
    fila = cursor.fetchone()

    now = datetime.now(timezone.utc)
    nombre_rol_inactivo = os.environ.get('NOMBRE_ROL_INACTIVO', 'Inactivo')
    tiene_rol_inactivo = any(r.name == nombre_rol_inactivo for r in usuario.roles)

    if fila and fila[0]:
        last_seen_dt = datetime.strptime(fila[0], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        dias_inactivo = (now - last_seen_dt).days
        fecha_str = last_seen_dt.strftime('%d/%m/%Y %H:%M UTC')
        mensajes = fila[1]
    else:
        last_seen_dt = usuario.joined_at or now
        dias_inactivo = (now - last_seen_dt).days
        fecha_str = "Sin mensajes (desde que entró al servidor)"
        mensajes = 0

    embed = discord.Embed(
        title=f"📊 Estado de Actividad: {usuario.display_name}",
        color=discord.Color.red() if tiene_rol_inactivo else discord.Color.green()
    )
    embed.set_thumbnail(url=usuario.display_avatar.url)
    embed.add_field(name="Estado actual", value="🛑 Inactivo" if tiene_rol_inactivo else "🟢 Activo", inline=True)
    embed.add_field(name="Días sin interactuar", value=f"**{dias_inactivo}** días", inline=True)
    embed.add_field(name="Mensajes registrados", value=str(mensajes), inline=True)
    embed.add_field(name="Última interacción", value=fecha_str, inline=False)
    embed.add_field(name="Unión al servidor", value=usuario.joined_at.strftime('%d/%m/%Y'), inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- Comando 2: Listar inactivos por umbral de días ---
@bot.tree.command(name="lista_inactivos", description="Lista los usuarios que llevan más de X días sin enviar mensajes")
@app_commands.describe(dias="Número mínimo de días de inactividad (por defecto 30)")
@app_commands.checks.has_permissions(administrator=True)
async def lista_inactivos(interaction: discord.Interaction, dias: int = 30):
    await interaction.response.defer(ephemeral=True)
    now = datetime.now(timezone.utc)
    inactivos_encontrados = []

    for member in interaction.guild.members:
        if member.bot:
            continue

        cursor.execute("SELECT last_seen FROM ultima_actividad WHERE user_id = ?", (member.id,))
        fila = cursor.fetchone()

        if fila and fila[0]:
            last_seen_dt = datetime.strptime(fila[0], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        else:
            last_seen_dt = member.joined_at or now

        dias_calc = (now - last_seen_dt).days
        if dias_calc >= dias:
            inactivos_encontrados.append((member, dias_calc))

    if not inactivos_encontrados:
        await interaction.followup.send(f"✅ No hay ningún usuario con más de **{dias}** días de inactividad.")
        return

    # Ordenar de mayor a menor inactividad
    inactivos_encontrados.sort(key=lambda x: x[1], reverse=True)

    texto = f"📋 **Usuarios inactivos por más de {dias} días ({len(inactivos_encontrados)} en total):**\n\n"
    for m, d in inactivos_encontrados[:15]:  # Muestra los primeros 15
        texto += f"• {m.mention} — **{d}** días sin actividad\n"

    if len(inactivos_encontrados) > 15:
        texto += f"\n*...y {len(inactivos_encontrados) - 15} usuarios más.*"

    await interaction.followup.send(texto)

# --- Comando 3: Escaneo manual e imposición del rol ---
@bot.tree.command(name="escanear_inactivos", description="Ejecuta un barrido para asignar el rol Inactivo a quienes superen el límite")
@app_commands.checks.has_permissions(administrator=True)
async def escanear_inactivos(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    dias_limite = int(os.environ.get('DIAS_INACTIVIDAD', 30))
    nombre_rol_inactivo = os.environ.get('NOMBRE_ROL_INACTIVO', 'Inactivo')
    rol_inactivo = discord.utils.get(interaction.guild.roles, name=nombre_rol_inactivo)

    if not rol_inactivo:
        await interaction.followup.send(f"❌ El rol **{nombre_rol_inactivo}** no existe en el servidor. Créalo antes de escanear.")
        return

    now = datetime.now(timezone.utc)
    asignados = 0

    for member in interaction.guild.members:
        if member.bot:
            continue

        cursor.execute("SELECT last_seen FROM ultima_actividad WHERE user_id = ?", (member.id,))
        fila = cursor.fetchone()

        if fila and fila[0]:
            last_seen_dt = datetime.strptime(fila[0], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        else:
            last_seen_dt = member.joined_at or now

        dias_calc = (now - last_seen_dt).days

        if dias_calc >= dias_limite and rol_inactivo not in member.roles:
            try:
                await member.add_roles(rol_inactivo, reason=f"Escaneo manual: >{dias_limite} días inactivo.")
                asignados += 1
            except discord.Forbidden:
                pass

    await interaction.followup.send(f"🔍 **Escaneo completado.** Se ha asignado el rol **{rol_inactivo.name}** a **{asignados}** usuario(s) por llevar más de {dias_limite} días inactivos.")

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
