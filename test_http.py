import json
import os
import random
import string
import sys
import unittest
from unittest import mock

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import app as modulo_app
import assinatura
import estado
import usuarios
from testes.clientehttp import Cliente, ServidorDeTeste

OFFER = "Db0ZHelOoxLY3Inr3ZCU"
EMAIL_ANALISTA = usuarios._variavel("SOMBRA_ANALISTA_EMAIL")
SENHA_ANALISTA = usuarios._variavel("SOMBRA_ANALISTA_SENHA")
EMAIL_SUPERVISOR = usuarios._variavel("SOMBRA_SUPERVISOR_EMAIL")
SENHA_SUPERVISOR = usuarios._variavel("SOMBRA_SUPERVISOR_SENHA")
CPF_NOVO_VALIDO = "12345678909"


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.servidor = ServidorDeTeste(modulo_app.Handler)

    @classmethod
    def tearDownClass(cls):
        cls.servidor.parar()

    def setUp(self):
        usuarios._FALHAS.clear()
        self.cliente = self.novo_cliente()

    def novo_cliente(self, **extras):
        return Cliente(self.servidor.porta, **extras)

    def entrar(self, cliente=None, email=None, senha=None):
        return (cliente or self.cliente).post("/api/login", {"email": email or EMAIL_ANALISTA, "senha": senha or SENHA_ANALISTA})

    def entrar_supervisor(self, cliente=None):
        return self.entrar(cliente, EMAIL_SUPERVISOR, SENHA_SUPERVISOR)

    def contatos(self, resposta):
        return {c["id"]: c for c in resposta.get_json()["dados"]["contatos"]}


