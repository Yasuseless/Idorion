import http.client
import json
import threading
from http.server import ThreadingHTTPServer


class Resposta:
    def __init__(self, status, cabecalhos, corpo):
        self.status_code = status
        self.cabecalhos = cabecalhos
        self.corpo = corpo

    def get_json(self):
        return json.loads(self.corpo.decode("utf-8"))

    def texto(self):
        return self.corpo.decode("utf-8")

    def cabecalho(self, nome):
        for chave, valor in self.cabecalhos:
            if chave.lower() == nome.lower():
                return valor
        return None

    def cookies_definidos(self):
        return [valor for chave, valor in self.cabecalhos if chave.lower() == "set-cookie"]


class Cliente:
    def __init__(self, porta, host=None, https=False):
        self.porta = porta
        self.host = host
        self.https = https
        self.cookies = {}

    def copia(self):
        outro = Cliente(self.porta, self.host, self.https)
        outro.cookies = dict(self.cookies)
        return outro

    def _cabecalhos(self, extras):
        cabecalhos = {}
        if self.host:
            cabecalhos["Host"] = self.host
        if self.https:
            cabecalhos["X-Forwarded-Proto"] = "https"
            if self.host:
                cabecalhos["Origin"] = "https://" + self.host
        if self.cookies:
            cabecalhos["Cookie"] = "; ".join(k + "=" + v for k, v in self.cookies.items())
        cabecalhos.update(extras)
        return cabecalhos

    def _guardar_cookies(self, resposta):
        for linha in resposta.cookies_definidos():
            partes = [p.strip() for p in linha.split(";")]
            nome, _, valor = partes[0].partition("=")
            atributos = {p.partition("=")[0].lower(): p.partition("=")[2] for p in partes[1:]}
            if atributos.get("max-age") == "0":
                self.cookies.pop(nome, None)
            else:
                self.cookies[nome] = valor

    def requisitar(self, metodo, caminho, json_corpo=None, dados_brutos=None, cabecalhos=None):
        corpo = None
        extras = dict(cabecalhos or {})
        if json_corpo is not None:
            corpo = json.dumps(json_corpo).encode("utf-8")
            extras.setdefault("Content-Type", "application/json")
        elif dados_brutos is not None:
            corpo = dados_brutos if isinstance(dados_brutos, bytes) else dados_brutos.encode("utf-8")
        conexao = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=60)
        try:
            conexao.request(metodo, caminho, body=corpo, headers=self._cabecalhos(extras))
            resposta = conexao.getresponse()
            resultado = Resposta(resposta.status, resposta.getheaders(), resposta.read())
        finally:
            conexao.close()
        self._guardar_cookies(resultado)
        return resultado

    def get(self, caminho, cabecalhos=None):
        return self.requisitar("GET", caminho, cabecalhos=cabecalhos)

    def post(self, caminho, json_corpo=None, dados_brutos=None, cabecalhos=None):
        return self.requisitar("POST", caminho, json_corpo, dados_brutos, cabecalhos)

    def put(self, caminho, cabecalhos=None):
        return self.requisitar("PUT", caminho, cabecalhos=cabecalhos)


class ServidorDeTeste:
    def __init__(self, handler):
        self.servidor = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()
        self.porta = self.servidor.server_address[1]

    def parar(self):
        self.servidor.shutdown()
        self.servidor.server_close()
