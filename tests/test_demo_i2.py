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


def test_setup_demo_no_adopta_bucket_sin_etiquetas(monkeypatch, tmp_path):
    """
    I2-C: Un bucket existente sin etiquetas no demuestra propiedad.

    Simulamos la respuesta NoSuchTagSet de S3.
    El programa debe rechazar el recurso si no puede acreditar
    que corresponde a una creación propia pendiente.

    No se modifican recursos reales de LocalStack.
    """
    from botocore.exceptions import ClientError
    from fenix.services import demo

    # Aislamos los archivos de estado de esta prueba.
    monkeypatch.chdir(tmp_path)

    cliente_original = demo.client

    class S3Simulado:
        def list_buckets(self):
            return {
                "Buckets": [
                    {"Name": "fenix-grupo01-demo"}
                ]
            }

        def get_bucket_tagging(self, Bucket):
            raise ClientError(
                {
                    "Error": {
                        "Code": "NoSuchTagSet",
                        "Message": "The TagSet does not exist",
                    }
                },
                "GetBucketTagging",
            )

        def put_bucket_tagging(self, **kwargs):
            pytest.fail(
                "No se debe etiquetar un bucket de propiedad desconocida"
            )

    def cliente_simulado(servicio):
        if servicio == "s3":
            return S3Simulado()
        return cliente_original(servicio)

    monkeypatch.setattr(demo, "client", cliente_simulado)

    # Aceptamos un rechazo explícito o el error NoSuchTagSet.
    # Lo fundamental es que nunca se adopte el bucket.
    with pytest.raises((RuntimeError, ClientError)):
        setup_demo("grupo01")

def test_setup_demo_reintenta_etiquetado_s3(monkeypatch, tmp_path):
    """
    I2-C: Recuperación de un fallo transitorio al etiquetar S3.

    Escenario:
    1. Fénix crea un bucket nuevo.
    2. El primer put_bucket_tagging() falla.
    3. El segundo intento funciona.
    4. El bucket termina con las etiquetas esperadas.

    La prueba utiliza un cliente S3 simulado y archivos
    temporales, sin modificar el bucket real de grupo01.
    """
    from botocore.exceptions import ClientError
    from fenix.services import demo

    # ARRANGE: aislamos los archivos locales de esta prueba.
    monkeypatch.chdir(tmp_path)

        # ============================================================
    # I2-C: SIMULADOR EC2 PARA AISLAR LA PRUEBA
    # ============================================================

    class EC2Simulado:
        """
        Simula los recursos EC2 necesarios para setup_demo().

        No se conecta a LocalStack ni crea recursos reales. Las etiquetas de la
        instancia viven en memoria para que create_tags se comporte como el
        emulador: reconciliar tiene que verse en el describe posterior.
        """

        def __init__(self):
            self.tags_instancia = {
                "ManagedBy": "ppa2",
                "Project": "fenix",
                "GroupId": "grupo02",
                "Name": "fenix-grupo02-node",
            }

        def create_tags(self, Resources=None, Tags=None, **kwargs):
            """Agrega o corrige etiquetas, sin duplicar recursos."""
            for tag in Tags or []:
                self.tags_instancia[tag["Key"]] = tag["Value"]
            return {}

        def describe_vpcs(self, **kwargs):
            return {
                "Vpcs": [
                    {"VpcId": "vpc-simulada", "IsDefault": True}
                ]
            }

        def describe_security_groups(self, **kwargs):
            return {
                "SecurityGroups": [
                    {
                        "GroupId": "sg-simulado",
                        "Tags": [
                            {"Key": "ManagedBy", "Value": "ppa2"},
                            {"Key": "GroupId", "Value": "grupo02"},
                        ],
                    }
                ]
            }

        def describe_images(self, **kwargs):
            return {
                "Images": [
                    {"ImageId": "ami-simulada"}
                ]
            }

        def describe_instances(self, **kwargs):
            return {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-simulada",
                                "Tags": [
                                    {"Key": clave, "Value": valor}
                                    for clave, valor in self.tags_instancia.items()
                                ],
                            }
                        ]
                    }
                ]
            }

    ec2_simulado = EC2Simulado()

    class S3Simulado:
        """Simula la creación y el etiquetado de un bucket."""

        def __init__(self):
            self.bucket_creado = False
            self.intentos_etiquetado = 0
            self.etiquetas = []

        def list_buckets(self):
            # Inicialmente no existe ningún bucket.
            return {"Buckets": []}

        def create_bucket(self, Bucket):
            assert Bucket == "fenix-grupo02-demo"
            self.bucket_creado = True
            return {}

        def put_bucket_tagging(self, Bucket, Tagging):
            assert self.bucket_creado

            self.intentos_etiquetado += 1

            if self.intentos_etiquetado == 1:
                # Simulamos un fallo transitorio de S3.
                raise ClientError(
                    {
                        "Error": {
                            "Code": "InternalError",
                            "Message": "Error temporal simulado",
                        }
                    },
                    "PutBucketTagging",
                )

            # El segundo intento funciona.
            self.etiquetas = Tagging["TagSet"]
            return {}

        def get_bucket_tagging(self, Bucket):
            return {"TagSet": self.etiquetas}

    s3_simulado = S3Simulado()

    def cliente_simulado(servicio):
        """
        Devuelve exclusivamente clientes simulados.

        Ninguna llamada de esta prueba utiliza AWS ni LocalStack.
        """
        if servicio == "s3":
            return s3_simulado

        if servicio == "ec2":
            return ec2_simulado

        raise AssertionError(
            f"Servicio inesperado durante la prueba: {servicio}"
        )

    monkeypatch.setattr(demo, "client", cliente_simulado)

    # ACT:
    # Ejecutamos la preparación con el S3 simulado.
    setup_demo("grupo02")

    # ASSERT:
    # Se creó el bucket y el etiquetado se intentó dos veces.
    assert s3_simulado.bucket_creado is True
    assert s3_simulado.intentos_etiquetado == 2

    # Comprobamos las etiquetas finales.
    etiquetas = {
        tag["Key"]: tag["Value"]
        for tag in s3_simulado.etiquetas
    }

    assert etiquetas == {
        "ManagedBy": "ppa2",
        "Project": "fenix",
        "GroupId": "grupo02",
        "Owner": "grupo02",
    }