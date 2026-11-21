"""Pruebas del receptor HTTP: F04, F05, F06 y F13 de la matriz de la cátedra.

El receptor es la unidad bajo prueba: las dependencias de I1 e I2 se sustituyen por
dobles para verificar el orden de las comprobaciones y que nunca se publique en un
rechazo. La verificación real contra SQS llega en la evidencia de integración.
"""
import json  # Arma cuerpos válidos para el contrato del evento.
import time  # Genera timestamps dentro y fuera de la ventana de 300 s.
import pytest  # Fixtures y aserciones.
from fastapi.testclient import TestClient  # Cliente HTTP sin abrir un puerto real.
from botocore.exceptions import BotoCoreError, ClientError  # Fallos de transporte del SDK.
from fenix.services import receiver  # Módulo del rol I3.
from fenix.utils.crypto import signature  # El emisor firma igual que la CLI real.
from fenix.utils.errors import ValidationError  # Contrato de I1: evento no permitido.

SECRETO = "clave-ficticia-solo-test"  # Fixture pública, no es secreto de ningún servicio.
PREFIJO = "fenix-grupo01"  # Prefijo de colas aprobado por el contrato.
URL_COLA = "http://localhost:4566/000000000000/fenix-grupo01"  # Igual a la que da el SDK.
INVENTARIO = {"node-01": {"type": "ec2", "id": "i-0001"}}  # Forma que espera I1.
EVENTO = {
    "schema_version": 1,
    "event_id": "evt-node-001",
    "event_type": "instance.stopped",
    "resource_id": "node-01",
    "occurred_at": "2026-10-22T18:00:00Z",
    "simulate_failures": 0,
}
CLIENTE = TestClient(receiver.app)  # Una sola app para todas las pruebas.


@pytest.fixture
def dobles(monkeypatch):
    """Sustituye las dependencias de I1/I2 y devuelve el registro de publicaciones.

    Args:
        monkeypatch: Fixture de pytest que restaura el entorno al terminar.
    Returns:
        Diccionario con la lista de eventos efectivamente publicados.
    """
    registro: dict = {"publicados": []}
    monkeypatch.setenv("WEBHOOK_SECRET", SECRETO)
    monkeypatch.setenv("QUEUE_PREFIX", PREFIJO)
    monkeypatch.setattr(receiver, "load", lambda ruta: INVENTARIO)  # Sin disco real.
    monkeypatch.setattr(receiver, "queue_url", lambda prefijo: URL_COLA)  # Sin red real.

    def validate_ok(evento, inventario):
        """Copia el evento como haría la validación de I1 con una entrada correcta."""
        return dict(evento)

    def publicar(url, evento, delay=0):
        """Registra la publicación y devuelve un MessageId verosímil."""
        registro["publicados"].append((url, evento))
        return "msg-0001"

    monkeypatch.setattr(receiver, "validate", validate_ok)
    monkeypatch.setattr(receiver, "publish", publicar)
    return registro


def firmar(cuerpo: bytes, timestamp: int | None = None) -> dict:
    """Genera las cabeceras que envía la CLI real para un cuerpo dado.

    Args:
        cuerpo: Bytes exactos que se envían.
        timestamp: Epoch a firmar; None usa el reloj actual.
    Returns:
        Diccionario de cabeceras listo para el cliente de pruebas.
    """
    momento = int(time.time()) if timestamp is None else timestamp
    digest = signature(SECRETO, momento, cuerpo)
    return {"X-Webhook-Signature": f"t={momento},v1={digest}",
            "Content-Type": "application/json"}


def cuerpo_valido() -> bytes:
    """Serializa el evento de ejemplo sin espacios, como la CLI del enunciado."""
    return json.dumps(EVENTO, separators=(",", ":")).encode("utf-8")