class TestPaginaEErros(_Base):
    def test_pagina_inicial_e_index_html(self):
        with open(os.path.join(RAIZ, "public", "index.html"), "rb") as f:
            esperado = f.read()
        for caminho in ("/", "/index.html"):
            resposta = self.cliente.get(caminho)
            self.assertEqual(resposta.status_code, 200)
            self.assertIn("text/html", resposta.cabecalho("Content-Type"))
            self.assertEqual(resposta.corpo, esperado)
            self.assertIn(b"<title>Consulta de atendimentos</title>", resposta.corpo)

    def test_rota_inexistente_retorna_404_json(self):
        resposta = self.cliente.get("/nada/aqui")
        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(resposta.get_json(), {"erro": "Rota não encontrada"})

    def test_arquivos_do_projeto_nao_sao_servidos(self):
        for caminho in ("/app.py", "/usuarios.py", "/vercel.json", "/.env.example", "/api/index.py", "/estado.py"):
            resposta = self.cliente.get(caminho)
            self.assertEqual(resposta.status_code, 404, caminho)
            self.assertNotIn("import ", resposta.texto(), caminho)

    def test_cabecalhos_de_seguranca(self):
        resposta = self.cliente.get("/api/sessao")
        self.assertEqual(resposta.cabecalho("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resposta.cabecalho("X-Frame-Options"), "DENY")
        self.assertEqual(resposta.cabecalho("Cache-Control"), "no-store")

    def test_saude_local_e_alias_para_diagnostico_do_caminho(self):
        for caminho in ("/api/saude", "/api/index", "/api"):
            corpo = self.cliente.get(caminho).get_json()
            self.assertTrue(corpo["ok"], caminho)
            self.assertEqual(corpo["caminho_recebido"], caminho)
            self.assertFalse(corpo["https_detectado"])
        texto = json.dumps(self.cliente.get("/api/saude").get_json())
        self.assertNotIn(SENHA_ANALISTA, texto)


class TestLoginESessao(_Base):
    def test_login_valido_do_analista_e_do_supervisor(self):
        resposta = self.entrar()
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.get_json()["papel"], "analista")
        self.assertIn(usuarios.NOME_COOKIE, self.cliente.cookies)
        outro = self.novo_cliente()
        resposta = self.entrar_supervisor(outro)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.get_json()["papel"], "supervisor")

    def test_login_invalido(self):
        self.assertEqual(self.entrar(senha="senha-errada").status_code, 401)
        self.assertEqual(self.entrar(email="ninguem@exemplo.com").status_code, 401)
        self.assertNotIn(usuarios.NOME_COOKIE, self.cliente.cookies)

    def test_login_sem_corpo_valido(self):
        self.assertEqual(self.cliente.post("/api/login", dados_brutos="").status_code, 400)
        self.assertEqual(self.cliente.post("/api/login", dados_brutos="[1,2]").status_code, 400)
        self.assertEqual(self.cliente.post("/api/login", dados_brutos="nao e json").status_code, 400)

    def test_bloqueio_apos_tentativas_invalidas(self):
        for _ in range(usuarios.MAX_FALHAS):
            self.assertEqual(self.entrar(email="alvo@exemplo.com", senha="x").status_code, 401)
        self.assertEqual(self.entrar(email="alvo@exemplo.com", senha="x").status_code, 429)

    def test_sessao_persiste_entre_requisicoes_expira_e_encerra(self):
        self.entrar()
        self.assertTrue(self.cliente.get("/api/sessao").get_json()["logado"])
        self.assertEqual(self.cliente.get("/api/offers/" + OFFER).status_code, 200)

        carga = json.dumps({"e": EMAIL_ANALISTA, "n": "X", "p": "analista", "x": 1}, separators=(",", ":"))
        expirado = assinatura.assinar(carga.encode(), usuarios.obter_segredo(), usuarios.FINALIDADE)
        vencido = self.novo_cliente()
        vencido.cookies[usuarios.NOME_COOKIE] = expirado
        self.assertFalse(vencido.get("/api/sessao").get_json()["logado"])
        self.assertEqual(vencido.get("/api/offers/" + OFFER).status_code, 401)

        self.assertEqual(self.cliente.post("/api/logout", {}).status_code, 200)
        self.assertNotIn(usuarios.NOME_COOKIE, self.cliente.cookies)
        self.assertFalse(self.cliente.get("/api/sessao").get_json()["logado"])

    def test_cookie_adulterado_ou_papel_forjado_e_recusado(self):
        self.entrar()
        token = self.cliente.cookies[usuarios.NOME_COOKIE]
        corpo, assinada = token.split(".")
        carga = json.loads(assinatura._desb64(corpo))
        carga["p"] = "supervisor"
        forjado = assinatura._b64(json.dumps(carga, separators=(",", ":")).encode()) + "." + assinada
        atacante = self.novo_cliente()
        atacante.cookies[usuarios.NOME_COOKIE] = forjado
        self.assertFalse(atacante.get("/api/sessao").get_json()["logado"])
        self.assertEqual(atacante.get("/api/supervisor/analistas").status_code, 401)

    def test_cookie_de_sessao_em_http_nao_tem_secure(self):
        linhas = self.entrar().cookies_definidos()
        self.assertEqual(len(linhas), 1)
        for atributo in ("HttpOnly", "SameSite=Strict", "Path=/", "Max-Age=28800"):
            self.assertIn(atributo, linhas[0])
        self.assertNotIn("Secure", linhas[0])

    def test_cookie_de_sessao_em_https_tem_secure(self):
        cliente = self.novo_cliente(host="demo.exemplo.com", https=True)
        linhas = self.entrar(cliente).cookies_definidos()
        self.assertEqual(len(linhas), 1)
        self.assertIn("Secure", linhas[0])

    def test_origin_de_outro_site_e_recusado_e_do_mesmo_host_e_aceito(self):
        corpo = {"email": EMAIL_ANALISTA, "senha": SENHA_ANALISTA}
        self.assertEqual(self.cliente.post("/api/login", corpo, cabecalhos={"Origin": "https://site-malicioso.example.com"}).status_code, 403)
        for origem in ("https://localhost", "http://localhost"):
            cliente = self.novo_cliente(host="localhost")
            self.assertEqual(cliente.post("/api/login", corpo, cabecalhos={"Origin": origem}).status_code, 200, origem)

    def test_metodo_nao_suportado_nao_derruba_o_servidor(self):
        self.assertEqual(self.cliente.put("/api/login").status_code, 501)
        self.assertEqual(self.cliente.get("/api/sessao").status_code, 200)


