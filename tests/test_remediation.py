"""Pruebas de la reparación convergente: F03 y casos de borde de I3-C.

Los clientes de Boto3 se sustituyen por dobles que registran las llamadas: la
reparación es la unidad bajo prueba y no se abre ninguna conexión real.
"""
import pytest  # Fixtures, parametrización y aserciones.
from botocore.exceptions import ClientError, EndpointConnectionError  # Fallos del SDK.
from fenix.services import remediation  # Módulo del rol I3.
from fenix.utils.errors import PermanentError, RetryableError  # Clasificación exigida.

INSTANCE = {"type": "ec2", "id": "i-0001", "security_group_id": "sg-0001",
            "expected_tags": {"ManagedBy": "ppa2", "Project": "fenix"}}
BUCKET = {"type": "s3", "id": "bucket-01-grupo01",
          "expected_tags": {"ManagedBy": "ppa2", "Project": "fenix",
                            "GroupId": "grupo01", "Owner": "grupo01"}}


def client_error(code: str, status: int) -> ClientError:
    """Construye un ClientError con el código y el estado HTTP indicados.

    Args:
        code: Código simbólico que devuelve el SDK, por ejemplo Throttling.
        status: Código HTTP asociado a la respuesta.
    Returns:
        Excepción lista para lanzar desde un doble.
    """
    return ClientError({"Error": {"Code": code, "Message": "detalle interno"},
                        "ResponseMetadata": {"HTTPStatusCode": status}}, "Operacion")


class FakeEC2:
    """Doble de cliente EC2 que devuelve una secuencia de estados observados."""

    def __init__(self, states: list[str]):
        """Guarda los estados que devolverá describe_instances en orden.

        Args:
            states: Secuencia de estados; el último se repite si se consulta más.
        """
        self.states = list(states)
        self.starts: list[list[str]] = []
        self.start_error: Exception | None = None
        self.no_instances = False

    def describe_instances(self, InstanceIds: list[str]) -> dict:
        """Devuelve el estado actual simulado de la instancia."""
        if self.no_instances:  # Recurso borrado por fuera del inventario.
            return {"Reservations": []}
        current = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"Reservations": [{"Instances": [{"State": {"Name": current}}]}]}

    def start_instances(self, InstanceIds: list[str]) -> dict:
        """Registra el arranque o lanza el error configurado."""
        if self.start_error is not None:
            raise self.start_error
        self.starts.append(list(InstanceIds))
        return {"StartingInstances": [{"InstanceId": InstanceIds[0]}]}


class FakeS3:
    """Doble de cliente S3 con etiquetas en memoria."""

    def __init__(self, tags: dict | None = None, read_error: Exception | None = None,
                 write_error: Exception | None = None, ignore_writes: bool = False):
        """Configura el estado inicial y los fallos que debe simular.

        Args:
            tags: Etiquetas actuales del bucket; None representa NoSuchTagSet.
            read_error: Excepción que lanzará get_bucket_tagging.
            write_error: Excepción que lanzará put_bucket_tagging.
            ignore_writes: Si es True, la escritura no modifica el estado.
        """
        self.tags = dict(tags) if tags is not None else None
        self.read_error = read_error
        self.write_error = write_error
        self.ignore_writes = ignore_writes
        self.writes: list[dict] = []

    def get_bucket_tagging(self, Bucket: str) -> dict:
        """Devuelve las etiquetas o lanza el error configurado."""
        if self.read_error is not None:
            raise self.read_error
        if not self.tags:  # Solo este código significa "bucket sin etiquetas".
            raise client_error("NoSuchTagSet", 404)
        return {"TagSet": [{"Key": k, "Value": v} for k, v in self.tags.items()]}

    def put_bucket_tagging(self, Bucket: str, Tagging: dict) -> dict:
        """Registra la escritura y actualiza el estado salvo que se ignore."""
        if self.write_error is not None:
            raise self.write_error
        merged = {t["Key"]: t["Value"] for t in Tagging["TagSet"]}
        self.writes.append(merged)
        if not self.ignore_writes:
            self.tags = merged
        return {}


