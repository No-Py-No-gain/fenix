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

SECRET = "clave-ficticia-solo-test"  # Fixture pública, no es secreto de ningún servicio.
PREFIX = "fenix-grupo01"  # Prefijo de colas aprobado por el contrato.
QUEUE_URL = "http://localhost:4566/000000000000/fenix-grupo01"  # Igual a la que da el SDK.
INVENTORY = {"node-01": {"type": "ec2", "id": "i-0001"}}  # Forma que espera I1.
EVENT = {
    "schema_version": 1,
    "event_id": "evt-node-001",
    "event_type": "instance.stopped",
    "resource_id": "node-01",
    "occurred_at": "2026-10-22T18:00:00Z",
    "simulate_failures": 0,
}
CLIENT = TestClient(receiver.app)  # Una sola app para todas las pruebas.


@pytest.fixture
def doubles(monkeypatch):
    """Sustituye las dependencias de I1/I2 y devuelve el registro de publicaciones.

    Args:
        monkeypatch: Fixture de pytest que restaura el entorno al terminar.
    Returns:
        Diccionario con la lista de eventos efectivamente publicados.
    """
    calls: dict = {"published": []}
    monkeypatch.setenv("WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("QUEUE_PREFIX", PREFIX)
    monkeypatch.setattr(receiver, "load", lambda path: INVENTORY)  # Sin disco real.
    monkeypatch.setattr(receiver, "queue_url", lambda prefix: QUEUE_URL)  # Sin red real.

    def validate_ok(event, inventory):
        """Copia el evento como haría la validación de I1 con una entrada correcta."""
        return dict(event)

    def fake_publish(url, event, delay=0):
        """Registra la publicación y devuelve un MessageId verosímil."""
        calls["published"].append((url, event))
        return "msg-0001"

    monkeypatch.setattr(receiver, "validate", validate_ok)
    monkeypatch.setattr(receiver, "publish", fake_publish)
    return calls


def sign_body(body: bytes, timestamp: int | None = None) -> dict:
    """Genera las cabeceras que envía la CLI real para un cuerpo dado.

    Args:
        body: Bytes exactos que se envían.
        timestamp: Epoch a firmar; None usa el reloj actual.
    Returns:
        Diccionario de cabeceras listo para el cliente de pruebas.
    """
    sent_at = int(time.time()) if timestamp is None else timestamp
    digest = signature(SECRET, sent_at, body)
    return {"X-Webhook-Signature": f"t={sent_at},v1={digest}",
            "Content-Type": "application/json"}


def valid_body() -> bytes:
    """Serializa el evento de ejemplo sin espacios, como la CLI del enunciado."""
    return json.dumps(EVENT, separators=(",", ":")).encode("utf-8")


def test_f02_valid_event_returns_202_with_message_id(doubles):
    """F02: acepta solo después de que SQS confirmó y devuelve los dos identificadores."""
    body = valid_body()
    response = CLIENT.post("/webhook", content=body, headers=sign_body(body))
    assert response.status_code == 202  # Aceptación durable, no 200 optimista.
    data = response.json()
    assert data["status"] == "accepted"
    assert data["event_id"] == EVENT["event_id"]  # Identifica la operación de negocio.
    assert data["message_id"] == "msg-0001"  # Viene de send_message, no se inventa.
    assert len(doubles["published"]) == 1  # Una sola publicación.
    url, event = doubles["published"][0]
    assert url == QUEUE_URL  # La URL la devuelve el SDK, no se arma a mano.
    assert event["event_id"] == EVENT["event_id"]


@pytest.mark.parametrize(
    "headers",
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
def test_f04_invalid_signature_returns_401_without_publishing(doubles, headers):
    """F04: firma ausente, malformada o incorrecta no publica nada."""
    response = CLIENT.post("/webhook", content=valid_body(), headers=headers)
    assert response.status_code == 401  # Nunca 400 ni 500: es autenticación.
    assert doubles["published"] == []  # El efecto exigido por la rúbrica: no publicar.


def test_f04_timestamp_from_an_hour_ago_returns_401(doubles):
    """F04: una firma correcta pero vieja se rechaza por la ventana de 300 s."""
    body = valid_body()
    headers = sign_body(body, timestamp=int(time.time()) - 3600)  # Una hora atrás.
    response = CLIENT.post("/webhook", content=body, headers=headers)
    assert response.status_code == 401
    assert doubles["published"] == []


def test_f05_same_data_different_bytes_returns_401(doubles):
    """F05: se firman unos bytes y se envían otros equivalentes como JSON."""
    signed = json.dumps(EVENT, separators=(",", ":")).encode("utf-8")
    sent = json.dumps(EVENT, indent=2).encode("utf-8")  # Mismo contenido, otros bytes.
    response = CLIENT.post("/webhook", content=sent, headers=sign_body(signed))
    assert response.status_code == 401  # La HMAC cubre bytes, no el significado.
    assert doubles["published"] == []


def test_401_reveals_neither_reason_nor_content(doubles):
    """El rechazo usa un mensaje genérico que no repite firma ni secreto."""
    response = CLIENT.post("/webhook", content=valid_body(),
                           headers={"X-Webhook-Signature": "t=9,v1=" + "b" * 64})
    detail = response.json()["detail"]
    assert SECRET not in detail  # Sin eco del secreto compartido.
    assert "b" * 64 not in detail  # Sin eco de la firma recibida.


@pytest.mark.parametrize(
    "body",
    [
        b'{"schema_version":',  # JSON truncado.
        b"",  # Cuerpo vacío con firma válida.
        b"\xff\xfe\x00",  # UTF-8 inválido.
    ],
)
def test_f06_unreadable_body_returns_400(doubles, body):
    """F06: JSON roto o UTF-8 inválido con firma válida es 400, no 401."""
    response = CLIENT.post("/webhook", content=body, headers=sign_body(body))
    assert response.status_code == 400
    assert doubles["published"] == []


def test_f06_json_list_returns_422(doubles, monkeypatch):
    """F06: una lista es JSON válido pero no es un evento; el contrato la rechaza."""
    def validate_rejects(event, inventory):
        """Simula la validación de I1 frente a un objeto que no es un evento."""
        raise ValidationError("El evento debe ser un objeto JSON")

    monkeypatch.setattr(receiver, "validate", validate_rejects)
    body = b"[1,2,3]"  # Firmado correctamente: el problema es el contrato.
    response = CLIENT.post("/webhook", content=body, headers=sign_body(body))
    assert response.status_code == 422
    assert doubles["published"] == []


def test_invalid_schema_returns_422_without_publishing(doubles, monkeypatch):
    """Un alias ajeno o una clave extra se rechazan antes de tocar SQS."""
    def validate_rejects(event, inventory):
        """Simula la validación de I1 frente a un recurso no inventariado."""
        raise ValidationError("resource_id no pertenece al inventario")

    monkeypatch.setattr(receiver, "validate", validate_rejects)
    body = valid_body()
    response = CLIENT.post("/webhook", content=body, headers=sign_body(body))
    assert response.status_code == 422
    assert doubles["published"] == []


def test_body_over_16_kib_returns_413(doubles):
    """Un cuerpo de más de 16 KiB se rechaza antes de autenticarlo."""
    body = b'{"relleno":"' + b"x" * 20_000 + b'"}'  # Supera el límite del contrato.
    response = CLIENT.post("/webhook", content=body, headers=sign_body(body))
    assert response.status_code == 413
    assert doubles["published"] == []


def test_f13_sqs_down_returns_503_without_promising_acceptance(doubles, monkeypatch):
    """F13: si SQS rechaza el envío, la respuesta no puede ser 202."""
    def fake_publish_failure(url, event, delay=0):
        """Replica un ClientError de SQS al enviar."""
        raise ClientError({"Error": {"Code": "InternalError", "Message": "boom"}},
                          "SendMessage")

    monkeypatch.setattr(receiver, "publish", fake_publish_failure)
    body = valid_body()
    response = CLIENT.post("/webhook", content=body, headers=sign_body(body))
    assert response.status_code == 503
    assert response.json()["detail"] != "accepted"  # Sin aceptación falsa.


def test_f13_transport_down_returns_503(doubles, monkeypatch):
    """F13: un fallo de transporte del SDK también es 503, no 500."""
    def fake_publish_network_failure(url, event, delay=0):
        """Replica un BotoCoreError de conexión con el emulador."""
        raise BotoCoreError()

    monkeypatch.setattr(receiver, "publish", fake_publish_network_failure)
    body = valid_body()
    response = CLIENT.post("/webhook", content=body, headers=sign_body(body))
    assert response.status_code == 503


def test_without_secret_configured_does_not_accept(doubles, monkeypatch):
    """Sin WEBHOOK_SECRET el receptor falla cerrado y no publica."""
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    body = valid_body()
    response = CLIENT.post("/webhook", content=body, headers=sign_body(body))
    assert response.status_code == 503  # Problema de configuración, no del cliente.
    assert doubles["published"] == []


def test_without_queue_prefix_does_not_accept(doubles, monkeypatch):
    """Sin QUEUE_PREFIX tampoco se puede prometer aceptación."""
    monkeypatch.delenv("QUEUE_PREFIX", raising=False)
    body = valid_body()
    response = CLIENT.post("/webhook", content=body, headers=sign_body(body))
    assert response.status_code == 503
    assert doubles["published"] == []


class FakeLog:
    """Doble del logger del proceso: anota nivel, mensaje y contexto público."""

    def __init__(self):
        """Empieza sin líneas registradas."""
        self.lines: list[tuple] = []

    def _record(self, level: str, message: str, extra: dict | None) -> None:
        """Guarda una línea con la forma que produce el pipeline real."""
        self.lines.append((level, message, extra or {}))

    def info(self, message: str, **options) -> None:
        """Registra un nivel INFO."""
        self._record("INFO", message, options.get("extra"))

    def warning(self, message: str, **options) -> None:
        """Registra un nivel WARNING."""
        self._record("WARNING", message, options.get("extra"))

    def error(self, message: str, **options) -> None:
        """Registra un nivel ERROR."""
        self._record("ERROR", message, options.get("extra"))


@pytest.fixture
def fake_log(monkeypatch):
    """Sustituye el logger del receptor por un doble observable.

    Args:
        monkeypatch: Fixture de pytest que restaura el módulo al terminar.
    Returns:
        El doble, con la lista de líneas registradas.
    """
    fake = FakeLog()
    monkeypatch.setattr(receiver, "_logger", lambda: fake)
    return fake


def test_202_records_event_id_and_message_id(doubles, fake_log):
    """Una aceptación queda registrada con los dos identificadores correlacionados."""
    body = valid_body()
    CLIENT.post("/webhook", content=body, headers=sign_body(body))
    level, message, extra = fake_log.lines[-1]
    assert (level, message) == ("INFO", "evento_aceptado")
    assert extra["event_id"] == EVENT["event_id"]  # Permite cruzar con el ledger.
    assert extra["message_id"] == "msg-0001"  # Y con la cola real.
    assert extra["status"] == 202
    assert extra["resource_id"] == EVENT["resource_id"]


def test_401_records_without_inventing_context(doubles, fake_log):
    """Un rechazo por firma no publica un event_id que todavía no se leyó."""
    CLIENT.post("/webhook", content=valid_body(), headers={})
    level, message, extra = fake_log.lines[-1]
    assert (level, message) == ("WARNING", "firma_invalida")
    assert extra["status"] == 401
    assert "event_id" not in extra  # No se inventa contexto: no se leyó el cuerpo.


def test_422_records_the_received_event_id(doubles, fake_log, monkeypatch):
    """Un contrato inválido deja rastro del evento que sí se pudo leer."""
    def validate_rejects(event, inventory):
        """Simula la validación de I1 frente a un recurso ajeno."""
        raise ValidationError("El recurso no pertenece al inventario autorizado")

    monkeypatch.setattr(receiver, "validate", validate_rejects)
    body = valid_body()
    CLIENT.post("/webhook", content=body, headers=sign_body(body))
    level, message, extra = fake_log.lines[-1]
    assert (level, message) == ("WARNING", "evento_invalido")
    assert extra["event_id"] == EVENT["event_id"]
    assert extra["status"] == 422
