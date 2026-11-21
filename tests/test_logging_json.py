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

CAMPOS = ("event_id", "message_id", "resource_id", "attempt", "action", "status")


def registro(mensaje: str = "hola %s", argumentos: tuple = ("mundo",)) -> logging.LogRecord:
    """Construye un LogRecord equivalente al que produce la biblioteca.

    Args:
        mensaje: Plantilla del mensaje.
        argumentos: Argumentos de la plantilla.
    Returns:
        Registro listo para formatear.
    """
    return logging.LogRecord("fenix-prueba", logging.INFO, __file__, 1, mensaje,
                             argumentos, None)


def cerrar(listener) -> None:
    """Detiene el listener y cierra su manejador, que stop() no cierra.

    Args:
        listener: QueueListener devuelto por setup.
    """
    listener.stop()  # Drena la cola y termina el hilo.
    for manejador in listener.handlers:
        manejador.close()  # Libera el archivo para poder leerlo.


def lineas_de(archivo: Path) -> list[dict]:
    """Lee y parsea un archivo JSONL.

    Args:
        archivo: Ruta del archivo activo.
    Returns:
        Lista de registros ya deserializados.
    """
    texto = archivo.read_text(encoding="utf-8")
    return [json.loads(linea) for linea in texto.splitlines() if linea.strip()]


def test_campos_de_contexto_son_null_cuando_no_aplican():
    """Criterio de la cátedra: los campos no aplicables se representan como null."""
    datos = json.loads(JSONFormatter().format(registro()))
    assert datos["message"] == "hola mundo"  # El mensaje se compone, no se duplica.
    for campo in CAMPOS:
        assert campo in datos  # La clave existe siempre...
        assert datos[campo] is None  # ...aunque no haya contexto aplicable.


def test_contexto_publico_se_conserva():
    """Los extras autorizados llegan al JSON sin abrir la puerta a cualquiera."""
    un_registro = registro()
    un_registro.event_id = "evt-node-001"
    un_registro.attempt = 2
    un_registro.status = "acknowledged"
    un_registro.campo_privado = "no debe publicarse"
    datos = json.loads(JSONFormatter().format(un_registro))
    assert datos["event_id"] == "evt-node-001"
    assert datos["attempt"] == 2
    assert datos["status"] == "acknowledged"
    assert "campo_privado" not in datos  # La lista de campos es cerrada.


def test_timestamp_es_utc_con_offset_explicito():
    """La marca temporal describe el instante del evento y siempre con zona."""
    datos = json.loads(JSONFormatter().format(registro()))
    assert datos["timestamp"].endswith("+00:00")  # UTC explícito, no hora local.
    instante = datetime.fromisoformat(datos["timestamp"])
    assert instante.utcoffset() == timezone.utc.utcoffset(None)  # Offset cero.


def test_extra_no_serializable_falla_en_voz_alta():
    """El formateador no oculta un extra que no puede publicarse."""
    un_registro = registro()
    un_registro.event_id = object()  # No es serializable.
    with pytest.raises(TypeError):
        JSONFormatter().format(un_registro)


def test_los_cinco_niveles_llegan_al_archivo(tmp_path, monkeypatch):
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
        cerrar(listener)
    archivo = next(Path("logs").glob("*.jsonl"))
    assert [d["level"] for d in lineas_de(archivo)] == [
        "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def test_la_escritura_ocurre_en_el_hilo_del_listener(tmp_path, monkeypatch):
    """El productor no escribe en disco: lo hace el hilo de QueueListener."""
    monkeypatch.chdir(tmp_path)
    logger, listener = setup("fenix-hilos")
    sink = listener.handlers[0]
    original = sink.emit
    hilos: list[str] = []

    def emitir(record):
        """Registra el hilo que efectivamente escribe el archivo."""
        hilos.append(threading.current_thread().name)
        original(record)

    try:
        sink.emit = emitir  # Se observa el manejador real, sin tocar atributos privados.
        logger.info("observado desde otro hilo")
    finally:
        cerrar(listener)
    assert hilos, "el registro tenía que llegar al manejador"
    assert hilos[0] != threading.current_thread().name  # No es el hilo que loguea.


def test_setup_repetido_no_duplica_registros(tmp_path, monkeypatch):
    """Llamar dos veces al setup deja exactamente una línea por registro."""
    monkeypatch.chdir(tmp_path)
    primero, listener_uno = setup("fenix-repetido")
    segundo, listener_dos = setup("fenix-repetido")  # Reemplaza el manejador anterior.
    try:
        segundo.info("una sola vez")
    finally:
        cerrar(listener_dos)
        primero.handlers.clear()  # Suelta el manejador ya reemplazado.
        cerrar(listener_uno)
    archivo = next(Path("logs").glob("*.jsonl"))
    assert len(lineas_de(archivo)) == 1


def test_f16_rotacion_comprimida_es_legible(tmp_path, monkeypatch):
    """F16: forzar rotación con tamaño reducido, abrir el .gz y parsear cada línea."""
    monkeypatch.chdir(tmp_path)
    logger, listener = setup("fenix-rotacion", max_bytes=1000, backup_count=100)
    try:
        for numero in range(40):
            logger.info("evento %s de la prueba de rotación", numero,
                        extra={"event_id": f"evt-{numero}", "resource_id": "node-01",
                               "attempt": numero, "action": "repair", "status": "done"})
    finally:
        cerrar(listener)
    comprimidos = sorted(Path("logs").glob("*.gz"))
    assert comprimidos, "la rotación tenía que producir históricos comprimidos"
    assert not list(Path("logs").glob("*.tmp")), "no pueden quedar temporales huérfanos"
    total = 0
    for archivo in comprimidos:
        with gzip.open(archivo, "rt", encoding="utf-8") as abierto:  # Lectura del .gz.
            for linea in abierto:
                datos = json.loads(linea)  # Cada línea es JSON válido.
                assert datos["timestamp"].endswith("+00:00")  # Y está en UTC.
                assert datos["level"] in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
                for campo in CAMPOS:
                    assert campo in datos  # Contexto completo, con null donde no aplica.
                assert datos["message"]  # Sin credenciales ni cuerpos: solo el mensaje.
                total += 1
    for archivo in Path("logs").glob("*.jsonl"):
        total += len(lineas_de(archivo))
    assert total == 40  # No se pierde ni se duplica ninguna línea al comprimir.