class TestPermissoes(_Base):
    ROTAS_SUPERVISOR = ("/api/supervisor/analistas", "/api/supervisor/grafico",
                        "/api/supervisor/atendimentos?analista=Analista%204")

    def test_sem_login_recebe_401(self):
        for rota in self.ROTAS_SUPERVISOR + ("/api/offers/" + OFFER, "/api/meus-atendimentos"):
            self.assertEqual(self.cliente.get(rota).status_code, 401, rota)
        self.assertEqual(self.cliente.post("/api/busca", {"q": OFFER}).status_code, 401)

    def test_analista_nao_acessa_rotas_do_supervisor(self):
        self.entrar()
        for rota in self.ROTAS_SUPERVISOR:
            self.assertEqual(self.cliente.get(rota).status_code, 403, rota)
        self.assertEqual(self.cliente.get("/api/meus-atendimentos").status_code, 200)

    def test_supervisor_acessa_suas_rotas_e_tambem_as_de_consulta(self):
        self.entrar_supervisor()
        for rota in self.ROTAS_SUPERVISOR:
            self.assertEqual(self.cliente.get(rota).status_code, 200, rota)
        self.assertEqual(self.cliente.get("/api/offers/" + OFFER).status_code, 200)


class TestBusca(_Base):
    def setUp(self):
        super().setUp()
        self.entrar()

    def buscar(self, consulta):
        return self.cliente.post("/api/busca", {"q": consulta})

    def test_busca_por_offer(self):
        corpo = self.buscar(OFFER).get_json()
        self.assertEqual(corpo["tipo"], "offer")
        self.assertEqual([r["offer"] for r in corpo["resultados"]], [OFFER])

    def test_busca_por_id_do_imovel(self):
        corpo = self.buscar("890250358").get_json()
        self.assertEqual(corpo["tipo"], "imovel")
        self.assertEqual([r["offer"] for r in corpo["resultados"]], [OFFER])

    def test_busca_por_dados_de_contato_censurados_e_completos(self):
        casos = (("+55119****4321", "telefone"), ("***.982.247-**", "cpf"),
                 ("marina.alves@exemplo.com", "email"), ("52998224725", "cpf"))
        for consulta, tipo in casos:
            corpo = self.buscar(consulta).get_json()
            self.assertEqual(corpo["tipo"], tipo, consulta)
            self.assertIn(OFFER, [r["offer"] for r in corpo["resultados"]], consulta)

    def test_busca_invalida_retorna_400(self):
        for consulta in ("", "@@@", "abc def", "***.***.***-**"):
            self.assertEqual(self.buscar(consulta).status_code, 400, consulta)

    def test_resposta_da_offer_nao_expoe_dados_completos(self):
        texto = self.cliente.get("/api/offers/" + OFFER).texto()
        for dado in ("52998224725", "marina.alves@exemplo.com", "98765-4321"):
            self.assertNotIn(dado, texto)

    def test_carrega_contatos_incluindo_alternativo_da_semente(self):
        contatos = {c["id"]: c for c in self.cliente.get("/api/offers/" + OFFER).get_json()["contatos"]}
        self.assertEqual(set(contatos), {"c1", "c2", "c3", "alt1"})
        self.assertEqual(contatos["alt1"]["papel"], "alternativo")


