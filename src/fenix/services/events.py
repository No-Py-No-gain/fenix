
"""Contrato de eventos de autorreparación; integrante 1."""

from fenix.utils.errors import ValidationError
from fenix.utils.validation import identifier, utc_text


def validate(event: dict, inventory: dict) -> dict:
    """Valida el contrato de eventos y sus recursos autorizados.

    Args:
        event: Diccionario con los seis campos del evento.
        inventory: Diccionario de aliases y recursos autorizados.

    Returns:
        Una copia validada del evento, sin modificar el inventario.

    Raises:
        ValidationError: Cuando un campo, fecha, tipo o recurso
            no cumple las reglas del contrato.

    Notes:
        Tipos permitidos:
            - instance.stopped
            - bucket.tags_missing

        simulate_failures admite enteros entre 0 y 5,
        excluyendo valores booleanos.

        La detección de conflictos por event_id se integra
        con el almacenamiento persistente del proyecto.
    """

    # ============================================================
    # I1-D: VALIDACIÓN DE ESTRUCTURAS
    # ============================================================

    # El evento debe ser un diccionario.
    if not isinstance(event, dict):
        raise ValidationError(
            "El evento debe ser un objeto JSON."
        )

    # El inventario también debe ser un diccionario.
    if not isinstance(inventory, dict):
        raise ValidationError(
            "El inventario debe ser un diccionario."
        )

    # ============================================================
    # I1-D: VALIDACIÓN DEL ESQUEMA CERRADO
    # ============================================================

    # Campos exactos que debe contener el evento.
    campos_requeridos = {
        "schema_version",
        "event_id",
        "event_type",
        "resource_id",
        "occurred_at",
        "simulate_failures",
    }

    # Rechazamos campos faltantes o adicionales.
    if set(event.keys()) != campos_requeridos:
        raise ValidationError(
            "El evento debe contener exactamente los campos requeridos."
        )

    # ============================================================
    # I1-D: VALIDACIÓN DE SCHEMA_VERSION
    # ============================================================

    # Exigimos un entero exacto para rechazar True y False.
    if type(event["schema_version"]) is not int:
        raise ValidationError(
            "schema_version debe ser un entero."
        )

    # La única versión permitida actualmente es 1.
    if event["schema_version"] != 1:
        raise ValidationError(
            "schema_version debe ser igual a 1."
        )

    # ============================================================
    # I1-D: VALIDACIÓN DE IDENTIFICADORES
    # ============================================================

    # Validamos el identificador único del evento.
    identifier(event["event_id"])

    # Validamos el alias del recurso solicitado.
    resource_id = identifier(event["resource_id"])

    # ============================================================
    # I1-D: VALIDACIÓN DEL TIPO DE EVENTO
    # ============================================================

    tipos_permitidos = {
        "instance.stopped",
        "bucket.tags_missing",
    }

    event_type = event["event_type"]

    # Rechazamos tipos desconocidos o valores que no sean texto.
    if not isinstance(event_type, str) or event_type not in tipos_permitidos:
        raise ValidationError(
            "event_type debe ser instance.stopped o bucket.tags_missing."
        )

    # ============================================================
    # I1-D: VALIDACIÓN DE LA FECHA UTC
    # ============================================================

    # Reutilizamos utc_text() para validar fecha y zona horaria.
    # La función rechaza fechas sin zona horaria o fuera de UTC.
    utc_text(event["occurred_at"])

    # ============================================================
    # I1-D: VALIDACIÓN DE SIMULATE_FAILURES
    # ============================================================

    simulate_failures = event["simulate_failures"]

    # Rechazamos booleanos y otros tipos no enteros.
    if type(simulate_failures) is not int:
        raise ValidationError(
            "simulate_failures debe ser un entero."
        )

    # Permitimos solamente valores entre 0 y 5.
    if not 0 <= simulate_failures <= 5:
        raise ValidationError(
            "simulate_failures debe estar entre 0 y 5."
        )

    # ============================================================
    # I1-D: AUTORIZACIÓN DEL RECURSO
    # ============================================================

    # El alias debe existir en el inventario autorizado.
    if resource_id not in inventory:
        raise ValidationError(
            "El recurso solicitado no pertenece al inventario autorizado."
        )

    # ============================================================
    # I1-D: CORRESPONDENCIA ENTRE EVENTO Y RECURSO
    # ============================================================

    # Asociamos cada evento con su tipo de recurso esperado.
    tipos_recursos = {
        "instance.stopped": "ec2",
        "bucket.tags_missing": "s3",
    }

    # Obtenemos la información del recurso autorizado.
    recurso = inventory[resource_id]

    # Verificamos la estructura de la entrada del inventario.
    if not isinstance(recurso, dict):
        raise ValidationError(
            "La entrada del inventario debe ser un diccionario."
        )

    # Consultamos el tipo registrado en el inventario.
    tipo_recurso = recurso.get("type")

    # Determinamos el tipo requerido por el evento.
    tipo_esperado = tipos_recursos[event_type]

    # Rechazamos operaciones sobre recursos incompatibles.
    if tipo_recurso != tipo_esperado:
        raise ValidationError(
            "El tipo de evento no coincide con el tipo de recurso."
        )

    # ============================================================
    # I1-D: DEVOLVER UNA COPIA DEL EVENTO VALIDADO
    # ============================================================

    # Creamos una copia independiente del diccionario original.
    evento_validado = event.copy()

    # Devolvemos el evento sin modificar su contenido.
    return evento_validado
