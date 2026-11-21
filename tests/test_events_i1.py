"""Pruebas del contrato de eventos - Integrante 1."""

from fenix.services.events import validate


def test_evento_ec2_valido():
    """Comprueba que se acepta un evento EC2 autorizado."""

    # Evento que cumple todos los requisitos.
    evento = {
        "schema_version": 1,
        "event_id": "evt-node-001",
        "event_type": "instance.stopped",
        "resource_id": "node-01",
        "occurred_at": "2026-10-22T18:00:00Z",
        "simulate_failures": 0,
    }

    # Inventario simulado para esta prueba.
    inventario = {
        "node-01": {"type": "ec2"},
        "bucket-01": {"type": "s3"},
    }

    # Ejecutamos nuestra función.
    resultado = validate(evento, inventario)

    # Comprobamos que el contenido sea correcto.
    assert resultado == evento

    # Comprobamos que devuelve un objeto diferente.
    assert resultado is not evento


import pytest

from fenix.utils.errors import ValidationError


def test_recurso_no_autorizado():
    """Comprueba que se rechaza un recurso fuera del inventario."""

    # El evento solicita node-99.
    evento = {
        "schema_version": 1,
        "event_id": "evt-node-002",
        "event_type": "instance.stopped",
        "resource_id": "node-99",
        "occurred_at": "2026-10-22T18:00:00Z",
        "simulate_failures": 0,
    }

    # El inventario solamente autoriza node-01.
    inventario = {
        "node-01": {"type": "ec2"},
    }

    # Esperamos que validate() lance ValidationError.
    with pytest.raises(ValidationError):
        validate(evento, inventario)

def test_tipo_evento_incompatible():
    """Rechaza un evento EC2 dirigido a un recurso S3."""

    # El evento solicita detener una instancia.
    evento = {
        "schema_version": 1,
        "event_id": "evt-node-003",
        "event_type": "instance.stopped",
        "resource_id": "bucket-01",
        "occurred_at": "2026-10-22T18:00:00Z",
        "simulate_failures": 0,
    }

    # El recurso existe, pero es de tipo S3.
    inventario = {
        "node-01": {"type": "ec2"},
        "bucket-01": {"type": "s3"},
    }

    # Comprobamos que se rechace por incompatibilidad.
    with pytest.raises(
        ValidationError,
        match="El tipo de evento no coincide con el tipo de recurso"
    ):
        validate(evento, inventario)

def test_evento_s3_valido():
    """Comprueba que se acepta un evento S3 autorizado."""

    # Evento que informa etiquetas faltantes en un bucket.
    evento = {
        "schema_version": 1,
        "event_id": "evt-bucket-001",
        "event_type": "bucket.tags_missing",
        "resource_id": "bucket-01",
        "occurred_at": "2026-10-22T18:00:00Z",
        "simulate_failures": 0,
    }

    # Inventario simulado con recursos autorizados.
    inventario = {
        "node-01": {"type": "ec2"},
        "bucket-01": {"type": "s3"},
    }

    # Ejecutamos la validación.
    resultado = validate(evento, inventario)

    # Verificamos que conserve los datos.
    assert resultado == evento

    # Verificamos que devuelva una copia.
    assert resultado is not evento

def test_evento_con_campo_faltante():
    """Rechaza un evento que no contiene todos los campos."""

    # Falta intencionalmente simulate_failures.
    evento = {
        "schema_version": 1,
        "event_id": "evt-node-004",
        "event_type": "instance.stopped",
        "resource_id": "node-01",
        "occurred_at": "2026-10-22T18:00:00Z",
    }

    inventario = {
        "node-01": {"type": "ec2"},
    }

    # Esperamos que se rechace por esquema incompleto.
    with pytest.raises(
        ValidationError,
        match="El evento debe contener exactamente los campos requeridos"
    ):
        validate(evento, inventario)

def test_evento_con_campo_adicional():
    """Rechaza un evento que contiene campos no permitidos."""

    # Evento válido, pero con un campo adicional.
    evento = {
        "schema_version": 1,
        "event_id": "evt-node-005",
        "event_type": "instance.stopped",
        "resource_id": "node-01",
        "occurred_at": "2026-10-22T18:00:00Z",
        "simulate_failures": 0,
        "instance_id": "i-1234567890abcdef0",
    }

    inventario = {
        "node-01": {"type": "ec2"},
    }

    # Esperamos que el esquema cerrado rechace el campo adicional.
    with pytest.raises(
        ValidationError,
        match="El evento debe contener exactamente los campos requeridos"
    ):
        validate(evento, inventario)