class TestAtendimentosEContatos(_Base):
    def setUp(self):
        super().setUp()
        self.entrar()
        self.rota = "/api/offers/" + OFFER

    def registrar(self, **campos):
        corpo = {"contato_id": "c2", "canal": "Chat", "tema": "Pagamentos",
                 "resumo": "Teste de registro feito pelo analista."}
        corpo.update(campos)
        return self.cliente.post(self.rota + "/atendimentos", corpo)

    def test_registro_valido(self):
        resposta = self.registrar()
        self.assertEqual(resposta.status_code, 201)
        atendimentos = self.contatos(resposta)["c2"]["atendimentos"]
        novo = max(atendimentos, key=lambda a: (a["data"], a.get("hora", "")))
        self.assertEqual(novo["origem"], "dashboard")
        self.assertEqual(novo["tema"], "Pagamentos")
        self.assertEqual(novo["analista"], "Analista Demonstração")
        self.assertRegex(novo["hora"], r"^\d{2}:\d{2}$")
        self.assertEqual(novo["data"], modulo_app.agora_br().strftime("%Y-%m-%d"))

    def test_registro_invalido(self):
        casos = (({"tema": ""}, "tema"), ({"tema": "Tema que nao existe"}, "tema"),
                 ({"canal": "Pombo-correio"}, "canal"), ({"resumo": "curto"}, "resumo"),
                 ({"resumo": "O cliente informou o CPF 529.982.247-25 no chat."}, "resumo"),
                 ({"ticket": "TK 12/3"}, "ticket"))
        for campos, campo in casos:
            resposta = self.registrar(**campos)
            self.assertEqual(resposta.status_code, 400, campos)
            self.assertEqual(resposta.get_json()["campo"], campo, campos)

    def test_registro_em_contato_inexistente_retorna_404_e_ticket_e_opcional(self):
        self.assertEqual(self.registrar(contato_id="zzz").status_code, 404)
        self.assertEqual(self.registrar(ticket="").status_code, 201)

    def test_contato_alternativo_e_bloqueio_de_duplicidade(self):
        corpo = {"pai_id": "c2", "nome": "D'Ávila-Souza Teste", "cpf": CPF_NOVO_VALIDO}
        resposta = self.cliente.post(self.rota + "/alternativos", corpo)
        self.assertEqual(resposta.status_code, 201)
        self.assertEqual(self.contatos(resposta)[resposta.get_json()["novo_id"]]["papel"], "alternativo")
        repetido = self.cliente.post(self.rota + "/alternativos", dict(corpo, nome="Outra Pessoa"))
        self.assertEqual(repetido.status_code, 409)

    def test_contato_alternativo_invalido(self):
        casos = ({"nome": ""}, {"nome": "Ana 2"}, {"nome": "Ana Reis", "cpf": "123.456.789-00"},
                 {"nome": "Ana Reis", "email": "ana@"}, {"nome": "Ana Reis", "telefone": "123"})
        for campos in casos:
            resposta = self.cliente.post(self.rota + "/alternativos", dict({"pai_id": "c2"}, **campos))
            self.assertEqual(resposta.status_code, 400, campos)

    def test_limite_de_um_alternativo_para_o_executivo_de_negociacao(self):
        primeiro = self.cliente.post(self.rota + "/alternativos", {"pai_id": "c3", "nome": "Assistente Um"})
        segundo = self.cliente.post(self.rota + "/alternativos", {"pai_id": "c3", "nome": "Assistente Dois"})
        self.assertEqual((primeiro.status_code, segundo.status_code), (201, 409))

    def test_classificacao_de_contato_alternativo(self):
        rota = self.rota + "/contatos/alt1/classificacao"
        resposta = self.cliente.post(rota, {"classificacao": "Irmã do comprador"})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(self.contatos(resposta)["alt1"]["classificacao"], "Irmã do comprador")
        invalida = self.cliente.post(rota, {"classificacao": "Irmã 2"})
        self.assertEqual((invalida.status_code, invalida.get_json()["campo"]), (400, "classificacao"))
        self.assertEqual(self.cliente.post(self.rota + "/contatos/c1/classificacao", {"classificacao": "X Y"}).status_code, 404)

    def test_corpo_json_invalido_retorna_400(self):
        self.assertEqual(self.cliente.post(self.rota + "/atendimentos", dados_brutos="isto nao e json").status_code, 400)


class TestSupervisorFiltros(_Base):
    def setUp(self):
        super().setUp()
        self.entrar_supervisor()

    def grafico(self, consulta=""):
        return self.cliente.get("/api/supervisor/grafico" + consulta)

    def test_filtro_por_tema_e_periodo(self):
        corpo = self.grafico("?tema=Prazos&inicio=2026-09-01&fim=2026-09-30").get_json()
        self.assertEqual(corpo["total"], 2)
        self.assertEqual([s["tema"] for s in corpo["series"]], ["Prazos"])
        self.assertEqual(self.grafico("?inicio=2026-12-01&fim=2026-12-31").get_json()["total"], 0)

    def test_datas_invalidas_sao_ignoradas_sem_erro_500(self):
        base = self.grafico().get_json()
        for consulta in ("?inicio=2026-13-45", "?fim=2026-02-30", "?inicio=2026-9-5", "?inicio=abc&fim=;;"):
            resposta = self.grafico(consulta)
            self.assertEqual(resposta.status_code, 200, consulta)
            corpo = resposta.get_json()
            self.assertEqual((corpo["inicio"], corpo["fim"]), (base["inicio"], base["fim"]), consulta)

    def test_tema_desconhecido_nao_derruba_o_grafico(self):
        self.assertEqual(self.grafico("?tema=Tema%20que%20nao%20existe").status_code, 200)

    def test_analistas_e_atendimentos_do_analista(self):
        analistas = self.cliente.get("/api/supervisor/analistas").get_json()["analistas"]
        self.assertTrue(any(a["total"] > 0 for a in analistas))
        corpo = self.cliente.get("/api/supervisor/atendimentos?analista=Analista%204").get_json()
        self.assertTrue(all(a["analista"] == "Analista 4" for a in corpo["atendimentos"]))