def test_f02_evento_valido_devuelve_202_con_message_id(dobles):
    """F02: acepta solo después de que SQS confirmó y devuelve los dos identificadores."""
    cuerpo = cuerpo_valido()
    respuesta = CLIENTE.post("/webhook", content=cuerpo, headers=firmar(cuerpo))
    assert respuesta.status_code == 202  # Aceptación durable, no 200 optimista.
    datos = respuesta.json()
    assert datos["status"] == "accepted"
    assert datos["event_id"] == EVENTO["event_id"]  # Identifica la operación de negocio.
    assert datos["message_id"] == "msg-0001"  # Viene de send_message, no se inventa.
    assert len(dobles["publicados"]) == 1  # Una sola publicación.
    url, evento = dobles["publicados"][0]
    assert url == URL_COLA  # La URL la devuelve el SDK, no se arma a mano.
    assert evento["event_id"] == EVENTO["event_id"]


@pytest.mark.parametrize(
    "cabeceras",
    [
        {},  # Firma ausente.
        {"X-Webhook-Signature": ""},  # Cabecera presente pero vacía.
        {"X-Webhook-Signature": "t=1"},  # Sin v1.
        {"X-Webhook-Signature": "v1=" + "a" * 64},  # Sin t.
        {"X-Webhook-Signature": "t=abc,v1=" + "a" * 64},  # Formato ambiguo.
        {"X-Webhook-Signature": "t=1,v1=" + "a" * 64 + ",t=1"},  # Clave duplicada.
        {"X-Webhook-Signature": "t=1000,v1=" + "c" * 64},  # Firma bien formada y falsa.
    ],
)
def test_f04_firma_invalida_devuelve_401_sin_publicar(dobles, cabeceras):
    """F04: firma ausente, malformada o incorrecta no publica nada."""
    respuesta = CLIENTE.post("/webhook", content=cuerpo_valido(), headers=cabeceras)
    assert respuesta.status_code == 401  # Nunca 400 ni 500: es autenticación.
    assert dobles["publicados"] == []  # El efecto exigido por la rúbrica: no publicar.


def test_f04_timestamp_de_hace_una_hora_devuelve_401(dobles):
    """F04: una firma correcta pero vieja se rechaza por la ventana de 300 s."""
    cuerpo = cuerpo_valido()
    headers = firmar(cuerpo, timestamp=int(time.time()) - 3600)  # Una hora atrás.
    respuesta = CLIENTE.post("/webhook", content=cuerpo, headers=headers)
    assert respuesta.status_code == 401
    assert dobles["publicados"] == []


def test_f05_mismos_datos_otros_bytes_devuelve_401(dobles):
    """F05: se firman unos bytes y se envían otros equivalentes como JSON."""
    firmado = json.dumps(EVENTO, separators=(",", ":")).encode("utf-8")
    enviado = json.dumps(EVENTO, indent=2).encode("utf-8")  # Mismo contenido, otros bytes.
    respuesta = CLIENTE.post("/webhook", content=enviado, headers=firmar(firmado))
    assert respuesta.status_code == 401  # La HMAC cubre bytes, no el significado.
    assert dobles["publicados"] == []


def test_401_no_revela_motivo_ni_contenido(dobles):
    """El rechazo usa un mensaje genérico que no repite firma ni secreto."""
    respuesta = CLIENTE.post("/webhook", content=cuerpo_valido(),
                             headers={"X-Webhook-Signature": "t=9,v1=" + "b" * 64})
    detalle = respuesta.json()["detail"]
    assert SECRETO not in detalle  # Sin eco del secreto compartido.
    assert "b" * 64 not in detalle  # Sin eco de la firma recibida.


@pytest.mark.parametrize(
    "cuerpo",
    [
        b'{"schema_version":',  # JSON truncado.
        b"",  # Cuerpo vacío con firma válida.
        b"\xff\xfe\x00",  # UTF-8 inválido.
    ],
)
def test_f06_cuerpo_ilegible_devuelve_400(dobles, cuerpo):
    """F06: JSON roto o UTF-8 inválido con firma válida es 400, no 401."""
    respuesta = CLIENTE.post("/webhook", content=cuerpo, headers=firmar(cuerpo))
    assert respuesta.status_code == 400
    assert dobles["publicados"] == []


