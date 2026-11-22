"""Pruebas del pipeline de observabilidad: F16 y verificación del logger entregado.

Se escribe siempre en un directorio temporal: ninguna prueba toca logs/ del proyecto.
"""
import gzip  # Abre los históricos rotados como exige la matriz.
import json  # Cada línea debe ser JSON parseable.
import logging  # Construye registros y comprueba los cinco niveles.
import threading  # Verifica en qué hilo ocurre la serialización.
from datetime import datetime, timezone  # Valida la marca temporal UTC.
from pathlib import Path  # Recorre los archivos generados.
import pytest  # Fixtures y aserciones.
from fenix.utils.logging_json import JSONFormatter, setup  # Módulo del rol I4.

FIELDS = ("event_id", "message_id", "resource_id", "attempt", "action", "status")


def log_record(message: str = "hola %s", arguments: tuple = ("mundo",)) -> logging.LogRecord:
    """Construye un LogRecord equivalente al que produce la biblioteca.

    Args:
        message: Plantilla del mensaje.
        arguments: Argumentos de la plantilla.
    Returns:
        Registro listo para formatear.
    """
    return logging.LogRecord("fenix-prueba", logging.INFO, __file__, 1, message,
                             arguments, None)


def close_listener(listener) -> None:
    """Detiene el listener y cierra su manejador, que stop() no cierra.

    Args:
        listener: QueueListener devuelto por setup.
    """
    listener.stop()  # Drena la cola y termina el hilo.
    for handler in listener.handlers:
        handler.close()  # Libera el archivo para poder leerlo.


def lines_of(path: Path) -> list[dict]:
    """Lee y parsea un archivo JSONL.

    Args:
        path: Ruta del archivo activo.
    Returns:
        Lista de registros ya deserializados.
    """
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_context_fields_are_null_when_not_applicable():
    """Criterio de la cátedra: los campos no aplicables se representan como null."""
    data = json.loads(JSONFormatter().format(log_record()))
    assert data["message"] == "hola mundo"  # El mensaje se compone, no se duplica.
    for field in FIELDS:
        assert field in data  # La clave existe siempre...
        assert data[field] is None  # ...aunque no haya contexto aplicable.


def test_public_context_is_preserved():
    """Los extras autorizados llegan al JSON sin abrir la puerta a cualquiera."""
    record = log_record()
    record.event_id = "evt-node-001"
    record.attempt = 2
    record.status = "acknowledged"
    record.private_field = "no debe publicarse"
    data = json.loads(JSONFormatter().format(record))
    assert data["event_id"] == "evt-node-001"
    assert data["attempt"] == 2
    assert data["status"] == "acknowledged"
    assert "private_field" not in data  # La lista de campos es cerrada.


def test_timestamp_is_utc_with_explicit_offset():
    """La marca temporal describe el instante del evento y siempre con zona."""
    data = json.loads(JSONFormatter().format(log_record()))
    assert data["timestamp"].endswith("+00:00")  # UTC explícito, no hora local.
    instant = datetime.fromisoformat(data["timestamp"])
    assert instant.utcoffset() == timezone.utc.utcoffset(None)  # Offset cero.


def test_non_serializable_extra_fails_loudly():
    """El formateador no oculta un extra que no puede publicarse."""
    record = log_record()
    record.event_id = object()  # No es serializable.
    with pytest.raises(TypeError):
        JSONFormatter().format(record)


def test_five_levels_reach_the_file(tmp_path, monkeypatch):
    """Criterio de la cátedra: DEBUG, INFO, WARNING, ERROR y CRITICAL demostrados."""
    monkeypatch.chdir(tmp_path)  # logs/ se crea dentro del temporal.
    logger, listener = setup("fenix-niveles")
    try:
        logger.debug("detalle de depuración")
        logger.info("evento aceptado")
        logger.warning("reintento programado")
        logger.error("intento fallido")
        logger.critical("detención controlada")
    finally:
        close_listener(listener)
    path = next(Path("logs").glob("*.jsonl"))
    assert [d["level"] for d in lines_of(path)] == [
        "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def test_write_happens_on_the_listener_thread(tmp_path, monkeypatch):
    """El productor no escribe en disco: lo hace el hilo de QueueListener."""
    monkeypatch.chdir(tmp_path)
    logger, listener = setup("fenix-hilos")
    sink = listener.handlers[0]
    original = sink.emit
    threads: list[str] = []

    def observe_emit(record):
        """Registra el hilo que efectivamente escribe el archivo."""
        threads.append(threading.current_thread().name)
        original(record)

    try:
        sink.emit = observe_emit  # Se observa el manejador real, sin tocar atributos privados.
        logger.info("observado desde otro hilo")
    finally:
        close_listener(listener)
    assert threads, "el registro tenía que llegar al manejador"
    assert threads[0] != threading.current_thread().name  # No es el hilo que loguea.


def test_repeated_setup_does_not_duplicate_records(tmp_path, monkeypatch):
    """Llamar dos veces al setup deja exactamente una línea por registro."""
    monkeypatch.chdir(tmp_path)
    first, listener_one = setup("fenix-repetido")
    second, listener_two = setup("fenix-repetido")  # Reemplaza el manejador anterior.
    try:
        second.info("una sola vez")
    finally:
        close_listener(listener_two)
        first.handlers.clear()  # Suelta el manejador ya reemplazado.
        close_listener(listener_one)
    path = next(Path("logs").glob("*.jsonl"))
    assert len(lines_of(path)) == 1


def test_f16_compressed_rotation_is_readable(tmp_path, monkeypatch):
    """F16: forzar rotación con tamaño reducido, abrir el .gz y parsear cada línea."""
    monkeypatch.chdir(tmp_path)
    logger, listener = setup("fenix-rotacion", max_bytes=1000, backup_count=100)
    try:
        for number in range(40):
            logger.info("evento %s de la prueba de rotación", number,
                        extra={"event_id": f"evt-{number}", "resource_id": "node-01",
                               "attempt": number, "action": "repair", "status": "done"})
    finally:
        close_listener(listener)
    compressed = sorted(Path("logs").glob("*.gz"))
    assert compressed, "la rotación tenía que producir históricos comprimidos"
    assert not list(Path("logs").glob("*.tmp")), "no pueden quedar temporales huérfanos"
    total = 0
    for path in compressed:
        with gzip.open(path, "rt", encoding="utf-8") as handle:  # Lectura del .gz.
            for line in handle:
                data = json.loads(line)  # Cada línea es JSON válido.
                assert data["timestamp"].endswith("+00:00")  # Y está en UTC.
                assert data["level"] in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
                for field in FIELDS:
                    assert field in data  # Contexto completo, con null donde no aplica.
                assert data["message"]  # Sin credenciales ni cuerpos: solo el mensaje.
                total += 1
    for path in Path("logs").glob("*.jsonl"):
        total += len(lines_of(path))
    assert total == 40  # No se pierde ni se duplica ninguna línea al comprimir.
