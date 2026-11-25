import base64
import hashlib
import hmac


def _b64(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode("ascii")


def _desb64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def _assinatura(corpo: str, segredo: bytes, finalidade: str) -> str:
    chave = hmac.new(segredo, ("sombra:" + finalidade).encode("utf-8"), hashlib.sha256).digest()
    return _b64(hmac.new(chave, corpo.encode("ascii"), hashlib.sha256).digest())


def assinar(carga: bytes, segredo: bytes, finalidade: str) -> str:
    corpo = _b64(carga)
    return corpo + "." + _assinatura(corpo, segredo, finalidade)


def verificar(texto: str, segredo: bytes, finalidade: str):
    try:
        corpo, recebida = texto.split(".")
        if not hmac.compare_digest(recebida, _assinatura(corpo, segredo, finalidade)):
            return None
        return _desb64(corpo)
    except (ValueError, TypeError, AttributeError):
        return None
