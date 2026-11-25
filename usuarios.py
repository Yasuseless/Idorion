import hashlib
import hmac
import json
import os
import secrets
import threading
import time

import assinatura

EM_VERCEL = bool(os.environ.get("VERCEL"))

NOME_DEMO = "Analista Demonstração"
NOME_SUPERVISOR = "Supervisor Demonstração"

NOME_COOKIE = "sombra_sessao"
FINALIDADE = "sessao"
ITERACOES = 120_000
DURACAO_SESSAO = 8 * 3600
MAX_FALHAS = 5
JANELA_BLOQUEIO = 300
TAMANHO_MINIMO_SENHA = 8
TAMANHO_MINIMO_SEGREDO = 32
LIMITE_REGISTRO_DE_FALHAS = 500

_PADROES_LOCAIS = {
    "SOMBRA_ANALISTA_EMAIL": "matrix@webhelpbr.com.br",
    "SOMBRA_ANALISTA_SENHA": "Neo@2026",
    "SOMBRA_SUPERVISOR_EMAIL": "supervisor@webhelpbr.com.br",
    "SOMBRA_SUPERVISOR_SENHA": "Supervisor@2026",
}

_SAL_FALSO = secrets.token_bytes(16)
_SEGREDO_LOCAL = secrets.token_bytes(32)
_FALHAS = {}
_TRAVA = threading.Lock()


class CredenciaisInvalidas(Exception):
    pass


class BloqueioTemporario(Exception):
    pass


class SegredoNaoConfigurado(Exception):
    pass


def _variavel(nome):
    valor = (os.environ.get(nome) or "").strip()
    if valor:
        return valor
    return None if EM_VERCEL else _PADROES_LOCAIS[nome]


def _hash(senha: str, sal: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), sal, ITERACOES)


USUARIOS = {}


def _registrar(var_email, var_senha, nome, papel):
    email = _variavel(var_email)
    senha = _variavel(var_senha)
    if not email or not senha or len(senha) < TAMANHO_MINIMO_SENHA:
        return False
    sal = secrets.token_bytes(16)
    USUARIOS[email.lower()] = {"nome": nome, "papel": papel, "sal": sal, "hash": _hash(senha, sal)}
    return True


ANALISTA_CONFIGURADO = _registrar("SOMBRA_ANALISTA_EMAIL", "SOMBRA_ANALISTA_SENHA", NOME_DEMO, "analista")
SUPERVISOR_CONFIGURADO = _registrar("SOMBRA_SUPERVISOR_EMAIL", "SOMBRA_SUPERVISOR_SENHA", NOME_SUPERVISOR, "supervisor")


def segredo_disponivel() -> bool:
    valor = (os.environ.get("SOMBRA_SEGREDO") or "").strip()
    return len(valor) >= TAMANHO_MINIMO_SEGREDO or not EM_VERCEL


def obter_segredo() -> bytes:
    valor = (os.environ.get("SOMBRA_SEGREDO") or "").strip()
    if len(valor) >= TAMANHO_MINIMO_SEGREDO:
        return valor.encode("utf-8")
    if EM_VERCEL:
        raise SegredoNaoConfigurado()
    return _SEGREDO_LOCAL


def segredo_ou_none():
    try:
        return obter_segredo()
    except SegredoNaoConfigurado:
        return None


def autenticar(email: str, senha: str) -> str:
    segredo = obter_segredo()
    email = str(email or "").strip().lower()[:120]
    senha = str(senha or "")[:200]
    agora = time.time()

    with _TRAVA:
        if len(_FALHAS) > LIMITE_REGISTRO_DE_FALHAS:
            for chave in [c for c, v in _FALHAS.items() if not any(agora - t < JANELA_BLOQUEIO for t in v)]:
                del _FALHAS[chave]
        recentes = [t for t in _FALHAS.get(email, []) if agora - t < JANELA_BLOQUEIO]
        _FALHAS[email] = recentes
        bloqueado = len(recentes) >= MAX_FALHAS
    if bloqueado:
        raise BloqueioTemporario()

    usuario = USUARIOS.get(email)
    calculado = _hash(senha, usuario["sal"] if usuario else _SAL_FALSO)
    if usuario is None or not hmac.compare_digest(calculado, usuario["hash"]):
        with _TRAVA:
            _FALHAS.setdefault(email, []).append(agora)
        raise CredenciaisInvalidas()

    with _TRAVA:
        _FALHAS.pop(email, None)

    carga = json.dumps({"e": email, "n": usuario["nome"], "p": usuario["papel"],
                        "x": int(agora + DURACAO_SESSAO)}, separators=(",", ":"), ensure_ascii=False)
    return assinatura.assinar(carga.encode("utf-8"), segredo, FINALIDADE)


def obter_sessao(token):
    if not token:
        return None
    segredo = segredo_ou_none()
    if segredo is None:
        return None
    bruto = assinatura.verificar(token, segredo, FINALIDADE)
    if bruto is None:
        return None
    try:
        carga = json.loads(bruto.decode("utf-8"))
        email, nome, papel, expira = carga["e"], carga["n"], carga["p"], carga["x"]
    except (ValueError, KeyError, TypeError):
        return None
    if not (isinstance(email, str) and isinstance(nome, str) and isinstance(expira, int)):
        return None
    if papel not in ("analista", "supervisor") or expira < time.time():
        return None
    return {"email": email, "nome": nome, "papel": papel}
