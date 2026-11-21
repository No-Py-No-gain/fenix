"""Pruebas de integración de SQS para el Integrante 2."""
from unittest.mock import patch, MagicMock
from fenix.services.sqs import retry_later
import json

from fenix.config.settings import client
from fenix.services.sqs import ensure_queues


def test_ensure_queues_crea_ambas_colas():
    """Comprueba que se crean la cola principal y la DLQ."""

    # Utilizamos un prefijo exclusivo de laboratorio.
    prefix = "efimero-test-i2"

    # Ejecutamos la función que implementamos.
    work_url, dlq_url = ensure_queues(prefix)

    # Verificamos los nombres de ambas colas.
    assert work_url.endswith(f"{prefix}-events")
    assert dlq_url.endswith(f"{prefix}-dlq")

def test_ensure_queues_configuracion():
    """Verifica los atributos de ambas colas y su RedrivePolicy."""

    # Prefijo exclusivo para las pruebas del Integrante 2.
    prefix = "efimero-test-i2"

    # Creamos o reutilizamos las colas.
    work_url, dlq_url = ensure_queues(prefix)

    # Cliente SQS configurado para LocalStack.
    sqs = client("sqs")

    # ============================================================
    # VERIFICAR COLA PRINCIPAL
    # ============================================================

    atributos = sqs.get_queue_attributes(
        QueueUrl=work_url,
        AttributeNames=[
            "VisibilityTimeout",
            "MessageRetentionPeriod",
            "RedrivePolicy",
        ],
    )["Attributes"]

    assert atributos["VisibilityTimeout"] == "45"
    assert atributos["MessageRetentionPeriod"] == "345600"

    # ============================================================
    # VERIFICAR REDRIVE POLICY
    # ============================================================

    politica = json.loads(atributos["RedrivePolicy"])

    assert politica["maxReceiveCount"] == "3"

    # Consultamos el ARN real de la DLQ.
    dlq_atributos = sqs.get_queue_attributes(
        QueueUrl=dlq_url,
        AttributeNames=[
            "QueueArn",
            "MessageRetentionPeriod",
        ],
    )["Attributes"]

    # La política debe apuntar a nuestra DLQ.
    assert politica["deadLetterTargetArn"] == dlq_atributos["QueueArn"]

    # ============================================================
    # VERIFICAR RETENCIÓN DE LA DLQ
    # ============================================================

    assert dlq_atributos["MessageRetentionPeriod"] == "1209600"

def test_ensure_queues_es_idempotente():
    """Comprueba que ejecutar dos veces reutiliza las mismas colas."""

    # Utilizamos el mismo prefijo en ambas ejecuciones.
    prefix = "efimero-test-i2"

    # Primera ejecución.
    work_url_1, dlq_url_1 = ensure_queues(prefix)

    # Segunda ejecución con exactamente el mismo prefijo.
    work_url_2, dlq_url_2 = ensure_queues(prefix)

    # Ambas ejecuciones deben devolver las mismas URLs.
    assert work_url_1 == work_url_2
    assert dlq_url_1 == dlq_url_2

def test_retry_later_primera_recepcion():
    """Comprueba que la primera recepción programe 2 segundos."""

    # Simulamos un mensaje recibido desde SQS.
    mensaje = {
        "ReceiptHandle": "receipt-prueba-001",
        "Attributes": {
            "ApproximateReceiveCount": "1"
        },
    }

    # Creamos un cliente SQS simulado.
    sqs_mock = MagicMock()

    # Sustituimos temporalmente client() por nuestro mock.
    with patch(
        "fenix.services.sqs.client",
        return_value=sqs_mock
    ):

        # Ejecutamos la función que implementamos.
        segundos = retry_later(
            "https://cola-prueba",
            mensaje
        )

    # Verificamos el cálculo: 2 ** 1 = 2.
    assert segundos == 2

    # Comprobamos que SQS recibió los parámetros correctos.
    sqs_mock.change_message_visibility.assert_called_once_with(
        QueueUrl="https://cola-prueba",
        ReceiptHandle="receipt-prueba-001",
        VisibilityTimeout=2,
    )

def test_retry_later_limite_maximo():
    """Comprueba que el backoff nunca supere 30 segundos."""

    # Simulamos un mensaje recibido 6 veces.
    mensaje = {
        "ReceiptHandle": "receipt-prueba-002",
        "Attributes": {
            "ApproximateReceiveCount": "6"
        },
    }

    # Creamos un cliente SQS simulado.
    sqs_mock = MagicMock()

    # Sustituimos temporalmente el cliente real.
    with patch(
        "fenix.services.sqs.client",
        return_value=sqs_mock
    ):
        segundos = retry_later(
            "https://cola-prueba",
            mensaje
        )

    # 2 ** 6 = 64, pero el máximo permitido es 30.
    assert segundos == 30

    # Verificamos que SQS reciba exactamente 30 segundos.
    sqs_mock.change_message_visibility.assert_called_once_with(
        QueueUrl="https://cola-prueba",
        ReceiptHandle="receipt-prueba-002",
        VisibilityTimeout=30,
    )
def test_retry_later_no_duplica_ni_elimina():
    """Comprueba que el reintento conserva el mensaje original."""

    # Simulamos un mensaje recibido por segunda vez.
    mensaje = {
        "ReceiptHandle": "receipt-prueba-003",
        "Attributes": {
            "ApproximateReceiveCount": "2"
        },
    }

    # Creamos un cliente SQS simulado.
    sqs_mock = MagicMock()

    # Reemplazamos temporalmente el cliente real.
    with patch(
        "fenix.services.sqs.client",
        return_value=sqs_mock
    ):
        segundos = retry_later(
            "https://cola-prueba",
            mensaje
        )

    # Segunda recepción: 2 ** 2 = 4 segundos.
    assert segundos == 4

    # Debe modificar la visibilidad del mensaje original.
    sqs_mock.change_message_visibility.assert_called_once_with(
        QueueUrl="https://cola-prueba",
        ReceiptHandle="receipt-prueba-003",
        VisibilityTimeout=4,
    )

    # No debe crear un mensaje nuevo.
    sqs_mock.send_message.assert_not_called()

    # No debe eliminar el mensaje original.
    sqs_mock.delete_message.assert_not_called()