class TestEstadoNoNavegador(_Base):
    def setUp(self):
        super().setUp()
        self.entrar()
        self.rota = "/api/offers/" + OFFER

    def registrar(self, cliente, resumo="Registro para testar o estado no navegador."):
        return cliente.post(self.rota + "/atendimentos", {"contato_id": "c2", "canal": "Chat", "tema": "Pagamentos", "resumo": resumo})

    def resumos(self, cliente):
        contatos = {c["id"]: c for c in cliente.get(self.rota).get_json()["contatos"]}
        return [a["resumo"] for a in contatos["c2"]["atendimentos"]]

    def test_escrita_define_exatamente_um_cookie_de_estado_com_flags(self):
        linhas = self.registrar(self.cliente).cookies_definidos()
        self.assertEqual(len(linhas), 1)
        self.assertTrue(linhas[0].startswith(estado.NOME_COOKIE + "="))
        for atributo in ("HttpOnly", "SameSite=Strict", "Path=/", "Max-Age=604800"):
            self.assertIn(atributo, linhas[0])

    def test_leituras_nao_definem_cookie(self):
        for rota in ("/api/sessao", self.rota, "/api/meus-atendimentos"):
            self.assertEqual(self.cliente.get(rota).cookies_definidos(), [], rota)

    def test_dados_criados_aparecem_nas_proximas_requisicoes_e_so_para_quem_tem_o_cookie(self):
        self.registrar(self.cliente, "Registro que deve aparecer depois.")
        self.assertIn("Registro que deve aparecer depois.", self.resumos(self.cliente))
        outro = self.novo_cliente()
        self.entrar(outro)
        self.assertNotIn("Registro que deve aparecer depois.", self.resumos(outro))

    def test_supervisor_no_mesmo_navegador_ve_o_que_o_analista_registrou(self):
        supervisor = self.cliente.copia()
        antes = self.novo_cliente()
        self.entrar_supervisor(antes)
        total_antes = antes.get("/api/supervisor/grafico").get_json()["total"]
        self.registrar(self.cliente)
        supervisor.cookies.pop(usuarios.NOME_COOKIE, None)
        supervisor.cookies.update({k: v for k, v in self.cliente.cookies.items() if k == estado.NOME_COOKIE})
        self.entrar_supervisor(supervisor)
        self.assertEqual(supervisor.get("/api/supervisor/grafico").get_json()["total"], total_antes + 1)
        analistas = {a["analista"]: a for a in supervisor.get("/api/supervisor/analistas").get_json()["analistas"]}
        self.assertGreaterEqual(analistas["Analista Demonstração"]["total"], 1)

    def test_alternativo_classificacao_e_duplicidade_persistem_pelo_cookie(self):
        corpo = {"pai_id": "c2", "nome": "Pessoa Persistente", "cpf": CPF_NOVO_VALIDO}
        novo_id = self.cliente.post(self.rota + "/alternativos", corpo).get_json()["novo_id"]
        classificacao = self.cliente.post(self.rota + "/contatos/%s/classificacao" % novo_id, {"classificacao": "Assistente do vendedor"})
        self.assertEqual(classificacao.status_code, 200)
        contatos = {c["id"]: c for c in self.cliente.get(self.rota).get_json()["contatos"]}
        self.assertEqual(contatos[novo_id]["classificacao"], "Assistente do vendedor")
        self.assertEqual(self.cliente.post(self.rota + "/alternativos", dict(corpo, nome="Outra Pessoa")).status_code, 409)

    def test_registros_em_sequencia_acumulam(self):
        for i in range(5):
            self.assertEqual(self.registrar(self.cliente, "Registro numero %d do teste de acumulo." % i).status_code, 201)
        resumos = self.resumos(self.cliente)
        for i in range(5):
            self.assertIn("Registro numero %d do teste de acumulo." % i, resumos)

    def test_cookie_de_estado_adulterado_ou_de_outro_segredo_e_ignorado_sem_erro(self):
        self.registrar(self.cliente, "Registro que nao deve sobreviver a adulteracao.")
        valido = self.cliente.cookies[estado.NOME_COOKIE]
        for ruim in (valido[:-4] + "AAAA", "lixo", assinatura.assinar(b"x", b"z" * 32, estado.FINALIDADE)):
            cliente = self.cliente.copia()
            cliente.cookies[estado.NOME_COOKIE] = ruim
            resposta = cliente.get(self.rota)
            self.assertEqual(resposta.status_code, 200)
            self.assertNotIn("Registro que nao deve sobreviver a adulteracao.", self.resumos(cliente))

    def test_limite_recusa_com_409_sem_perder_o_que_ja_existe(self):
        self.registrar(self.cliente, "Registro anterior ao limite que precisa sobreviver.")
        cookie_antes = self.cliente.cookies[estado.NOME_COOKIE]
        sorteio = random.Random(2026)
        recusada = None
        for i in range(40):
            texto = " ".join("".join(sorteio.choice(string.ascii_lowercase) for _ in range(sorteio.randint(3, 9)))
                             for _ in range(300))[:1490]
            resposta = self.registrar(self.cliente, texto)
            if resposta.status_code == 409:
                recusada = resposta
                break
        self.assertIsNotNone(recusada)
        self.assertIn("Limite de registros", recusada.get_json()["erro"])
        self.assertEqual(recusada.cookies_definidos(), [])
        self.assertNotEqual(self.cliente.cookies[estado.NOME_COOKIE], cookie_antes)
        self.assertIn("Registro anterior ao limite que precisa sobreviver.", self.resumos(self.cliente))
        self.assertEqual(self.cliente.get("/api/sessao").status_code, 200)


