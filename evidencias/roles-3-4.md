# Evidencias de los roles I3 e I4 — Proyecto Fénix

Registro reproducible de lo que se ejecutó, con el resultado observado. Cada fila de la matriz
indica el comando o el test que la produce. Nada de esta tabla se completó sin haberlo corrido.

Fecha de la corrida: 2026-11-21.

## Entorno utilizado

| Elemento | Versión observada |
| --- | --- |
| Python (host) | 3.14.4 — misma versión que el TP-2 Vulcano, fijada para todo el grupo |
| LocalStack | `localstack/localstack:4` → 4.14.0, edición community |
| Servicios habilitados | `sqs`, `s3`, `ec2` con `EC2_VM_MANAGER=mock` |
| Endpoint | `http://localhost:4566` (el guardián de `settings.py` rechaza cualquier otro) |
| Dependencias | congeladas en `requirements-lock.txt` |

La imagen `localstack/localstack:2026.10.0` **no sirve sin licencia**: aborta con código 55 y el
mensaje "No credentials were found in the environment. Please set the LOCALSTACK_AUTH_TOKEN".
Se usó la etiqueta `4`, que corre sin token en la edición community.

## Matriz de pruebas

| ID | Caso | Resultado esperado | Resultado observado | Evidencia | Responsable |
| --- | --- | --- | --- | --- | --- |
| F03 | Bucket sin `Owner`, con una etiqueta ajena | Tags obligatorias restauradas y la ajena preservada | `repaired`; `Owner=grupo01` agregada, `Ajeno=conservar` intacta | `tests/test_remediation.py::test_f03_preserva_etiquetas_ajenas` | I3 |
| F04 | Firma ausente, alterada, duplicada, falsa o de hace una hora | `401` y ningún mensaje publicado | `401 Firma inválida` en los cinco casos, `publish` no invocado | HTTP real + `test_receiver.py` | I3 |
| F05 | Mismos datos JSON con bytes distintos tras firmar | `401` por HMAC inválida | `401 Firma inválida` | HTTP real + `test_crypto.py::test_f05_*` | I3 |
| F06 | JSON roto y UTF-8 inválido con firma válida | `400`, sin publicar | `400 JSON inválido` en ambos | HTTP real | I3 |
| F06 | JSON válido con contrato incorrecto | `422`, sin publicar | `422` con la validación de I1 simulada | `test_receiver.py::test_esquema_invalido_devuelve_422_sin_publicar` | I3 |
| F13 | SQS no disponible al recibir el webhook | `503`, sin aceptación falsa | `503` con `ClientError` y con `BotoCoreError` | `test_receiver.py::test_f13_*` | I3 |
| F14 | Aviso con 429, timeout y luego éxito | Aviso transitorio acotado, sin filtar el token | `RetryableError` con `reintentar_despues=30`, sin token en el mensaje | `test_notifications.py::test_f14_*` | I4 |
| F16 | Forzar rotación, abrir el `.gz` y parsear cada línea | JSON válido, UTC y contexto completo | 40 líneas íntegras entre activo e históricos, sin `.tmp` huérfanos | `tests/test_logging_json.py::test_f16_rotacion_comprimida_es_legible` | I4 |
| F02 | Evento válido de punta a punta | `202` con `event_id` y `message_id` | **Pendiente**: requiere `events.validate` de I1 | — | I3 |
| F08 | Mensaje directo a SQS con JSON corrupto | El worker sigue y el mensaje va a la DLQ | **Pendiente**: requiere el worker de I5 | — | I5 |
| F10 | Falla persistente con `simulate_failures=5` | Sin ACK, derivación automática a la DLQ | **Pendiente**: requiere el worker de I5 | — | I5 |
| F12 | Corte después de `repair_done` y antes de `notify` | El reinicio recupera sólo el aviso pendiente | **Pendiente**: requiere `remediation.handle` de I5 | — | I5 |

## Evidencia 1 — respuestas HTTP reales

Servidor: `python -m uvicorn fenix.services.receiver:app --host 127.0.0.1 --port 8002 --workers 1`,
un solo proceso, `WEBHOOK_SECRET` de 43 caracteres exportado en el entorno de la corrida.
Los cuerpos se firmaron con `fenix.utils.crypto.signature`, el mismo firmador de la CLI.

