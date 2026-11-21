"""Contratos de validación a completar por el integrante 1."""
import re  # Comprueba el formato de los identificadores.
from datetime import datetime, timedelta, timezone #Trabaja con fechas conscientes de UTC.
from fenix.utils.errors import ValidationError  # Error público del dominio.


def identifier(value: str) -> str:
    """Valida un identificador corto y devuelve su texto sin modificar.

    Args:
        value: Texto entre 3 y 32 caracteres; letras minúsculas, dígitos o guion.
    Returns:
        El identificador original válido.
    Raises:
        ValidationError: Si el tipo o el patrón no cumplen el contrato.
        NotImplementedError: Mientras el alumno no complete TODO I1-A.
    Examples:
        identifier("grupo-01") devuelve "grupo-01"; "../ajeno" debe fallar.
    """
        # 1. Comprobar que el valor sea texto.
    if not isinstance(value, str):
        raise ValidationError(
            "El identificador debe ser una cadena de texto."
        )

    # 2. Validar el formato completo del identificador.
    if re.fullmatch(r"[a-z0-9-]{3,32}", value) is None:
        raise ValidationError(
            "El identificador debe tener entre 3 y 32 caracteres "
            "y contener solo minúsculas, números o guiones."
        )

    # 3. Devolver el identificador original.
    return value


def ttl_seconds(value: str) -> int:
    """Valida el TTL didáctico empleado en la opción de entornos efímeros.

    Args:
        value: Texto con un entero entre 60 y 600 segundos inclusive.
    Returns:
        El número entero de segundos.
    Raises:
        ValidationError: Si no es entero o está fuera de los límites.
        NotImplementedError: Mientras no se complete TODO I1-B.
    Notes:
        La CLI traduce ValidationError a parser.error para salir con código 2.
    """

    if not isinstance(value, str):
        raise ValidationError(
            "El TTL debe recibirse como una cadena de texto."
        )
    # I1-B: Convertir el texto recibido a un número entero.
    try:
        ttl = int(value)

    # Si el texto no representa un entero válido,
    # capturamos ValueError y lanzamos nuestro error.
    except ValueError as error:
        raise ValidationError(
            "El TTL debe ser un número entero válido."
        ) from error

    # Comprobar que el TTL esté dentro del rango permitido.
    if not 60 <= ttl <= 600:
        raise ValidationError(
            "El TTL debe estar entre 60 y 600 segundos."
        )

    # Devolver el TTL como número entero.
    return ttl



def utc_text(value: str) -> datetime:
    """Interpreta una fecha UTC explícita sin aceptar fechas ingenuas.

    Args:
        value: ISO 8601 terminado en Z o con offset +00:00.
    Returns:
        Fecha con zona UTC; por ejemplo 2026-10-22T18:00:00+00:00.
    Raises:
        ValidationError: Si falta zona, hay offset no nulo o formato inválido.
        NotImplementedError: Mientras no se complete TODO I1-C.
    """

    # I1-C: Comprobar que la fecha recibida sea texto.
    if not isinstance(value, str):
        raise ValidationError(
            "La fecha debe recibirse como una cadena de texto."
        )

    # Reemplazar el sufijo Z por +00:00.
    # Ambos representan la zona horaria UTC.
    texto_fecha = value.replace("Z", "+00:00")

    # Intentar convertir el texto a un objeto datetime.
    try:
        fecha = datetime.fromisoformat(texto_fecha)

    # Capturar fechas que no tengan un formato válido.
    except ValueError as error:
        raise ValidationError(
            "La fecha debe tener un formato ISO 8601 válido."
        ) from error

    # Comprobar que la fecha tenga zona horaria explícita.
    if fecha.tzinfo is None:
        raise ValidationError(
            "La fecha debe incluir explícitamente la zona horaria UTC."
        )

    # Comprobar que el desplazamiento horario sea cero.
    if fecha.utcoffset() != timedelta(0):
        raise ValidationError(
            "La fecha debe estar expresada en UTC."
        )

    # Normalizar la fecha a la zona horaria UTC.
    return fecha.astimezone(timezone.utc)
