import json
import logging
import re
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import estado
import usuarios
from analistas import validar_analista
from busca import BuscaInvalida, buscar
from contatos_alternativos import ContatoInvalido, validar_classificacao, validar_novo_contato
from mascaramento import mascarar_cpf, mascarar_email, mascarar_telefone
from registros import TEMAS, RegistroInvalido, validar_registro
from salesforce_simulado import (ErroContato, adicionar_alternativo, adicionar_atendimento, buscar_offer,
                                 classificar_alternativo)
from supervisor import atendimentos_do_analista, resumo_por_analista, volumetria

PASTA = Path(__file__).resolve().parent
PAGINA_INICIAL = PASTA / "public" / "index.html"
FUSO_BRASILIA = timezone(timedelta(hours=-3))
TAMANHO_MAXIMO_CORPO = 8192

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")

Resposta = namedtuple("Resposta", "status corpo tipo cookies")


def agora_br():
    return datetime.now(FUSO_BRASILIA)


def montar_resposta(offer: str):
    dados = buscar_offer(offer)
    if dados is None:
        return None
    hoje = agora_br().date()
    dados["temas"] = list(TEMAS)
    for contato in dados["contatos"]:

        contato["telefone"] = mascarar_telefone(contato["telefone"]) if contato.get("telefone") else ""
        contato["email"] = mascarar_email(contato["email"]) if contato.get("email") else ""
        contato["cpf"] = mascarar_cpf(contato["cpf"]) if contato.get("cpf") else ""
        for atendimento in contato["atendimentos"]:
            atendimento["analista"] = validar_analista(atendimento["analista"])

            if atendimento.get("tema") not in TEMAS:
                atendimento["tema"] = "Não classificado"
            atendimento.setdefault("origem", "salesforce")
        if contato["atendimentos"]:
            ultimo = max(datetime.strptime(a["data"], "%Y-%m-%d").date() for a in contato["atendimentos"])
            contato["ultimo_atendimento_dias"] = (hoje - ultimo).days
        else:
            contato["ultimo_atendimento_dias"] = None
    return dados


def _json(status, dados, cookies=()):
    corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
    return Resposta(status, corpo, "application/json; charset=utf-8", tuple(cookies))


def _parametro(consulta, nome, limite):
    return consulta.get(nome, [""])[0][:limite]


def _data_valida(texto):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", texto or ""):
        return None
    try:
        datetime.strptime(texto, "%Y-%m-%d")
    except ValueError:
        return None
    return texto


