"""Pruebas de la reparación convergente: F03 y casos de borde de I3-C.

Los clientes de Boto3 se sustituyen por dobles que registran las llamadas: la
reparación es la unidad bajo prueba y no se abre ninguna conexión real.
"""
import pytest  # Fixtures, parametrización y aserciones.
from botocore.exceptions import ClientError, EndpointConnectionError  # Fallos del SDK.
from fenix.services import remediation  # Módulo del rol I3.
from fenix.utils.errors import PermanentError, RetryableError  # Clasificación exigida.

INSTANCIA = {"type": "ec2", "id": "i-0001", "security_group_id": "sg-0001",
             "expected_tags": {"ManagedBy": "ppa2", "Project": "fenix"}}
BUCKET = {"type": "s3", "id": "bucket-01-grupo01",
          "expected_tags": {"ManagedBy": "ppa2", "Project": "fenix",
                            "GroupId": "grupo01", "Owner": "grupo01"}}


def error_cliente(codigo: str, estado: int) -> ClientError:
    """Construye un ClientError con el código y el estado HTTP indicados.

    Args:
        codigo: Código simbólico que devuelve el SDK, por ejemplo Throttling.
        estado: Código HTTP asociado a la respuesta.
    Returns:
        Excepción lista para lanzar desde un doble.
    """
    return ClientError({"Error": {"Code": codigo, "Message": "detalle interno"},
                        "ResponseMetadata": {"HTTPStatusCode": estado}}, "Operacion")


class EC2Falso:
    """Doble de cliente EC2 que devuelve una secuencia de estados observados."""

    def __init__(self, estados: list[str]):
        """Guarda los estados que devolverá describe_instances en orden.

        Args:
            estados: Secuencia de estados; el último se repite si se consulta más.
        """
        self.estados = list(estados)
        self.arranques: list[list[str]] = []
        self.error_arranque: Exception | None = None
        self.sin_instancias = False

    def describe_instances(self, InstanceIds: list[str]) -> dict:
        """Devuelve el estado actual simulado de la instancia."""
        if self.sin_instancias:  # Recurso borrado por fuera del inventario.
            return {"Reservations": []}
        actual = self.estados.pop(0) if len(self.estados) > 1 else self.estados[0]
        return {"Reservations": [{"Instances": [{"State": {"Name": actual}}]}]}

    def start_instances(self, InstanceIds: list[str]) -> dict:
        """Registra el arranque o lanza el error configurado."""
        if self.error_arranque is not None:
            raise self.error_arranque
        self.arranques.append(list(InstanceIds))
        return {"StartingInstances": [{"InstanceId": InstanceIds[0]}]}


class S3Falso:
    """Doble de cliente S3 con etiquetas en memoria."""

    def __init__(self, etiquetas: dict | None = None, error_lectura: Exception | None = None,
                 error_escritura: Exception | None = None, ignora_escritura: bool = False):
        """Configura el estado inicial y los fallos que debe simular.

        Args:
            etiquetas: Etiquetas actuales del bucket; None representa NoSuchTagSet.
            error_lectura: Excepción que lanzará get_bucket_tagging.
            error_escritura: Excepción que lanzará put_bucket_tagging.
            ignora_escritura: Si es True, la escritura no modifica el estado.
        """
        self.etiquetas = dict(etiquetas) if etiquetas is not None else None
        self.error_lectura = error_lectura
        self.error_escritura = error_escritura
        self.ignora_escritura = ignora_escritura
        self.escrituras: list[dict] = []

    def get_bucket_tagging(self, Bucket: str) -> dict:
        """Devuelve las etiquetas o lanza el error configurado."""
        if self.error_lectura is not None:
            raise self.error_lectura
        if not self.etiquetas:  # Solo este código significa "bucket sin etiquetas".
            raise error_cliente("NoSuchTagSet", 404)
        return {"TagSet": [{"Key": k, "Value": v} for k, v in self.etiquetas.items()]}

    def put_bucket_tagging(self, Bucket: str, Tagging: dict) -> dict:
        """Registra la escritura y actualiza el estado salvo que se ignore."""
        if self.error_escritura is not None:
            raise self.error_escritura
        combinadas = {t["Key"]: t["Value"] for t in Tagging["TagSet"]}
        self.escrituras.append(combinadas)
        if not self.ignora_escritura:
            self.etiquetas = combinadas
        return {}


