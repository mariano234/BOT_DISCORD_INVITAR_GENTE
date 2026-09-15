import os
import io
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
# 2. BASE DE DATOS LOCAL Y CONFIGURACIÓN
# ==========================================
conn = sqlite3.connect('bot_database.db', check_same_thread=False)
cursor = conn.cursor()

# Registro de actividad
cursor.execute('''
    CREATE TABLE IF NOT EXISTS ultima_actividad (
        user_id INTEGER PRIMARY KEY,
        last_seen TEXT,
        total_mensajes INTEGER DEFAULT 1
    )
''')

# Hilos de verificación pendientes
cursor.execute('''
    CREATE TABLE IF NOT EXISTS hilos_verificacion (
        thread_id INTEGER PRIMARY KEY,
        user_id INTEGER,
        resultado INTEGER
    )
''')

# Configuración por servidor
cursor.execute('''
    CREATE TABLE IF NOT EXISTS configuracion (
        guild_id INTEGER PRIMARY KEY,
        dias_inactividad INTEGER DEFAULT 30,
        nombre_rol_inactivo TEXT DEFAULT 'Inactivo',
        canal_logs_id INTEGER DEFAULT NULL
    )
''')

# Roles inmunes
cursor.execute('''
    CREATE TABLE IF NOT EXISTS roles_inmunes (
        guild_id INTEGER,
        role_id INTEGER,
        PRIMARY KEY (guild_id, role_id)
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

def obtener_config(guild_id: int):
    cursor.execute("SELECT dias_inactividad, nombre_rol_inactivo, canal_logs_id FROM configuracion WHERE guild_id = ?", (guild_id,))
    fila = cursor.fetchone()
    if not fila:
        cursor.execute("INSERT INTO configuracion (guild_id) VALUES (?)", (guild_id,))
        conn.commit()
        return 30, 'Inactivo', None
    return fila

# ==========================================
# 3. BOT DE DISCORD Y TAREAS EN SEGUNDO PLANO
# ==========================================
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True          
        intents.message_content = True  
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.tree.add_command(invite_context_menu)
        comprobar_inactividad_task.start(self)

bot = MyBot()

# --- COMANDO INSTANTÁNEO DE SINCRONIZACIÓN (!sync) ---
@bot.command()
@commands.has_permissions(administrator=True)
async def sync(ctx):
    bot.tree.copy_global_to(guild=ctx.guild)
    synced = await bot.tree.sync(guild=ctx.guild)
    await ctx.send(f"✅ ¡{len(synced)} comandos Slash sincronizados al instante en este servidor!")

# --- TAREA PROGRAMADA: Auditoría nocturna de inactividad ---
@tasks.loop(hours=24)
async def comprobar_inactividad_task(bot_instance):
    await bot_instance.wait_until_ready()
    now = datetime.now(timezone.utc)

    for guild in bot_instance.guilds:
        dias_limite, nombre_rol_inactivo, canal_logs_id = obtener_config(guild.id)
        rol_inactivo = discord.utils.get(guild.roles, name=nombre_rol_inactivo)
        
        if not rol_inactivo:
            continue

        cursor.execute("SELECT role_id FROM roles_inmunes WHERE guild_id = ?", (guild.id,))
        roles_inmunes_ids = [row[0] for row in cursor.fetchall()]

        canal_logs = guild.get_channel(canal_logs_id) if canal_logs_id else None

        for member in guild.members:
            if member.bot:
                continue

            if any(role.id in roles_inmunes_ids for role in member.roles):
                continue

            cursor.execute("SELECT last_seen FROM ultima_actividad WHERE user_id = ?", (member.id,))
            fila = cursor.fetchone()

            if fila and fila[0]:
                last_seen_dt = datetime.strptime(fila[0], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                dias_inactivo = (now - last_seen_dt).days
            else:
                dias_inactivo = 0

            if dias_inactivo >= dias_limite and rol_inactivo not in member.roles:
                try:
                    await member.add_roles(rol_inactivo, reason=f"Inactividad automática > {dias_limite} días")
                    if canal_logs:
                        embed_log = discord.Embed(
                            title="🛑 Usuario marcado como Inactivo",
                            description=f"El usuario {member.mention} ha superado los **{dias_inactivo}** días sin actividad.",
                            color=discord.Color.red()
                        )
                        await canal_logs.send(embed=embed_log)
                except discord.Forbidden:
                    pass

# --- EVENTO AL ENTRAR UN NUEVO MIEMBRO ---
@bot.event
async def on_member_join(member: discord.Member):
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
                f"¡Hola {member.mention}! Responde a este mensaje escribiendo únicamente "
                f"el resultado de la siguiente suma:\n\n👉 **¿Cuánto es {num1} + {num2}?**"
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
        registrar_actividad(message.author.id)

        _, nombre_rol_inactivo, canal_logs_id = obtener_config(message.guild.id)
        rol_inactivo = discord.utils.get(message.guild.roles, name=nombre_rol_inactivo)

        if rol_inactivo and rol_inactivo in message.author.roles:
            try:
                await message.author.remove_roles(rol_inactivo, reason="Re-activación automática por nuevo mensaje.")
                if canal_logs_id:
                    canal_logs = message.guild.get_channel(canal_logs_id)
                    if canal_logs:
                        embed_reactivado = discord.Embed(
                            title="🎉 Usuario Reactivado",
                            description=f"¡{message.author.mention} ha vuelto a hablar y se le ha retirado el estado Inactivo!",
                            color=discord.Color.green()
                        )
                        await canal_logs.send(embed=embed_reactivado)
            except discord.Forbidden:
                pass

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
                        await message.channel.send(f"🎉 ¡Respuesta correcta! Se te ha asignado el rol **{rol.name}**.")
                    except discord.Forbidden:
                        await message.channel.send("⚠️ No tengo permisos suficientes para asignarte el rol.")

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
                await message.channel.send("❌ Respuesta incorrecta. Vuelve a intentarlo.")

    await bot.process_commands(message)

# ==========================================
# 4. COMANDOS SLASH
# ==========================================

@bot.tree.command(name="config_inactividad", description="Configura los parámetros de inactividad del servidor")
@app_commands.describe(
    dias="Días sin hablar para ser considerado inactivo",
    nombre_rol="Nombre del rol que se asignará automáticamente",
    canal_logs="Canal para notificar altas y bajas de inactivos"
)
@app_commands.checks.has_permissions(administrator=True)
async def config_inactividad(interaction: discord.Interaction, dias: int = None, nombre_rol: str = None, canal_logs: discord.TextChannel = None):
    d_actual, r_actual, c_actual = obtener_config(interaction.guild.id)

    nuevos_dias = dias if dias is not None else d_actual
    nuevo_rol = nombre_rol if nombre_rol is not None else r_actual
    nuevo_canal = canal_logs.id if canal_logs is not None else c_actual

    cursor.execute('''
        UPDATE configuracion 
        SET dias_inactividad = ?, nombre_rol_inactivo = ?, canal_logs_id = ? 
        WHERE guild_id = ?
    ''', (nuevos_dias, nuevo_rol, nuevo_canal, interaction.guild.id))
    conn.commit()

    embed = discord.Embed(title="⚙️ Configuración de Inactividad Actualizada", color=discord.Color.blue())
    embed.add_field(name="Días límite", value=f"**{nuevos_dias}** días", inline=True)
    embed.add_field(name="Rol asignado", value=f"**{nuevo_rol}**", inline=True)
    embed.add_field(name="Canal de logs", value=f"<#{nuevo_canal}>" if nuevo_canal else "No configurado", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="inmunidad_rol", description="Otorga o quita la inmunidad de inactividad a un rol (VIP, Staff, etc.)")
@app_commands.describe(rol="El rol que deseas proteger o desproteger")
@app_commands.checks.has_permissions(administrator=True)
async def inmunidad_rol(interaction: discord.Interaction, rol: discord.Role):
    cursor.execute("SELECT role_id FROM roles_inmunes WHERE guild_id = ? AND role_id = ?", (interaction.guild.id, rol.id))
    existe = cursor.fetchone()

    if existe:
        cursor.execute("DELETE FROM roles_inmunes WHERE guild_id = ? AND role_id = ?", (interaction.guild.id, rol.id))
        conn.commit()
        await interaction.response.send_message(f"🔴 Se ha retirado la inmunidad al rol **{rol.name}**.", ephemeral=True)
    else:
        cursor.execute("INSERT INTO roles_inmunes (guild_id, role_id) VALUES (?, ?)", (interaction.guild.id, rol.id))
        conn.commit()
        await interaction.response.send_message(f"🛡️ El rol **{rol.name}** ahora es **INMUNE** a la inactividad.", ephemeral=True)

# --- COMANDO ACTUALIZADO: Genera un archivo .txt con TODOS los usuarios ---
@bot.tree.command(name="lista_inactivos", description="Genera un informe en .txt con todos los usuarios inactivos")
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
            dias_calc = (now - last_seen_dt).days
            if dias_calc >= dias:
                inactivos_encontrados.append((member, dias_calc))

    if not inactivos_encontrados:
        await interaction.followup.send(f"✅ No hay ningún usuario registrado con más de **{dias}** días de inactividad.")
        return

    inactivos_encontrados.sort(key=lambda x: x[1], reverse=True)

    # Construir el contenido del archivo TXT
    lineas = []
    lineas.append(f"INFORME DE USUARIOS INACTIVOS (+{dias} DÍAS)")
    lineas.append(f"Servidor: {interaction.guild.name}")
    lineas.append(f"Fecha del reporte: {now.strftime('%d/%m/%Y %H:%M UTC')}")
    lineas.append(f"Total encontrados: {len(inactivos_encontrados)}")
    lineas.append("=" * 60 + "\n")

    for member, dias_calc in inactivos_encontrados:
        lineas.append(f"• Usuario: {member.name} (ID: {member.id}) | Inactivo por: {dias_calc} días")

    contenido = "\n".join(lineas)

    # Convertir el texto a archivo adjunto en memoria
    buffer = io.BytesIO(contenido.encode('utf-8'))
    archivo = discord.File(fp=buffer, filename=f"inactivos_{dias}dias.txt")

    await interaction.followup.send(
        content=f"📋 Se han encontrado **{len(inactivos_encontrados)}** usuarios inactivos. Adjunto el informe completo en `.txt`:",
        file=archivo
    )

@bot.tree.command(name="escanear_inactivos", description="Ejecuta un barrido para asignar el rol Inactivo a quienes superen el límite")
@app_commands.checks.has_permissions(administrator=True)
async def escanear_inactivos(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    dias_limite, nombre_rol_inactivo, _ = obtener_config(interaction.guild.id)
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
            dias_calc = (now - last_seen_dt).days
            if dias_calc >= dias_limite and rol_inactivo not in member.roles:
                try:
                    await member.add_roles(rol_inactivo, reason=f"Escaneo manual: >{dias_limite} días inactivo.")
                    asignados += 1
                except discord.Forbidden:
                    pass

    await interaction.followup.send(f"🔍 **Escaneo completado.** Se ha asignado el rol **{rol_inactivo.name}** a **{asignados}** usuario(s) registrados por llevar más de {dias_limite} días inactivos.")

@bot.tree.command(name="top_activos", description="Muestra la clasificación de los usuarios más activos del servidor")
async def top_activos(interaction: discord.Interaction):
    cursor.execute("SELECT user_id, total_mensajes FROM ultima_actividad ORDER BY total_mensajes DESC LIMIT 10")
    registros = cursor.fetchall()

    if not registros:
        await interaction.response.send_message("No hay datos de actividad aún.", ephemeral=True)
        return

    embed = discord.Embed(title="🏆 Top 10 Miembros Más Activos", color=discord.Color.gold())
    medallas = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

    descripcion = ""
    for idx, (uid, msgs) in enumerate(registros):
        user = interaction.guild.get_member(uid)
        nombre = user.mention if user else f"Usuario ID {uid}"
        descripcion += f"{medallas[idx]} {nombre} — **{msgs}** mensajes\n"

    embed.description = descripcion
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="salud_servidor", description="Muestra estadísticas sobre el estado de actividad global del servidor")
@app_commands.checks.has_permissions(administrator=True)
async def salud_servidor(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    dias_limite, nombre_rol_inactivo, _ = obtener_config(interaction.guild.id)
    rol_inactivo = discord.utils.get(interaction.guild.roles, name=nombre_rol_inactivo)

    total_miembros = len([m for m in interaction.guild.members if not m.bot])
    inactivos_con_rol = len(rol_inactivo.members) if rol_inactivo else 0

    activos = total_miembros - inactivos_con_rol
    porcentaje_activos = round((activos / total_miembros) * 100, 1) if total_miembros > 0 else 0

    embed = discord.Embed(title=f"📈 Salud de la Comunidad: {interaction.guild.name}", color=discord.Color.purple())
    embed.add_field(name="Total Miembros (Humano)", value=str(total_miembros), inline=True)
    embed.add_field(name="🟢 Miembros Activos", value=f"{activos} ({porcentaje_activos}%)", inline=True)
    embed.add_field(name="🛑 Miembros Inactivos", value=f"{inactivos_con_rol} ({round(100 - porcentaje_activos, 1)}%)", inline=True)
    embed.add_field(name="Criterio de Inactividad", value=f">{dias_limite} días sin interactuar", inline=False)

    await interaction.followup.send(embed=embed)

@bot.tree.command(name="actividad_usuario", description="Muestra información detallada sobre la actividad de un miembro")
async def actividad_usuario(interaction: discord.Interaction, usuario: discord.Member):
    cursor.execute("SELECT last_seen, total_mensajes FROM ultima_actividad WHERE user_id = ?", (usuario.id,))
    fila = cursor.fetchone()

    now = datetime.now(timezone.utc)
    _, nombre_rol_inactivo, _ = obtener_config(interaction.guild.id)
    tiene_rol_inactivo = any(r.name == nombre_rol_inactivo for r in usuario.roles)

    if fila and fila[0]:
        last_seen_dt = datetime.strptime(fila[0], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        dias_inactivo = (now - last_seen_dt).days
        fecha_str = last_seen_dt.strftime('%d/%m/%Y %H:%M UTC')
        mensajes = fila[1]
    else:
        dias_inactivo = 0
        fecha_str = "Sin mensajes registrados aún"
        mensajes = 0

    embed = discord.Embed(
        title=f"📊 Estado de Actividad: {usuario.display_name}",
        color=discord.Color.red() if tiene_rol_inactivo else discord.Color.green()
    )
    embed.set_thumbnail(url=usuario.display_avatar.url)
    embed.add_field(name="Estado actual", value="🛑 Inactivo" if tiene_rol_inactivo else "🟢 Activo", inline=True)
    embed.add_field(name="Días sin hablar", value=f"**{dias_inactivo}** días", inline=True)
    embed.add_field(name="Mensajes totales", value=str(mensajes), inline=True)
    embed.add_field(name="Última interacción", value=fecha_str, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

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
