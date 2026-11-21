"""Acciones convergentes a completar por integrante 3 y coordinadas por 5."""
from botocore.exceptions import BotoCoreError, ClientError  # Fallos del SDK y de red.
from fenix.config.settings import client
from fenix.utils.state import load, save
from fenix.services.events import validate
from fenix.services.notifications import notify
from fenix.utils.errors import RetryableError, PermanentError

CODIGOS_TRANSITORIOS = frozenset({  # Se reintentan en la próxima recepción SQS.
    "Throttling", "ThrottlingException", "RequestLimitExceeded", "RequestThrottled",
    "SlowDown", "RequestTimeout", "RequestTimeoutException", "ServiceUnavailable",
    "InternalError", "InternalFailure", "PriorRequestNotComplete"})
ESTADOS_TERMINALES = frozenset({"stopping", "shutting-down", "terminated"})  # Sin vuelta.


def _traducir(error: ClientError, contexto: str) -> RetryableError | PermanentError:
    """Clasifica un ClientError por código y estado HTTP, nunca por intuición.

    Args:
        error: Excepción devuelta por Boto3.
        contexto: Nombre público del servicio; nunca incluye URLs ni credenciales.
    Returns:
        RetryableError si la falla puede desaparecer; PermanentError si no.
    """
    respuesta = getattr(error, "response", None) or {}  # Algunos dobles no la traen.
    codigo = respuesta.get("Error", {}).get("Code", "desconocido")  # Código simbólico.
    estado = respuesta.get("ResponseMetadata", {}).get("HTTPStatusCode", 0) or 0
    if codigo in CODIGOS_TRANSITORIOS or estado >= 500:  # 5xx nunca es culpa del evento.
        return RetryableError(f"{contexto} no disponible: {codigo}")  # Se reintenta.
    return PermanentError(f"{contexto} rechazó la operación: {codigo}")  # Mensaje venenoso.


def _estado_instancia(ec2, instance_id: str) -> str:
    """Lee el estado real de la instancia, sin suponerlo desde el evento.

    Args:
        ec2: Cliente EC2 ya construido.
        instance_id: Identificador tomado del inventario, no del mensaje.
    Returns:
        Nombre del estado, por ejemplo running, stopped o pending.
    Raises:
        PermanentError: Si el identificador no corresponde a ninguna instancia.
    """
    respuesta = ec2.describe_instances(InstanceIds=[instance_id])
    instancias = [instancia for reserva in respuesta.get("Reservations") or []
                  for instancia in reserva.get("Instances") or []]
    if not instancias:  # Un ID del inventario sin recurso no se reemplaza por otro.
        raise PermanentError("La instancia del inventario ya no existe en el emulador")
    return instancias[0].get("State", {}).get("Name", "")


def _etiquetas_actuales(s3, bucket: str) -> dict[str, str]:
    """Lee las etiquetas del bucket distinguiendo vacío de error.

    Args:
        s3: Cliente S3 ya construido.
        bucket: Nombre del bucket inventariado.
    Returns:
        Diccionario de etiquetas actuales; vacío solo si el SDK dijo NoSuchTagSet.
    Raises:
        ClientError: Cualquier otro fallo, para que el llamador lo clasifique.
    """
    try:
        respuesta = s3.get_bucket_tagging(Bucket=bucket)
    except ClientError as error:  # Solo este código significa "sin etiquetas".
        if error.response.get("Error", {}).get("Code") == "NoSuchTagSet":
            return {}
        raise  # NoSuchBucket, AccessDenied o throttling no se adivinan: se clasifican.
    return {etiqueta["Key"]: etiqueta["Value"] for etiqueta in respuesta.get("TagSet", [])}


def _reparar_instancia(resource: dict) -> str:
    """Lleva una instancia EC2 mock a running y verifica el resultado.

    Args:
        resource: Registro de inventario con el InstanceId autorizado.
    Returns:
        "repaired" si arrancó o "already_ok" si ya estaba en running.
    Raises:
        RetryableError: Estado intermedio o falla transitoria del emulador.
        PermanentError: Estado terminal o recurso inexistente.
    """
    ec2 = client("ec2")
    instance_id = resource["id"]  # Viene del inventario, nunca del evento.
    estado = _estado_instancia(ec2, instance_id)
    if estado == "running":  # Convergencia: repetir el evento no vuelve a arrancar.
        return "already_ok"
    if estado in ESTADOS_TERMINALES:  # Ni start ni reboot devuelven de aquí.
        raise PermanentError(f"La instancia quedó en estado terminal: {estado}")
    if estado != "stopped":  # pending u otro intermedio: se reintenta, no se espera.
        raise RetryableError(f"La instancia no está lista para arrancar: {estado}")
    ec2.start_instances(InstanceIds=[instance_id])  # Idempotente sólo si el estado lo es.
    estado = _estado_instancia(ec2, instance_id)  # Se verifica, no se supone.
    if estado == "running":
        return "repaired"
    raise RetryableError(f"El arranque no se observó completo: {estado}")


