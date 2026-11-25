import json
import os
import sys
import unittest
from unittest import mock

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import assinatura
import estado
import salesforce_simulado
import usuarios

SEGREDO = b"s" * 32
OFFER = "Db0ZHelOoxLY3Inr3ZCU"


def registro(resumo="Registro de teste do nucleo."):
    return {"canal": "Chat", "tema": "Pagamentos", "ticket": "", "resumo": resumo, "data": "2026-11-07",
            "hora": "10:00", "analista": "Analista Demonstração", "origem": "dashboard"}


class TestAssinatura(unittest.TestCase):
    def test_ida_e_volta(self):
        texto = assinatura.assinar(b"conteudo", SEGREDO, "teste")
        self.assertEqual(assinatura.verificar(texto, SEGREDO, "teste"), b"conteudo")

    def test_rejeita_adulteracao_segredo_e_finalidade_diferentes(self):
        texto = assinatura.assinar(b"conteudo", SEGREDO, "teste")
        corpo, assinada = texto.split(".")
        self.assertIsNone(assinatura.verificar(assinatura.assinar(b"outro", SEGREDO, "teste").split(".")[0] + "." + assinada, SEGREDO, "teste"))
        self.assertIsNone(assinatura.verificar(texto, b"x" * 32, "teste"))
        self.assertIsNone(assinatura.verificar(texto, SEGREDO, "outra-finalidade"))

    def test_entradas_malformadas_nao_levantam_erro(self):
        for texto in ("", "semponto", "a.b.c", ".", "é.é", None, 123):
            self.assertIsNone(assinatura.verificar(texto, SEGREDO, "teste"), repr(texto))


class TestEstado(unittest.TestCase):
    def novo(self):
        return estado.Estado()

    def test_hidratacao_aplica_alternativos_classificacao_e_atendimentos(self):
        e = self.novo()
        e.anotar_alternativo(OFFER, {"id": "alt7", "pai": "c1", "nome": "Ana Reis", "telefone": "", "email": "", "cpf": ""})
        e.anotar_classificacao(OFFER, "alt7", "Irmã do comprador")
        e.anotar_atendimento(OFFER, "alt7", registro())
        offers = e.offers(salesforce_simulado._OFFERS_SEMENTE)
        contato = [c for c in offers[OFFER]["contatos"] if c["id"] == "alt7"][0]
        self.assertEqual(contato["classificacao"], "Irmã do comprador")
        self.assertEqual(contato["atendimentos"][0]["resumo"], "Registro de teste do nucleo.")
        semente = [c for c in salesforce_simulado._OFFERS_SEMENTE[OFFER]["contatos"] if c["id"] == "alt7"]
        self.assertEqual(semente, [])

    def test_ida_e_volta_pelo_cookie(self):
        e = self.novo()
        e.anotar_atendimento(OFFER, "c2", registro("Primeiro registro de teste."))
        valor = e.serializar(SEGREDO)
        with estado.ativar(valor, SEGREDO) as recarregado:
            self.assertEqual(recarregado.delta, e.delta)

    def test_cookie_invalido_ou_de_outro_segredo_vira_estado_vazio(self):
        e = self.novo()
        e.anotar_atendimento(OFFER, "c2", registro())
        valor = e.serializar(SEGREDO)
        for invalido in (valor[:-3] + "AAA", "lixo", "", None):
            with estado.ativar(invalido, SEGREDO) as r:
                self.assertEqual(r.delta, {"a": [], "r": [], "k": {}})
        with estado.ativar(valor, b"y" * 32) as r:
            self.assertEqual(r.delta["r"], [])

    def test_conteudo_assinado_mas_malformado_e_ignorado(self):
        malformado = assinatura.assinar(b"nao e zlib", SEGREDO, estado.FINALIDADE)
        with estado.ativar(malformado, SEGREDO) as r:
            self.assertEqual(r.delta["r"], [])

    def test_limite_do_cookie_levanta_excecao(self):
        e = self.novo()
        with self.assertRaises(estado.LimiteDeEstado):
            for i in range(500):
                e.anotar_atendimento(OFFER, "c2", registro(("%d " % i) * 400))
                e.serializar(SEGREDO)

    def test_cookie_de_estado_tem_tamanho_dentro_do_limite_com_uso_tipico(self):
        e = self.novo()
        for i in range(10):
            e.anotar_atendimento(OFFER, "c2", registro("Atendimento %d: cliente informou o envio dos documentos por e-mail e aguarda retorno." % i))
        self.assertLessEqual(len(e.serializar(SEGREDO)), estado.LIMITE_COOKIE)

    def test_estados_de_requisicoes_diferentes_nao_se_misturam(self):
        with estado.ativar(None, SEGREDO) as a:
            salesforce_simulado.adicionar_atendimento(OFFER, "c2", registro("So existe na requisicao A."))
            self.assertTrue(a.alterado)
        with estado.ativar(None, SEGREDO) as b:
            resumos = [x["resumo"] for x in salesforce_simulado.buscar_offer(OFFER)["contatos"][1]["atendimentos"]]
            self.assertNotIn("So existe na requisicao A.", resumos)
            self.assertFalse(b.alterado)