@pytest.fixture
def emulator(monkeypatch):
    """Devuelve una función para instalar el doble de cliente del servicio pedido.

    Args:
        monkeypatch: Fixture de pytest que restaura el módulo al terminar.
    Returns:
        Función que recibe un doble y lo publica como cliente de Boto3.
    """
    def install(fake):
        """Sustituye settings.client dentro del módulo de reparación."""
        monkeypatch.setattr(remediation, "client", lambda service: fake)
        return fake
    return install


def test_instance_already_running_is_not_repaired(emulator):
    """Una instancia que ya cumple la política no recibe ninguna acción."""
    ec2 = emulator(FakeEC2(["running"]))
    assert remediation.repair(INSTANCE, "instance.stopped") == "already_ok"
    assert ec2.starts == []  # Repetir el evento no puede volver a arrancar.


def test_stopped_instance_starts_and_verifies(emulator):
    """Convergencia: stopped pasa a running y se comprueba leyendo el estado real."""
    ec2 = emulator(FakeEC2(["stopped", "running"]))
    assert remediation.repair(INSTANCE, "instance.stopped") == "repaired"
    assert ec2.starts == [["i-0001"]]  # Un solo arranque, sobre el ID inventariado.


def test_pending_instance_asks_for_another_receipt(emulator):
    """Un arranque en curso no se espera dentro del mismo mensaje."""
    ec2 = emulator(FakeEC2(["pending"]))
    with pytest.raises(RetryableError):
        remediation.repair(INSTANCE, "instance.stopped")
    assert ec2.starts == []  # No se fuerza nada mientras el estado es incierto.


def test_instance_that_never_reaches_running_asks_for_retry(emulator):
    """Si tras arrancar el estado sigue sin ser running, la reparación no se da por hecha."""
    emulator(FakeEC2(["stopped", "pending"]))
    with pytest.raises(RetryableError):
        remediation.repair(INSTANCE, "instance.stopped")


@pytest.mark.parametrize("state", ["terminated", "shutting-down", "stopping"])
def test_terminal_state_is_permanent_failure(emulator, state):
    """Un estado del que no se vuelve no se reintenta: se explica y se descarta."""
    emulator(FakeEC2([state]))
    with pytest.raises(PermanentError):
        remediation.repair(INSTANCE, "instance.stopped")


def test_missing_instance_is_permanent_failure(emulator):
    """El inventario apunta a un recurso que ya no existe: no se adopta otro."""
    fake = FakeEC2(["running"])
    fake.no_instances = True
    emulator(fake)
    with pytest.raises(PermanentError):
        remediation.repair(INSTANCE, "instance.stopped")


@pytest.mark.parametrize(
    "code, status, expected",
    [
        ("Throttling", 400, RetryableError),  # Limitación temporal del emulador.
        ("RequestLimitExceeded", 400, RetryableError),
        ("ServiceUnavailable", 503, RetryableError),
        ("InternalError", 500, RetryableError),
        ("UnauthorizedOperation", 403, PermanentError),  # Credenciales rechazadas.
        ("InvalidInstanceID.NotFound", 400, PermanentError),
    ],
)
def test_client_error_is_classified_by_code(emulator, code, status, expected):
    """No todo ClientError es transitorio: se mira el código y el estado HTTP."""
    fake = FakeEC2(["stopped"])
    fake.start_error = client_error(code, status)
    emulator(fake)
    with pytest.raises(expected):
        remediation.repair(INSTANCE, "instance.stopped")


def test_emulator_down_is_retryable(emulator):
    """LocalStack caído se reintenta después: no es un mensaje venenoso."""
    fake = FakeEC2(["stopped"])
    fake.start_error = EndpointConnectionError(endpoint_url="http://localhost:4566")
    emulator(fake)
    with pytest.raises(RetryableError):
        remediation.repair(INSTANCE, "instance.stopped")


def test_f03_bucket_without_tags_restores_them(emulator):
    """F03: un bucket sin etiquetas recupera las obligatorias del inventario."""
    s3 = emulator(FakeS3(tags=None))
    assert remediation.repair(BUCKET, "bucket.tags_missing") == "repaired"
    assert s3.tags == BUCKET["expected_tags"]


