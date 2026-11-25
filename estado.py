import contextlib
import contextvars
import copy
import json
import zlib

import assinatura

NOME_COOKIE = "sombra_estado"
FINALIDADE = "estado"
DURACAO = 7 * 24 * 3600
LIMITE_COOKIE = 3500
LIMITE_DESCOMPRIMIDO = 200_000

_ATUAL = contextvars.ContextVar("sombra_estado", default=None)


class LimiteDeEstado(Exception):
    pass


def _vazio():
    return {"a": [], "r": [], "k": {}}


def _lista_de_textos(valor, tamanho):
    return isinstance(valor, list) and len(valor) == tamanho and all(isinstance(x, str) for x in valor)


def _delta_valido(delta):
    return (isinstance(delta, dict)
            and isinstance(delta.get("a"), list) and all(_lista_de_textos(i, 7) for i in delta["a"])
            and isinstance(delta.get("r"), list) and all(_lista_de_textos(i, 10) for i in delta["r"])
            and isinstance(delta.get("k"), dict)
            and all(isinstance(c, str) and isinstance(v, str) for c, v in delta["k"].items()))


def _carregar(valor, segredo):
    if not valor or segredo is None:
        return _vazio()
    carga = assinatura.verificar(valor, segredo, FINALIDADE)
    if carga is None:
        return _vazio()
    try:
        descompressor = zlib.decompressobj()
        bruto = descompressor.decompress(carga, LIMITE_DESCOMPRIMIDO)
        if descompressor.unconsumed_tail:
            return _vazio()
        delta = json.loads(bruto.decode("utf-8"))
    except (zlib.error, ValueError):
        return _vazio()
    return delta if _delta_valido(delta) else _vazio()


def _achar(offers, offer, contato_id):
    dados = offers.get(offer)
    if dados is None:
        return None
    for contato in dados["contatos"]:
        if contato["id"] == contato_id:
            return contato
    return None


def _aplicar(offers, delta):
    for offer, id_, pai, nome, telefone, email, cpf in delta["a"]:
        dados = offers.get(offer)
        if dados is not None:
            dados["contatos"].append({"id": id_, "papel": "alternativo", "pai": pai, "nome": nome,
                                      "classificacao": "", "telefone": telefone, "email": email,
                                      "cpf": cpf, "atendimentos": []})
    for chave, texto in delta["k"].items():
        offer, _, contato_id = chave.partition("|")
        contato = _achar(offers, offer, contato_id)
        if contato is not None and contato["papel"] == "alternativo":
            contato["classificacao"] = texto
    for offer, contato_id, data, hora, canal, tema, ticket, analista, origem, resumo in delta["r"]:
        contato = _achar(offers, offer, contato_id)
        if contato is not None:
            contato["atendimentos"].append({"data": data, "hora": hora, "canal": canal, "tema": tema,
                                            "ticket": ticket, "analista": analista, "origem": origem,
                                            "resumo": resumo})


class Estado:
    def __init__(self, delta=None):
        self.delta = delta if delta is not None else _vazio()
        self.alterado = False
        self._offers = None

    def offers(self, semente):
        if self._offers is None:
            copia = copy.deepcopy(semente)
            _aplicar(copia, self.delta)
            self._offers = copia
        return self._offers

    def anotar_alternativo(self, offer, contato):
        self.delta["a"].append([offer, contato["id"], contato["pai"], contato["nome"],
                                contato["telefone"], contato["email"], contato["cpf"]])
        self.alterado = True

    def anotar_atendimento(self, offer, contato_id, registro):
        self.delta["r"].append([offer, contato_id, registro["data"], str(registro.get("hora") or ""),
                                registro["canal"], registro["tema"], str(registro.get("ticket") or ""),
                                registro["analista"], str(registro.get("origem") or "dashboard"),
                                registro["resumo"]])
        self.alterado = True

    def anotar_classificacao(self, offer, contato_id, texto):
        self.delta["k"][offer + "|" + contato_id] = texto
        self.alterado = True

    def serializar(self, segredo):
        bruto = json.dumps(self.delta, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        texto = assinatura.assinar(zlib.compress(bruto, 9), segredo, FINALIDADE)
        if len(texto) > LIMITE_COOKIE:
            raise LimiteDeEstado()
        return texto


@contextlib.contextmanager
def ativar(valor_cookie, segredo):
    estado = Estado(_carregar(valor_cookie, segredo))
    marca = _ATUAL.set(estado)
    try:
        yield estado
    finally:
        _ATUAL.reset(marca)


def atual():
    estado = _ATUAL.get()
    if estado is None:
        estado = Estado()
        _ATUAL.set(estado)
    return estado
