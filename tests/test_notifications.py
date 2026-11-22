"""Pruebas del aviso externo: F14 y clasificación de fallos de I4-A.

Las llamadas HTTP se sustituyen por dobles: el adaptador es la unidad bajo prueba y
ningún test envía mensajes reales ni necesita credenciales.
"""
import pytest  # Fixtures y aserciones.
from fenix.services import notifications  # Módulo del rol I4.
from fenix.utils.errors import PermanentError, RetryableError  # Clasificación exigida.

TOKEN = "123456:token-ficticio-solo-test"  # Fixture pública, no abre ningún bot real.
CHAT = "999999"  # chat_id autorizado del grupo de pruebas.
SUMMARY = "servicio restaurado"  # Texto público sin secretos ni cuerpos HTTP.


class FakeResponse:
    """Doble de respuesta HTTP con estado, cuerpo JSON y cabeceras controlables."""

    def __init__(self, status: int = 200, payload=None, headers: dict | None = None):
        """Configura la respuesta que verá el adaptador.

        Args:
            status: Código HTTP devuelto.
            payload: Diccionario del cuerpo JSON, o excepción si el cuerpo es ilegible.
            headers: Cabeceras de la respuesta, por ejemplo Retry-After.
        """
        self.status_code = status
        self.payload = payload
        self.headers = headers or {}
        self.text = "" if payload is None else str(payload)

    def json(self):
        """Devuelve el cuerpo o lanza el error de decodificación configurado."""
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeRequests:
    """Doble del módulo requests que registra la llamada y lanza lo configurado."""

    def __init__(self, response=None, error: Exception | None = None):
        """Guarda la respuesta o el error que devolverá post.

        Args:
            response: FakeResponse a devolver.
            error: Excepción a lanzar en lugar de responder.
        """
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def post(self, url: str, **options) -> FakeResponse:
        """Registra la petición y devuelve la respuesta o lanza el error."""
        self.calls.append({"url": url, **options})
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture
def telegram(monkeypatch):
    """Prepara el entorno de Telegram y publica el doble de requests.

    Args:
        monkeypatch: Fixture de pytest que restaura el entorno al terminar.
    Returns:
        Función que instala un FakeRequests y lo devuelve.
    """
    monkeypatch.setenv("NOTIFY_PROVIDER", "telegram")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT)
    monkeypatch.setattr(notifications, "load_dotenv", lambda *args, **kwargs: False)

    def install(response=None, error: Exception | None = None) -> FakeRequests:
        """Sustituye el módulo requests dentro del adaptador."""
        fake = FakeRequests(response, error)
        monkeypatch.setattr(notifications, "requests", fake)
        return fake

    return install


def test_console_mode_makes_no_calls(monkeypatch, capsys):
    """El modo de desarrollo imprime y no toca la red ni exige credenciales."""
    monkeypatch.setenv("NOTIFY_PROVIDER", "console")  # Modo explícito, no por defecto.
    notifications.notify(SUMMARY)
    assert capsys.readouterr().out.strip() == SUMMARY  # Sin eventos completos.


def test_notify_loads_local_configuration(monkeypatch):
    """El adaptador no depende de que otro módulo haya cargado el archivo local."""
    calls: list[bool] = []
    monkeypatch.setenv("NOTIFY_PROVIDER", "console")
    monkeypatch.setattr(notifications, "load_dotenv", lambda *a, **k: calls.append(True))
    notifications.notify(SUMMARY)
    assert calls == [True]  # Se carga exactamente una vez por aviso.


def test_telegram_confirms_with_ok_true(telegram):
    """Un 200 con ok=true es la única confirmación válida del proveedor."""
    fake = telegram(FakeResponse(200, {"ok": True}))
    notifications.notify(SUMMARY)
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"].endswith("/sendMessage")  # Endpoint de sendMessage.
    assert call["url"].startswith("https://api.telegram.org/bot")  # Host del proveedor.
    assert call["json"] == {"chat_id": CHAT, "text": SUMMARY}  # Sin parse_mode.
    assert call["timeout"] == (2, 5)  # Conexión y lectura acotadas por el contrato.