```
F04 firma ausente                    -> HTTP 401  {"detail":"Firma inválida"}
F05 mismos datos, otros bytes        -> HTTP 401  {"detail":"Firma inválida"}
F04 timestamp de hace una hora       -> HTTP 401  {"detail":"Firma inválida"}
F04 clave duplicada                  -> HTTP 401  {"detail":"Firma inválida"}
F04 firma bien formada y falsa       -> HTTP 401  {"detail":"Firma inválida"}
F06 JSON roto, firma válida          -> HTTP 400  {"detail":"JSON inválido"}
F06 UTF-8 inválido, firma válida     -> HTTP 400  {"detail":"JSON inválido"}
Límite 16 KiB, cuerpo de 20 KiB      -> HTTP 413  {"detail":"Cuerpo demasiado grande"}
```

El servidor registró las 8 peticiones (`POST /webhook`), así que las respuestas provienen del
proceso real y no de un cliente simulado. Todos los rechazos comparten el mismo mensaje: el error
no sirve para distinguir una firma equivocada de una fecha vencida.

## Evidencia 2 — suite automatizada

```
$ python -m pytest -q
96 passed, 6 failed

$ python -m pytest tests/test_crypto.py tests/test_receiver.py \
    tests/test_remediation.py tests/test_notifications.py tests/test_logging_json.py -q
91 passed
```

Las 6 fallas pertenecen a `tests/test_contracts.py` (validación de I1) y a
`tests/test_scenarios.py` (worker de I5). Ninguna corresponde a nuestros módulos.

## Evidencia 3 — log JSON del pipeline real

```
$ python -m fenix.cli worker --once
Revisar configuración o TODO pendiente: QueueDoesNotExist
exit=1
```

```json
{"timestamp": "2026-11-21T15:23:16.161443+00:00", "level": "ERROR", "message": "fallo_controlado",
 "process": 350106, "thread": "MainThread", "event_id": null, "message_id": null,
 "resource_id": null, "attempt": null, "action": null, "status": "QueueDoesNotExist"}
```

Muestra la marca temporal en UTC con offset, los seis campos de contexto presentes y en `null`
donde no aplican (§4.2.6) y ningún secreto. El código de salida 1 es el cierre controlado del
contrato, no un éxito disimulado.

## Evidencia 4 — servicios del emulador verificados

```
$ curl -s http://localhost:4566/_localstack/health
"ec2": "available", "s3": "available", "sqs": "available"   edition: community, version: 4.14.0

$ python -c "... describe_images / describe_vpcs / create_bucket ..."
ec2 imágenes mock: 1159        ec2 vpcs: ['vpc-3b6bae7b196d5eca0']
s3 bucket de prueba creado y borrado en la misma corrida
```

El bucket de comprobación se eliminó en la misma corrida para no dejar recursos fuera del
inventario. No se creó ninguna instancia ni cola: eso corresponde a `setup-demo` e `init-queues`
de I2, que todavía no están implementados.

## Pendientes, con su motivo

1. **Aviso real al canal elegido.** El `chat_id` recibido (`9139765941`) es el identificador del
   bot `MyAppFenix0101Bot`, no el chat de una persona: Telegram responde
   `403 Forbidden: the bot can't send messages to the bot`. Falta que un integrante abra el bot en
   Telegram y le envíe `/start`; después el `chat_id` real se obtiene con `getUpdates` y se guarda
   en el `.env`. El adaptador ya está implementado y probado; lo único que falta es la credencial.
2. **`WEBHOOK_SECRET` ausente del entorno del grupo.** El receptor falla cerrado con `503` si no
   está. Para esta evidencia se exportó un secreto de fixture en el proceso; el entregable necesita
   el propio, generado con `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
3. **F02, F08, F10 y F12** dependen de módulos de I1, I2 e I5 todavía sin implementar.

## Cómo reproducir

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -e .
python -m pytest tests/test_crypto.py tests/test_receiver.py \
    tests/test_remediation.py tests/test_notifications.py tests/test_logging_json.py -q

# Respuestas HTTP reales: exportar WEBHOOK_SECRET antes de iniciar el receptor.
python -m uvicorn fenix.services.receiver:app --host 127.0.0.1 --port 8002 --workers 1
```

Ninguna prueba de este documento toca recursos compartidos: los clientes Boto3 se sustituyen por
dobles con `monkeypatch` en los tests, y la única corrida contra el emulador borra lo que crea.