def test_schema_version_booleano():
    """Rechaza True como valor de schema_version."""

    # True no debe aceptarse aunque Python lo trate como 1.
    evento = {
        "schema_version": True,
        "event_id": "evt-node-006",
        "event_type": "instance.stopped",
        "resource_id": "node-01",
        "occurred_at": "2026-10-22T18:00:00Z",
        "simulate_failures": 0,
    }

    inventario = {
        "node-01": {"type": "ec2"},
    }

    # Comprobamos que se rechace el valor booleano.
    with pytest.raises(
        ValidationError,
        match="schema_version debe ser un entero"
    ):
        validate(evento, inventario)


def test_simulate_failures_booleano():
    """Rechaza True como cantidad de fallos simulados."""

    # El evento tiene todos sus campos correctos,
    # excepto simulate_failures, que es booleano.
    evento = {
        "schema_version": 1,
        "event_id": "evt-node-007",
        "event_type": "instance.stopped",
        "resource_id": "node-01",
        "occurred_at": "2026-10-22T18:00:00Z",
        "simulate_failures": True,
    }

    inventario = {
        "node-01": {"type": "ec2"},
    }

    # Verificamos que se lance la excepción esperada.
    with pytest.raises(
        ValidationError,
        match="simulate_failures debe ser un entero"
    ):
        validate(evento, inventario)

def test_simulate_failures_fuera_de_rango():
    """Rechaza cantidades de fallos superiores al máximo permitido."""

    # Todos los campos son válidos excepto simulate_failures.
    evento = {
        "schema_version": 1,
        "event_id": "evt-node-008",
        "event_type": "instance.stopped",
        "resource_id": "node-01",
        "occurred_at": "2026-10-22T18:00:00Z",
        "simulate_failures": 6,
    }

    inventario = {
        "node-01": {"type": "ec2"},
    }

    # Esperamos que se rechace el valor fuera del rango.
    with pytest.raises(
        ValidationError,
        match="simulate_failures debe estar entre 0 y 5"
    ):
        validate(evento, inventario)
def test_simulate_failures_negativo():
    """Rechaza cantidades de fallos menores que cero."""

    # Evento con una cantidad negativa de fallos.
    evento = {
        "schema_version": 1,
        "event_id": "evt-node-009",
        "event_type": "instance.stopped",
        "resource_id": "node-01",
        "occurred_at": "2026-10-22T18:00:00Z",
        "simulate_failures": -1,
    }

    inventario = {
        "node-01": {"type": "ec2"},
    }

    # Comprobamos que se rechace el valor negativo.
    with pytest.raises(
        ValidationError,
        match="simulate_failures debe estar entre 0 y 5"
    ):
        validate(evento, inventario)

def test_simulate_failures_limite_maximo():
    """Acepta el valor máximo permitido de fallos simulados."""

    # Evento válido con simulate_failures igual a 5.
    evento = {
        "schema_version": 1,
        "event_id": "evt-node-010",
        "event_type": "instance.stopped",
        "resource_id": "node-01",
        "occurred_at": "2026-10-22T18:00:00Z",
        "simulate_failures": 5,
    }

    inventario = {
        "node-01": {"type": "ec2"},
    }

    # Ejecutamos la validación.
    resultado = validate(evento, inventario)

    # Comprobamos que el valor máximo sea aceptado.
    assert resultado["simulate_failures"] == 5

    # Comprobamos que devuelve una copia.
    assert resultado is not evento

def test_evento_fecha_sin_zona_horaria():
    """Rechaza un evento cuya fecha no especifica UTC."""

    # La fecha no incluye Z ni +00:00.
    evento = {
        "schema_version": 1,
        "event_id": "evt-node-011",
        "event_type": "instance.stopped",
        "resource_id": "node-01",
        "occurred_at": "2026-10-22T18:00:00",
        "simulate_failures": 0,
    }

    inventario = {
        "node-01": {"type": "ec2"},
    }

    # Comprobamos que utc_text() rechace la fecha.
    with pytest.raises(ValidationError):
        validate(evento, inventario)

def test_evento_fecha_con_zona_no_utc():
    """Rechaza una fecha expresada con desplazamiento -03:00."""

    # La fecha tiene zona horaria, pero no es UTC.
    evento = {
        "schema_version": 1,
        "event_id": "evt-node-012",
        "event_type": "instance.stopped",
        "resource_id": "node-01",
        "occurred_at": "2026-10-22T15:00:00-03:00",
        "simulate_failures": 0,
    }

    inventario = {
        "node-01": {"type": "ec2"},
    }

    # Verificamos que validate() rechace la fecha no UTC.
    with pytest.raises(ValidationError):
        validate(evento, inventario)

