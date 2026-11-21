"""Firma de apoyo y verificación a completar por el integrante 3."""
import hashlib  # Selecciona SHA-256 para autenticar el contenido.
import hmac  # Usa una comparación segura para las firmas.
import re  # Valida la forma exacta de la cabecera sin aceptar variantes de int().
import time  # Comprueba la ventana temporal.
from fenix.utils.errors import ValidationError  # Rechazo de autenticación público.

PATRON_EPOCH = re.compile(r"[0-9]{1,12}")  # Solo dígitos: int() admite '_', '+' y espacios.
PATRON_FIRMA = re.compile(r"[0-9a-f]{64}")  # Coincide con lo que emite signature().
VENTANA_SEGUNDOS = 300  # Tolerancia hacia pasado y futuro que fija el contrato.


def signature(secret: str, timestamp: int, body: bytes) -> str:
    """Firma exactamente timestamp, punto y cuerpo binario original.

    Args:
        secret: Secreto compartido de al menos 16 caracteres, leído del entorno.
        timestamp: Unix epoch en segundos, igual al enviado en la cabecera.
        body: Bytes exactos enviados por HTTP; no volver a serializar el JSON.
    Returns:
        HMAC-SHA256 hexadecimal de 64 caracteres.
    Raises:
        ValidationError: Si el secreto es demasiado corto.
    """
    if len(secret) < 16:  # Se comprueba también al enviar la prueba.
        raise ValidationError("Configurar WEBHOOK_SECRET de 16 caracteres o más")
    signed = str(timestamp).encode("ascii") + b"." + body  # Prefijo autenticado.
    return hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def verify(secret: str, header: str, body: bytes, now: int | None = None) -> None:
    """Rechaza cabeceras ambiguas y firmas o fechas inválidas.

    Args:
        secret: Secreto compartido del entorno, nunca del payload.
        header: Cabecera exacta t=<epoch>,v1=<hex> sin claves duplicadas.
        body: Bytes del cuerpo antes de llamar a json.loads.
        now: Reloj inyectable para pruebas; None usa time.time.
    Returns:
        None si las comprobaciones son satisfactorias.
    Raises:
        ValidationError: Formato inválido, firma incorrecta o diferencia mayor a 300 s.
    Notes:
        La ventana de tiempo mitiga replay antiguo. No impide repetir un evento
        válido dentro de la ventana; event_id e idempotencia cubren ese caso.
        Todos los rechazos comparten un mensaje genérico: el error no sirve para
        distinguir entre firma equivocada y fecha vencida, ni repite lo recibido.
    """
    campos: dict[str, str] = {}  # Cada clave se admite una sola vez.
    try:  # Un header None también es un formato inválido, no un error del servidor.
        for parte in header.split(","):
            clave, separador, valor = parte.partition("=")
            if not separador or clave in campos:  # Parte sin '=' o clave duplicada.
                raise ValueError("cabecera ambigua")
            campos[clave] = valor
        if set(campos) != {"t", "v1"}:  # Ni claves extra ni ausentes.
            raise ValueError("claves inesperadas")
        if not PATRON_EPOCH.fullmatch(campos["t"]):  # Rechaza '1_700' y ' 1000'.
            raise ValueError("epoch no entero")
        if not PATRON_FIRMA.fullmatch(campos["v1"]):  # 64 hexadecimales en minúscula.
            raise ValueError("firma malformada")
    except (AttributeError, ValueError) as error:  # Se traduce sin repetir el valor.
        raise ValidationError("Firma inválida") from error
    timestamp = int(campos["t"])  # Ya se comprobó que son solo dígitos.
    esperada = signature(secret, timestamp, body)  # Firma los bytes originales.
    if not hmac.compare_digest(esperada, campos["v1"]):  # Comparación de tiempo constante.
        raise ValidationError("Firma inválida")  # Mismo mensaje que un formato roto.
    ahora = int(time.time()) if now is None else now  # Reloj inyectable en pruebas.
    if abs(ahora - timestamp) > VENTANA_SEGUNDOS:  # 300 s inclusive en ambos sentidos.
        raise ValidationError("Firma fuera de la ventana de 300 segundos")