def test_f06_lista_json_devuelve_422(dobles, monkeypatch):
    """F06: una lista es JSON válido pero no es un evento; el contrato la rechaza."""
    def validate_rechaza(evento, inventario):
        """Simula la validación de I1 frente a un objeto que no es un evento."""
        raise ValidationError("El evento debe ser un objeto JSON")

    monkeypatch.setattr(receiver, "validate", validate_rechaza)
    cuerpo = b"[1,2,3]"  # Firmado correctamente: el problema es el contrato.
    respuesta = CLIENTE.post("/webhook", content=cuerpo, headers=firmar(cuerpo))
    assert respuesta.status_code == 422
    assert dobles["publicados"] == []


def test_esquema_invalido_devuelve_422_sin_publicar(dobles, monkeypatch):
    """Un alias ajeno o una clave extra se rechazan antes de tocar SQS."""
    def validate_rechaza(evento, inventario):
        """Simula la validación de I1 frente a un recurso no inventariado."""
        raise ValidationError("resource_id no pertenece al inventario")

    monkeypatch.setattr(receiver, "validate", validate_rechaza)
    cuerpo = cuerpo_valido()
    respuesta = CLIENTE.post("/webhook", content=cuerpo, headers=firmar(cuerpo))
    assert respuesta.status_code == 422
    assert dobles["publicados"] == []


def test_cuerpo_mayor_a_16_kib_devuelve_413(dobles):
    """Un cuerpo de más de 16 KiB se rechaza antes de autenticarlo."""
    cuerpo = b'{"relleno":"' + b"x" * 20_000 + b'"}'  # Supera el límite del contrato.
    respuesta = CLIENTE.post("/webhook", content=cuerpo, headers=firmar(cuerpo))
    assert respuesta.status_code == 413
    assert dobles["publicados"] == []


def test_f13_sqs_caido_devuelve_503_sin_prometer_aceptacion(dobles, monkeypatch):
    """F13: si SQS rechaza el envío, la respuesta no puede ser 202."""
    def publicar_falla(url, evento, delay=0):
        """Replica un ClientError de SQS al enviar."""
        raise ClientError({"Error": {"Code": "InternalError", "Message": "boom"}},
                          "SendMessage")

    monkeypatch.setattr(receiver, "publish", publicar_falla)
    cuerpo = cuerpo_valido()
    respuesta = CLIENTE.post("/webhook", content=cuerpo, headers=firmar(cuerpo))
    assert respuesta.status_code == 503
    assert respuesta.json()["detail"] != "accepted"  # Sin aceptación falsa.


def test_f13_transporte_caido_devuelve_503(dobles, monkeypatch):
    """F13: un fallo de transporte del SDK también es 503, no 500."""
    def publicar_sin_red(url, evento, delay=0):
        """Replica un BotoCoreError de conexión con el emulador."""
        raise BotoCoreError()

    monkeypatch.setattr(receiver, "publish", publicar_sin_red)
    cuerpo = cuerpo_valido()
    respuesta = CLIENTE.post("/webhook", content=cuerpo, headers=firmar(cuerpo))
    assert respuesta.status_code == 503


def test_sin_secreto_configurado_no_acepta(dobles, monkeypatch):
    """Sin WEBHOOK_SECRET el receptor falla cerrado y no publica."""
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    cuerpo = cuerpo_valido()
    respuesta = CLIENTE.post("/webhook", content=cuerpo, headers=firmar(cuerpo))
    assert respuesta.status_code == 503  # Problema de configuración, no del cliente.
    assert dobles["publicados"] == []


def test_sin_prefijo_de_cola_no_acepta(dobles, monkeypatch):
    """Sin QUEUE_PREFIX tampoco se puede prometer aceptación."""
    monkeypatch.delenv("QUEUE_PREFIX", raising=False)
    cuerpo = cuerpo_valido()
    respuesta = CLIENTE.post("/webhook", content=cuerpo, headers=firmar(cuerpo))
    assert respuesta.status_code == 503
    assert dobles["publicados"] == []
