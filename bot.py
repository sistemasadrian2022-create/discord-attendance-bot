import os
import json
import asyncio
import aiohttp
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytz
import discord
from discord.ext import commands
from discord import ui, ButtonStyle, Embed

# Cargar variables de entorno
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("⚠️  python-dotenv no instalado. Instala con: pip install python-dotenv")

# =========================
# CONFIGURACIÓN BÁSICA
# =========================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN") or "MTQwMzk1NjMwNDY5OTE5NTQ1Mw.GFGDK0.zf1SnzlJeuvGkZ3rsUlOAv2_RpONgAIY9stMW0"
GOOGLE_SHEETS_WEBHOOK_URL = os.getenv("GOOGLE_SHEETS_WEBHOOK_URL") or "https://script.google.com/macros/s/AKfycbz2sBsVENHhOnFvAW86KSfd_a5Qu0UZeVbcuXA-gGCI1AGn9aEV9w0Prwo0EdfJtxCH/exec"

# Para hosting: obtener PORT del entorno
PORT = int(os.getenv("PORT", 5000))

if not DISCORD_TOKEN:
    raise SystemExit(
        "❌ ERROR: Token no encontrado.\n"
        "Define la variable de entorno DISCORD_TOKEN"
    )

if not GOOGLE_SHEETS_WEBHOOK_URL:
    print("⚠️  GOOGLE_SHEETS_WEBHOOK_URL no configurado. Los eventos no se guardarán en Google Sheets.")

LOG_CHANNEL_ID: int = int(os.getenv("DISCORD_LOG_CHANNEL_ID", "0"))
TZ_ARGENTINA = pytz.timezone("America/Argentina/Buenos_Aires")

# Variable global para trackear breaks
breaks_activos = {}

# =========================
# HORARIOS DE USUARIOS CON EQUIPOS - ACTUALIZADO CON NOMBRES DE COLORES
# =========================
HORARIOS_USUARIOS = {
    # TEAM 1 - BlackTeam
    "mauricio t1": {"inicio": "05:00", "fin": "13:00", "team": "T1"},
    "mauricio blackteam": {"inicio": "05:00", "fin": "13:00", "team": "T1"},
    "antonio t1": {"inicio": "13:00", "fin": "21:00", "team": "T1"},
    "antonio blackteam": {"inicio": "13:00", "fin": "21:00", "team": "T1"},
    "hosman t1": {"inicio": "21:00", "fin": "05:00", "team": "T1"},
    "hosman blackteam": {"inicio": "21:00", "fin": "05:00", "team": "T1"},
    
    # TEAM 2 - RedTeam
    "gleidys t2": {"inicio": "06:30", "fin": "13:30", "team": "T2"},
    "gleidys redteam": {"inicio": "06:30", "fin": "13:30", "team": "T2"},
    "yerika t2": {"inicio": "14:30", "fin": "22:30", "team": "T2"},
    "yerika redteam": {"inicio": "14:30", "fin": "22:30", "team": "T2"},
    "luis t2": {"inicio": "22:30", "fin": "06:30", "team": "T2"},
    "luis redteam": {"inicio": "22:30", "fin": "06:30", "team": "T2"},
    
    # TEAM 3 - BlueTeam
    "mariangela t3": {"inicio": "05:00", "fin": "13:00", "team": "T3"},
    "mariangela blueteam": {"inicio": "05:00", "fin": "13:00", "team": "T3"},
    "stephen t3": {"inicio": "21:00", "fin": "05:00", "team": "T3"},
    "stephen blueteam": {"inicio": "21:00", "fin": "05:00", "team": "T3"},
    "kyle t3": {"inicio": "13:00", "fin": "21:00", "team": "T3"},
    "kyle blueteam": {"inicio": "13:00", "fin": "21:00", "team": "T3"}
}

def obtener_nombre_usuario(user: discord.Member) -> str:
    """Obtiene el nombre del usuario (nickname del servidor o display_name)"""
    if hasattr(user, 'nick') and user.nick:
        return user.nick.lower()
    return user.display_name.lower()

def obtener_info_usuario(nombre_usuario: str) -> dict:
    """Obtiene el horario y equipo asignado al usuario - MEJORADO para nombres de colores"""
    nombre_lower = nombre_usuario.lower().strip()
    
    # Buscar por nombre exacto
    if nombre_lower in HORARIOS_USUARIOS:
        info = HORARIOS_USUARIOS[nombre_lower].copy()
        info["nombre_completo"] = nombre_lower
        return info
    
    # Buscar por contenido parcial (nombre + team/color)
    for usuario_key, info in HORARIOS_USUARIOS.items():
        # Extraer solo el nombre base (sin t1/t2/t3/blackteam/redteam/blueteam)
        nombre_base = usuario_key.split()[0]  # mauricio, antonio, hosman, etc.
        
        # Si el nombre del usuario contiene el nombre base
        if nombre_base in nombre_lower:
            info_copy = info.copy()
            info_copy["nombre_completo"] = usuario_key
            return info_copy
    
    # Buscar por palabras individuales
    for usuario_key, info in HORARIOS_USUARIOS.items():
        if any(palabra in nombre_lower for palabra in usuario_key.split()):
            info_copy = info.copy()
            info_copy["nombre_completo"] = usuario_key
            return info_copy
    
    return None

def obtener_horario_usuario(nombre_usuario: str) -> dict:
    """Obtiene solo el horario asignado al usuario (compatibilidad)"""
    info = obtener_info_usuario(nombre_usuario)
    if info:
        return {"inicio": info["inicio"], "fin": info["fin"]}
    return None

def calcular_horas_jornada(inicio_str: str, fin_str: str) -> float:
    """Calcula las horas de la jornada laboral"""
    def hora_a_minutos(hora_str: str) -> int:
        hora, minuto = map(int, hora_str.split(':'))
        return hora * 60 + minuto
    
    inicio_mins = hora_a_minutos(inicio_str)
    fin_mins = hora_a_minutos(fin_str)
    
    if fin_mins < inicio_mins:  # Turno nocturno
        return (24 * 60 - inicio_mins + fin_mins) / 60
    else:  # Turno diurno
        return (fin_mins - inicio_mins) / 60

