import os
import socket
import subprocess
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

from testes.clientehttp import Cliente

SEGREDO = "segredo-de-teste-das-instancias-0123456789"
SEGREDO_DIFERENTE = "outro-segredo-de-teste-9876543210-fedcba"
EMAIL_ANALISTA = "analista.teste@exemplo.com"
SENHA_ANALISTA = "senha-do-analista-1"
EMAIL_SUPERVISOR = "supervisor.teste@exemplo.com"
SENHA_SUPERVISOR = "senha-do-supervisor-1"
OFFER = "Db0ZHelOoxLY3Inr3ZCU"
HOST = "demo.exemplo.com"


def _porta_livre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Instancia:
    def __init__(self, segredo):
        self.porta = _porta_livre()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("SOMBRA_", "VERCEL", "KV_", "UPSTASH_"))}
        env.update({"VERCEL": "1", "SOMBRA_SEGREDO": segredo,
                    "SOMBRA_ANALISTA_EMAIL": EMAIL_ANALISTA, "SOMBRA_ANALISTA_SENHA": SENHA_ANALISTA,
                    "SOMBRA_SUPERVISOR_EMAIL": EMAIL_SUPERVISOR, "SOMBRA_SUPERVISOR_SENHA": SENHA_SUPERVISOR})
        codigo = ("import sys; sys.path.insert(0, %r); sys.path.insert(0, %r); "
                  "import importlib.util as u; e = u.spec_from_file_location('api_index', %r); m = u.module_from_spec(e); "
                  "e.loader.exec_module(m); from http.server import HTTPServer; "
                  "HTTPServer(('127.0.0.1', %d), m.handler).serve_forever()"
                  % (RAIZ, os.path.join(RAIZ, "api"), os.path.join(RAIZ, "api", "index.py"), self.porta))
        self.processo = subprocess.Popen([sys.executable, "-c", codigo], cwd=RAIZ, env=env,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._aguardar()

    def _aguardar(self):
        limite = time.time() + 30
        while time.time() < limite:
            if self.processo.poll() is not None:
                raise RuntimeError("a instância encerrou ao iniciar")
            try:
                socket.create_connection(("127.0.0.1", self.porta), timeout=0.5).close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("a instância não respondeu a tempo")

    def parar(self):
        self.processo.terminate()
        try:
            self.processo.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.processo.kill()

    def cliente(self, **extras):
        return Cliente(self.porta, **extras)


class TestVariasInstancias(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = Instancia(SEGREDO)
        cls.b = Instancia(SEGREDO)
        cls.c = Instancia(SEGREDO_DIFERENTE)

    @classmethod
    def tearDownClass(cls):
        for instancia in (cls.a, cls.b, cls.c):
            instancia.parar()

    def em(self, instancia, origem):
        cliente = instancia.cliente()
        cliente.cookies = dict(origem.cookies)
        return cliente

    def entrar(self, cliente, email=EMAIL_ANALISTA, senha=SENHA_ANALISTA):
        return cliente.post("/api/login", {"email": email, "senha": senha})

    def registrar(self, cliente, resumo):
        return cliente.post("/api/offers/%s/atendimentos" % OFFER,
                            {"contato_id": "c2", "canal": "Chat", "tema": "Pagamentos", "resumo": resumo})

    def resumos(self, cliente):
        contatos = {c["id"]: c for c in cliente.get("/api/offers/" + OFFER).get_json()["contatos"]}
        return [a["resumo"] for a in contatos["c2"]["atendimentos"]]

    def test_as_instancias_sao_processos_distintos(self):
        self.assertEqual(len({self.a.processo.pid, self.b.processo.pid, self.c.processo.pid}), 3)
        self.assertTrue(self.a.cliente().get("/api/saude").get_json()["ok"])
        self.assertTrue(self.a.cliente().get("/api/saude").get_json()["em_vercel"])

    def test_login_numa_instancia_vale_em_outra_com_o_papel_correto(self):
        sessao_a = self.a.cliente()
        self.assertEqual(self.entrar(sessao_a).status_code, 200)
        em_b = self.em(self.b, sessao_a)
        corpo = em_b.get("/api/sessao").get_json()
        self.assertEqual((corpo["logado"], corpo["papel"]), (True, "analista"))
        self.assertEqual(em_b.get("/api/meus-atendimentos").status_code, 200)
        self.assertEqual(em_b.get("/api/supervisor/analistas").status_code, 403)

    def test_supervisor_logado_numa_instancia_acessa_painel_em_outra(self):
        sessao_a = self.a.cliente()
        self.assertEqual(self.entrar(sessao_a, EMAIL_SUPERVISOR, SENHA_SUPERVISOR).status_code, 200)
        em_b = self.em(self.b, sessao_a)
        self.assertEqual(em_b.get("/api/supervisor/analistas").status_code, 200)
        self.assertEqual(em_b.get("/api/supervisor/grafico").status_code, 200)

    def test_cookie_nao_vale_em_instancia_com_outro_segredo(self):
        sessao_a = self.a.cliente()
        self.entrar(sessao_a, EMAIL_SUPERVISOR, SENHA_SUPERVISOR)
        em_c = self.em(self.c, sessao_a)
        self.assertFalse(em_c.get("/api/sessao").get_json()["logado"])
        self.assertEqual(em_c.get("/api/supervisor/analistas").status_code, 401)

    def test_dados_criados_numa_instancia_aparecem_em_outra_e_no_painel(self):
        sessao_a = self.a.cliente()
        self.entrar(sessao_a)
        antes = self.b.cliente()
        self.entrar(antes, EMAIL_SUPERVISOR, SENHA_SUPERVISOR)
        total_antes = antes.get("/api/supervisor/grafico").get_json()["total"]

        self.assertEqual(self.registrar(sessao_a, "Registro criado na instancia A para ser lido na B.").status_code, 201)
        em_b = self.em(self.b, sessao_a)
        self.assertIn("Registro criado na instancia A para ser lido na B.", self.resumos(em_b))

        supervisor_b = self.b.cliente()
        supervisor_b.cookies = {k: v for k, v in sessao_a.cookies.items() if k == "sombra_estado"}
        self.entrar(supervisor_b, EMAIL_SUPERVISOR, SENHA_SUPERVISOR)
        self.assertEqual(supervisor_b.get("/api/supervisor/grafico").get_json()["total"], total_antes + 1)

    def test_escrita_na_instancia_b_continua_o_que_a_instancia_a_gravou(self):
        sessao_a = self.a.cliente()
        self.entrar(sessao_a)
        self.registrar(sessao_a, "Primeiro registro, gravado na instancia A.")
        em_b = self.em(self.b, sessao_a)
        self.registrar(em_b, "Segundo registro, gravado na instancia B.")
        em_a = self.em(self.a, em_b)
        resumos = self.resumos(em_a)
        self.assertIn("Primeiro registro, gravado na instancia A.", resumos)
        self.assertIn("Segundo registro, gravado na instancia B.", resumos)

    def test_estado_de_instancia_com_outro_segredo_e_ignorado(self):
        sessao_a = self.a.cliente()
        self.entrar(sessao_a)
        self.registrar(sessao_a, "Registro que a instancia C nao pode ler.")
        em_c = self.c.cliente()
        em_c.cookies = {k: v for k, v in sessao_a.cookies.items() if k == "sombra_estado"}
        self.assertEqual(self.entrar(em_c).status_code, 200)
        self.assertNotIn("Registro que a instancia C nao pode ler.", self.resumos(em_c))

    def test_atras_do_proxy_https_os_cookies_saem_com_secure(self):
        cliente = self.a.cliente(host=HOST, https=True)
        login = self.entrar(cliente)
        self.assertEqual(login.status_code, 200)
        self.assertTrue(all("Secure" in c for c in login.cookies_definidos()))
        escrita = self.registrar(cliente, "Registro atras do proxy https para checar o cookie.")
        self.assertEqual(escrita.status_code, 201)
        self.assertEqual(len(escrita.cookies_definidos()), 1)
        self.assertIn("Secure", escrita.cookies_definidos()[0])
        self.assertTrue(cliente.get("/api/saude").get_json()["https_detectado"])

    def test_limitacao_conhecida_logout_nao_revoga_uma_copia_do_cookie(self):
        sessao_a = self.a.cliente()
        self.entrar(sessao_a)
        copia = self.em(self.b, sessao_a)
        sessao_a.post("/api/logout", {})
        self.assertFalse(sessao_a.get("/api/sessao").get_json()["logado"])
        self.assertTrue(copia.get("/api/sessao").get_json()["logado"])

    def test_limitacao_conhecida_bloqueio_de_tentativas_e_por_instancia(self):
        alvo = "alvo.bloqueio@exemplo.com"
        a = self.a.cliente()
        for _ in range(5):
            self.assertEqual(self.entrar(a, alvo, "errada-errada").status_code, 401)
        self.assertEqual(self.entrar(a, alvo, "errada-errada").status_code, 429)
        self.assertEqual(self.entrar(self.b.cliente(), alvo, "errada-errada").status_code, 401)
        self.assertEqual(self.entrar(self.a.cliente(), EMAIL_ANALISTA, SENHA_ANALISTA).status_code, 200)

    def test_requisicoes_simultaneas_nao_misturam_o_estado_de_cada_navegador(self):
        clientes = []
        for i in range(6):
            cliente = self.a.cliente()
            self.entrar(cliente)
            self.registrar(cliente, "Registro exclusivo do navegador numero %d." % i)
            clientes.append(cliente)

        def verificar(i):
            cliente = clientes[i].copia()
            for _ in range(8):
                resumos = self.resumos(cliente)
                exclusivos = [r for r in resumos if r.startswith("Registro exclusivo do navegador")]
                if exclusivos != ["Registro exclusivo do navegador numero %d." % i]:
                    return False
            return True

        with ThreadPoolExecutor(max_workers=6) as pool:
            resultados = list(pool.map(verificar, range(6)))
        self.assertEqual(resultados, [True] * 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