class TestFalhasSeguras(_Base):
    def test_erro_inesperado_retorna_500_generico_e_servidor_continua(self):
        self.entrar()
        with mock.patch.object(modulo_app, "buscar", side_effect=RuntimeError("SEGREDO-INTERNO")):
            resposta = self.cliente.post("/api/busca", {"q": OFFER})
        self.assertEqual(resposta.status_code, 500)
        self.assertEqual(resposta.get_json(), {"erro": "Erro interno do servidor."})
        self.assertNotIn("SEGREDO-INTERNO", resposta.texto())
        self.assertNotIn("Traceback", resposta.texto())
        self.assertEqual(self.cliente.post("/api/busca", {"q": OFFER}).status_code, 200)

    def test_sem_segredo_na_vercel_login_retorna_503_e_saude_avisa(self):
        with mock.patch.object(usuarios, "EM_VERCEL", True), mock.patch.dict(os.environ, {"SOMBRA_SEGREDO": ""}):
            resposta = self.entrar()
            saude = self.cliente.get("/api/saude").get_json()
            sessao = self.cliente.get("/api/sessao")
        self.assertEqual(resposta.status_code, 503)
        self.assertIn("segredo", resposta.get_json()["erro"])
        self.assertFalse(saude["ok"])
        self.assertFalse(saude["segredo_configurado"])
        self.assertEqual(sessao.get_json(), {"logado": False})

    def test_segredo_curto_na_vercel_e_considerado_ausente(self):
        with mock.patch.object(usuarios, "EM_VERCEL", True), mock.patch.dict(os.environ, {"SOMBRA_SEGREDO": "curto"}):
            self.assertFalse(usuarios.segredo_disponivel())
            with self.assertRaises(usuarios.SegredoNaoConfigurado):
                usuarios.obter_segredo()


if __name__ == "__main__":
    unittest.main(verbosity=2)