class TestSessaoAssinada(unittest.TestCase):
    def setUp(self):
        usuarios._FALHAS.clear()
        self.email = usuarios._variavel("SOMBRA_ANALISTA_EMAIL")
        self.senha = usuarios._variavel("SOMBRA_ANALISTA_SENHA")

    def test_autenticar_gera_token_que_obter_sessao_aceita(self):
        token = usuarios.autenticar(self.email, self.senha)
        sessao = usuarios.obter_sessao(token)
        self.assertEqual(sessao["papel"], "analista")
        self.assertEqual(sessao["nome"], usuarios.NOME_DEMO)

    def test_token_adulterado_ou_com_papel_trocado_e_recusado(self):
        token = usuarios.autenticar(self.email, self.senha)
        corpo, assinada = token.split(".")
        carga = json.loads(assinatura._desb64(corpo))
        carga["p"] = "supervisor"
        forjado = assinatura._b64(json.dumps(carga, separators=(",", ":")).encode()) + "." + assinada
        self.assertIsNone(usuarios.obter_sessao(forjado))

    def test_token_expirado_e_recusado(self):
        carga = json.dumps({"e": self.email, "n": "X", "p": "analista", "x": 1}, separators=(",", ":"))
        token = assinatura.assinar(carga.encode(), usuarios.obter_segredo(), usuarios.FINALIDADE)
        self.assertIsNone(usuarios.obter_sessao(token))

    def test_cookie_de_estado_nao_vale_como_sessao(self):
        e = estado.Estado()
        e.anotar_atendimento(OFFER, "c2", registro())
        self.assertIsNone(usuarios.obter_sessao(e.serializar(usuarios.obter_segredo())))

    def test_senha_errada_e_bloqueio_apos_cinco_falhas(self):
        for _ in range(usuarios.MAX_FALHAS):
            with self.assertRaises(usuarios.CredenciaisInvalidas):
                usuarios.autenticar("alvo@exemplo.com", "x")
        with self.assertRaises(usuarios.BloqueioTemporario):
            usuarios.autenticar("alvo@exemplo.com", "x")

    def test_login_correto_zera_as_falhas(self):
        with self.assertRaises(usuarios.CredenciaisInvalidas):
            usuarios.autenticar(self.email, "errada-errada")
        usuarios.autenticar(self.email, self.senha)
        self.assertNotIn(self.email.lower(), usuarios._FALHAS)

    def test_registro_de_falhas_nao_cresce_sem_limite(self):
        with mock.patch.object(usuarios, "ITERACOES", 1):
            for i in range(usuarios.LIMITE_REGISTRO_DE_FALHAS + 20):
                with self.assertRaises(usuarios.CredenciaisInvalidas):
                    usuarios.autenticar("a%d@exemplo.com" % i, "x")
        self.assertLessEqual(len(usuarios._FALHAS), usuarios.LIMITE_REGISTRO_DE_FALHAS + 25)


if __name__ == "__main__":
    unittest.main(verbosity=2)
