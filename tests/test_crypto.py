"""Pruebas de la firma HMAC del receptor: F04 y F05 de la matriz de la cátedra."""
import pytest  # Ejecuta casos parametrizados y verifica la excepción de dominio.
from fenix.utils.crypto import signature, verify  # Módulo del rol I3.
from fenix.utils.errors import ValidationError  # Rechazo público de autenticación.

SECRET = "clave-ficticia-solo-test"  # Fixture pública: no es secreto de ningún servicio.
BODY = b'{"x":1}'  # Bytes exactos; el espaciado forma parte de la firma.
NOW = 1_700_000_000  # Reloj fijo para no depender de la hora de la corrida.


def auth_header(body: bytes = BODY, timestamp: int = NOW) -> str:
    """Arma una cabecera válida con el formato exigido por el contrato.

    Args:
        body: Bytes que se firmaron.
        timestamp: Epoch que acompaña a la firma.
    Returns:
        Cabecera t=<epoch>,v1=<64 hex>.
    """
    return f"t={timestamp},v1={signature(SECRET, timestamp, body)}"


def test_valid_signature_is_accepted():
    """Acepta la cabecera correcta sin devolver nada."""
    verify(SECRET, auth_header(), BODY, now=NOW)  # No debe levantar excepción.


def test_f05_same_data_different_bytes_do_not_authenticate():
    """F05: el mismo JSON con otro espaciado es otra firma y se rechaza."""
    verify(SECRET, auth_header(), BODY, now=NOW)  # El original sigue siendo válido.
    with pytest.raises(ValidationError):  # Re-serializar cambia los bytes firmados.
        verify(SECRET, auth_header(), b'{"x": 1}', now=NOW)


@pytest.mark.parametrize(
    "header",
    [
        "",  # Firma ausente.
        "t=1700000000",  # Falta v1.
        "v1=" + "a" * 64,  # Falta t.
        "t=1700000000,v1=",  # v1 vacío.
        "t=1700000000,t=1700000000,v1=" + "a" * 64,  # Clave duplicada.
        "t=1700000000,v1=" + "a" * 64 + ",x=1",  # Clave ajena al contrato.
        "t=1700000000,v1=" + "a" * 64 + ",t=1700000000",  # Duplicada al final.
        "t=abc,v1=" + "a" * 64,  # Epoch no entero.
        "t=1_700_000_000,v1=" + "a" * 64,  # int() aceptaría guiones bajos.
        "t=1700000000,v1=abc",  # Hex demasiado corto.
        "t=1700000000,v1=" + "z" * 64,  # Hex inválido.
        " t=1700000000,v1=" + "a" * 64,  # Espacio: formato ambiguo.
        "t=1700000000,v1=" + "a" * 64 + "=00",  # Valor con separador extra.
    ],
)
def test_f04_invalid_headers_are_rejected(header):
    """F04: firma malformada, ambigua o incompleta nunca autentica."""
    with pytest.raises(ValidationError):  # Misma excepción para no dar pistas.
        verify(SECRET, header, BODY, now=NOW)


def test_f04_window_is_300_seconds_either_way():
    """F04: 300 s de tolerancia inclusive hacia pasado y futuro."""
    verify(SECRET, auth_header(timestamp=NOW - 300), BODY, now=NOW)  # Borde pasado.
    verify(SECRET, auth_header(timestamp=NOW + 300), BODY, now=NOW)  # Borde futuro.
    with pytest.raises(ValidationError):  # Un segundo más viejo ya no sirve.
        verify(SECRET, auth_header(timestamp=NOW - 301), BODY, now=NOW)
    with pytest.raises(ValidationError):  # Un segundo más nuevo tampoco.
        verify(SECRET, auth_header(timestamp=NOW + 301), BODY, now=NOW)


def test_f04_signature_from_another_secret_does_not_authenticate():
    """F04: una firma bien formada con otro secreto se rechaza igual."""
    foreign = signature("otro-secreto-ficticio", NOW, BODY)  # Secreto distinto.
    with pytest.raises(ValidationError):
        verify(SECRET, f"t={NOW},v1={foreign}", BODY, now=NOW)


def test_rejection_leaks_neither_header_nor_secret():
    """El mensaje de error no puede servir para adivinar el secreto ni la firma."""
    header = "t=abc,v1=deadbeef"  # Contenido reconocible para buscarlo en el mensaje.
    with pytest.raises(ValidationError) as captured:
        verify(SECRET, header, BODY, now=NOW)
    assert SECRET not in str(captured.value)  # Sin eco del secreto compartido.
    assert "deadbeef" not in str(captured.value)  # Sin eco de la cabecera recibida.


def test_short_secret_is_a_configuration_error():
    """Un secreto menor a 16 caracteres falla también al verificar."""
    with pytest.raises(ValidationError):
        verify("corto", auth_header(), BODY, now=NOW)
