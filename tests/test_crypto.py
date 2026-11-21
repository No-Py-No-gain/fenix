"""Pruebas de la firma HMAC del receptor: F04 y F05 de la matriz de la cátedra."""
import pytest  # Ejecuta casos parametrizados y verifica la excepción de dominio.
from fenix.utils.crypto import signature, verify  # Módulo del rol I3.
from fenix.utils.errors import ValidationError  # Rechazo público de autenticación.

SECRETO = "clave-ficticia-solo-test"  # Fixture pública: no es secreto de ningún servicio.
CUERPO = b'{"x":1}'  # Bytes exactos; el espaciado forma parte de la firma.
AHORA = 1_700_000_000  # Reloj fijo para no depender de la hora de la corrida.


def cabecera(cuerpo: bytes = CUERPO, timestamp: int = AHORA) -> str:
    """Arma una cabecera válida con el formato exigido por el contrato.

    Args:
        cuerpo: Bytes que se firmaron.
        timestamp: Epoch que acompaña a la firma.
    Returns:
        Cabecera t=<epoch>,v1=<64 hex>.
    """
    return f"t={timestamp},v1={signature(SECRETO, timestamp, cuerpo)}"


def test_firma_valida_se_acepta():
    """Acepta la cabecera correcta sin devolver nada."""
    verify(SECRETO, cabecera(), CUERPO, now=AHORA)  # No debe levantar excepción.


def test_f05_mismos_datos_otros_bytes_no_autentican():
    """F05: el mismo JSON con otro espaciado es otra firma y se rechaza."""
    verify(SECRETO, cabecera(), CUERPO, now=AHORA)  # El original sigue siendo válido.
    with pytest.raises(ValidationError):  # Re-serializar cambia los bytes firmados.
        verify(SECRETO, cabecera(), b'{"x": 1}', now=AHORA)


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
def test_f04_cabeceras_invalidas_se_rechazan(header):
    """F04: firma malformada, ambigua o incompleta nunca autentica."""
    with pytest.raises(ValidationError):  # Misma excepción para no dar pistas.
        verify(SECRETO, header, CUERPO, now=AHORA)


def test_f04_ventana_de_300_segundos_en_ambos_sentidos():
    """F04: 300 s de tolerancia inclusive hacia pasado y futuro."""
    verify(SECRETO, cabecera(timestamp=AHORA - 300), CUERPO, now=AHORA)  # Borde pasado.
    verify(SECRETO, cabecera(timestamp=AHORA + 300), CUERPO, now=AHORA)  # Borde futuro.
    with pytest.raises(ValidationError):  # Un segundo más viejo ya no sirve.
        verify(SECRETO, cabecera(timestamp=AHORA - 301), CUERPO, now=AHORA)
    with pytest.raises(ValidationError):  # Un segundo más nuevo tampoco.
        verify(SECRETO, cabecera(timestamp=AHORA + 301), CUERPO, now=AHORA)


def test_f04_firma_de_otro_secreto_no_autentica():
    """F04: una firma bien formada con otro secreto se rechaza igual."""
    ajena = signature("otro-secreto-ficticio", AHORA, CUERPO)  # Secreto distinto.
    with pytest.raises(ValidationError):
        verify(SECRETO, f"t={AHORA},v1={ajena}", CUERPO, now=AHORA)


def test_el_rechazo_no_filtra_cabecera_ni_secreto():
    """El mensaje de error no puede servir para adivinar el secreto ni la firma."""
    header = "t=abc,v1=deadbeef"  # Contenido reconocible para buscarlo en el mensaje.
    with pytest.raises(ValidationError) as capturada:
        verify(SECRETO, header, CUERPO, now=AHORA)
    assert SECRETO not in str(capturada.value)  # Sin eco del secreto compartido.
    assert "deadbeef" not in str(capturada.value)  # Sin eco de la cabecera recibida.


def test_secreto_corto_es_error_de_configuracion():
    """Un secreto menor a 16 caracteres falla también al verificar."""
    with pytest.raises(ValidationError):
        verify("corto", cabecera(), CUERPO, now=AHORA)