def _reparar_etiquetas(resource: dict) -> str:
    """Restaura las etiquetas obligatorias de un bucket preservando las ajenas.

    Args:
        resource: Registro de inventario con BucketName y expected_tags.
    Returns:
        "repaired" si escribió o "already_ok" si ya cumplía la política.
    Raises:
        RetryableError: Lectura o escritura que no se puede confirmar.
        PermanentError: Bucket inexistente o política rechazada.
    """
    s3 = client("s3")
    bucket = resource["id"]  # Nombre propio del grupo, no un nombre elegido por el emisor.
    esperadas = dict(resource.get("expected_tags") or {})  # Política mínima obligatoria.
    actuales = _etiquetas_actuales(s3, bucket)
    if all(actuales.get(clave) == valor for clave, valor in esperadas.items()):
        return "already_ok"  # Ya converge: no se reescribe ni se notifica de nuevo.
    combinadas = {**actuales, **esperadas}  # Las ajenas sobreviven a la mezcla.
    s3.put_bucket_tagging(Bucket=bucket, Tagging={"TagSet": [
        {"Key": clave, "Value": valor} for clave, valor in combinadas.items()]})
    verificadas = _etiquetas_actuales(s3, bucket)  # Lectura de confirmación.
    if all(verificadas.get(clave) == valor for clave, valor in esperadas.items()):
        return "repaired"
    raise RetryableError("Las etiquetas restauradas no se observan tras la escritura")


def repair(resource: dict, event_type: str) -> str:
    """Acerca un recurso autorizado a su estado deseado y verifica el resultado.

    Args:
        resource: Registro de inventario con type/id y etiquetas esperadas.
        event_type: instance.stopped o bucket.tags_missing ya validado.
    Returns:
        "repaired" si cambió el estado; "already_ok" si ya cumplía la política.
    Raises:
        RetryableError: La API falla temporalmente o el estado final aún no se observa.
        PermanentError: Recurso inexistente, estado terminal o evento incompatible.
    Side Effects:
        Arranca una instancia EC2 mock detenida o restaura tags de un bucket S3.
    Notes:
        No usar reboot_instances: repetir un reinicio no es convergencia.
        Un estado pending requiere otra recepción, sin esperas largas en el worker.
        Solo NoSuchTagSet se lee como "sin etiquetas"; cualquier otro ClientError se
        clasifica por código. Un ClientError capturado a ciegas escondería un bucket
        ajeno detrás de un recurso inventariado.
        La propiedad del recurso la valida I1 contra el inventario antes de llegar
        aquí: esta función sólo recibe registros autorizados.
    """
    tipo = resource.get("type")  # El par evento/recurso no se adivina.
    try:  # Un único punto de traducción de errores del SDK.
        if event_type == "instance.stopped" and tipo == "ec2":
            return _reparar_instancia(resource)
        if event_type == "bucket.tags_missing" and tipo == "s3":
            return _reparar_etiquetas(resource)
    except ClientError as error:  # Throttling, 5xx, permisos y recursos ausentes.
        raise _traducir(error, "La API del emulador") from error
    except BotoCoreError as error:  # Emulador apagado o endpoint inalcanzable.
        raise RetryableError("El emulador no responde") from error
    raise PermanentError("El evento no corresponde al tipo de recurso inventariado")


def handle(event: dict) -> None:
    """Completa reparación y notificación usando un registro durable por evento.

    Args:
        event: Objeto recibido de SQS, que se vuelve a validar aquí.
    Returns:
        None solo cuando reparación y aviso quedaron registrados como exitosos.
    Raises:
        ValidationError: Si el evento fue inyectado directamente a SQS y no es válido.
        RetryableError: Si la simulación o una dependencia temporal falla.
        PermanentError: Conflicto de event_id o recurso no autorizado.
        NotImplementedError: Mientras falte TODO I5-B.
    Side Effects:
        Actualiza recursos, data/events.json y el canal de comunicación.
    Notes:
        Un solo proceso modifica el ledger. Un timeout al notificar puede repetir
        un aviso: no existe una transacción común entre JSON, AWS y Telegram/Slack.
    """
    # TODO I5-B: cargar inventario y validar; cargar ledger por event_id.
    # Vincular event_id al contenido del evento; rechazar reutilización con otro contenido.
    # Guardar attempts incrementado antes de simular fallas; persiste entre reinicios.
    # Si attempts <= simulate_failures, lanzar RetryableError sin modificar recursos.
    # Si repair_done es falso: repair; guardar resultado y repair_done=True.
    # Si notified es falso: notify resumen; guardar notified=True después del éxito.
    # Si ambas etapas ya estaban completas, retornar sin efectos repetidos.
    # Cada save debe preservar el resto del ledger; no sobrescribir otros eventos.
    raise NotImplementedError("I5-B: integrar etapas y ledger durable")
