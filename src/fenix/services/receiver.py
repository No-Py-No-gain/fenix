"""Receptor FastAPI: autenticación, validación y aceptación durable por SQS."""
import atexit  # Apaga el hilo de registro cuando termina el proceso.
import os  # Recupera el secreto sin incluirlo en el repositorio.
import json  # Decodifica después de verificar los bytes.
from fastapi import FastAPI, Request, HTTPException  # Interfaz HTTP del curso.
from starlette.concurrency import run_in_threadpool  # Evita bloquear el event loop.
from botocore.exceptions import BotoCoreError, ClientError
from fenix.utils.crypto import verify
from fenix.utils.errors import ValidationError
from fenix.utils.logging_json import setup
from fenix.utils.state import load
from fenix.services.events import validate
from fenix.services.sqs import queue_url, publish

app = FastAPI(title="Fénix receptor de alertas")  # Un servidor, un worker Uvicorn.

_registro = None  # Pipeline de registro de este proceso; se crea en el primer uso.


def _logger():
    """Devuelve el logger del proceso, creándolo una sola vez.

    Returns:
        Logger con el pipeline JSON y su QueueListener ya iniciado.
    Notes:
        Se inicializa de forma perezosa para no abrir archivos ni hilos al importar
        el módulo, que también ocurre en las pruebas. Cada proceso escribe su propio
        archivo, así que un receptor por proceso.
    """
    global _registro
    if _registro is None:
        _registro = setup("fenix-receptor")
        atexit.register(_registro[1].stop)  # Drena la cola al salir.
    return _registro[0]


def _identificador(cuerpo) -> str | None:
    """Extrae el event_id sólo si es un texto, para no publicar datos arbitrarios.

    Args:
        cuerpo: Contenido ya deserializado del cuerpo HTTP.
    Returns:
        El event_id recibido, o None si no hay uno publicable.
    """
    if isinstance(cuerpo, dict):
        candidato = cuerpo.get("event_id")
        if isinstance(candidato, str):
            return candidato
    return None


@app.post("/webhook", status_code=202)
async def webhook(request: Request) -> dict:
    """Recibe un evento firmado y contesta 202 solo después de encolarlo.

    Args:
        request: Petición con X-Webhook-Signature y cuerpo de hasta 16 KiB.
    Returns:
        JSON con status=accepted, event_id y message_id de SQS.
    Raises:
        HTTPException: 401 firma; 400 JSON; 413 tamaño; 422 contrato; 503 SQS.
    Side Effects:
        Publica en SQS; no repara recursos ni llama a Telegram/Slack aquí.
    Notes:
        Leer bytes completos basta para el laboratorio; un proxy limitaría antes
        el tamaño de la transferencia en un despliegue real.
        El orden importa: tamaño, autenticación, lectura, contrato y recién ahí SQS.
        El secreto ausente es una falla de configuración del servidor (503), no del
        cliente: no se responde 401 por un problema que el emisor no puede corregir.
    """
    raw = await request.body()  # Se conservan los bytes originales.
    registro = _logger()  # Cada paso queda en el registro con su contexto público.
    if len(raw) > 16384:  # Mantiene acotados los ejemplos didácticos.
        registro.warning("cuerpo_demasiado_grande",
                         extra={"status": 413, "action": "reject"})
        raise HTTPException(413, "Cuerpo demasiado grande")  # No encola.
    secret = os.environ.get("WEBHOOK_SECRET", "")  # Nunca se registra ni se repite.
    if len(secret) < 16:  # Misma exigencia que la firma: falla cerrado y temprano.
        registro.error("receptor_sin_configurar",
                       extra={"status": 503, "action": "reject"})
        raise HTTPException(503, "Receptor sin configurar")  # No promete aceptación.
    try:
        verify(secret, request.headers.get("X-Webhook-Signature", ""), raw)  # Bytes.
    except ValidationError as error:  # Formato, firma o ventana: misma respuesta.
        registro.warning("firma_invalida",
                         extra={"status": 401, "action": "reject"})
        raise HTTPException(401, "Firma inválida") from error  # Sin detalle interno.
    try:
        cuerpo = json.loads(raw)  # Recién ahora se interpreta el contenido.
    except (json.JSONDecodeError, UnicodeDecodeError) as error:  # Roto o no UTF-8.
        registro.warning("json_invalido",
                         extra={"status": 400, "action": "reject"})
        raise HTTPException(400, "JSON inválido") from error
    try:  # El disco y Boto3 bloquean: se ejecutan fuera del bucle de eventos.
        inventario = await run_in_threadpool(load, "data/inventory.json")
        evento = validate(cuerpo, inventario)  # Función pura: solo valida el contrato.
        url = await run_in_threadpool(queue_url, os.environ["QUEUE_PREFIX"])
        message_id = await run_in_threadpool(publish, url, evento)  # send_message real.
    except ValidationError as error:  # JSON válido con contrato incorrecto.
        registro.warning("evento_invalido", extra={
            "status": 422, "action": "reject", "event_id": _identificador(cuerpo)})
        raise HTTPException(422, "Evento inválido") from error
    except KeyError as error:  # Falta QUEUE_PREFIX: configuración, no culpa del emisor.
        registro.error("receptor_sin_configurar", extra={
            "status": 503, "action": "reject", "event_id": _identificador(cuerpo)})
        raise HTTPException(503, "Receptor sin configurar") from error
    except (ClientError, BotoCoreError) as error:  # SQS caído o credenciales rechazadas.
        registro.error("mensajeria_no_disponible", extra={
            "status": 503, "action": "publish", "event_id": _identificador(cuerpo)})
        raise HTTPException(503, "Mensajería no disponible") from error
    registro.info("evento_aceptado", extra={  # Sólo después de la confirmación de SQS.
        "status": 202, "action": "publish", "event_id": evento["event_id"],
        "message_id": message_id, "resource_id": evento["resource_id"]})
    return {"status": "accepted", "event_id": evento["event_id"],  # Solo con ACK de SQS.
            "message_id": message_id}  # Identificador del mensaje, no del recurso.
