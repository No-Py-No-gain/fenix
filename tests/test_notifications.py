"""Pruebas del aviso externo: F14 y clasificación de fallos de I4-A.

Las llamadas HTTP se sustituyen por dobles: el adaptador es la unidad bajo prueba y
ningún test envía mensajes reales ni necesita credenciales.
"""
import pytest  # Fixtures y aserciones.
from fenix.services import notifications  # Módulo del rol I4.
from fenix.utils.errors import PermanentError, RetryableError  # Clasificación exigida.

TOKEN = "123456:token-ficticio-solo-test"  # Fixture pública, no abre ningún bot real.
CHAT = "999999"  # chat_id autorizado del grupo de pruebas.
RESUMEN = "servicio restaurado"  # Texto público sin secretos ni cuerpos HTTP.


class RespuestaFalsa:
    """Doble de respuesta HTTP con estado, cuerpo JSON y cabeceras controlables."""

    def __init__(self, estado: int = 200, cuerpo=None, cabeceras: dict | None = None):
        """Configura la respuesta que verá el adaptador.

        Args:
            estado: Código HTTP devuelto.
            cuerpo: Diccionario del cuerpo JSON, o excepción si el cuerpo es ilegible.
            cabeceras: Cabeceras de la respuesta, por ejemplo Retry-After.
        """
        self.status_code = estado
        self.cuerpo = cuerpo
        self.headers = cabeceras or {}
        self.text = "" if cuerpo is None else str(cuerpo)

    def json(self):
        """Devuelve el cuerpo o lanza el error de decodificación configurado."""
        if isinstance(self.cuerpo, Exception):
            raise self.cuerpo
        return self.cuerpo


class RequestsFalso:
    """Doble del módulo requests que registra la llamada y lanza lo configurado."""

    def __init__(self, respuesta=None, error: Exception | None = None):
        """Guarda la respuesta o el error que devolverá post.

        Args:
            respuesta: RespuestaFalsa a devolver.
            error: Excepción a lanzar en lugar de responder.
        """
        self.respuesta = respuesta
        self.error = error
        self.llamadas: list[dict] = []

    def post(self, url: str, **opciones) -> RespuestaFalsa:
        """Registra la petición y devuelve la respuesta o lanza el error."""
        self.llamadas.append({"url": url, **opciones})
        if self.error is not None:
            raise self.error
        return self.respuesta


@pytest.fixture
def telegram(monkeypatch):
    """Prepara el entorno de Telegram y publica el doble de requests.

    Args:
        monkeypatch: Fixture de pytest que restaura el entorno al terminar.
    Returns:
        Función que instala un RequestsFalso y lo devuelve.
    """
    monkeypatch.setenv("NOTIFY_PROVIDER", "telegram")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT)

    def instalar(respuesta=None, error: Exception | None = None) -> RequestsFalso:
        """Sustituye el módulo requests dentro del adaptador."""
        doble = RequestsFalso(respuesta, error)
        monkeypatch.setattr(notifications, "requests", doble)
        return doble

    return instalar


def test_modo_console_no_hace_llamadas(monkeypatch, capsys):
    """El modo de desarrollo imprime y no toca la red ni exige credenciales."""
    monkeypatch.delenv("NOTIFY_PROVIDER", raising=False)  # Default entregado.
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    notifications.notify(RESUMEN)
    assert capsys.readouterr().out.strip() == RESUMEN  # Sin eventos completos.


def test_telegram_confirma_con_ok_true(telegram):
    """Un 200 con ok=true es la única confirmación válida del proveedor."""
    doble = telegram(RespuestaFalsa(200, {"ok": True}))
    notifications.notify(RESUMEN)
    assert len(doble.llamadas) == 1
    llamada = doble.llamadas[0]
    assert llamada["url"].endswith("/sendMessage")  # Endpoint de sendMessage.
    assert llamada["url"].startswith("https://api.telegram.org/bot")  # Host del proveedor.
    assert llamada["json"] == {"chat_id": CHAT, "text": RESUMEN}  # Sin parse_mode.
    assert llamada["timeout"] == (2, 5)  # Conexión y lectura acotadas por el contrato.


