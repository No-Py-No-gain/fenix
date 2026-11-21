"""
Pruebas automatizadas de I2-C.

Integrante 2 - Proyecto Fénix.

Objetivo:
Verificar que setup_demo() prepara el laboratorio
de forma segura y respeta las reglas del proyecto.
"""

import pytest

from fenix.services.demo import setup_demo
from fenix.utils.errors import ValidationError


def test_setup_demo_rechaza_grupo_invalido():
    """
    Verifica que setup_demo() rechace un grupo inválido.

    El identificador debe cumplir el contrato:
    - Entre 3 y 32 caracteres.
    - Letras minúsculas, números y guiones.
    - Sin espacios ni letras mayúsculas.

    No necesitamos conectarnos a LocalStack para esta prueba,
    porque la validación ocurre antes de crear recursos.
    """

    # ARRANGE: preparamos un identificador inválido.
    grupo_invalido = "GRUPO INVALIDO"

    # ACT + ASSERT:
    # esperamos que la función lance ValidationError.
    with pytest.raises(ValidationError):
        setup_demo(grupo_invalido)

def test_setup_demo_es_idempotente():
    """
    Verifica que ejecutar setup_demo() dos veces
    reutilice los mismos recursos del laboratorio.

    Utilizamos el grupo ya preparado en LocalStack.
    """

    # ARRANGE:
    # Elegimos el grupo de nuestro laboratorio.
    grupo = "grupo01"

    # ACT:
    # Ejecutamos dos veces la preparación.
    primer_inventario = setup_demo(grupo)
    segundo_inventario = setup_demo(grupo)

    # ASSERT:
    # Ambas ejecuciones deben devolver los mismos recursos.
    assert (
        primer_inventario["node-01"]["id"]
        == segundo_inventario["node-01"]["id"]
    )

    assert (
        primer_inventario["bucket-01"]["id"]
        == segundo_inventario["bucket-01"]["id"]
    )

    # El inventario completo debe ser equivalente.
    assert primer_inventario == segundo_inventario

def test_setup_demo_rechaza_bucket_ajeno(monkeypatch):
    """
    Verifica que Fénix rechace un bucket que no le pertenece.

    Simulamos un bucket existente con etiquetas incorrectas.
    No modificamos recursos reales de LocalStack.
    """

    # ARRANGE:
    # Guardamos la función original que crea clientes AWS.
    from fenix.services import demo

    cliente_original = demo.client

    class S3Simulado:
        """Simula un bucket existente que pertenece a otro grupo."""

        def list_buckets(self):
            return {
                "Buckets": [
                    {"Name": "fenix-grupo01-demo"}
                ]
            }

        def get_bucket_tagging(self, Bucket):
            return {
                "TagSet": [
                    {"Key": "ManagedBy", "Value": "otro-proyecto"},
                    {"Key": "Project", "Value": "fenix"},
                    {"Key": "GroupId", "Value": "otro-grupo"},
                    {"Key": "Owner", "Value": "otro-grupo"},
                ]
            }

    def cliente_simulado(servicio):
        # Sustituimos solamente el cliente S3.
        if servicio == "s3":
            return S3Simulado()

        # EC2 sigue utilizando el cliente original.
        return cliente_original(servicio)

    # Reemplazamos temporalmente la función client.
    monkeypatch.setattr(demo, "client", cliente_simulado)

    # ACT + ASSERT:
    # La función debe rechazar el bucket ajeno.
    with pytest.raises(RuntimeError):
        setup_demo("grupo01")


    