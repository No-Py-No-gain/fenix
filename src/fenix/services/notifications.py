"""Adaptador HTTP guiado. Elegir Telegram O Slack, no ambos obligatoriamente."""
import os  # Recupera credenciales fuera del repositorio.
import requests  # Aplica timeout explícito a cada llamada HTTP.
from dotenv import load_dotenv  # Carga la configuración local del grupo si falta.
from requests.exceptions import (ConnectionError as ErrorDeConexion,  # Fallos de red.
                                 RequestException as ErrorDeRequests, Timeout)
from fenix.utils.errors import PermanentError, RetryableError

TOPE_ESPERA = 30  # Tope de espera admitido por el ejercicio para Retry-After.
LIMITE_TEXTO = 1000  # Resumen público máximo que acepta el contrato del aviso.


def _espera_sugerida(respuesta) -> int | None:
    """Lee Retry-After y lo acota al alcance del ejercicio.

    Args:
        respuesta: Respuesta HTTP del proveedor.
    Returns:
        Segundos de espera entre 0 y 30, o None si el proveedor no lo indicó.
    """
    cabeceras = getattr(respuesta, "headers", None) or {}  # Un doble puede no traerlas.
    try:
        segundos = int(cabeceras.get("Retry-After"))  # Puede venir ausente o no numérico.
    except (TypeError, ValueError):
        return None
    return min(max(segundos, 0), TOPE_ESPERA)  # Nunca se espera más de lo permitido.


def _descripcion(respuesta) -> str:
    """Extrae la descripción pública del proveedor para el diagnóstico.

    Args:
        respuesta: Respuesta HTTP del proveedor.
    Returns:
        Sufijo con el texto recortado, o cadena vacía si no hay descripción útil.
    Notes:
        Telegram explica el rechazo en el JSON y esa frase no contiene el token:
        es la única forma de distinguir un chat bloqueado de un bot inválido.
        Sin ella, un 403 sólo dice "rechazado" y obliga a adivinar la configuración.
    """
    try:
        cuerpo = respuesta.json()
    except ValueError:  # Un 4xx sin JSON sigue siendo un rechazo permanente.
        return ""
    texto = cuerpo.get("description") if isinstance(cuerpo, dict) else None
    if not isinstance(texto, str) or not texto:
        return ""
    return " - " + texto[:120]  # Recortado: no se copia la respuesta entera.


def _telegram(texto: str) -> None:
    """Envía el resumen con la Bot API y exige la confirmación ok=true.

    Args:
        texto: Resumen público de hasta 1000 caracteres.
    Returns:
        None solo si el proveedor confirmó el envío.
    Raises:
        RetryableError: Timeout, conexión, 429 o 5xx.
        PermanentError: Configuración inválida o rechazo funcional del proveedor.
    Notes:
        Los errores se relanzan con `from None`: la excepción de requests lleva la
        URL del bot, que contiene el token. Encadenarla filtraría el secreto a
        cualquier log que imprima el árbol de la excepción.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")  # Nunca sale del entorno.
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")  # Chat autorizado del grupo.
    if not token or not chat_id:  # Falla de configuración, no del proveedor.
        raise PermanentError("Configurar TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"  # Endpoint del proveedor.
    try:
        respuesta = requests.post(url, json={"chat_id": chat_id,
                                            "text": texto[:LIMITE_TEXTO]},
                                  timeout=(2, 5))  # Conexión 2 s y lectura 5 s.
    except (Timeout, ErrorDeConexion):  # Lo que puede resolverse solo.
        raise RetryableError("El proveedor de aviso no respondió") from None
    except ErrorDeRequests:  # Cualquier otro fallo de transporte, sin copiar detalles.
        raise RetryableError("Fallo de transporte al enviar el aviso") from None
    estado = respuesta.status_code  # Se decide por estado, no por cuerpo.
    if estado == 429:  # Limitación explícita del proveedor.
        espera = _espera_sugerida(respuesta)
        detalle = f"reintentar_despues={espera}" if espera is not None else "sin_retry_after"
        raise RetryableError(f"El proveedor pidió esperar: HTTP {estado} {detalle}")
    if estado >= 500:  # 5xx tampoco depende del mensaje.
        raise RetryableError(f"El proveedor falló temporalmente: HTTP {estado}")
    if estado >= 400:  # 400/401/403: diagnóstico de configuración, no reintento ciego.
        raise PermanentError(f"Configuración de Telegram rechazada: HTTP {estado}"
                             f"{_descripcion(respuesta)}")
    try:
        cuerpo = respuesta.json()  # Telegram responde JSON también en errores.
    except ValueError:  # Un 200 sin JSON no confirma nada.
        raise RetryableError("Respuesta ilegible del proveedor") from None
    if not isinstance(cuerpo, dict) or cuerpo.get("ok") is not True:
        raise PermanentError("El proveedor no confirmó ok=true")  # HTTP 200 no basta.


def notify(text: str) -> None:
    """Envía un mensaje breve y valida la respuesta del proveedor seleccionado.

    Args:
        text: Resumen público de hasta 1000 caracteres, sin secretos ni raw body.
    Returns:
        None únicamente después de confirmar éxito del proveedor.
    Raises:
        RetryableError: Timeout, conexión, 429 o respuesta 5xx.
        PermanentError: Configuración inválida, 4xx definitivo o rechazo funcional.
    Side Effects:
        Publica en el chat/canal del grupo. Un timeout puede causar duplicados.
    Notes:
        El modo console sirve para desarrollo; no reemplaza la evidencia final real.
        No registrar la excepción requests original: puede contener el token en URL.
        Un aviso fallido no borra el mensaje de SQS: el worker lo conserva y la DLQ
        limita los intentos. Aquí no se repite en un bucle interno.
        La configuración se lee del entorno y, si falta, del archivo local del grupo.
        load_dotenv no sobrescribe lo ya exportado: el operador manda sobre el archivo.
    """
    load_dotenv()  # Se carga acá para no depender de que otro módulo lo haya hecho.
    modo = os.getenv("NOTIFY_PROVIDER", "console").strip().lower()  # Adaptador explícito.
    if modo == "console":  # Ensayo local sin credenciales de terceros.
        print(text[:LIMITE_TEXTO])  # No imprimir eventos completos o URLs privadas.
        return  # El modo debe figurar en el informe de pruebas.
    if modo == "telegram":  # Alternativa elegida por el grupo para la evidencia final.
        _telegram(text)
        return
    raise PermanentError(f"NOTIFY_PROVIDER no soportado: {modo}")  # Sin adivinar.
