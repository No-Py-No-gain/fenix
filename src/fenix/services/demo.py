"""Laboratorio acotado de recursos. Integrante 2 prepara, integrante 6 verifica."""
from botocore.exceptions import ClientError
from fenix.config.settings import client
from fenix.utils.state import load, save
from fenix.utils.validation import identifier


def setup_demo(group: str) -> dict:
    """Prepara una instancia mock y un bucket a partir de identificadores estables.

    Args:
        group: ID validado del grupo; determina prefijos de los recursos.
    Returns:
        Inventario con aliases node-01 y bucket-01 y sus IDs reales del emulador.
    Raises:
        ClientError: Si el emulador no soporta una operación o falla su ejecución.
    Side Effects:
        Crea recursos de laboratorio etiquetados y data/inventory.json.
    Notes:
        Reutilizar por tags antes de crear y guardar IDs después de cada creación.
        Obtener una ImageId con describe_images; no copiar una AMI de AWS real.
    """
    # ============================================================
    # I2-C: VALIDAR EL IDENTIFICADOR DEL GRUPO
    # ============================================================

    # Validamos el identificador antes de crear recursos.
    # Esto evita nombres incorrectos o no permitidos.
    group = identifier(group)

        # ============================================================
    # I2-C: LOCALIZAR LA VPC PREDETERMINADA
    # ============================================================

    # Crear el cliente EC2 apuntando a LocalStack.
    ec2 = client("ec2")

    # Buscar las VPC marcadas como predeterminadas.
    respuesta = ec2.describe_vpcs(
        Filters=[
            {
                "Name": "isDefault",
                "Values": ["true"],
            }
        ]
    )

    # Obtener la lista de VPC encontradas.
    vpcs = respuesta.get("Vpcs", [])

    # Comprobar que exista una VPC predeterminada.
    if not vpcs:
        raise RuntimeError(
            "No se encontró una VPC predeterminada en LocalStack."
        )

    # Guardar el identificador de la VPC.
    vpc_id = vpcs[0]["VpcId"]

    # ============================================================
    # I2-C: BUSCAR UN SECURITY GROUP EXISTENTE
    # ============================================================

    # Construimos un nombre estable para nuestro grupo.
    sg_name = f"fenix-{group}-sg"

    # Buscamos el Security Group dentro de nuestra VPC.
    respuesta_sg = ec2.describe_security_groups(
        Filters=[
            {
                "Name": "group-name",
                "Values": [sg_name],
            },
            {
                "Name": "vpc-id",
                "Values": [vpc_id],
            },
        ]
    )

    # Obtenemos los grupos encontrados.
    security_groups = respuesta_sg.get("SecurityGroups", [])

    # Si existe, conservamos su información para verificar
    # posteriormente que pertenece a nuestro laboratorio.
    # ============================================================
    # I2-C: CREAR O REUTILIZAR EL SECURITY GROUP
    # ============================================================

    if security_groups:

        # Recuperamos el Security Group existente.
        sg = security_groups[0]
        sg_id = sg["GroupId"]

        # Convertimos sus etiquetas en un diccionario.
        tags = {
            tag["Key"]: tag["Value"]
            for tag in sg.get("Tags", [])
        }

        # Verificamos que pertenezca a nuestro laboratorio.
        if (
            tags.get("ManagedBy") != "ppa2"
            or tags.get("GroupId") != group
        ):
            raise RuntimeError(
                f"El Security Group {sg_name} existe, "
                "pero no pertenece a este laboratorio."
            )

    else:

        # Creamos el Security Group dentro de nuestra VPC.
        respuesta_creacion = ec2.create_security_group(
            GroupName=sg_name,
            Description=f"Security Group de Fenix para {group}",
            VpcId=vpc_id,
        )

        # Guardamos su identificador.
        sg_id = respuesta_creacion["GroupId"]

        # Etiquetamos el recurso para identificar su propietario.
        ec2.create_tags(
            Resources=[sg_id],
            Tags=[
                {"Key": "ManagedBy", "Value": "ppa2"},
                {"Key": "GroupId", "Value": group},
            ],
        )
    # ============================================================
    # I2-C: OBTENER UNA IMAGEN EC2 DISPONIBLE
    # ============================================================

    # Consultamos las imágenes disponibles en LocalStack.
    respuesta_imagenes = ec2.describe_images()

    # Recuperamos la lista de imágenes.
    imagenes = respuesta_imagenes.get("Images", [])

    # Verificamos que exista al menos una imagen.
    if not imagenes:
        raise RuntimeError(
            "No se encontraron imágenes EC2 disponibles en LocalStack."
        )

    # Seleccionamos una imagen de manera determinista.
    imagenes.sort(key=lambda imagen: imagen["ImageId"])

    # Guardamos el identificador para crear nuestra instancia.
    image_id = imagenes[0]["ImageId"]

    # ============================================================
    # I2-C: BUSCAR UNA INSTANCIA EC2 EXISTENTE
    # ============================================================

    # Nombre estable para identificar nuestra instancia.
    instance_name = f"fenix-{group}-node"

    # Buscamos instancias que pertenezcan a nuestro proyecto.
    respuesta_instancias = ec2.describe_instances(
        Filters=[
            {
                "Name": "tag:Project",
                "Values": ["fenix"],
            },
            {
                "Name": "tag:GroupId",
                "Values": [group],
            },
            {
                "Name": "tag:Name",
                "Values": [instance_name],
            },
            {
                "Name": "instance-state-name",
                "Values": [
                    "pending",
                    "running",
                    "stopping",
                    "stopped",
                ],
            },
        ]
    )

    # Extraemos las instancias de las reservas encontradas.
    instancias = [
        instancia
        for reserva in respuesta_instancias.get("Reservations", [])
        for instancia in reserva.get("Instances", [])
    ]

    # ============================================================
    # I2-C: CREAR O REUTILIZAR LA INSTANCIA EC2
    # ============================================================

    if len(instancias) > 1:
        # No elegimos arbitrariamente entre recursos duplicados.
        raise RuntimeError(
            "Se encontraron varias instancias para el mismo grupo."
        )

    if instancias:

        # Recuperamos la instancia encontrada.
        instancia = instancias[0]
        instance_id = instancia["InstanceId"]

        # Verificamos las etiquetas de propiedad.
        tags = {
            tag["Key"]: tag["Value"]
            for tag in instancia.get("Tags", [])
        }

        if (
            tags.get("ManagedBy") != "ppa2"
            or tags.get("Project") != "fenix"
            or tags.get("GroupId") != group
            or tags.get("Name") != instance_name
        ):
            raise RuntimeError(
                "La instancia existente no pertenece al laboratorio."
            )

    else:

        # Creamos una instancia de laboratorio en LocalStack.
        respuesta_creacion = ec2.run_instances(
            ImageId=image_id,
            InstanceType="t3.micro",
            MinCount=1,
            MaxCount=1,
            SecurityGroupIds=[sg_id],

            # Identificador estable para evitar creaciones duplicadas.
            ClientToken=f"fenix-{group}-node",

            # Asignamos las etiquetas al crear el recurso.
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "ManagedBy", "Value": "ppa2"},
                        {"Key": "Project", "Value": "fenix"},
                        {"Key": "GroupId", "Value": group},
                        {"Key": "Name", "Value": instance_name},
                    ],
                }
            ],
        )

        # Guardamos el identificador de la instancia creada.
        instance_id = respuesta_creacion["Instances"][0]["InstanceId"]

    # ============================================================
    # I2-C: BUSCAR UN BUCKET S3 EXISTENTE
    # ============================================================

    # Creamos el cliente S3 conectado a LocalStack.
    s3 = client("s3")

    # Construimos el nombre estable del bucket.
    bucket_name = f"fenix-{group}-demo"

    # Consultamos los buckets disponibles.
    respuesta_buckets = s3.list_buckets()

    # Extraemos sus nombres.
    nombres_buckets = [
        bucket["Name"]
        for bucket in respuesta_buckets.get("Buckets", [])
    ]

    # Comprobamos si el bucket ya existe.
    bucket_existe = bucket_name in nombres_buckets


    # ============================================================
    # I2-C: CREAR O REUTILIZAR EL BUCKET S3
    # ============================================================
        # ============================================================
    # I2-C: ESTADO PERSISTENTE DE CREACION S3
    # ============================================================

    # Ruta local para registrar una creación incompleta.
    # El directorio data/ está excluido de Git.
    pending_path = "data/demo-pending.json"

    # Recuperamos el registro anterior, si existe.
    pending = load(pending_path)

    # Las etiquetas que identifican los recursos del laboratorio.
    etiquetas_esperadas = {
        "ManagedBy": "ppa2",
        "Project": "fenix",
        "GroupId": group,
        "Owner": group,
    }

    # Un registro pendiente solo identifica una operación previa.
    # No constituye por sí mismo una prueba de propiedad.
    if pending and (
        pending.get("bucket") != bucket_name
        or pending.get("group") != group
        or pending.get("status") not in ("intent", "created")
    ):
        raise RuntimeError(
            "Existe un registro S3 pendiente incompatible. "
            "Se requiere revisión antes de continuar."
        )
    if bucket_existe:

        # El bucket ya existe en LocalStack.
        # Consultamos sus etiquetas para verificar la propiedad.
        try:
            respuesta_tags = s3.get_bucket_tagging(
                Bucket=bucket_name
            )

            tags = {
                tag["Key"]: tag["Value"]
                for tag in respuesta_tags.get("TagSet", [])
            }

        except ClientError as error:

            codigo = error.response["Error"]["Code"]

            # NoSuchTagSet significa que el bucket existe,
            # pero todavía no tiene etiquetas.
            if codigo != "NoSuchTagSet":
                raise

            # Un registro de intención no confirma creación.
            # Solo consideramos la recuperación cuando
            # create_bucket() terminó y registramos ese resultado.
            if not (
                pending
                and pending.get("status") == "created"
                and pending.get("bucket") == bucket_name
                and pending.get("group") == group
            ):
                raise RuntimeError(
                    f"El bucket {bucket_name} no tiene etiquetas "
                    "y no existe una creación confirmada. "
                    "Se requiere revisión manual."
                ) from error

            # IMPORTANTE:
            # El registro local no acredita por sí solo
            # la propiedad del recurso. Antes de etiquetarlo,
            # debemos verificar su procedencia.
            raise RuntimeError(
                f"El bucket {bucket_name} tiene una creación "
                "pendiente registrada, pero falta verificar "
                "su propiedad antes de recuperar las etiquetas."
            ) from error

        # Reutilizamos únicamente buckets correctamente
        # identificados como recursos del laboratorio.
        if (
            tags.get("ManagedBy") != "ppa2"
            or tags.get("Project") != "fenix"
            or tags.get("GroupId") != group
            or tags.get("Owner") != group
        ):
            raise RuntimeError(
                f"El bucket {bucket_name} existe, "
                "pero no pertenece a este laboratorio."
            )

    else:

        # Creamos el bucket en LocalStack.
        # Para us-east-1 no necesitamos LocationConstraint.
                # Registramos la intención antes de crear el bucket.
        # Si el proceso se interrumpe, sabremos que existió
        # un intento, pero no asumiremos que fue exitoso.
        save(
            pending_path,
            {
                "bucket": bucket_name,
                "group": group,
                "status": "intent",
            },
        )

        # Creamos el bucket en LocalStack.
        # Esta operación y el etiquetado son independientes.
        s3.create_bucket(
            Bucket=bucket_name
        )

        # La API confirmó la creación.
        # Guardamos esta información antes de etiquetar.
        save(
            pending_path,
            {
                "bucket": bucket_name,
                "group": group,
                "status": "created",
            },
        )

        # Identificamos el propietario y el proyecto.
        # ============================================================
        # I2-C: RECUPERACION DE FALLOS TRANSITORIOS DE S3
        # ============================================================

        # Definimos las etiquetas que identifican al laboratorio.
        etiquetas_esperadas = {
            "ManagedBy": "ppa2",
            "Project": "fenix",
            "GroupId": group,
            "Owner": group,
        }

        # Convertimos las etiquetas al formato solicitado por S3.
        tagging = {
            "TagSet": [
                {"Key": clave, "Value": valor}
                for clave, valor in etiquetas_esperadas.items()
            ]
        }

        # Reintentamos solamente cuando S3 informa un error
        # interno transitorio y el bucket acaba de ser creado.
        max_intentos = 2

        for intento in range(1, max_intentos + 1):
            try:
                s3.put_bucket_tagging(
                    Bucket=bucket_name,
                    Tagging=tagging,
                )

                # Etiquetado exitoso: finalizamos los intentos.
                break

            except ClientError as error:
                codigo = error.response["Error"]["Code"]

                # No reintentamos errores de otro tipo.
                if codigo != "InternalError":
                    raise

                # Si falló el último intento, propagamos el error.
                if intento == max_intentos:
                    raise


    # ============================================================
    # I2-C: PREPARAR EL INVENTARIO DEL LABORATORIO
    # ============================================================

    # Asociamos cada alias con el recurso real de LocalStack.
    # Los alias permiten trabajar sin introducir IDs arbitrarios.
    inventario = {
        "node-01": {
            "type": "ec2",
            "id": instance_id,
            "group": group,
            "image_id": image_id,
        },
        "bucket-01": {
            "type": "s3",
            "id": bucket_name,
            "group": group,
        },
    }

    # ============================================================
    # I2-C: VERIFICAR LA PROPIEDAD DE LOS RECURSOS
    # ============================================================

    # Consultamos nuevamente la instancia EC2.
    respuesta_ec2 = ec2.describe_instances(
        InstanceIds=[instance_id]
    )

    instancia_verificada = (
        respuesta_ec2["Reservations"][0]["Instances"][0]
    )

    # Convertimos las etiquetas EC2 en un diccionario.
    tags_ec2 = {
        tag["Key"]: tag["Value"]
        for tag in instancia_verificada.get("Tags", [])
    }

    # Comprobamos que la instancia pertenezca al laboratorio.
    if (
        tags_ec2.get("ManagedBy") != "ppa2"
        or tags_ec2.get("Project") != "fenix"
        or tags_ec2.get("GroupId") != group
        or tags_ec2.get("Name") != instance_name
    ):
        raise RuntimeError(
            "No se pudo verificar la propiedad de la instancia EC2."
        )

    # Consultamos nuevamente las etiquetas del bucket S3.
    respuesta_s3 = s3.get_bucket_tagging(
        Bucket=bucket_name
    )

    tags_s3 = {
        tag["Key"]: tag["Value"]
        for tag in respuesta_s3.get("TagSet", [])
    }

    # Comprobamos que el bucket pertenezca al laboratorio.
    if (
        tags_s3.get("ManagedBy") != "ppa2"
        or tags_s3.get("Project") != "fenix"
        or tags_s3.get("GroupId") != group
        or tags_s3.get("Owner") != group
    ):
        raise RuntimeError(
            "No se pudo verificar la propiedad del bucket S3."
        )

    # ============================================================
    # I2-C: GUARDAR EL INVENTARIO DEL LABORATORIO
    # ============================================================

    # Guardamos únicamente los recursos cuya propiedad
    # fue verificada correctamente en LocalStack.
        # Guardamos únicamente los recursos cuya propiedad
    # fue verificada correctamente en LocalStack.
    save("data/inventory.json", inventario)

    # ============================================================
    # I2-C: FINALIZAR LA OPERACION PENDIENTE
    # ============================================================

    # Llegamos aquí únicamente después de verificar la propiedad
    # de EC2 y S3 y guardar correctamente el inventario.
    #
    # Por lo tanto, ya no existe una operación de creación
    # pendiente y podemos limpiar su registro.
    #
    # Si ocurre un error antes de este punto, conservamos
    # el registro para no perder información del fallo.
    save(pending_path, {})

    # Devolvemos el inventario para que otros componentes
    # del proyecto puedan utilizarlo.
    return inventario

def inject_fault(alias: str) -> None:
    """Detiene el nodo o elimina una etiqueta del bucket de laboratorio.

    Args:
        alias: node-01 o bucket-01 presente en inventory.json.
    Returns:
        None cuando el fallo simulado se verifica mediante el SDK.
    Raises:
        ValidationError: Si el alias no pertenece al inventario.
        NotImplementedError: Mientras falte TODO I6-A.
    Side Effects:
        Modifica únicamente el recurso de prueba de ese grupo.
    """
    # TODO I6-A: resolver el alias en inventario; jamás recibir InstanceId libre.
    # node-01: stop_instances y comprobar stopped, con consultas limitadas.
    # bucket-01: quitar Owner conservando el resto de etiquetas.
    # Esta función fabrica el problema; no debe enviar el evento automáticamente.
    raise NotImplementedError("I6-A: inyectar y comprobar fallo")
