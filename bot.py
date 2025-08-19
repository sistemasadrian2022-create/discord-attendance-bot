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
# HORARIOS DE USUARIOS CON EQUIPOS
# =========================
HORARIOS_USUARIOS = {
    # TEAM 1
    "mauricio t1": {"inicio": "05:00", "fin": "13:00", "team": "T1"},      # 8 horas
    "antonio t1": {"inicio": "13:00", "fin": "21:00", "team": "T1"},       # 8 horas
    "hosman t1": {"inicio": "21:00", "fin": "05:00", "team": "T1"},        # 8 horas (nocturno)
    
    # TEAM 2
    "gleidys t2": {"inicio": "06:30", "fin": "13:30", "team": "T2"},       # 7 horas
    "yerika t2": {"inicio": "14:30", "fin": "22:30", "team": "T2"},        # 8 horas
    "luis t2": {"inicio": "22:30", "fin": "06:30", "team": "T2"},          # 8 horas (nocturno)
    
    # TEAM 3
    "mariangela t3": {"inicio": "05:00", "fin": "13:00", "team": "T3"},    # 8 horas
    "stephen t3": {"inicio": "13:00", "fin": "21:00", "team": "T3"},       # 8 horas
    "kyle t3": {"inicio": "21:00", "fin": "05:00", "team": "T3"}           # 8 horas (nocturno)
}

def obtener_nombre_usuario(user: discord.Member) -> str:
    """Obtiene el nombre del usuario (nickname del servidor o display_name)"""
    if hasattr(user, 'nick') and user.nick:
        return user.nick.lower()
    return user.display_name.lower()

def obtener_info_usuario(nombre_usuario: str) -> dict:
    """Obtiene el horario y equipo asignado al usuario"""
    nombre_lower = nombre_usuario.lower().strip()
    
    # Buscar por nombre exacto
    if nombre_lower in HORARIOS_USUARIOS:
        info = HORARIOS_USUARIOS[nombre_lower].copy()
        info["nombre_completo"] = nombre_lower
        return info
    
    # Buscar por contenido parcial
    for usuario, info in HORARIOS_USUARIOS.items():
        if usuario in nombre_lower or any(palabra in nombre_lower for palabra in usuario.split()):
            info_copy = info.copy()
            info_copy["nombre_completo"] = usuario
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
    """Valida si el tiempo de break fue excedido"""
    tiempo_break = (hora_logout_break - hora_break).total_seconds() / 60  # minutos
    
    if tiempo_break > 30:  # Más de 30 minutos
        return False, f"- BREAK EXCEDIDO ({int(tiempo_break)} min)"
    else:
        return True, ""

def validar_login(usuario_nombre: str, hora_actual: datetime) -> tuple:
    """Valida si el login está dentro del horario permitido - LÓGICA SIMPLE"""
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
    
    # EJEMPLO LUIS: 22:30
    # 22:00-22:30 = TEMPRANO ✅ (hasta 30 min antes)
    # 22:30-22:40 = A TIEMPO ✅ (hasta 10 min después) 
    # 22:40+ = TARDE ❌
    
    ventana_temprano_inicio = hora_inicio_mins - 30  # 22:00 para Luis
    ventana_tarde_fin = hora_inicio_mins + 10        # 22:40 para Luis
    
    if hora_actual_mins < ventana_temprano_inicio:
        # Muy temprano - fuera de horario
        print("⚠️ Login MUY TEMPRANO")
        return False, "- MUY TEMPRANO"
    elif ventana_temprano_inicio <= hora_actual_mins <= hora_inicio_mins:
        # Ventana temprana válida
        print("✅ Login temprano (permitido)")
        return True, ""
    elif hora_inicio_mins < hora_actual_mins <= ventana_tarde_fin:
        # Dentro de tolerancia después del inicio
        print("✅ Login a tiempo")
        return True, ""
    else:
        # Después de la tolerancia = tarde
        print("⚠️ Login TARDE")
        return False, "- TARDE"

