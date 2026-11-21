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
| — | Aviso real al canal elegido (§5.3.1) | Envío confirmado por el proveedor | `ok=true` y mensaje recibido en el chat privado | `notify()` con `NOTIFY_PROVIDER=telegram` | I4 |
| F02 | Evento válido de punta a punta | `202` con `event_id` y `message_id` | `202` con ambos identificadores y el mensaje en la cola real | Evidencia 6 | I3 |
| F08 | Mensaje directo a SQS con JSON corrupto | El worker sigue y el mensaje va a la DLQ | **Pendiente**: requiere el worker de I5 | — | I5 |
| F10 | Falla persistente con `simulate_failures=5` | Sin ACK, derivación automática a la DLQ | **Pendiente**: requiere el worker de I5 | — | I5 |
| F12 | Corte después de `repair_done` y antes de `notify` | El reinicio recupera sólo el aviso pendiente | **Pendiente**: requiere `remediation.handle` de I5 | — | I5 |

## Evidencia 1 — respuestas HTTP reales

Servidor: `python -m uvicorn fenix.services.receiver:app --host 127.0.0.1 --port 8002 --workers 1`,
un solo proceso, con el `WEBHOOK_SECRET` exportado en el entorno de la corrida. Los cuerpos se
firmaron con `fenix.utils.crypto.signature`, el mismo firmador de la CLI.

La corrida se repitió dos veces: primero con un secreto de fixture y después con el **secreto real
del grupo**, tomado del archivo de configuración (43 caracteres) y exportado al proceso sin
imprimirlo en ningún paso. El resultado fue idéntico 8 de 8, así que la evidencia es reproducible
con la configuración real y no depende de un valor de prueba.

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

## Evidencia 5 — aviso real al canal elegido

El `chat_id` inicialmente configurado (`9139765941`) era el identificador del bot, no el de una
persona: Telegram respondía `403 Forbidden: the bot can't send messages to the bot`. Un bot de
BotFather no contesta `/start` por sí solo; sólo deja el mensaje como *update* pendiente. Tras
enviar `/start` desde la cuenta del integrante, `getUpdates` devolvió el chat real (privado) y el
envío quedó confirmado:

```
$ NOTIFY_PROVIDER=telegram TELEGRAM_CHAT_ID=<chat real> python -c 'from fenix.services...notify(...)'
RESULTADO: el proveedor confirmo el envio (ok=true)
```

`getWebhookInfo` confirmó que el bot no tiene webhook configurado, que es la condición para que
`getUpdates` entregue los mensajes. Un HTTP 200 no se tomó como prueba: la confirmación es el
`ok=true` del JSON, tal como exige el contrato.

## Pendientes, con su motivo

1. ~~Aviso real al canal elegido~~: **resuelto**, ver la Evidencia 5.
2. ~~`WEBHOOK_SECRET` ausente del entorno del grupo~~: **resuelto**. El secreto está cargado (43
   caracteres, sin espacios ni comillas) y con él se repitió la Evidencia 1 completa. El receptor
   lo lee del entorno, así que al arrancarlo conviene la forma explícita de §5.6,
   `python -m uvicorn fenix.services.receiver:app ... --env-file <archivo>`, o exportarlo antes:
   si se arranca sin ninguna de las dos, el primer request responde 503 porque el secreto se lee
   antes de que cualquier cliente Boto3 cargue el archivo.
3. **F02, F08, F10 y F12** dependen de módulos de I1, I2 e I5 todavía sin implementar.

## Hallazgo de integración — contrato del inventario (§3.3)

Con los módulos de I1 e I2 ya en `main`, `init-queues` y `setup-demo` se ejecutaron sobre el
emulador real (ambas idempotentes: la segunda corrida devolvió las mismas URLs y los mismos IDs).
El inventario generado **no persiste `expected_tags` para ninguno de los dos recursos, ni
`security_group_id` para `node-01`**, aunque §3.3 los exige textualmente:

> El inventario guarda para node-01 type=ec2, id=InstanceId, security_group_id y expected_tags;
> Para bucket-01 type=s3, id=BucketName y expected_tags.

Los recursos en la nube **sí** están etiquetados correctamente (`ManagedBy=ppa2`, `Project=fenix`,
`GroupId=grupo01`; el bucket además con `Owner=grupo01`), pero la instancia EC2 **no tiene la
etiqueta `Owner`**, que §3.3 también exige para ambos.

Efecto observado antes del arreglo: `repair` recibía una política vacía, la comparación daba
verdadera y el bucket quedaba declarado `already_ok` sin haber verificado nada. Eso es declarar
éxito después de un fallo, prohibido por §4.2.2. Corregido del lado de I3: un registro sin
`expected_tags` o sin `id` ahora levanta `PermanentError` en lugar de inventar conformidad.

Queda pendiente del lado de I2 persistir el contrato completo en `data/inventory.json` y agregar
`Owner` a la instancia; sin eso la reparación de etiquetas no puede verificar su estado deseado.

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

## Evidencia 6 — flujo nominal completo: receptor, CLI y cola real

Con `main` ya fusionado (I1-I2, I3 e I4), se ejecutó la secuencia de §5.6 tal cual, sin atajos:

```
$ python -m fenix.cli init-queues
('http://.../fenix-grupo01-events', 'http://.../fenix-grupo01-dlq')
$ python -m fenix.cli init-queues      # segunda vez: mismas URLs, sin duplicados (F01)
('http://.../fenix-grupo01-events', 'http://.../fenix-grupo01-dlq')
$ python -m fenix.cli setup-demo
{'node-01': {'type': 'ec2', 'id': 'i-84d8d65082d8f650d', ...}, 'bucket-01': {...}}
$ python -m uvicorn fenix.services.receiver:app --host 127.0.0.1 --port 8000 --workers 1
$ python -m fenix.cli send examples/node-event.json
202 {"status":"accepted","event_id":"evt-node-001","message_id":"89ca3084-e7d4-48b1-9067-847abfbd704c"}
```

Y el mensaje leído de la cola real con Boto3:

```
cola: fenix-grupo01-events
MessageId: 89ca3084-e7d4-48b1-9067-847abfbd704c
event_id: evt-node-001 | event_type: instance.stopped | resource_id: node-01
Body: {"schema_version": 1, "event_id": "evt-node-001", ...}
```

El `message_id` del 202 y el `MessageId` de la cola son **el mismo**: la respuesta no promete nada
que SQS no haya confirmado. La visibilidad se devolvió a 0 con `change_message_visibility` para que
el worker de I5 pueda consumirlo; no se borró nada.

**Lo que todavía no se demuestra**: el worker. Con `worker.run` y `remediation.handle` pendientes
(I5), el mensaje queda en la cola y F08, F10 y F12 siguen esperando ese módulo.