def validar_break_tiempo(hora_break: datetime, hora_logout_break: datetime) -> tuple:
    """Valida si el tiempo de break fue excedido - TOLERANCIA 40 MIN TOTAL"""
    tiempo_break = (hora_logout_break - hora_break).total_seconds() / 60  # minutos
    
    if tiempo_break > 40:  # Más de 40 minutos (30 + 10 tolerancia)
        return False, f"- BREAK EXCEDIDO ({int(tiempo_break)} min)"
    else:
        return True, ""

def validar_login(usuario_nombre: str, hora_actual: datetime) -> tuple:
    """Valida si el login está dentro del horario permitido - TOLERANCIA 10 MIN"""
    horario = obtener_horario_usuario(usuario_nombre)
    if not horario:
        return True, ""  # Si no tiene horario asignado, permitir
    
    # Obtener hora actual en minutos desde medianoche
    hora_actual_mins = hora_actual.hour * 60 + hora_actual.minute
    
    # Convertir horarios a minutos
    def hora_a_minutos(hora_str: str) -> int:
        hora, minuto = map(int, hora_str.split(':'))
        return hora * 60 + minuto
    
    hora_inicio_mins = hora_a_minutos(horario["inicio"])
    
    print(f"🔍 Validando login: {usuario_nombre}")
    print(f"📅 Hora actual: {hora_actual.strftime('%H:%M')} ({hora_actual_mins} mins)")
    print(f"⏰ Horario inicio: {horario['inicio']} ({hora_inicio_mins} mins)")
    
    # TOLERANCIA: SOLO 10 MINUTOS (antes y después)
    # 10 min antes = TEMPRANO ✅
    # Hora exacta hasta 10 min después = A TIEMPO ✅  
    # Más de 10 min después = TARDE ❌
    
    # Calcular diferencia considerando turnos nocturnos
    diferencia_mins = 0
    if hora_inicio_mins > 12 * 60:  # Turno nocturno (inicia después del mediodía)
        # EJEMPLO LUIS: 22:30 (1350 mins)
        if hora_actual_mins >= hora_inicio_mins:
            # Mismo día: 22:30, 22:45, 23:00, etc.
            diferencia_mins = hora_actual_mins - hora_inicio_mins
        elif hora_actual_mins < 12 * 60:  # Próximo día (00:00-11:59)
            # Día siguiente: 01:36 = ya pasaron (24*60 - 1350) + 96 = 186 minutos = 3h 6min
            diferencia_mins = (24 * 60 - hora_inicio_mins) + hora_actual_mins
        else:
            # Entre mediodía y hora de inicio = FUERA DE HORARIO
            print("⚠️ Login FUERA DE HORARIO LABORAL")
            return False, "- FUERA DE HORARIO"
    else:
        # Turno diurno normal
        diferencia_mins = hora_actual_mins - hora_inicio_mins
    
    print(f"📊 Diferencia: {diferencia_mins} minutos ({diferencia_mins/60:.1f} horas)")
    
    # Evaluar según diferencia - TOLERANCIA 10 MIN
    if diferencia_mins < -10:
        print("⚠️ Login MUY TEMPRANO")
        return False, "- MUY TEMPRANO"
    elif -10 <= diferencia_mins <= 0:
        print("✅ Login temprano (permitido)")
        return True, ""
    elif 0 < diferencia_mins <= 10:
        print("✅ Login a tiempo")
        return True, ""
    else:
        horas_tarde = diferencia_mins / 60
        print(f"⚠️ Login TARDE ({horas_tarde:.1f} horas)")
        return False, f"- TARDE ({horas_tarde:.1f}h)"

def validar_logout(usuario_nombre: str, hora_actual: datetime, tiene_login: bool) -> tuple:
    """Valida el logout - TOLERANCIA 10 MIN"""
    horario = obtener_horario_usuario(usuario_nombre)
    if not horario:
        return True, ""
    
    if not tiene_login:
        return True, "- NO MARCO INICIO"
    
    # Obtener hora actual en minutos
    hora_actual_mins = hora_actual.hour * 60 + hora_actual.minute
    
    def hora_a_minutos(hora_str: str) -> int:
        hora, minuto = map(int, hora_str.split(':'))
        return hora * 60 + minuto
    
    hora_fin_mins = hora_a_minutos(horario["fin"])
    hora_inicio_mins = hora_a_minutos(horario["inicio"])
    
    print(f"🔍 Validando logout: {usuario_nombre}")
    print(f"📅 Hora actual: {hora_actual.strftime('%H:%M')} ({hora_actual_mins} mins)")
    print(f"⏰ Horario fin: {horario['fin']} ({hora_fin_mins} mins)")
    
    # TOLERANCIA: SOLO 10 MINUTOS después del horario de salida
    tolerancia_logout = 10
    
    # Calcular diferencia considerando turnos nocturnos
    diferencia_mins = 0
    if hora_inicio_mins > hora_fin_mins:  # Turno nocturno
        # EJEMPLO LUIS: 22:30 - 06:30
        if hora_actual_mins <= 12 * 60:  # Parte matutina (00:00-11:59)
            # Logout en la mañana del día siguiente
            diferencia_mins = hora_actual_mins - hora_fin_mins
        else:
            # Logout el mismo día (muy temprano)
            print("⚠️ Logout MUY TEMPRANO (mismo día)")
            return False, "- MUY TEMPRANO"
    else:
        # Turno diurno normal
        diferencia_mins = hora_actual_mins - hora_fin_mins
    
    print(f"📊 Diferencia: {diferencia_mins} minutos")
    
    # Evaluar según diferencia - TOLERANCIA 10 MIN
    if diferencia_mins <= 0:
        print("✅ Logout a tiempo")
        return True, ""
    elif diferencia_mins <= tolerancia_logout:
        print("✅ Logout dentro de tolerancia")
        return True, ""
    else:
        print("⚠️ Logout FUERA DE TIEMPO")
        return False, "- FUERA DE TIEMPO"