def test_f03_preserves_foreign_tags(emulator):
    """F03: la mezcla restaura Owner sin borrar una etiqueta que no es de la política."""
    s3 = emulator(FakeS3(tags={"ManagedBy": "ppa2", "Project": "fenix",
                               "GroupId": "grupo01", "Ajeno": "conservar"}))
    assert remediation.repair(BUCKET, "bucket.tags_missing") == "repaired"
    assert s3.tags["Owner"] == "grupo01"  # La faltante se agregó.
    assert s3.tags["Ajeno"] == "conservar"  # La ajena sobrevivió intacta.


def test_complete_bucket_is_not_rewritten(emulator):
    """Con todas las etiquetas correctas no hay escritura: idempotencia real."""
    s3 = emulator(FakeS3(tags=dict(BUCKET["expected_tags"])))
    assert remediation.repair(BUCKET, "bucket.tags_missing") == "already_ok"
    assert s3.writes == []


def test_tag_with_different_value_is_corrected(emulator):
    """Una etiqueta obligatoria con el valor equivocado se reemplaza, no se duplica."""
    tags = dict(BUCKET["expected_tags"])
    tags["Owner"] = "otro-grupo"
    s3 = emulator(FakeS3(tags=tags))
    assert remediation.repair(BUCKET, "bucket.tags_missing") == "repaired"
    assert s3.tags["Owner"] == "grupo01"


def test_missing_bucket_is_not_confused_with_missing_tags(emulator):
    """Solo NoSuchTagSet equivale a cero etiquetas; otro error no se adivina."""
    emulator(FakeS3(read_error=client_error("NoSuchBucket", 404)))
    with pytest.raises(PermanentError):
        remediation.repair(BUCKET, "bucket.tags_missing")


def test_read_with_throttling_is_retryable(emulator):
    """Un NoSuchTagSet leído bajo throttling no se interpreta como bucket vacío."""
    emulator(FakeS3(read_error=client_error("Throttling", 400)))
    with pytest.raises(RetryableError):
        remediation.repair(BUCKET, "bucket.tags_missing")


def test_write_not_observed_asks_for_retry(emulator):
    """Si tras escribir el SDK las etiquetas no se leen, la etapa no está verificada."""
    emulator(FakeS3(tags=None, ignore_writes=True))
    with pytest.raises(RetryableError):
        remediation.repair(BUCKET, "bucket.tags_missing")


def test_inventory_without_expected_tags_is_not_marked_repaired(emulator):
    """Sin política declarada no hay estado deseado: no se puede declarar already_ok.

    Un inventario que no guarda `expected_tags` (§3.3) haría que la comparación con
    una política vacía diera siempre verdadera y el bucket quedara "correcto" sin
    haber verificado nada. Es un fallo permanente, no un éxito silencioso.
    """
    s3 = emulator(FakeS3(tags={"ManagedBy": "ppa2"}))
    with pytest.raises(PermanentError):
        remediation.repair({"type": "s3", "id": "bucket-01"}, "bucket.tags_missing")
    assert s3.writes == []  # Tampoco se escribe a ciegas.


def test_resource_without_identifier_is_rejected(emulator):
    """Un registro de inventario sin id no se resuelve con un valor por defecto."""
    emulator(FakeEC2(["running"]))
    with pytest.raises(PermanentError):
        remediation.repair({"type": "ec2"}, "instance.stopped")


@pytest.mark.parametrize(
    "resource, event_type",
    [
        (INSTANCE, "bucket.tags_missing"),  # Evento S3 sobre un recurso EC2.
        (BUCKET, "instance.stopped"),  # Evento EC2 sobre un bucket.
        (INSTANCE, "instance.rebooted"),  # Tipo que el contrato no admite.
    ],
)
def test_incompatible_event_and_resource_are_rejected(emulator, resource, event_type):
    """La reparación no adivina: un par incompatible es falla permanente."""
    emulator(FakeEC2(["running"]))
    with pytest.raises(PermanentError):
        remediation.repair(resource, event_type)