@pytest.fixture
def emulador(monkeypatch):
    """Devuelve una función para instalar el doble de cliente del servicio pedido.

    Args:
        monkeypatch: Fixture de pytest que restaura el módulo al terminar.
    Returns:
        Función que recibe un doble y lo publica como cliente de Boto3.
    """
    def instalar(doble):
        """Sustituye settings.client dentro del módulo de reparación."""
        monkeypatch.setattr(remediation, "client", lambda servicio: doble)
        return doble
    return instalar


def test_instancia_ya_en_running_no_se_repara(emulador):
    """Una instancia que ya cumple la política no recibe ninguna acción."""
    ec2 = emulador(EC2Falso(["running"]))
    assert remediation.repair(INSTANCIA, "instance.stopped") == "already_ok"
    assert ec2.arranques == []  # Repetir el evento no puede volver a arrancar.


def test_instancia_detenida_arranca_y_verifica(emulador):
    """Convergencia: stopped pasa a running y se comprueba leyendo el estado real."""
    ec2 = emulador(EC2Falso(["stopped", "running"]))
    assert remediation.repair(INSTANCIA, "instance.stopped") == "repaired"
    assert ec2.arranques == [["i-0001"]]  # Un solo arranque, sobre el ID inventariado.


def test_instancia_en_pending_pide_otra_recepcion(emulador):
    """Un arranque en curso no se espera dentro del mismo mensaje."""
    ec2 = emulador(EC2Falso(["pending"]))
    with pytest.raises(RetryableError):
        remediation.repair(INSTANCIA, "instance.stopped")
    assert ec2.arranques == []  # No se fuerza nada mientras el estado es incierto.


def test_instancia_que_no_llega_a_running_pide_reintento(emulador):
    """Si tras arrancar el estado sigue sin ser running, la reparación no se da por hecha."""
    emulador(EC2Falso(["stopped", "pending"]))
    with pytest.raises(RetryableError):
        remediation.repair(INSTANCIA, "instance.stopped")


@pytest.mark.parametrize("estado", ["terminated", "shutting-down", "stopping"])
def test_estado_terminal_es_falla_permanente(emulador, estado):
    """Un estado del que no se vuelve no se reintenta: se explica y se descarta."""
    emulador(EC2Falso([estado]))
    with pytest.raises(PermanentError):
        remediation.repair(INSTANCIA, "instance.stopped")


def test_instancia_inexistente_es_falla_permanente(emulador):
    """El inventario apunta a un recurso que ya no existe: no se adopta otro."""
    doble = EC2Falso(["running"])
    doble.sin_instancias = True
    emulador(doble)
    with pytest.raises(PermanentError):
        remediation.repair(INSTANCIA, "instance.stopped")


@pytest.mark.parametrize(
    "codigo, estado, esperada",
    [
        ("Throttling", 400, RetryableError),  # Limitación temporal del emulador.
        ("RequestLimitExceeded", 400, RetryableError),
        ("ServiceUnavailable", 503, RetryableError),
        ("InternalError", 500, RetryableError),
        ("UnauthorizedOperation", 403, PermanentError),  # Credenciales rechazadas.
        ("InvalidInstanceID.NotFound", 400, PermanentError),
    ],
)
def test_clienterror_se_clasifica_por_codigo(emulador, codigo, estado, esperada):
    """No todo ClientError es transitorio: se mira el código y el estado HTTP."""
    doble = EC2Falso(["stopped"])
    doble.error_arranque = error_cliente(codigo, estado)
    emulador(doble)
    with pytest.raises(esperada):
        remediation.repair(INSTANCIA, "instance.stopped")


def test_emulador_apagado_es_falla_transitoria(emulador):
    """LocalStack caído se reintenta después: no es un mensaje venenoso."""
    doble = EC2Falso(["stopped"])
    doble.error_arranque = EndpointConnectionError(endpoint_url="http://localhost:4566")
    emulador(doble)
    with pytest.raises(RetryableError):
        remediation.repair(INSTANCIA, "instance.stopped")


def test_f03_bucket_sin_etiquetas_las_restaura(emulador):
    """F03: un bucket sin etiquetas recupera las obligatorias del inventario."""
    s3 = emulador(S3Falso(etiquetas=None))
    assert remediation.repair(BUCKET, "bucket.tags_missing") == "repaired"
    assert s3.etiquetas == BUCKET["expected_tags"]