def validar_logout(usuario_nombre: str, hora_actual: datetime, tiene_login: bool) -> tuple:
    """Valida el logout - LÓGICA SIMPLE"""
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
    
    print(f"🔍 Validando logout: {usuario_nombre}")
    print(f"📅 Hora actual: {hora_actual.strftime('%H:%M')} ({hora_actual_mins} mins)")
    print(f"⏰ Horario fin: {horario['fin']} ({hora_fin_mins} mins)")
    
    # EJEMPLO LUIS: 06:30
    # 06:30 = A TIEMPO ✅
    # 06:40+ = FUERA DE TIEMPO ❌
    
    tolerancia_logout = 10  # 10 minutos después del fin
    ventana_logout_fin = hora_fin_mins + tolerancia_logout  # 06:40 para Luis
    
    if hora_actual_mins <= hora_fin_mins:
        print("✅ Logout a tiempo")
        return True, ""
    elif hora_actual_mins <= ventana_logout_fin:
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
        
        timeout = aiohttp.ClientTimeout(total=10)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
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
                    return False
                    
    except asyncio.TimeoutError:
        print("❌ Timeout enviando a Google Sheets (10 segundos)")
        return False
    except Exception as e:
        print(f"❌ Error enviando a Google Sheets: {e}")
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
# MODAL SELECTOR DE CANTIDAD
# =========================
class LogoutSelectorModal(ui.Modal):
    def __init__(self, validacion_msg: str = ""):
        super().__init__(title="LOGOUT - SELECCIONA CANTIDAD", timeout=300)
        self.validacion_msg = validacion_msg

    cantidad_modelos = ui.TextInput(
        label="¿Cuántos modelos trabajaste?",
        placeholder="Escribe: 1, 2 o 3",
        required=True,
        max_length=1,
        min_length=1
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            cantidad_str = self.cantidad_modelos.value.strip()
            
            if cantidad_str not in ['1', '2', '3']:
                await interaction.response.send_message(
                    "❌ **Error**: Debes escribir 1, 2 o 3",
                    ephemeral=True
                )
                return
            
            cantidad = int(cantidad_str)
            
            # Abrir modal específico según cantidad
            if cantidad == 1:
                modal = LogoutModal1Modelo(self.validacion_msg)
            elif cantidad == 2:
                modal = LogoutModal2Modelos(self.validacion_msg)
            else:
                modal = LogoutModal3Modelos(self.validacion_msg)
            
            await interaction.response.send_modal(modal)
        
        except Exception as e:
            print(f"❌ Error en selector: {e}")
            await interaction.followup.send(
                "❌ Error procesando selección. Intenta nuevamente.",
                ephemeral=True
            )

# =========================
# MODAL PARA 1 MODELO
# =========================
class LogoutModal1Modelo(ui.Modal):
    def __init__(self, validacion_msg: str = ""):
        super().__init__(title="LOGOUT - 1 MODELO", timeout=300)
        self.validacion_msg = validacion_msg

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
            
            # Actualizar mensaje
            await interaction.edit_original_response(
                content="✅ **Logout registrado exitosamente** - Revisa tu mensaje privado para más detalles.",
                embed=None
            )
            
            # Enviar DM
            await self._enviar_dm(interaction, embed, modelos_data, monto_total_bruto, team)
            
            # Eliminar mensaje después de 3 segundos
            await asyncio.sleep(3)
            try:
                await interaction.delete_original_response()
            except:
                pass
            
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
    def __init__(self, validacion_msg: str = ""):
        super().__init__(validacion_msg)
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
# MODAL PARA 3 MODELOS
# =========================
class LogoutModal3Modelos(LogoutModal1Modelo):
    def __init__(self, validacion_msg: str = ""):
        super().__init__(validacion_msg)
        self.title = "LOGOUT - 3 MODELOS"

    # Campos adicionales para modelos 2 y 3
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
    
    modelo_3 = ui.TextInput(
        label="Modelo 3",
        placeholder="Nombre del modelo 3...",
        required=True,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Para 3 modelos, el tercero no tiene monto por limitación de Discord (máximo 5 campos)
        await interaction.response.send_message(
            "❌ **Limitación de Discord**: Solo se pueden registrar hasta 2 modelos con montos.\n"
            "Para 3 modelos, usa el comando manual o registra por separado.",
            ephemeral=True
        )

# =========================
# VISTA CON 4 BOTONES
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
    if GOOGLE_SHEETS_WEBHOOK_URL:
        print(f"🔗 URL: {GOOGLE_SHEETS_WEBHOOK_URL[:50]}...")
    print("="*70)
    
    bot.add_view(PanelAsistenciaPermanente())
    print("🔧 Vista de asistencia agregada con validaciones corregidas")

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
            "⏰ **Tolerancias**: 30 min antes ✅ - 10 min después ⚠️\n"
            "⚠️ Más de 10 min tarde = **'TARDE'**"
        ),
        inline=False
    )
    
    embed.add_field(
        name="⏸️ BREAK - Inicio de pausa/descanso",
        value=(
            "Presionarlo **cada vez que te ausentes** del puesto.\n"
            "✅ **Para pausas de más de 5 minutos**\n"
            "❌ **No usar** para ausencias de 1-2 minutos"
        ),
        inline=False
    )
    
    embed.add_field(
        name="▶️ LOGOUT BREAK - Fin de pausa/vuelta al trabajo",
        value=(
            "Presionarlo **apenas vuelvas** de la pausa.\n"
            "⏰ **Máximo 30 minutos** - Más = BREAK EXCEDIDO\n"
            "Marca que estás **nuevamente disponible y activo**"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🔴 LOGOUT - Salida/Fin de jornada + Reporte de Ventas",
        value=(
            "Presionarlo **al finalizar** tu turno completo.\n"
            "⏰ **Tolerancia**: Hasta 10 min después ✅ - Más = FUERA DE TIEMPO ❌\n"
            "📋 **Incluye reporte obligatorio** de modelos trabajados\n"
            "🔢 **Selector**: 1 o 2 modelos (máximo por Discord)"
        ),
        inline=False
    )
    
    embed.add_field(
        name="📋 REGLAS SIMPLES",
        value=(
            "• **Login**: 30 min antes ✅ - 10 min después ⚠️ - Más = TARDE ❌\n"
            "• **Break**: Máximo 30 minutos - Más = EXCEDIDO ⚠️\n"
            "• **Logout**: Hasta 10 min después ✅ - Más = FUERA DE TIEMPO ❌\n"
            "• **Sin distinción** nocturno/diurno - Solo UN horario\n"
            "• **Activa DMs** para recibir confirmaciones detalladas"
        ),
        inline=False
    )
    
    embed.set_footer(
        text="📧 Confirmaciones por DM | ⏰ Hora Argentina | 📊 Lógica simple",
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
            f"Canal logs: {'✅ Configurado' if LOG_CHANNEL_ID else '❌ No configurado'}"
        ),
        inline=False
    )
    
    embed.add_field(
        name="⏰ Tolerancias Simples",
        value=(
            "**Login**: 30 min antes ✅ - 10 min después ⚠️\n"
            "**Logout**: Hasta 10 min después ✅ - Más = FUERA DE TIEMPO ❌\n"
            "**Break**: Máximo 30 minutos\n"
            "**Sin distinción** de horario nocturno/diurno"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🎮 Funciones Disponibles",
        value=(
            "🟢 **Login** - Validación simple de horarios\n"
            "⏸️ **Break** - Registro de inicio\n"
            "▶️ **Logout Break** - Validación de tiempo\n"
            "🔴 **Logout** - Validación + Reporte ventas"
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
    
    # Organizar por equipos
    equipos = {"T1": [], "T2": [], "T3": []}
    
    for usuario, info in HORARIOS_USUARIOS.items():
        team = info["team"]
        nombre = usuario.replace(f" {team.lower()}", "").title()
        horas = calcular_horas_jornada(info["inicio"], info["fin"])
        equipos[team].append(f"**{info['inicio']} - {info['fin']}** │ {nombre} ({horas}h)")
    
    for team, miembros in equipos.items():
        if miembros:
            embed.add_field(
                name=f"🏆 EQUIPO {team}",
                value="\n".join(miembros),
                inline=False
            )
    
    embed.add_field(
        name="⏰ Tolerancias y Reglas",
        value=(
            "• **Login**: 30 min antes ✅ - 10 min después ⚠️\n"
            "• **Break**: Máximo 30 minutos\n"
            "• **Logout**: Hasta 10 min después ✅\n"
            "• **Lógica simple**: Un horario por persona"
        ),
        inline=False
    )
    
    embed.set_footer(text="Cada equipo registra en su propia hoja de Google Sheets")
    
    await ctx.reply(embed=embed, mention_author=False)

@bot.command(name="test_horario")
async def test_horario_command(ctx: commands.Context, usuario: str = None):
    """Comando para probar validaciones de horario"""
    if not usuario:
        await ctx.reply("Uso: `!test_horario <nombre_usuario>`")
        return
    
    hora_actual = datetime.now(TZ_ARGENTINA)
    
    # Test login
    _, msg_login = validar_login(usuario, hora_actual)
    
    # Test logout
    _, msg_logout = validar_logout(usuario, hora_actual, True)
    
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
    
    # Mostrar horario del usuario
    horario = obtener_horario_usuario(usuario)
    if horario:
        horas = calcular_horas_jornada(horario["inicio"], horario["fin"])
        embed.add_field(
            name="⏰ Horario Asignado",
            value=f"`{horario['inicio']} - {horario['fin']}` ({horas}h)",
            inline=False
        )
    else:
        embed.add_field(
            name="⚠️ Horario",
            value="Usuario no encontrado en la base de datos",
            inline=False
        )
    
    await ctx.reply(embed=embed, mention_author=False)

# =========================
# EJECUCIÓN
# =========================
if __name__ == "__main__":
    print("🚀 Iniciando bot de control de asistencia con validaciones simples...")
    
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
