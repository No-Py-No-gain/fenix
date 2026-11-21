"""Apoyo reutilizable de Tritón y Vulcano; integrante 4 debe verificarlo.

Notas de la revisión de I4 (verificadas sobre la stdlib del intérprete del grupo):
    - `QueueHandler.prepare()` formatea el registro en el hilo que loguea y anula
      `exc_info`, `exc_text` y `stack_info` antes de encolarlo. Como el QueueHandler
      del pipeline no lleva formateador, ese formateo es el del Formatter por
      defecto (`%(message)s`): el costo de serializar JSON sigue en el hilo del
      listener, que es el único que escribe el archivo.
    - Consecuencia directa: el archivo nunca contiene el árbol de la excepción. En
      este worker secuencial la cátedra no lo exige; si se necesitara, debe
      agregarse explícitamente y sanitizado ANTES de encolar, no después.
    - `QueueListener.stop()` drena la cola pero no cierra los manejadores: quien
      necesite leer el archivo inmediatamente debe cerrarlos por su cuenta.
    - El grupo fija una única versión de Python para todos: el comportamiento de
      `prepare()` cambia entre versiones y la revisión se hizo sobre 3.14.
"""
import gzip  # Comprime los históricos rotados.
import json  # Emite una estructura por línea.
import logging  # Usa la biblioteca estándar de observabilidad.
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
import queue  # Desacopla productores y escritura en un hilo del mismo proceso.
import shutil  # Copia el archivo al compresor.
import os  # Elimina el archivo original después de comprimirlo.
from datetime import datetime, timezone  # Emite marcas UTC.
from pathlib import Path  # Crea la carpeta de logs.

class JSONFormatter(logging.Formatter):
    """Serializa campos permitidos; no copia indiscriminadamente record.__dict__.

    Notes:
        Cada proceso usa su propio archivo para evitar rotaciones concurrentes.
    """
    def format(self, record: logging.LogRecord) -> str:
        """Convierte un registro en JSON compatible con json.loads.

        Args:
            record: Registro del logger con extras públicos opcionales.
        Returns:
            Una línea JSON con fecha UTC, nivel, evento y contexto autorizado.
        Raises:
            TypeError: Si un extra no es serializable; usar escalares públicos.
        """
        data = {"timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                "level": record.levelname, "message": record.getMessage(),
                "process": record.process, "thread": record.threadName}  # Campos base.
        for name in ("event_id", "message_id", "resource_id", "attempt", "action", "status"):
            data[name] = getattr(record, name, None)  # Lista cerrada de extras.
        return json.dumps(data, ensure_ascii=False)  # El mensaje no contiene secretos.


def gzip_name(name: str) -> str:
    """Calcula el nombre comprimido de un histórico.

    Args:
        name: Nombre propuesto por RotatingFileHandler.
    Returns:
        El mismo nombre con sufijo .gz.
    """
    return name + ".gz"  # Permite rotaciones y backups automáticos.


def gzip_rotate(source: str, dest: str) -> None:
    """Comprime y luego elimina el histórico original, sin publicar un .gz truncado.

    Args:
        source: Archivo cerrado por el manejador.
        dest: Ruta del archivo comprimido.
    Returns:
        None.
    Raises:
        OSError: Si falla la lectura, compresión, reemplazo o eliminación.
    Notes:
        Escribe primero en `<dest>.tmp` y publica con os.replace: un corte a mitad
        de la compresión deja el temporal, nunca un .gz incompleto que parezca
        válido. El original se borra recién después del reemplazo exitoso.
    """
    temporal = dest + ".tmp"  # Nadie lee este nombre: es reemplazado de una vez.
    try:
        with open(source, "rb") as origin, gzip.open(temporal, "wb") as target:
            shutil.copyfileobj(origin, target)  # Procesa el contenido por bloques.
        os.replace(temporal, dest)  # Publicación atómica del histórico.
    except OSError:  # Se retira el temporal para no acumular basura entre rotaciones.
        if os.path.exists(temporal):
            os.remove(temporal)
        raise
    os.remove(source)  # Solo se borra después del cierre exitoso del gzip.


def setup(name: str, *, max_bytes: int = 2 * 1024 * 1024,
          backup_count: int = 3) -> tuple[logging.Logger, QueueListener]:
    """Inicia el pipeline JSON en un archivo exclusivo de este proceso.

    Args:
        name: Nombre público del componente, por ejemplo fenix-worker.
        max_bytes: Tamaño que dispara la rotación; los valores por defecto son los
            del apoyo entregado y no cambian el comportamiento existente.
        backup_count: Cantidad de históricos comprimidos que se conservan.
    Returns:
        Logger y listener; el llamador debe ejecutar listener.stop en finally.
    Raises:
        OSError: Si no se puede crear logs/ o abrir el archivo.
    Side Effects:
        Inicia un hilo y abre un manejador rotativo; llamar una vez por proceso.
    Notes:
        Los dos parámetros son keyword-only y aditivos: existen para poder forzar la
        rotación en una prueba sin fabricar megabytes de registro.
    """
    Path("logs").mkdir(exist_ok=True)  # Prepara una ruta local ignorada por Git.
    sink = RotatingFileHandler(f"logs/{name}-{os.getpid()}.jsonl", maxBytes=max_bytes,
                               backupCount=backup_count, encoding="utf-8")  # Acotado.
    sink.setFormatter(JSONFormatter())  # Toda línea mantiene el mismo contrato.
    sink.namer, sink.rotator = gzip_name, gzip_rotate  # Asocia callbacks.
    records = queue.Queue()  # Esta cola en memoria NO sustituye a SQS.
    logger = logging.getLogger(name)  # Mantiene un nombre por componente.
    logger.setLevel(logging.DEBUG)  # Permite demostrar los cinco niveles.
    logger.handlers.clear()  # Evita duplicados si se reinicializa por una prueba.
    logger.propagate = False  # No replica mensajes en el logger raíz.
    logger.addHandler(QueueHandler(records))  # El productor no escribe en disco.
    listener = QueueListener(records, sink)  # Solo este hilo escribe el archivo.
    listener.start()  # Empieza el consumo de registros.
    return logger, listener  # El cierre pertenece al llamador.