def test_f03_preserva_etiquetas_ajenas(emulador):
    """F03: la mezcla restaura Owner sin borrar una etiqueta que no es de la política."""
    s3 = emulador(S3Falso(etiquetas={"ManagedBy": "ppa2", "Project": "fenix",
                                     "GroupId": "grupo01", "Ajeno": "conservar"}))
    assert remediation.repair(BUCKET, "bucket.tags_missing") == "repaired"
    assert s3.etiquetas["Owner"] == "grupo01"  # La faltante se agregó.
    assert s3.etiquetas["Ajeno"] == "conservar"  # La ajena sobrevivió intacta.


def test_bucket_completo_no_se_reescribe(emulador):
    """Con todas las etiquetas correctas no hay escritura: idempotencia real."""
    s3 = emulador(S3Falso(etiquetas=dict(BUCKET["expected_tags"])))
    assert remediation.repair(BUCKET, "bucket.tags_missing") == "already_ok"
    assert s3.escrituras == []


def test_etiqueta_con_valor_distinto_se_corrige(emulador):
    """Una etiqueta obligatoria con el valor equivocado se reemplaza, no se duplica."""
    etiquetas = dict(BUCKET["expected_tags"])
    etiquetas["Owner"] = "otro-grupo"
    s3 = emulador(S3Falso(etiquetas=etiquetas))
    assert remediation.repair(BUCKET, "bucket.tags_missing") == "repaired"
    assert s3.etiquetas["Owner"] == "grupo01"


def test_bucket_inexistente_no_se_confunde_con_sin_etiquetas(emulador):
    """Solo NoSuchTagSet equivale a cero etiquetas; otro error no se adivina."""
    emulador(S3Falso(error_lectura=error_cliente("NoSuchBucket", 404)))
    with pytest.raises(PermanentError):
        remediation.repair(BUCKET, "bucket.tags_missing")


def test_lectura_con_throttling_es_transitoria(emulador):
    """Un NoSuchTagSet leído bajo throttling no se interpreta como bucket vacío."""
    emulador(S3Falso(error_lectura=error_cliente("Throttling", 400)))
    with pytest.raises(RetryableError):
        remediation.repair(BUCKET, "bucket.tags_missing")


def test_escritura_que_no_se_observa_pide_reintento(emulador):
    """Si tras escribir el SDK las etiquetas no se leen, la etapa no está verificada."""
    emulador(S3Falso(etiquetas=None, ignora_escritura=True))
    with pytest.raises(RetryableError):
        remediation.repair(BUCKET, "bucket.tags_missing")


def test_inventario_sin_expected_tags_no_se_da_por_reparado(emulador):
    """Sin política declarada no hay estado deseado: no se puede declarar already_ok.

    Un inventario que no guarda `expected_tags` (§3.3) haría que la comparación con
    una política vacía diera siempre verdadera y el bucket quedara "correcto" sin
    haber verificado nada. Es un fallo permanente, no un éxito silencioso.
    """
    s3 = emulador(S3Falso(etiquetas={"ManagedBy": "ppa2"}))
    with pytest.raises(PermanentError):
        remediation.repair({"type": "s3", "id": "bucket-01"}, "bucket.tags_missing")
    assert s3.escrituras == []  # Tampoco se escribe a ciegas.


def test_recurso_sin_identificador_se_rechaza(emulador):
    """Un registro de inventario sin id no se resuelve con un valor por defecto."""
    emulador(EC2Falso(["running"]))
    with pytest.raises(PermanentError):
        remediation.repair({"type": "ec2"}, "instance.stopped")


@pytest.mark.parametrize(
    "resource, event_type",
    [
        (INSTANCIA, "bucket.tags_missing"),  # Evento S3 sobre un recurso EC2.
        (BUCKET, "instance.stopped"),  # Evento EC2 sobre un bucket.
        (INSTANCIA, "instance.rebooted"),  # Tipo que el contrato no admite.
    ],
)
def test_evento_y_recurso_incompatibles_se_rechazan(emulador, resource, event_type):
    """La reparación no adivina: un par incompatible es falla permanente."""
    emulador(EC2Falso(["running"]))
    with pytest.raises(PermanentError):
        remediation.repair(resource, event_type)