# =========================
# FUNCIÓN PARA GOOGLE SHEETS
# =========================
async def actualizar_registro_usuario(
    user: discord.abc.User,
    action: str,
    guild: Optional[discord.Guild],
    channel: Optional[discord.abc.GuildChannel],
    modelos_data: Optional[list] = None,
    validacion_msg: Optional[str] = None
):
    """Actualiza o crea el registro del usuario en Google Sheets"""
    if not GOOGLE_SHEETS_WEBHOOK_URL:
        print("⚠️  No se puede enviar a Google Sheets: URL no configurada")
        return False
    
    try:
        # Obtener timestamp en zona horaria Argentina
        timestamp_argentina = datetime.now(TZ_ARGENTINA)
        
        # Obtener nombre del usuario (nickname o display_name)
        usuario_nombre = obtener_nombre_usuario(user) if hasattr(user, 'nick') else str(user)
        
        # Obtener información del equipo
        info_usuario = obtener_info_usuario(usuario_nombre)
        team = info_usuario["team"] if info_usuario else "SIN_EQUIPO"
        
        data = {
            "timestamp": timestamp_argentina.isoformat(),
            "usuario": usuario_nombre,
            "action": action,
            "team": team,
            "validacion": validacion_msg or ""
        }
        
        # Agregar datos de modelos si es logout
        if action == "logout" and modelos_data:
            data.update({
                "modelos_data": modelos_data,
                "cantidad_modelos": len(modelos_data)
            })
        
        print(f"🔍 Actualizando registro: {usuario_nombre} - {action} - Team: {team}")
        if modelos_data:
            print(f"📊 Modelos: {len(modelos_data)} modelos registrados")
        
        # Aumentar timeout a 30 segundos
        timeout = aiohttp.ClientTimeout(total=30)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Intentar hasta 2 veces
            for intento in range(2):
                try:
                    async with session.post(
                        GOOGLE_SHEETS_WEBHOOK_URL,
                        json=data,
                        headers={'Content-Type': 'application/json'}
                    ) as response:
                        
                        if response.status == 200:
                            result = await response.json()
                            if result.get("result") == "success":
                                print(f"✅ Registro actualizado: {usuario_nombre} - {action} - {team}")
                                return True
                            else:
                                print(f"❌ Error en Google Sheets: {result.get('error', 'Unknown error')}")
                                return False
                        else:
                            print(f"❌ HTTP Error {response.status} enviando a Google Sheets")
                            if intento == 0:  # Si es el primer intento, reintentar
                                print("🔄 Reintentando en 2 segundos...")
                                await asyncio.sleep(2)
                                continue
                            return False
                            
                except asyncio.TimeoutError:
                    print(f"❌ Timeout enviando a Google Sheets (intento {intento + 1}/2)")
                    if intento == 0:  # Si es el primer intento, reintentar
                        print("🔄 Reintentando en 2 segundos...")
                        await asyncio.sleep(2)
                        continue
                    return False
                except Exception as e:
                    print(f"❌ Error enviando a Google Sheets (intento {intento + 1}/2): {e}")
                    if intento == 0:  # Si es el primer intento, reintentar
                        print("🔄 Reintentando en 2 segundos...")
                        await asyncio.sleep(2)
                        continue
                    return False
                
                # Si llegamos aquí, fue exitoso
                break
            
            return False  # Si llegamos aquí, ambos intentos fallaron
                    
    except Exception as e:
        print(f"❌ Error general enviando a Google Sheets: {e}")
        return False

def build_embed(user: discord.abc.User, event: str, where: Optional[discord.abc.GuildChannel], validacion_msg: str = "") -> Embed:
    """Construye un embed para mostrar el evento registrado"""
    ts = datetime.now(TZ_ARGENTINA).strftime("%d/%m/%Y %H:%M:%S")
    
    # Colores según validación
    if validacion_msg:
        if "TARDE" in validacion_msg:
            color = discord.Color.orange()
        elif "TEMPRANO" in validacion_msg:
            color = discord.Color.yellow()
        elif "NO MARCO" in validacion_msg:
            color = discord.Color.purple()
        elif "FUERA DE TIEMPO" in validacion_msg:
            color = discord.Color.red()
        elif "EXCEDIDO" in validacion_msg:
            color = discord.Color.yellow()
        else:
            color = discord.Color.red()
    else:
        event_config = {
            "Login": {"color": discord.Color.green()},
            "Break": {"color": discord.Color.blue()},
            "Logout Break": {"color": discord.Color.purple()}, 
            "Logout": {"color": discord.Color.red()}
        }
        config = event_config.get(event, {"color": discord.Color.default()})
        color = config["color"]
    
    embed = Embed(
        title=f"📝 {event} Registrado {validacion_msg}",
        description=f"**Horario Argentina** 🇦🇷",
        color=color,
        timestamp=datetime.now(timezone.utc)
    )
    
    # Obtener nombre del usuario
    usuario_display = obtener_nombre_usuario(user) if hasattr(user, 'nick') else str(user)
    
    embed.add_field(
        name="👤 Usuario", 
        value=f"{user.mention} (`{usuario_display}`)", 
        inline=False
    )
    
    embed.add_field(
        name="⏰ Fecha/Hora (Argentina)", 
        value=f"`{ts}`", 
        inline=False
    )
    
    if validacion_msg:
        embed.add_field(
            name="⚠️ Observación",
            value=f"`{validacion_msg}`",
            inline=False
        )
    
    if where and isinstance(where, discord.abc.GuildChannel):
        embed.add_field(
            name="📍 Ubicación", 
            value=f"**{where.guild.name}** - #{where.name}", 
            inline=False
        )
    
    embed.set_footer(text="✅ Registro actualizado en Google Sheets")
    return embed