def test_telegram_200_con_ok_false_es_falla_permanente(telegram):
    """Un HTTP 200 no basta: si el JSON dice ok=false se rechaza como permanente."""
    telegram(RespuestaFalsa(200, {"ok": False, "description": "chat not found"}))
    with pytest.raises(PermanentError):
        notifications.notify(RESUMEN)


def test_telegram_respuesta_ilegible_es_transitoria(telegram):
    """Un cuerpo que no es JSON no permite confirmar: se reintenta después."""
    telegram(RespuestaFalsa(200, ValueError("sin JSON")))
    with pytest.raises(RetryableError):
        notifications.notify(RESUMEN)


def test_f14_429_es_transitorio_y_acota_retry_after(telegram):
    """F14: el 429 espera como máximo 30 s aunque el proveedor pida más."""
    telegram(RespuestaFalsa(429, {"ok": False}, {"Retry-After": "120"}))
    with pytest.raises(RetryableError) as capturada:
        notifications.notify(RESUMEN)
    assert "reintentar_despues=30" in str(capturada.value)  # Tope del ejercicio.


@pytest.mark.parametrize("estado", [500, 502, 503])
def test_f14_cinco_xx_es_transitorio(telegram, estado):
    """F14: un 5xx puede resolverse solo; el mensaje se conserva."""
    telegram(RespuestaFalsa(estado, {"ok": False}))
    with pytest.raises(RetryableError):
        notifications.notify(RESUMEN)


@pytest.mark.parametrize("estado", [400, 401, 403])
def test_errores_de_configuracion_son_permanentes(telegram, estado):
    """Credenciales o chat inválidos no se arreglan reintentando."""
    telegram(RespuestaFalsa(estado, {"ok": False}))
    with pytest.raises(PermanentError) as capturada:
        notifications.notify(RESUMEN)
    assert str(estado) in str(capturada.value)  # Diagnóstico de configuración.


@pytest.mark.parametrize("error", [
    notifications.requests.Timeout("timeout de prueba"),
    notifications.requests.ConnectionError("conexión rechazada"),
])
def test_f14_timeout_y_conexion_no_filtran_el_token(telegram, error):
    """F14: un fallo de requests es transitorio y no arrastra la URL con el token."""
    telegram(error=error)
    with pytest.raises(RetryableError) as capturada:
        notifications.notify(RESUMEN)
    assert TOKEN not in str(capturada.value)  # Sin secreto en el mensaje público.
    assert capturada.value.__cause__ is None  # Sin cadena que imprima la URL del bot.


def test_sin_credenciales_no_llama_al_proveedor(telegram, monkeypatch):
    """Falta de configuración se diagnostica sin emitir la petición."""
    doble = telegram(RespuestaFalsa(200, {"ok": True}))
    monkeypatch.delenv("TELEGRAM_CHAT_ID")  # Sólo se retira el chat autorizado.
    with pytest.raises(PermanentError):
        notifications.notify(RESUMEN)
    assert doble.llamadas == []  # Sin petición: el problema es de configuración.


def test_proveedor_desconocido_es_error_de_configuracion(monkeypatch):
    """Un NOTIFY_PROVIDER que nadie implementa no se adivina."""
    monkeypatch.setenv("NOTIFY_PROVIDER", "discord")
    with pytest.raises(PermanentError):
        notifications.notify(RESUMEN)


def test_texto_largo_se_recorta_a_mil_caracteres(telegram):
    """El contrato limita el resumen a 1000 caracteres aunque llegue más texto."""
    doble = telegram(RespuestaFalsa(200, {"ok": True}))
    notifications.notify("x" * 1500)
    assert len(doble.llamadas[0]["json"]["text"]) == 1000
