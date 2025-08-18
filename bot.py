import os
import json
import asyncio
import aiohttp
from datetime import datetime, timedelta
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
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GOOGLE_SHEETS_WEBHOOK_URL = os.getenv("https://script.google.com/macros/s/AKfycbxbPPWC26Gs2bunPVdB_hbFf7RQYhXJPD4n1KVVorrXvgLFRLinzkhCiTISJsP7qmte/exec")

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

# =========================
# HORARIOS DE USUARIOS CON EQUIPOS
# =========================
HORARIOS_USUARIOS = {
    # TEAM 1
    "mauricio t1": {"inicio": "05:00", "fin": "13:00", "team": "T1"},
    "antonio t1": {"inicio": "13:00", "fin": "21:00", "team": "T1"},
    "hosman t1": {"inicio": "21:00", "fin": "05:00", "team": "T1"},  # Nocturno
    
    # TEAM 2
    "gleidys t2": {"inicio": "06:30", "fin": "13:30", "team": "T2"},
    "yerika t2": {"inicio": "14:30", "fin": "22:30", "team": "T2"},
    "luis t2": {"inicio": "22:30", "fin": "06:30", "team": "T2"},  # Nocturno
    
    # TEAM 3
    "mariangela t3": {"inicio": "05:00", "fin": "13:00", "team": "T3"},
    "stephen t3": {"inicio": "13:00", "fin": "21:00", "team": "T3"},
    "kyle t3": {"inicio": "21:00", "fin": "05:00", "team": "T3"}  # Nocturno
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

def parse_hora(hora_str: str, fecha_base: datetime) -> datetime:
    """Convierte string HH:MM a datetime en zona horaria Argentina"""
    hora, minuto = map(int, hora_str.split(':'))
    fecha = fecha_base.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    return TZ_ARGENTINA.localize(fecha)

def validar_login(usuario_nombre: str, hora_actual: datetime) -> tuple:
    """Valida si el login está dentro del horario permitido"""
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
    hora_fin_mins = hora_a_minutos(horario["fin"])
    
    print(f"🔍 Validando login: {usuario_nombre}")
    print(f"📅 Hora actual: {hora_actual.strftime('%H:%M')} ({hora_actual_mins} mins)")
    print(f"⏰ Horario: {horario['inicio']} - {horario['fin']} ({hora_inicio_mins} - {hora_fin_mins} mins)")
    
    # Tolerancia de 10 minutos DESPUÉS del horario de entrada
    tolerancia = 10
    
    # Verificar si es turno nocturno (fin < inicio)
    if hora_fin_mins < hora_inicio_mins:
        print("🌙 Turno nocturno detectado")
        # Luis: 22:30 - 06:30
        # Llegó a las 22:15 (antes de las 22:30) = A TIEMPO o TEMPRANO
        # Solo marcar TARDE si llega después de 22:40 (22:30 + 10 min tolerancia)
        
        hora_limite_tarde = hora_inicio_mins + tolerancia  # 22:40
        
        if hora_actual_mins >= hora_inicio_mins:
            # Está en la parte nocturna (22:30-23:59)
            if hora_actual_mins <= hora_limite_tarde:
                print("✅ Login a tiempo (parte nocturna)")
                return True, ""
            else:
                print("⚠️ Login tarde (parte nocturna)")
                return False, "- TARDE"
        elif hora_actual_mins >= (hora_inicio_mins - 60):  # Permitir hasta 1 hora antes
            # Llegó temprano (21:30-22:29) = PERMITIDO
            print("✅ Login temprano (permitido)")
            return True, ""
        else:
            # Para turnos nocturnos, también validar la parte matutina (00:00-06:40)
            hora_limite_fin = hora_fin_mins + tolerancia  # 06:40
            if hora_actual_mins <= hora_limite_fin:
                print("✅ Login a tiempo (parte matutina)")
                return True, ""
            else:
                print("⚠️ Login tarde (parte matutina)")
                return False, "- TARDE"
    else:
        print("☀️ Turno diurno detectado")
        # Turno diurno normal
        hora_limite_tarde = hora_inicio_mins + tolerancia
        
        if hora_actual_mins <= hora_limite_tarde:
            print("✅ Login a tiempo")
            return True, ""
        else:
            print("⚠️ Login tarde")
            return False, "- TARDE"
            
def validar_logout(usuario_nombre: str, hora_actual: datetime, tiene_login: bool) -> tuple:
    """Valida el logout considerando horarios y jornada completa"""
    horario = obtener_horario_usuario(usuario_nombre)
    if not horario:
        return True, ""
    
    fecha_hoy = hora_actual.date()
    hora_fin = parse_hora(horario["fin"], datetime.combine(fecha_hoy, datetime.min.time()))
    
    # Manejar turnos nocturnos
    if horario["fin"] <= horario["inicio"]:
        if hora_actual.hour < 12:  # Parte matutina del turno nocturno
            hora_fin = parse_hora(horario["fin"], datetime.combine(fecha_hoy, datetime.min.time()))
        else:  # Parte nocturna - fin al día siguiente
            hora_fin = parse_hora(horario["fin"], datetime.combine(fecha_hoy + timedelta(days=1), datetime.min.time()))
    
    # Tolerancia de 10 minutos después
    tolerancia = timedelta(minutes=10)
    hora_limite = hora_fin + tolerancia
    
    # Si no completó su jornada y se va tarde
    if not tiene_login:
        return True, "- NO MARCO INICIO"
    
    if hora_actual <= hora_limite:
        return True, ""
    else:
        # Verificar si trabajó al menos 8 horas
        return False, "- TARDE"

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
            "usuario": usuario_nombre,  # Usar nickname del servidor
            "action": action,
            "team": team,  # NUEVO: Equipo del usuario
            "validacion": validacion_msg or ""
        }
        
        # Agregar datos de modelos si es logout
        if action == "logout" and modelos_data:
            data.update({
                "modelos_data": modelos_data,  # Lista con datos de múltiples modelos
                "cantidad_modelos": len(modelos_data)
            })
        
        print(f"🔍 Actualizando registro: {usuario_nombre} - {action} - Team: {team}")
        if modelos_data:
            print(f"📊 Modelos: {len(modelos_data)} modelos registrados")
        print(f"📤 Datos: {data}")
        
        timeout = aiohttp.ClientTimeout(total=10)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                GOOGLE_SHEETS_WEBHOOK_URL,
                json=data,
                headers={'Content-Type': 'application/json'}
            ) as response:
                
                if response.status == 200:
                    result = await response.json()
                    print(f"📥 Respuesta Google Sheets: {result}")
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
    
    # Colores y emojis según el evento y validación
    if validacion_msg:
        if "TARDE" in validacion_msg:
            color = discord.Color.orange()
        elif "EXCEDIDO" in validacion_msg:
            color = discord.Color.yellow()
        elif "NO MARCO" in validacion_msg:
            color = discord.Color.purple()
        else:
            color = discord.Color.red()
    else:
        event_config = {
            "Login": {"color": discord.Color.green(), "emoji": "🟢"},
            "Break": {"color": discord.Color.blue(), "emoji": "⏸️"},
            "Logout Break": {"color": discord.Color.purple(), "emoji": "▶️"}, 
            "Logout": {"color": discord.Color.red(), "emoji": "🔴"}
        }
        config = event_config.get(event, {"color": discord.Color.default(), "emoji": "📝"})
        color = config["color"]
    
    embed = Embed(
        title=f"📝 {event} Registrado {validacion_msg}",
        description=f"**Horario Argentina** 🇦🇷",
        color=color,
        timestamp=datetime.utcnow()
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

    # SOLO SELECTOR DE CANTIDAD
    cantidad_modelos = ui.TextInput(
        label="¿Cuántos modelos trabajaste?",
        placeholder="Escribe: 1, 2 o 3",
        required=True,
        max_length=1,
        min_length=1
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Validar cantidad
            cantidad_str = self.cantidad_modelos.value.strip()
            
            if cantidad_str not in ['1', '2', '3']:
                await interaction.response.send_message(
                    "❌ **Error**: Debes escribir 1, 2 o 3",
                    ephemeral=True
                )
                return
            
            cantidad = int(cantidad_str)
            
            # Cerrar modal actual y abrir el modal dinámico correspondiente
            await interaction.response.send_message(
                f"✅ **{cantidad} modelo{'s' if cantidad > 1 else ''}** - Abriendo formulario...",
                ephemeral=True,
                delete_after=1
            )
            
            # Esperar un momento y abrir modal dinámico
            await asyncio.sleep(0.5)
            
            # Crear vista con botón para abrir modal dinámico
            view = AbrirModalDinamicoView(cantidad, self.validacion_msg)
            
            await interaction.followup.send(
                f"📝 **Formulario para {cantidad} modelo{'s' if cantidad > 1 else ''}:**",
                view=view,
                ephemeral=True
            )
        
        except Exception as e:
            print(f"❌ Error en selector: {e}")
            await interaction.followup.send(
                "❌ Error procesando selección. Intenta nuevamente.",
                ephemeral=True
            )

# =========================
# VISTA PARA ABRIR MODAL DINÁMICO
# =========================
class AbrirModalDinamicoView(ui.View):
    def __init__(self, cantidad: int, validacion_msg: str = ""):
        super().__init__(timeout=60)
        self.cantidad = cantidad
        self.validacion_msg = validacion_msg
        
        # Actualizar el label del botón con la cantidad
        self.children[0].label = f"📝 Completar {self.cantidad} Modelo{'s' if self.cantidad > 1 else ''}"

    @ui.button(
        label=f"📝 Completar Modelos",  # Se actualizará dinámicamente
        style=ButtonStyle.success,
        emoji="📝"
    )
    async def abrir_formulario(self, interaction: discord.Interaction, button: ui.Button):
        # Crear modal específico según cantidad
        if self.cantidad == 1:
            modal = LogoutModal1Modelo(self.validacion_msg)
        elif self.cantidad == 2:
            modal = LogoutModal2Modelos(self.validacion_msg)
        elif self.cantidad == 3:
            modal = LogoutModal3Modelos(self.validacion_msg)
        else:
            await interaction.response.send_message(
                "❌ Error: Cantidad inválida",
                ephemeral=True
            )
            return
        
        await interaction.response.send_modal(modal)

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
                # Validar nombre
                if not modelo_raw["nombre"]:
                    await interaction.followup.send(
                        f"❌ **Error**: El nombre del Modelo {modelo_raw['numero']} es obligatorio",
                        ephemeral=True
                    )
                    return
                
                # Validar y convertir monto
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
            embed = await self._crear_embed_confirmacion(
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

    async def _crear_embed_confirmacion(self, interaction, modelos_data, monto_total_bruto, team):
        cantidad = len(modelos_data)
        monto_total_neto = monto_total_bruto * 0.80
        
        embed = Embed(
            title=f"🔴 Logout y Ventas Registrados {self.validacion_msg}",
            description=f"**Jornada finalizada - Equipo {team}** ({cantidad} modelo{'s' if cantidad > 1 else ''})",
            color=discord.Color.orange() if self.validacion_msg else discord.Color.red(),
            timestamp=datetime.utcnow()
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
class LogoutModal2Modelos(ui.Modal):
    def __init__(self, validacion_msg: str = ""):
        super().__init__(title="LOGOUT - 2 MODELOS", timeout=300)
        self.validacion_msg = validacion_msg

    # Campos del modelo 1
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

    # Campos del modelo 2
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
                # Validar nombre
                if not modelo_raw["nombre"]:
                    await interaction.followup.send(
                        f"❌ **Error**: El nombre del Modelo {modelo_raw['numero']} es obligatorio",
                        ephemeral=True
                    )
                    return
                
                # Validar y convertir monto
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
            embed = await self._crear_embed_confirmacion(
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

    async def _crear_embed_confirmacion(self, interaction, modelos_data, monto_total_bruto, team):
        cantidad = len(modelos_data)
        monto_total_neto = monto_total_bruto * 0.80
        
        embed = Embed(
            title=f"🔴 Logout y Ventas Registrados {self.validacion_msg}",
            description=f"**Jornada finalizada - Equipo {team}** ({cantidad} modelo{'s' if cantidad > 1 else ''})",
            color=discord.Color.orange() if self.validacion_msg else discord.Color.red(),
            timestamp=datetime.utcnow()
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
# MODAL PARA 3 MODELOS
# =========================
class LogoutModal3Modelos(LogoutModal1Modelo):
    def __init__(self, validacion_msg: str = ""):
        # Llamar al constructor de ui.Modal directamente
        ui.Modal.__init__(self, title="LOGOUT - 3 MODELOS", timeout=300)
        self.validacion_msg = validacion_msg
        
        # Redefinir campos del modelo 1
        self.modelo_1 = ui.TextInput(
            label="Modelo",
            placeholder="Nombre del modelo...",
            required=True,
            max_length=100
        )
        
        self.monto_1 = ui.TextInput(
            label="Monto Bruto",
            placeholder="$",
            required=True,
            max_length=20
        )

    # Campos para modelos 2 y 3
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
            },
            {
                "numero": 3,
                "nombre": self.modelo_3.value.strip(),
                "monto_str": "0"  # Discord solo permite 5 campos, así que modelo 3 no tiene monto por limitación
            }
        ])

# Eliminar la referencia a LogoutVentasModalDinamico ya que no se usa
# =========================
# MODAL PARA 2 MODELOS
# =========================
    def __init__(self, cantidad_modelos: int, validacion_msg: str = ""):
        super().__init__(title=f"LOGOUT - REPORTE DE {cantidad_modelos} MODELO{'S' if cantidad_modelos > 1 else ''}", timeout=300)
        self.cantidad_modelos = cantidad_modelos
        self.validacion_msg = validacion_msg
        
        # Crear campos dinámicamente según la cantidad
        self.campos_modelos = []
        self.campos_montos = []
        
        for i in range(cantidad_modelos):
            numero = i + 1
            
            # Campo para nombre del modelo
            campo_modelo = ui.TextInput(
                label=f"Modelo {numero}",
                placeholder=f"Nombre del modelo {numero}...",
                required=True,
                max_length=100
            )
            
            # Campo para monto del modelo
            campo_monto = ui.TextInput(
                label=f"Monto Bruto Modelo {numero}:",
                placeholder="$",
                required=True,
                max_length=20
            )
            
            self.campos_modelos.append(campo_modelo)
            self.campos_montos.append(campo_monto)
            
            # Agregar los campos al modal (máximo 5 campos por modal)
            self.add_item(campo_modelo)
            self.add_item(campo_monto)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Responder INMEDIATAMENTE solo con mensaje efímero
            await interaction.response.send_message(
                f"🔴 **Procesando logout y reporte de {self.cantidad_modelos} modelo{'s' if self.cantidad_modelos > 1 else ''}...** ⏳",
                ephemeral=True
            )
            
            # Procesar datos de cada modelo
            modelos_data = []
            monto_total_bruto = 0
            
            for i in range(self.cantidad_modelos):
                nombre_modelo = self.campos_modelos[i].value
                monto_str = self.campos_montos[i].value.replace("$", "").replace(",", "").strip()
                
                try:
                    monto_bruto = float(monto_str)
                except ValueError:
                    await interaction.followup.send(
                        f"❌ **Error**: El monto del modelo {i+1} debe ser un número válido.",
                        ephemeral=True
                    )
                    return
                
                monto_neto = monto_bruto * 0.80  # 80% del bruto
                monto_total_bruto += monto_bruto
                
                modelos_data.append({
                    "numero": i + 1,
                    "nombre": nombre_modelo,
                    "monto_bruto": monto_bruto,
                    "monto_neto": monto_neto
                })
            
            # Calcular totales
            monto_total_neto = monto_total_bruto * 0.80
            
            # Obtener información del usuario
            usuario_apodo = obtener_nombre_usuario(interaction.user) if hasattr(interaction.user, 'nick') else str(interaction.user)
            info_usuario = obtener_info_usuario(usuario_apodo)
            team = info_usuario["team"] if info_usuario else "SIN_EQUIPO"
            
            # Actualizar registro con validación
            success = await actualizar_registro_usuario(
                interaction.user,
                "logout",
                interaction.guild,
                interaction.channel,
                modelos_data=modelos_data,
                validacion_msg=self.validacion_msg
            )
            
            # Crear embed de confirmación SOLO PARA DM
            embed = Embed(
                title=f"🔴 Logout y Ventas Registrados {self.validacion_msg}",
                description=f"**Jornada finalizada - Equipo {team}** ({self.cantidad_modelos} modelo{'s' if self.cantidad_modelos > 1 else ''})",
                color=discord.Color.orange() if self.validacion_msg else discord.Color.red(),
                timestamp=datetime.utcnow()
            )
            
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
            
            # Agregar totales
            embed.add_field(
                name="📊 TOTALES",
                value=f"💵 **Total Bruto**: `${monto_total_bruto:,.2f}`\n💰 **Total Neto**: `${monto_total_neto:,.2f}`",
                inline=False
            )
            
            embed.add_field(name="⏰ Fecha/Hora (Argentina)", value=f"`{datetime.now(TZ_ARGENTINA).strftime('%d/%m/%Y %H:%M:%S')}`", inline=False)
            
            if self.validacion_msg:
                embed.add_field(name="⚠️ Observación", value=f"`{self.validacion_msg}`", inline=False)
            
            if success:
                embed.set_footer(text=f"✅ Logout registrado en Hoja {team}")
            else:
                embed.set_footer(text="⚠️ Error guardando en Google Sheets")
            
            # ACTUALIZAR EL MENSAJE EFÍMERO CON CONFIRMACIÓN SIMPLE
            await interaction.edit_original_response(
                content="✅ **Logout registrado exitosamente** - Revisa tu mensaje privado para más detalles.",
                embed=None
            )
            
            # ENVIAR EMBED COMPLETO SOLO POR DM
            try:
                dm_message = f"🔴 **Logout registrado exitosamente - Equipo {team}** ({self.cantidad_modelos} modelo{'s' if self.cantidad_modelos > 1 else ''})"
                await interaction.user.send(content=dm_message, embed=embed)
            except discord.Forbidden:
                # Si no puede enviar DM, mostrar mensaje efímero con los datos
                resumen = f"🔴 **Logout registrado exitosamente**\n🏆 **Equipo**: {team}\n"
                for modelo in modelos_data:
                    resumen += f"👩‍💼 **Modelo {modelo['numero']}**: {modelo['nombre']} (${modelo['monto_bruto']:,.2f})\n"
                resumen += f"💵 **Total**: ${monto_total_bruto:,.2f}\n💡 **Tip**: Activa los mensajes directos para recibir reportes completos."
                
                await interaction.followup.send(
                    content=resumen,
                    ephemeral=True
                )
            
            # ELIMINAR EL MENSAJE DESPUÉS DE 3 SEGUNDOS
            await asyncio.sleep(3)
            try:
                await interaction.delete_original_response()
            except:
                pass
            
            # SOLO LOG AL CANAL ADMINISTRATIVO
            if LOG_CHANNEL_ID:
                try:
                    log_channel = interaction.client.get_channel(LOG_CHANNEL_ID)
                    if log_channel and log_channel != interaction.channel:
                        await log_channel.send(embed=embed)
                except Exception as e:
                    print(f"❌ Error enviando a canal de logs: {e}")
        
        except Exception as e:
            print(f"❌ Error en modal de logout: {e}")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        "❌ **Error procesando logout**. El logout puede haberse registrado. Verifica en Google Sheets.",
                        ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        "❌ **Error procesando logout**. Inténtalo nuevamente.",
                        ephemeral=True
                    )
            except:
                print(f"❌ No se pudo notificar error al usuario: {e}")

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
                es_valido, validacion_msg = validar_login(usuario_nombre, hora_actual)
            elif action == "break":
                # El break no tiene validación especial al iniciarlo
                pass
            elif action == "logout_break":
                # Aquí se validaría el tiempo de break, pero necesitaríamos el timestamp del break
                # Por ahora solo registramos
                pass
            
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
        """Logout con modal selector que abre modal específico"""
        try:
            # Validar logout
            usuario_nombre = obtener_nombre_usuario(interaction.user) if hasattr(interaction.user, 'nick') else str(interaction.user)
            hora_actual = datetime.now(TZ_ARGENTINA)
            
            es_valido, validacion_msg = validar_logout(usuario_nombre, hora_actual, True)
            
            # Abrir modal selector (Paso 1)
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
    print("🔧 Vista de asistencia agregada con selector dinámico de modelos")

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
            "⚠️ Si lo haces tarde, el sistema te registrará como **'Tarde'**."
        ),
        inline=False
    )
    
    embed.add_field(
        name="⏸️ BREAK - Inicio de pausa/descanso",
        value=(
            "Presionarlo **cada vez que te ausentes** del puesto (baño, comer, personal).\n"
            "❌ **No usarlo** si vas a estar solo 1-2 minutos.\n"
            "✅ **Solo para pausas de más de 5 minutos**."
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
            "📋 **Primero seleccionas** cuántos modelos trabajaste (1, 2 o 3)\n"
            "📝 **Luego completas** los datos de cada modelo\n"
            "⚠️ **OBLIGATORIO** completar el reporte de ventas."
        ),
        inline=False
    )
    
    embed.add_field(
        name="📋 REGLAS IMPORTANTES",
        value=(
            "• Los botones se deben usar en **orden lógico**: `Login → Break → Logout Break → Logout`\n"
            "• **No marcar** un Break sin luego marcar un Logout Break\n"
            "• **El Logout incluye** el reporte obligatorio de ventas\n"
            "• **Selector dinámico**: Elige 1, 2 o 3 modelos según trabajaste\n"
            "• Usar siempre desde el **mismo dispositivo** y cuenta de Discord asignada\n"
            "• **Activa los mensajes directos** para recibir confirmaciones"
        ),
        inline=False
    )
    
    embed.set_footer(
        text="📧 Las confirmaciones llegan por DM | ⏰ Hora de Argentina | 📊 Una fila por usuario",
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
        name="🎮 Botones Disponibles",
        value=(
            "🟢 **Login** - Entrada\n"
            "⏸️ **Break** - Inicio pausa\n"
            "▶️ **Logout Break** - Fin pausa\n"
            "🔴 **Logout** - Salida + Selector de modelos"
        ),
        inline=False
    )
    
    embed.add_field(
        name="📋 Sistema Dinámico de Modelos",
        value=(
            "• **Selector**: Elige 1, 2 o 3 modelos\n"
            "• **Modal dinámico**: Campos según cantidad seleccionada\n"
            "• **Hojas por equipo** (T1, T2, T3)\n"
            "• **Cuenta automática** desde apodo Discord\n"
            "• **Totales automáticos** de ventas"
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
        equipos[team].append(f"**{info['inicio']} - {info['fin']}** │ {nombre}")
    
    for team, miembros in equipos.items():
        if miembros:
            embed.add_field(
                name=f"🏆 EQUIPO {team}",
                value="\n".join(miembros),
                inline=False
            )
    
    embed.add_field(
        name="📋 Tolerancias",
        value="• Login/Logout: 10 minutos\n• Break: 30 minutos máximo",
        inline=False
    )
    
    embed.set_footer(text="Cada equipo tiene su propia hoja de registro")
    
    await ctx.reply(embed=embed, mention_author=False)

# =========================
# EJECUCIÓN
# =========================
if __name__ == "__main__":
    print("🚀 Iniciando bot de control de asistencia con modelos dinámicos...")
    
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