# =========================
# MODAL SELECTOR DE CANTIDAD (PASO 1)
# =========================
class LogoutSelectorModal(ui.Modal):
    def __init__(self, validacion_msg: str = ""):
        super().__init__(title="LOGOUT - SELECCIONA CANTIDAD", timeout=300)
        self.validacion_msg = validacion_msg

    cantidad_modelos = ui.TextInput(
        label="¿Cuántos modelos trabajaste?",
        placeholder="Escribe: 1 o 2",
        required=True,
        max_length=1,
        min_length=1
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            cantidad_str = self.cantidad_modelos.value.strip()
            
            if cantidad_str not in ['1', '2']:
                await interaction.response.send_message(
                    "❌ **Error**: Debes escribir 1 o 2 (máximo 2 modelos por limitación Discord)",
                    ephemeral=True
                )
                return
            
            cantidad = int(cantidad_str)
            
            # Crear mensaje con botón "Rellenar" - NO EFÍMERO para poder eliminarlo
            embed = Embed(
                title=f"📝 Logout con {cantidad} modelo{'s' if cantidad > 1 else ''}",
                description=f"**Presiona el botón para completar los datos de {cantidad} modelo{'s' if cantidad > 1 else ''}:**",
                color=discord.Color.blue()
            )
            
            # Vista con botón para rellenar
            view = LogoutRellenarView(cantidad, self.validacion_msg)
            
            # CAMBIO: Quitar ephemeral=True para poder eliminar el mensaje después
            await interaction.response.send_message(
                embed=embed,
                view=view
                # Sin ephemeral=True - ahora es visible para todos pero se eliminará
            )
        
        except Exception as e:
            print(f"❌ Error en selector: {e}")
            await interaction.response.send_message(
                "❌ Error procesando selección. Inténtalo nuevamente.",
                ephemeral=True
            )

# =========================
# VISTA CON BOTÓN "RELLENAR" (MEJORADA)
# =========================
class LogoutRellenarView(ui.View):
    def __init__(self, cantidad_modelos: int, validacion_msg: str = ""):
        super().__init__(timeout=300)
        self.cantidad_modelos = cantidad_modelos
        self.validacion_msg = validacion_msg
        self.mensaje_rellenar = None  # Guardar referencia del mensaje

    @ui.button(
        label=f"📝 Rellenar Datos",
        style=ButtonStyle.primary,
        emoji="📝"
    )
    async def btn_rellenar(self, interaction: discord.Interaction, button: ui.Button):
        try:
            # Guardar referencia del mensaje que contiene este botón
            self.mensaje_rellenar = interaction.message
            
            # Abrir modal según cantidad
            if self.cantidad_modelos == 1:
                modal = LogoutModal1Modelo(self.validacion_msg, self.mensaje_rellenar)
            else:
                modal = LogoutModal2Modelos(self.validacion_msg, self.mensaje_rellenar)
            
            await interaction.response.send_modal(modal)
            
        except Exception as e:
            print(f"❌ Error abriendo modal rellenar: {e}")
            await interaction.response.send_message(
                "❌ Error abriendo formulario. Inténtalo nuevamente.",
                ephemeral=True
            )

# =========================
# PANEL DE ASISTENCIA PERMANENTE
# =========================
class PanelAsistenciaPermanente(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _handle_simple_event(self, interaction: discord.Interaction, action: str, emoji: str, event_name: str):
        """Maneja eventos simples con validaciones de horario"""
        user = interaction.user
        channel = interaction.channel
        
        try:
            await interaction.response.send_message(
                f"{emoji} **{event_name}** procesando...",
                ephemeral=True,
                delete_after=3
            )
            
            # Obtener nombre del usuario
            usuario_nombre = obtener_nombre_usuario(user) if hasattr(user, 'nick') else str(user)
            hora_actual = datetime.now(TZ_ARGENTINA)
            validacion_msg = ""
            
            # Validar según el tipo de evento
            if action == "login":
                _, validacion_msg = validar_login(usuario_nombre, hora_actual)
            elif action == "break":
                # Registrar inicio de break
                breaks_activos[user.id] = hora_actual
                print(f"📝 Break iniciado para {usuario_nombre} a las {hora_actual.strftime('%H:%M')}")
            elif action == "logout_break":
                # Validar tiempo de break si existe
                if user.id in breaks_activos:
                    hora_break = breaks_activos[user.id]
                    _, validacion_msg = validar_break_tiempo(hora_break, hora_actual)
                    del breaks_activos[user.id]  # Limpiar break
                    print(f"📝 Break finalizado para {usuario_nombre}. {validacion_msg}")
            
            # Actualizar registro
            success = await actualizar_registro_usuario(
                user, action, interaction.guild, channel, validacion_msg=validacion_msg
            )
            
            # Crear embed
            embed = build_embed(user, event_name, channel, validacion_msg)
            
            # Preparar mensaje
            if success:
                dm_message = f"{emoji} **{event_name}** registrado exitosamente."
            else:
                dm_message = f"{emoji} **{event_name}** registrado localmente. ⚠️ Error con Google Sheets."
            
            if validacion_msg:
                dm_message += f" {validacion_msg}"
            
            # Enviar confirmación por DM
            try:
                await user.send(content=dm_message, embed=embed)
            except discord.Forbidden:
                await interaction.followup.send(
                    f"{emoji} {user.mention} **{event_name}** registrado.\n"
                    f"💡 Activa los DMs para confirmaciones privadas.",
                    ephemeral=True,
                    delete_after=8
                )
            
            # Log al canal
            if LOG_CHANNEL_ID and success:
                try:
                    log_channel = interaction.client.get_channel(LOG_CHANNEL_ID)
                    if log_channel and log_channel != channel:
                        await log_channel.send(embed=embed)
                except Exception as e:
                    print(f"❌ Error enviando a canal de logs: {e}")
                    
        except Exception as e:
            print(f"❌ Error en botón {event_name}: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"❌ Error procesando **{event_name}**. Inténtalo nuevamente.",
                    ephemeral=True,
                    delete_after=5
                )

    @ui.button(
        label="🟢 Login", 
        style=ButtonStyle.success, 
        custom_id="attendance_login",
        row=0
    )
    async def btn_login(self, interaction: discord.Interaction, button: ui.Button):
        await self._handle_simple_event(interaction, "login", "🟢", "Login")

    @ui.button(
        label="⏸️ Break", 
        style=ButtonStyle.primary, 
        custom_id="attendance_break",
        row=0
    )
    async def btn_break(self, interaction: discord.Interaction, button: ui.Button):
        await self._handle_simple_event(interaction, "break", "⏸️", "Break")

    @ui.button(
        label="▶️ Logout Break", 
        style=ButtonStyle.secondary, 
        custom_id="attendance_logout_break",
        row=0
    )
    async def btn_logout_break(self, interaction: discord.Interaction, button: ui.Button):
        await self._handle_simple_event(interaction, "logout_break", "▶️", "Logout Break")

    @ui.button(
        label="🔴 Logout", 
        style=ButtonStyle.danger, 
        custom_id="attendance_logout",
        row=0
    )
    async def btn_logout(self, interaction: discord.Interaction, button: ui.Button):
        """Logout con modal selector"""
        try:
            # Validar logout
            usuario_nombre = obtener_nombre_usuario(interaction.user) if hasattr(interaction.user, 'nick') else str(interaction.user)
            hora_actual = datetime.now(TZ_ARGENTINA)
            
            _, validacion_msg = validar_logout(usuario_nombre, hora_actual, True)
            
            # Abrir modal selector
            modal = LogoutSelectorModal(validacion_msg)
            await interaction.response.send_modal(modal)
            
        except Exception as e:
            print(f"❌ Error en botón logout: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Error abriendo formulario de logout. Inténtalo nuevamente.",
                    ephemeral=True,
                    delete_after=5
                )

# =========================
# MODAL PARA 1 MODELO
# =========================
class LogoutModal1Modelo(ui.Modal):
    def __init__(self, validacion_msg: str = "", mensaje_rellenar=None):
        super().__init__(title="LOGOUT - 1 MODELO", timeout=300)
        self.validacion_msg = validacion_msg
        self.mensaje_rellenar = mensaje_rellenar  # Referencia del mensaje a eliminar

    modelo_1 = ui.TextInput(
        label="Modelo",
        placeholder="Nombre del modelo...",
        required=True,
        max_length=100
    )
    
    monto_1 = ui.TextInput(
        label="Monto Bruto",
        placeholder="$",
        required=True,
        max_length=20
    )

    async def on_submit(self, interaction: discord.Interaction):
        await self._procesar_logout(interaction, [
            {
                "numero": 1,
                "nombre": self.modelo_1.value.strip(),
                "monto_str": self.monto_1.value
            }
        ])

    async def _procesar_logout(self, interaction: discord.Interaction, modelos_raw: list):
        try:
            await interaction.response.send_message(
                "🔴 **Procesando logout y reporte de ventas...** ⏳",
                ephemeral=True
            )
            
            # Procesar y validar modelos
            modelos_data = []
            monto_total_bruto = 0
            
            for modelo_raw in modelos_raw:
                if not modelo_raw["nombre"]:
                    await interaction.followup.send(
                        f"❌ **Error**: El nombre del Modelo {modelo_raw['numero']} es obligatorio",
                        ephemeral=True
                    )
                    return
                
                try:
                    monto_bruto = float(modelo_raw["monto_str"].replace("$", "").replace(",", "").strip())
                except ValueError:
                    await interaction.followup.send(
                        f"❌ **Error**: El monto del Modelo {modelo_raw['numero']} debe ser un número válido",
                        ephemeral=True
                    )
                    return
                
                monto_neto = monto_bruto * 0.80
                monto_total_bruto += monto_bruto
                
                modelos_data.append({
                    "numero": modelo_raw["numero"],
                    "nombre": modelo_raw["nombre"],
                    "monto_bruto": monto_bruto,
                    "monto_neto": monto_neto
                })
            
            # Obtener información del usuario
            usuario_apodo = obtener_nombre_usuario(interaction.user) if hasattr(interaction.user, 'nick') else str(interaction.user)
            info_usuario = obtener_info_usuario(usuario_apodo)
            team = info_usuario["team"] if info_usuario else "SIN_EQUIPO"
            
            # Actualizar registro
            success = await actualizar_registro_usuario(
                interaction.user,
                "logout",
                interaction.guild,
                interaction.channel,
                modelos_data=modelos_data,
                validacion_msg=self.validacion_msg
            )
            
            # Crear embed
            embed = self._crear_embed_confirmacion(
                interaction, modelos_data, monto_total_bruto, team
            )
            
            # Actualizar mensaje del modal (este)
            await interaction.edit_original_response(
                content="✅ **Logout registrado exitosamente** - Revisa tu mensaje privado para más detalles.",
                embed=None,
                view=None
            )
            
            # NUEVO: Eliminar mensaje del botón "Rellenar" usando referencia directa
            if self.mensaje_rellenar:
                try:
                    # Pequeño delay para asegurar que el mensaje existe
                    await asyncio.sleep(0.5)
                    await self.mensaje_rellenar.delete()
                    print(f"🗑️ Eliminado mensaje del botón Rellenar (referencia directa)")
                except discord.NotFound:
                    print(f"⚠️ Mensaje del botón ya fue eliminado")
                except Exception as e:
                    print(f"⚠️ No se pudo eliminar mensaje del botón: {e}")
            
            # Enviar DM
            await self._enviar_dm(interaction, embed, modelos_data, monto_total_bruto, team)
            
            # Eliminar mensaje del modal después de 3 segundos
            await asyncio.sleep(3)
            try:
                await interaction.delete_original_response()
                print(f"🗑️ Eliminado mensaje del modal")
            except Exception as e:
                print(f"⚠️ No se pudo eliminar mensaje del modal: {e}")
            
            # Log al canal
            if LOG_CHANNEL_ID:
                try:
                    log_channel = interaction.client.get_channel(LOG_CHANNEL_ID)
                    if log_channel and log_channel != interaction.channel:
                        await log_channel.send(embed=embed)
                except Exception as e:
                    print(f"❌ Error enviando a canal de logs: {e}")
        
        except Exception as e:
            print(f"❌ Error procesando logout: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Error procesando logout. Inténtalo nuevamente.",
                    ephemeral=True
                )

    def _crear_embed_confirmacion(self, interaction, modelos_data, monto_total_bruto, team):
        cantidad = len(modelos_data)
        monto_total_neto = monto_total_bruto * 0.80
        
        embed = Embed(
            title=f"🔴 Logout y Ventas Registrados {self.validacion_msg}",
            description=f"**Jornada finalizada - Equipo {team}** ({cantidad} modelo{'s' if cantidad > 1 else ''})",
            color=discord.Color.orange() if self.validacion_msg else discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
        
        usuario_apodo = obtener_nombre_usuario(interaction.user) if hasattr(interaction.user, 'nick') else str(interaction.user)
        
        embed.add_field(name="👤 Usuario", value=interaction.user.mention, inline=True)
        embed.add_field(name="🏆 Equipo", value=f"`{team}`", inline=True)
        embed.add_field(name="📱 Cuenta/Usuario", value=f"`{usuario_apodo}`", inline=True)
        
        # Agregar información de cada modelo
        for modelo in modelos_data:
            embed.add_field(
                name=f"👩‍💼 Modelo {modelo['numero']}",
                value=f"`{modelo['nombre']}`\n💵 Bruto: `${modelo['monto_bruto']:,.2f}`\n💰 Neto: `${modelo['monto_neto']:,.2f}`",
                inline=True
            )
        
        # Totales
        embed.add_field(
            name="📊 TOTALES",
            value=f"💵 **Total Bruto**: `${monto_total_bruto:,.2f}`\n💰 **Total Neto**: `${monto_total_neto:,.2f}`",
            inline=False
        )
        
        embed.add_field(name="⏰ Fecha/Hora (Argentina)", value=f"`{datetime.now(TZ_ARGENTINA).strftime('%d/%m/%Y %H:%M:%S')}`", inline=False)
        
        if self.validacion_msg:
            embed.add_field(name="⚠️ Observación", value=f"`{self.validacion_msg}`", inline=False)
        
        embed.set_footer(text=f"✅ Logout registrado en Hoja {team}")
        return embed

    async def _enviar_dm(self, interaction, embed, modelos_data, monto_total_bruto, team):
        cantidad = len(modelos_data)
        try:
            dm_message = f"🔴 **Logout registrado exitosamente - Equipo {team}** ({cantidad} modelo{'s' if cantidad > 1 else ''})"
            await interaction.user.send(content=dm_message, embed=embed)
        except discord.Forbidden:
            resumen = f"🔴 **Logout registrado exitosamente**\n🏆 **Equipo**: {team}\n"
            for modelo in modelos_data:
                resumen += f"👩‍💼 **Modelo {modelo['numero']}**: {modelo['nombre']} (${modelo['monto_bruto']:,.2f})\n"
            resumen += f"💵 **Total**: ${monto_total_bruto:,.2f}\n💡 **Tip**: Activa los mensajes directos para recibir reportes completos."
            
            await interaction.followup.send(
                content=resumen,
                ephemeral=True
            )

# =========================
# MODAL PARA 2 MODELOS
# =========================
class LogoutModal2Modelos(LogoutModal1Modelo):
    def __init__(self, validacion_msg: str = "", mensaje_rellenar=None):
        super().__init__(validacion_msg, mensaje_rellenar)
        self.title = "LOGOUT - 2 MODELOS"

    # Campos adicionales para modelo 2
    modelo_2 = ui.TextInput(
        label="Modelo 2",
        placeholder="Nombre del modelo 2...",
        required=True,
        max_length=100
    )
    
    monto_2 = ui.TextInput(
        label="Monto Bruto 2",
        placeholder="$",
        required=True,
        max_length=20
    )

    async def on_submit(self, interaction: discord.Interaction):
        await self._procesar_logout(interaction, [
            {
                "numero": 1,
                "nombre": self.modelo_1.value.strip(),
                "monto_str": self.monto_1.value
            },
            {
                "numero": 2,
                "nombre": self.modelo_2.value.strip(),
                "monto_str": self.monto_2.value
            }
        ])

# =========================
# BOT SETUP
# =========================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
    case_insensitive=True
)

@bot.event
async def on_ready():
    print("="*70)
    print(f"✅ Bot de Asistencia conectado!")
    print(f"📝 Usuario: {bot.user}")
    print(f"🏠 Servidores: {len(bot.guilds)}")
    print(f"📊 Google Sheets: {'✅ Configurado' if GOOGLE_SHEETS_WEBHOOK_URL else '❌ No configurado'}")
    print(f"🇦🇷 Zona horaria: Argentina (Buenos Aires)")
    print(f"🎨 Soporte para nombres de colores: BlackTeam, RedTeam, BlueTeam")
    if GOOGLE_SHEETS_WEBHOOK_URL:
        print(f"🔗 URL: {GOOGLE_SHEETS_WEBHOOK_URL[:50]}...")
    print("="*70)
    
    bot.add_view(PanelAsistenciaPermanente())
    print("🔧 Vista de asistencia agregada - Soporte para jornadas laborales nocturnas")

@bot.command(name="setup_attendance", aliases=["setup"])
@commands.has_permissions(administrator=True)
async def setup_attendance(ctx: commands.Context):
    """Configura el panel de asistencia con horarios"""
    
    embed = Embed(
        title="🕐 SISTEMA DE CONTROL DE ASISTENCIA",
        description="**Registra tus eventos de trabajo con un solo clic:**",
        color=discord.Color.gold()
    )
    
    embed.add_field(
        name="🟢 LOGIN - Entrada/Inicio de jornada",
        value=(
            "Presionarlo **apenas empieces tu turno** de trabajo.\n"
            "Debe ser lo **primero que hagas** al conectarte.\n"
            "Si lo haces tarde, el sistema te registrará como **'Tarde'**."
        ),
        inline=False
    )
    
    embed.add_field(
        name="⏸️ BREAK - Inicio de pausa/descanso",
        value=(
            "Presionarlo **cada vez que te ausentes** del puesto (baño, comer, personal).\n"
            "**No usarlo** si vas a estar solo 1-2 minutos.\n"
            "**Solo para pausas de más de 5 minutos**."
        ),
        inline=False
    )
    
    embed.add_field(
        name="▶️ LOGOUT BREAK - Fin de pausa/vuelta al trabajo",
        value=(
            "Presionarlo **apenas vuelvas** de la pausa.\n"
            "Esto marca que estás **nuevamente disponible y activo**."
        ),
        inline=False
    )
    
    embed.add_field(
        name="🔴 LOGOUT - Salida/Fin de jornada + Reporte de Ventas",
        value=(
            "Presionarlo **al finalizar** tu turno.\n"
            "**Primero seleccionas** cuántos modelos trabajaste (1 o 2)\n"
            "**Luego presionas** el botón 'Rellenar Datos'\n"
            "**Finalmente completas** los datos de cada modelo\n"
            "**OBLIGATORIO** completar el reporte de ventas."
        ),
        inline=False
    )
    
    embed.add_field(
        name="📋 REGLAS IMPORTANTES",
        value=(
            "• Los botones se deben usar en **orden lógico**: `Login → Break → Logout Break → Logout`\n"
            "• **No marcar** un Break sin luego marcar un Logout Break\n"
            "• **El Logout incluye** el reporte obligatorio de ventas\n"
            "• **Flujo Logout**: Selector → Botón Rellenar → Formulario → Completar\n"
            "• **Máximo 2 modelos** por limitación de Discord\n"
            "• **Jornadas nocturnas** se registran en la misma fila\n"
            "• Usar siempre desde el **mismo dispositivo** y cuenta de Discord asignada\n"
            "• **Activa los mensajes directos** para recibir confirmaciones"
        ),
        inline=False
    )
    
    embed.set_footer(
        text="📧 Las confirmaciones llegan por DM | ⏰ Hora de Argentina | 🌙 Soporte jornadas nocturnas",
        icon_url=ctx.guild.icon.url if ctx.guild.icon else None
    )
    
    view = PanelAsistenciaPermanente()
    await ctx.send(embed=embed, view=view)
    
    try:
        await ctx.message.delete()
    except:
        pass

@bot.command(name="status")
async def status_command(ctx: commands.Context):
    """Muestra el estado del sistema"""
    embed = Embed(
        title="📊 Estado del Sistema de Asistencia",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="🔧 Configuración",
        value=(
            f"Bot: ✅ Conectado\n"
            f"Google Sheets: {'✅ Configurado' if GOOGLE_SHEETS_WEBHOOK_URL else '❌ No configurado'}\n"
            f"Zona horaria: `{TZ_ARGENTINA}`\n"
            f"Canal logs: {'✅ Configurado' if LOG_CHANNEL_ID else '❌ No configurado'}\n"
            f"Jornadas nocturnas: ✅ Soportadas"
        ),
        inline=False
    )
    
    embed.add_field(
        name="⏰ Tolerancias Finales",
        value=(
            "**Login**: 10 min antes ✅ - 10 min después ⚠️\n"
            "**Logout**: Hasta 10 min después ✅\n"
            "**Break**: Máximo 40 minutos (30 + 10 tolerancia)\n"
            "**Cálculo real** de tiempo transcurrido\n"
            "**Turnos nocturnos**: Misma fila de jornada"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🎨 Nombres Soportados",
        value=(
            "**T1 BlackTeam**: Mauricio, Antonio, Hosman\n"
            "**T2 RedTeam**: Gleidys, Yerika, Luis\n"
            "**T3 BlueTeam**: Mariangela, Stephen, Kyle\n"
            "✅ Acepta tanto `Luis T2` como `Luis RedTeam`"
        ),
        inline=False
    )
    
    await ctx.reply(embed=embed, mention_author=False)

@bot.command(name="horarios")
async def horarios_command(ctx: commands.Context):
    """Muestra los horarios asignados por equipos"""
    embed = Embed(
        title="📅 HORARIOS ASIGNADOS POR EQUIPOS 🇦🇷",
        color=discord.Color.blue()
    )
    
    # Organizar por equipos con nombres de colores
    equipos_info = {
        "T1": {"nombre": "BlackTeam", "color": "⚫", "miembros": []},
        "T2": {"nombre": "RedTeam", "color": "🔴", "miembros": []},
        "T3": {"nombre": "BlueTeam", "color": "🔵", "miembros": []}
    }
    
    # Procesar solo entradas únicas (evitar duplicados T1/BlackTeam)
    usuarios_procesados = set()
    
    for usuario, info in HORARIOS_USUARIOS.items():
        # Extraer nombre base
        nombre_base = usuario.split()[0].title()
        team = info["team"]
        
        # Solo procesar cada usuario una vez por equipo
        if (nombre_base, team) not in usuarios_procesados:
            horas = calcular_horas_jornada(info["inicio"], info["fin"])
            turno_tipo = "🌙 Nocturno" if info["inicio"] > info["fin"] else "☀️ Diurno"
            
            equipos_info[team]["miembros"].append(
                f"**{info['inicio']} - {info['fin']}** │ {nombre_base} ({horas}h) {turno_tipo}"
            )
            usuarios_procesados.add((nombre_base, team))
    
    for team, info_equipo in equipos_info.items():
        if info_equipo["miembros"]:
            embed.add_field(
                name=f"{info_equipo['color']} EQUIPO {team} - {info_equipo['nombre']}",
                value="\n".join(info_equipo["miembros"]),
                inline=False
            )
    
    embed.add_field(
        name="⏰ Tolerancias y Reglas",
        value=(
            "• **Login**: 10 min antes ✅ - 10 min después ⚠️\n"
            "• **Break**: Máximo 40 minutos (30 + 10 tolerancia)\n"
            "• **Logout**: Hasta 10 min después ✅\n"
            "• **Jornadas nocturnas**: Eventos en misma fila\n"
            "• **Calcula tiempo real** transcurrido para turnos nocturnos"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🎨 Formato de Nombres Aceptados",
        value=(
            "• `Mauricio T1` o `Mauricio BlackTeam`\n"
            "• `Luis T2` o `Luis RedTeam`\n"
            "• `Stephen T3` o `Stephen BlueTeam`\n"
            "• Sistema detecta automáticamente el equipo"
        ),
        inline=False
    )
    
    embed.set_footer(text="Cada equipo registra en su propia hoja de Google Sheets")
    
    await ctx.reply(embed=embed, mention_author=False)

@bot.command(name="test_sheets")
@commands.has_permissions(administrator=True)
async def test_sheets_command(ctx: commands.Context):
    """Prueba la conexión con Google Sheets"""
    if not GOOGLE_SHEETS_WEBHOOK_URL:
        await ctx.reply("❌ **Google Sheets URL no configurada**")
        return
    
    await ctx.reply("🔄 **Probando conexión con Google Sheets...**")
    
    try:
        # Datos de prueba
        test_data = {
            "timestamp": datetime.now(TZ_ARGENTINA).isoformat(),
            "usuario": "test_user",
            "action": "test",
            "team": "TEST",
            "validacion": "- PRUEBA CONEXIÓN JORNADAS"
        }
        
        timeout = aiohttp.ClientTimeout(total=30)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                GOOGLE_SHEETS_WEBHOOK_URL,
                json=test_data,
                headers={'Content-Type': 'application/json'}
            ) as response:
                
                if response.status == 200:
                    result = await response.json()
                    if result.get("result") == "success":
                        await ctx.reply("✅ **Google Sheets funcionando correctamente - Soporte jornadas laborales activo**")
                    else:
                        await ctx.reply(f"❌ **Error en Google Sheets**: {result.get('error', 'Unknown error')}")
                else:
                    await ctx.reply(f"❌ **HTTP Error {response.status}** conectando a Google Sheets")
                    
    except asyncio.TimeoutError:
        await ctx.reply("❌ **Timeout conectando a Google Sheets** (30 segundos)")
    except Exception as e:
        await ctx.reply(f"❌ **Error de conexión**: {str(e)}")

@bot.command(name="test_horario")
async def test_horario_command(ctx: commands.Context, *, usuario: str = None):
    """Comando para probar validaciones de horario - Mejorado para nombres de colores"""
    if not usuario:
        await ctx.reply("Uso: `!test_horario <nombre_usuario>`\nEjemplos: `Luis T2`, `Luis RedTeam`, `Mauricio BlackTeam`")
        return
    
    hora_actual = datetime.now(TZ_ARGENTINA)
    
    # Test login
    _, msg_login = validar_login(usuario, hora_actual)
    
    # Test logout
    _, msg_logout = validar_logout(usuario, hora_actual, True)
    
    # Obtener info del usuario
    info_usuario = obtener_info_usuario(usuario)
    
    embed = Embed(
        title=f"🧪 Test de Validaciones - {usuario}",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="🕐 Hora Actual (Argentina)",
        value=f"`{hora_actual.strftime('%H:%M:%S')}`",
        inline=False
    )
    
    embed.add_field(
        name="🟢 Validación LOGIN",
        value=f"{'✅ Válido' if not msg_login else '❌ Inválido'} `{msg_login}`",
        inline=True
    )
    
    embed.add_field(
        name="🔴 Validación LOGOUT", 
        value=f"{'✅ Válido' if not msg_logout else '❌ Inválido'} `{msg_logout}`",
        inline=True
    )
    
    if info_usuario:
        horario = {"inicio": info_usuario["inicio"], "fin": info_usuario["fin"]}
        horas = calcular_horas_jornada(horario["inicio"], horario["fin"])
        turno_tipo = "🌙 Nocturno" if horario["inicio"] > horario["fin"] else "☀️ Diurno"
        
        embed.add_field(
            name="⏰ Horario Asignado",
            value=f"`{horario['inicio']} - {horario['fin']}` ({horas}h) {turno_tipo}",
            inline=False
        )
        
        embed.add_field(
            name="🏆 Equipo Detectado",
            value=f"`{info_usuario['team']}` - Usuario reconocido: `{info_usuario['nombre_completo']}`",
            inline=False
        )
    else:
        embed.add_field(
            name="⚠️ Horario",
            value="Usuario no encontrado en la base de datos.\nFormatos válidos: `Luis T2`, `Luis RedTeam`, `Mauricio BlackTeam`",
            inline=False
        )
    
    await ctx.reply(embed=embed, mention_author=False)

# =========================
# EJECUCIÓN
# =========================
if __name__ == "__main__":
    print("🚀 Iniciando bot de control de asistencia - VERSIÓN CON JORNADAS LABORALES")
    
    try:
        import pytz
        import discord
        import aiohttp
        print("✅ Dependencias verificadas")
    except ImportError as e:
        print(f"❌ Falta instalar dependencia: {e}")
        print("Ejecuta: pip install discord.py pytz python-dotenv aiohttp")
        exit(1)
    
    try:
        bot.run(DISCORD_TOKEN)
    except discord.LoginFailure:
        print("❌ ERROR: Token inválido.")
    except Exception as e:
        print(f"❌ Error inesperado: {e}")