def test_evento_fecha_utc_con_offset_cero():
    """Acepta una fecha UTC expresada con +00:00."""

    # Evento con fecha UTC válida.
    evento = {
        "schema_version": 1,
        "event_id": "evt-node-013",
        "event_type": "instance.stopped",
        "resource_id": "node-01",
        "occurred_at": "2026-10-22T18:00:00+00:00",
        "simulate_failures": 0,
    }

    inventario = {
        "node-01": {"type": "ec2"},
    }

    # Ejecutamos la validación.
    resultado = validate(evento, inventario)

    # Comprobamos que la fecha fue aceptada.
    assert resultado["occurred_at"] == "2026-10-22T18:00:00+00:00"

    # Comprobamos que devuelve una copia.
    assert resultado is not evento

def test_schema_version_incorrecta():
    """Rechaza versiones de esquema diferentes de 1."""

    # Evento con una versión no permitida.
    evento = {
        "schema_version": 2,
        "event_id": "evt-node-014",
        "event_type": "instance.stopped",
        "resource_id": "node-01",
        "occurred_at": "2026-10-22T18:00:00Z",
        "simulate_failures": 0,
    }

    inventario = {
        "node-01": {"type": "ec2"},
    }

    # Comprobamos que se rechace la versión incorrecta.
    with pytest.raises(
        ValidationError,
        match="schema_version debe ser igual a 1"
    ):
        validate(evento, inventario)

def test_tipo_evento_desconocido():
    """Rechaza tipos de eventos no permitidos."""

    # El evento utiliza un tipo que Fénix no reconoce.
    evento = {
        "schema_version": 1,
        "event_id": "evt-node-015",
        "event_type": "instance.deleted",
        "resource_id": "node-01",
        "occurred_at": "2026-10-22T18:00:00Z",
        "simulate_failures": 0,
    }

    inventario = {
        "node-01": {"type": "ec2"},
    }

    # Verificamos que se rechace el evento desconocido.
    with pytest.raises(
        ValidationError,
        match="event_type debe ser instance.stopped o bucket.tags_missing"
    ):
        validate(evento, inventario)

def test_event_id_invalido():
    """Rechaza identificadores de evento con formato incorrecto."""

    # El identificador contiene letras mayúsculas.
    evento = {
        "schema_version": 1,
        "event_id": "EVT-NODE-016",
        "event_type": "instance.stopped",
        "resource_id": "node-01",
        "occurred_at": "2026-10-22T18:00:00Z",
        "simulate_failures": 0,
    }

    inventario = {
        "node-01": {"type": "ec2"},
    }

    # Esperamos que identifier() rechace el identificador.
    with pytest.raises(ValidationError):
        validate(evento, inventario)

def test_resource_id_invalido():
    """Rechaza identificadores de recursos con formato incorrecto."""

    # El identificador contiene mayúsculas y guion bajo.
    evento = {
        "schema_version": 1,
        "event_id": "evt-node-017",
        "event_type": "instance.stopped",
        "resource_id": "NODE_01",
        "occurred_at": "2026-10-22T18:00:00Z",
        "simulate_failures": 0,
    }

    inventario = {
        "node-01": {"type": "ec2"},
    }

    # Verificamos que identifier() rechace el recurso.
    with pytest.raises(ValidationError):
        validate(evento, inventario)

def test_evento_no_es_diccionario():
    """Rechaza eventos que no sean diccionarios."""

    # Enviamos una lista en lugar de un diccionario.
    evento = [
        "instance.stopped",
        "node-01",
    ]

    # Inventario válido.
    inventario = {
        "node-01": {"type": "ec2"},
    }

    # Comprobamos que validate() rechace el tipo incorrecto.
    with pytest.raises(
        ValidationError,
        match="El evento debe ser un objeto JSON"
    ):
        validate(evento, inventario)

def test_inventario_no_es_diccionario():
    """Rechaza inventarios que no sean diccionarios."""

    # Evento correctamente formado.
    evento = {
        "schema_version": 1,
        "event_id": "evt-node-018",
        "event_type": "instance.stopped",
        "resource_id": "node-01",
        "occurred_at": "2026-10-22T18:00:00Z",
        "simulate_failures": 0,
    }

    # Inventario incorrecto: utilizamos una lista.
    inventario = [
        "node-01",
        "bucket-01",
    ]

    # Comprobamos que se rechace el inventario.
    with pytest.raises(
        ValidationError,
        match="El inventario debe ser un diccionario"
    ):
        validate(evento, inventario)