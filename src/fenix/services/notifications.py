"""Adaptador HTTP guiado. Elegir Telegram O Slack, no ambos obligatoriamente."""
import os  # Recupera credenciales fuera del repositorio.
import requests  # Aplica timeout explícito a cada llamada HTTP.
from dotenv import load_dotenv  # Carga la configuración local del grupo si falta.
from requests.exceptions import (ConnectionError as ConnectionFailure,  # Fallos de red.
                                 RequestException as TransportFailure, Timeout)
from fenix.utils.errors import PermanentError, RetryableError

MAX_RETRY_WAIT_SECONDS = 30  # Tope de espera admitido por el ejercicio para Retry-After.
MAX_TEXT_LENGTH = 1000  # Resumen público máximo que acepta el contrato del aviso.


def _suggested_wait(response) -> int | None:
    """Lee Retry-After y lo acota al alcance del ejercicio.

    Args:
        response: Respuesta HTTP del proveedor.
    Returns:
        Segundos de espera entre 0 y 30, o None si el proveedor no lo indicó.
    """
    headers = getattr(response, "headers", None) or {}  # Un doble puede no traerlas.
    try:
        seconds = int(headers.get("Retry-After"))  # Puede venir ausente o no numérico.
    except (TypeError, ValueError):
        return None
    return min(max(seconds, 0), MAX_RETRY_WAIT_SECONDS)  # Nunca se espera más de lo permitido.


def _provider_description(response) -> str:
    """Extrae la descripción pública del proveedor para el diagnóstico.

    Args:
        response: Respuesta HTTP del proveedor.
    Returns:
        Sufijo con el texto recortado, o cadena vacía si no hay descripción útil.
    Notes:
        Telegram explica el rechazo en el JSON y esa frase no contiene el token:
        es la única forma de distinguir un chat bloqueado de un bot inválido.
        Sin ella, un 403 sólo dice "rechazado" y obliga a adivinar la configuración.
    """
    try:
        payload = response.json()
    except ValueError:  # Un 4xx sin JSON sigue siendo un rechazo permanente.
        return ""
    text = payload.get("description") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text:
        return ""
    return " - " + text[:120]  # Recortado: no se copia la respuesta entera.


def _send_telegram(text: str) -> None:
    """Envía el resumen con la Bot API y exige la confirmación ok=true.

    Args:
        text: Resumen público de hasta 1000 caracteres.
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
        response = requests.post(url, json={"chat_id": chat_id,
                                            "text": text[:MAX_TEXT_LENGTH]},
                                 timeout=(2, 5))  # Conexión 2 s y lectura 5 s.
    except (Timeout, ConnectionFailure):  # Lo que puede resolverse solo.
        raise RetryableError("El proveedor de aviso no respondió") from None
    except TransportFailure:  # Cualquier otro fallo de transporte, sin copiar detalles.
        raise RetryableError("Fallo de transporte al enviar el aviso") from None
    status = response.status_code  # Se decide por estado, no por cuerpo.
    if status == 429:  # Limitación explícita del proveedor.
        wait = _suggested_wait(response)
        detail = f"reintentar_despues={wait}" if wait is not None else "sin_retry_after"
        raise RetryableError(f"El proveedor pidió esperar: HTTP {status} {detail}")
    if status >= 500:  # 5xx tampoco depende del mensaje.
        raise RetryableError(f"El proveedor falló temporalmente: HTTP {status}")
    if status >= 400:  # 400/401/403: diagnóstico de configuración, no reintento ciego.
        raise PermanentError(f"Configuración de Telegram rechazada: HTTP {status}"
                             f"{_provider_description(response)}")
    try:
        payload = response.json()  # Telegram responde JSON también en errores.
    except ValueError:  # Un 200 sin JSON no confirma nada.
        raise RetryableError("Respuesta ilegible del proveedor") from None
    if not isinstance(payload, dict) or payload.get("ok") is not True:
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
    provider = os.getenv("NOTIFY_PROVIDER", "console").strip().lower()  # Adaptador explícito.
    if provider == "console":  # Ensayo local sin credenciales de terceros.
        print(text[:MAX_TEXT_LENGTH])  # No imprimir eventos completos o URLs privadas.
        return  # El modo debe figurar en el informe de pruebas.
    if provider == "telegram":  # Alternativa elegida por el grupo para la evidencia final.
        _send_telegram(text)
        return
    raise PermanentError(f"NOTIFY_PROVIDER no soportado: {provider}")  # Sin adivinar.