class Handler(BaseHTTPRequestHandler):
    server_version = "Sombra"
    sys_version = ""

    def log_message(self, formato, *argumentos):
        logging.debug(formato, *argumentos)

    def do_GET(self):
        self._atender("GET")

    def do_POST(self):
        self._atender("POST")

    def _cookies(self):
        resultado = {}
        for parte in (self.headers.get("Cookie") or "").split(";"):
            nome, separador, valor = parte.strip().partition("=")
            if separador and nome:
                resultado.setdefault(nome, valor)
        return resultado

    def _https(self):
        return self.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower() == "https"

    def _cookie(self, nome, valor, duracao):
        partes = [nome + "=" + valor, "Max-Age=" + str(duracao), "Path=/", "HttpOnly", "SameSite=Strict"]
        if self._https():
            partes.append("Secure")
        return "; ".join(partes)

    def _origem_confiavel(self):
        origem = self.headers.get("Origin")
        if origem is None:
            return True
        host = self.headers.get("Host", "")
        return origem in ("http://" + host, "https://" + host)

    def _ler_json(self):
        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if not 0 < tamanho <= TAMANHO_MAXIMO_CORPO:
            return None
        try:
            corpo = json.loads(self.rfile.read(tamanho).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return corpo if isinstance(corpo, dict) else None

    def _exigir_login(self, cookies):
        sessao = usuarios.obter_sessao(cookies.get(usuarios.NOME_COOKIE))
        if sessao is None:
            return None, _json(401, {"erro": "Sessão expirada. Entre novamente."})
        return sessao, None

    def _exigir_supervisor(self, cookies):
        sessao, erro = self._exigir_login(cookies)
        if erro is not None:
            return None, erro
        if sessao.get("papel") != "supervisor":
            return None, _json(403, {"erro": "Acesso restrito a supervisores."})
        return sessao, None

    def _pagina_inicial(self):
        if not PAGINA_INICIAL.exists():
            return Resposta(500, "Arquivo index.html não encontrado no servidor.".encode("utf-8"),
                            "text/plain; charset=utf-8", ())
        return Resposta(200, PAGINA_INICIAL.read_bytes(), "text/html; charset=utf-8", ())

    def _saude(self, caminho):
        dados = {
            "caminho_recebido": caminho,
            "https_detectado": self._https(),
            "em_vercel": usuarios.EM_VERCEL,
            "segredo_configurado": usuarios.segredo_disponivel(),
            "credenciais_analista_configuradas": usuarios.ANALISTA_CONFIGURADO,
            "credenciais_supervisor_configuradas": usuarios.SUPERVISOR_CONFIGURADO,
        }
        dados["ok"] = (dados["segredo_configurado"] and dados["credenciais_analista_configuradas"]
                       and dados["credenciais_supervisor_configuradas"])
        return _json(200, dados)

    def _tratar_get(self, caminho, consulta, cookies):
        if caminho in ("/", "/index.html"):
            return self._pagina_inicial()

        if caminho in ("/api/saude", "/api/index", "/api"):
            return self._saude(caminho)

        if caminho == "/api/sessao":
            sessao = usuarios.obter_sessao(cookies.get(usuarios.NOME_COOKIE))
            if sessao:
                return _json(200, {"logado": True, "nome": sessao["nome"], "email": sessao["email"],
                                   "papel": sessao["papel"]})
            return _json(200, {"logado": False})

        if caminho == "/api/meus-atendimentos":
            sessao, erro = self._exigir_login(cookies)
            if erro is not None:
                return erro
            return _json(200, {"atendimentos": atendimentos_do_analista(sessao["nome"])})

        if caminho == "/api/supervisor/analistas":
            _, erro = self._exigir_supervisor(cookies)
            if erro is not None:
                return erro
            return _json(200, {"analistas": resumo_por_analista()})

        if caminho == "/api/supervisor/grafico":
            _, erro = self._exigir_supervisor(cookies)
            if erro is not None:
                return erro
            tema = _parametro(consulta, "tema", 60)
            inicio = _data_valida(_parametro(consulta, "inicio", 10))
            fim = _data_valida(_parametro(consulta, "fim", 10))
            return _json(200, volumetria(tema or None, inicio, fim))

        if caminho == "/api/supervisor/atendimentos":
            _, erro = self._exigir_supervisor(cookies)
            if erro is not None:
                return erro
            nome = _parametro(consulta, "analista", 60)
            return _json(200, {"analista": nome, "atendimentos": atendimentos_do_analista(nome)})

        correspondencia = re.fullmatch(r"/api/offers/([A-Za-z0-9]{1,40})", caminho)
        if correspondencia:
            sessao, erro = self._exigir_login(cookies)
            if erro is not None:
                return erro
            offer = correspondencia.group(1)
            dados = montar_resposta(offer)
            logging.info("AUDITORIA usuario=%s offer=%s encontrada=%s", sessao["email"], offer, dados is not None)
            if dados is None:
                return _json(404, {"erro": "Offer não encontrada"})
            return _json(200, dados)

        return _json(404, {"erro": "Rota não encontrada"})

    def _tratar_post(self, caminho, cookies):
        if not self._origem_confiavel():
            return _json(403, {"erro": "Origem não permitida."})

        if caminho == "/api/login":
            corpo = self._ler_json()
            if corpo is None:
                return _json(400, {"erro": "Requisição inválida."})
            try:
                token = usuarios.autenticar(corpo.get("email"), corpo.get("senha"))
            except usuarios.SegredoNaoConfigurado:
                return _json(503, {"erro": "O servidor ainda não foi configurado: falta o segredo de sessão."})
            except usuarios.BloqueioTemporario:
                logging.info("AUDITORIA login bloqueado por excesso de tentativas")
                return _json(429, {"erro": "Muitas tentativas. Aguarde alguns minutos e tente de novo."})
            except usuarios.CredenciaisInvalidas:
                logging.info("AUDITORIA login recusado")
                return _json(401, {"erro": "E-mail ou senha incorretos."})
            sessao = usuarios.obter_sessao(token)
            logging.info("AUDITORIA login usuario=%s", sessao["email"])
            cookie = self._cookie(usuarios.NOME_COOKIE, token, usuarios.DURACAO_SESSAO)
            return _json(200, {"nome": sessao["nome"], "email": sessao["email"], "papel": sessao["papel"]}, (cookie,))

        if caminho == "/api/logout":
            sessao = usuarios.obter_sessao(cookies.get(usuarios.NOME_COOKIE))
            if sessao:
                logging.info("AUDITORIA logout usuario=%s", sessao["email"])
            return _json(200, {"ok": True}, (self._cookie(usuarios.NOME_COOKIE, "", 0),))

        sessao, erro = self._exigir_login(cookies)
        if erro is not None:
            return erro

        if caminho == "/api/busca":
            corpo = self._ler_json()
            if corpo is None:
                return _json(400, {"erro": "Requisição inválida."})
            consulta = str(corpo.get("q", ""))[:80]
            try:
                tipo, resultados = buscar(consulta)
            except BuscaInvalida as erro_busca:
                return _json(400, {"erro": str(erro_busca)})
            termo = consulta if tipo in ("offer", "imovel") else "[protegido]"
            logging.info("AUDITORIA usuario=%s busca=%s termo=%s resultados=%d",
                         sessao["email"], tipo, termo, len(resultados))
            return _json(200, {"tipo": tipo, "resultados": resultados})

        alternativos = re.fullmatch(r"/api/offers/([A-Za-z0-9]{1,40})/alternativos", caminho)
        if alternativos:
            corpo = self._ler_json()
            if corpo is None:
                return _json(400, {"erro": "Requisição inválida."})
            offer = alternativos.group(1)
            pai_id = str(corpo.get("pai_id", ""))[:20]
            try:
                novo = validar_novo_contato(corpo)
                novo_id = adicionar_alternativo(offer, pai_id, novo)
            except ContatoInvalido as erro_contato:
                return _json(400, {"erro": erro_contato.mensagem, "campo": erro_contato.campo})
            except ErroContato as erro_contato:
                return _json(erro_contato.status, {"erro": erro_contato.mensagem})
            logging.info("AUDITORIA usuario=%s offer=%s acao=adicionar_contato_alternativo qualificado=%s novo=%s",
                         sessao["email"], offer, pai_id, novo_id)
            return _json(201, {"novo_id": novo_id, "dados": montar_resposta(offer)})

        classificacao = re.fullmatch(r"/api/offers/([A-Za-z0-9]{1,40})/contatos/(alt\d{1,4})/classificacao", caminho)
        if classificacao:
            corpo = self._ler_json()
            if corpo is None:
                return _json(400, {"erro": "Requisição inválida."})
            offer, contato_id = classificacao.groups()
            try:
                texto = validar_classificacao(corpo)
                classificar_alternativo(offer, contato_id, texto)
            except ContatoInvalido as erro_contato:
                return _json(400, {"erro": erro_contato.mensagem, "campo": erro_contato.campo})
            except ErroContato as erro_contato:
                return _json(erro_contato.status, {"erro": erro_contato.mensagem})
            logging.info("AUDITORIA usuario=%s offer=%s acao=classificar_contato_alternativo contato=%s",
                         sessao["email"], offer, contato_id)
            return _json(200, {"dados": montar_resposta(offer)})

        atendimentos = re.fullmatch(r"/api/offers/([A-Za-z0-9]{1,40})/atendimentos", caminho)
        if atendimentos:
            corpo = self._ler_json()
            if corpo is None:
                return _json(400, {"erro": "Requisição inválida."})
            offer = atendimentos.group(1)
            contato_id = str(corpo.get("contato_id", ""))[:20]
            try:
                registro = validar_registro(corpo)
                agora = agora_br()
                registro.update({"data": agora.strftime("%Y-%m-%d"), "hora": agora.strftime("%H:%M"),
                                 "analista": sessao["nome"], "origem": "dashboard"})
                adicionar_atendimento(offer, contato_id, registro)
            except RegistroInvalido as erro_registro:
                return _json(400, {"erro": erro_registro.mensagem, "campo": erro_registro.campo})
            except ErroContato as erro_contato:
                return _json(erro_contato.status, {"erro": erro_contato.mensagem})
            logging.info("AUDITORIA usuario=%s offer=%s acao=registrar_atendimento contato=%s canal=%s",
                         sessao["email"], offer, contato_id, registro["canal"])
            return _json(201, {"contato_id": contato_id, "dados": montar_resposta(offer)})

        return _json(404, {"erro": "Rota não encontrada"})

    def _anexar_estado(self, resposta, estado_atual, segredo):
        if not estado_atual.alterado or resposta.status >= 300 or segredo is None:
            return resposta
        try:
            valor = estado_atual.serializar(segredo)
        except estado.LimiteDeEstado:
            return _json(409, {"erro": "Limite de registros da demonstração neste navegador atingido. "
                                       "Limpe os cookies deste site para recomeçar."})
        cookie = self._cookie(estado.NOME_COOKIE, valor, estado.DURACAO)
        return resposta._replace(cookies=resposta.cookies + (cookie,))

    def _processar(self, metodo):
        caminho, _, texto_consulta = self.path.partition("?")
        consulta = parse_qs(texto_consulta)
        cookies = self._cookies()
        segredo = usuarios.segredo_ou_none()
        with estado.ativar(cookies.get(estado.NOME_COOKIE), segredo) as estado_atual:
            if metodo == "GET":
                resposta = self._tratar_get(caminho, consulta, cookies)
            else:
                resposta = self._tratar_post(caminho, cookies)
            return self._anexar_estado(resposta, estado_atual, segredo)

    def _atender(self, metodo):
        try:
            resposta = self._processar(metodo)
        except Exception:
            logging.exception("Erro inesperado")
            resposta = _json(500, {"erro": "Erro interno do servidor."})
        self._enviar(resposta)

    def _enviar(self, resposta):
        self.send_response(resposta.status)
        self.send_header("Content-Type", resposta.tipo)
        self.send_header("Content-Length", str(len(resposta.corpo)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        for cookie in resposta.cookies:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(resposta.corpo)


if __name__ == "__main__":
    servidor = ThreadingHTTPServer(("127.0.0.1", 8000), Handler)
    print("Dashboard rodando em http://127.0.0.1:8000  (Ctrl+C para encerrar)")
    servidor.serve_forever()