def test_telegram_200_with_ok_false_is_permanent_failure(telegram):
    """Un HTTP 200 no basta: si el JSON dice ok=false se rechaza como permanente."""
    telegram(FakeResponse(200, {"ok": False, "description": "chat not found"}))
    with pytest.raises(PermanentError):
        notifications.notify(SUMMARY)


def test_telegram_unreadable_response_is_retryable(telegram):
    """Un cuerpo que no es JSON no permite confirmar: se reintenta después."""
    telegram(FakeResponse(200, ValueError("sin JSON")))
    with pytest.raises(RetryableError):
        notifications.notify(SUMMARY)


def test_f14_429_is_retryable_and_caps_retry_after(telegram):
    """F14: el 429 espera como máximo 30 s aunque el proveedor pida más."""
    telegram(FakeResponse(429, {"ok": False}, {"Retry-After": "120"}))
    with pytest.raises(RetryableError) as captured:
        notifications.notify(SUMMARY)
    assert "reintentar_despues=30" in str(captured.value)  # Tope del ejercicio.


@pytest.mark.parametrize("status", [500, 502, 503])
def test_f14_five_xx_is_retryable(telegram, status):
    """F14: un 5xx puede resolverse solo; el mensaje se conserva."""
    telegram(FakeResponse(status, {"ok": False}))
    with pytest.raises(RetryableError):
        notifications.notify(SUMMARY)


@pytest.mark.parametrize("status", [400, 401, 403])
def test_configuration_errors_are_permanent(telegram, status):
    """Credenciales o chat inválidos no se arreglan reintentando."""
    telegram(FakeResponse(status, {"ok": False}))
    with pytest.raises(PermanentError) as captured:
        notifications.notify(SUMMARY)
    assert str(status) in str(captured.value)  # Diagnóstico de configuración.


@pytest.mark.parametrize("error", [
    notifications.requests.Timeout("timeout de prueba"),
    notifications.requests.ConnectionError("conexión rechazada"),
])
def test_f14_timeout_and_connection_do_not_leak_the_token(telegram, error):
    """F14: un fallo de requests es transitorio y no arrastra la URL con el token."""
    telegram(error=error)
    with pytest.raises(RetryableError) as captured:
        notifications.notify(SUMMARY)
    assert TOKEN not in str(captured.value)  # Sin secreto en el mensaje público.
    assert captured.value.__cause__ is None  # Sin cadena que imprima la URL del bot.


def test_missing_credentials_do_not_call_the_provider(telegram, monkeypatch):
    """Falta de configuración se diagnostica sin emitir la petición."""
    fake = telegram(FakeResponse(200, {"ok": True}))
    monkeypatch.delenv("TELEGRAM_CHAT_ID")  # Sólo se retira el chat autorizado.
    with pytest.raises(PermanentError):
        notifications.notify(SUMMARY)
    assert fake.calls == []  # Sin petición: el problema es de configuración.


def test_403_includes_provider_diagnosis(telegram):
    """Un 403 permanente explica la causa sin exponer el token."""
    telegram(FakeResponse(403, {"ok": False,
                                 "description": "Forbidden: the bot can't send messages to the bot"}))
    with pytest.raises(PermanentError) as captured:
        notifications.notify(SUMMARY)
    assert "can't send messages to the bot" in str(captured.value)  # Causa concreta.
    assert TOKEN not in str(captured.value)  # Sin secreto en el diagnóstico.


def test_unknown_provider_is_a_configuration_error(monkeypatch):
    """Un NOTIFY_PROVIDER que nadie implementa no se adivina."""
    monkeypatch.setenv("NOTIFY_PROVIDER", "discord")
    with pytest.raises(PermanentError):
        notifications.notify(SUMMARY)


def test_long_text_is_truncated_to_1000_characters(telegram):
    """El contrato limita el resumen a 1000 caracteres aunque llegue más texto."""
    fake = telegram(FakeResponse(200, {"ok": True}))
    notifications.notify("x" * 1500)
    assert len(fake.calls[0]["json"]["text"]) == 1000
