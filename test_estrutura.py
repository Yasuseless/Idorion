import ast
import json
import os
import sys
import unittest
from http.server import BaseHTTPRequestHandler

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

ARQUIVOS_DE_DEPENDENCIA = ("requirements.txt", "pyproject.toml", "Pipfile", "Pipfile.lock", "setup.py", "setup.cfg")
FRAMEWORKS = ("flask", "fastapi", "django", "starlette", "werkzeug", "redis", "upstash")


def _modulos_do_projeto():
    return sorted(f for f in os.listdir(RAIZ) if f.endswith(".py"))


def _importados(caminho):
    with open(caminho, encoding="utf-8") as f:
        arvore = ast.parse(f.read())
    nomes = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            nomes.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            nomes.add(no.module.split(".")[0])
    return nomes


class TestEstruturaParaAVercel(unittest.TestCase):
    def test_interface_fica_em_public_e_nao_na_raiz(self):
        self.assertTrue(os.path.isfile(os.path.join(RAIZ, "public", "index.html")))
        self.assertFalse(os.path.exists(os.path.join(RAIZ, "index.html")))

    def test_vercel_json_forca_other_e_reescreve_apenas_a_api_para_funcao_existente(self):
        with open(os.path.join(RAIZ, "vercel.json"), encoding="utf-8") as f:
            configuracao = json.load(f)
        self.assertIn("framework", configuracao)
        self.assertIsNone(configuracao["framework"])
        regras = configuracao["rewrites"]
        self.assertEqual(regras, [{"source": "/api/(.*)", "destination": "/api/index"}])
        self.assertTrue(os.path.isfile(os.path.join(RAIZ, "api", "index.py")))
        self.assertEqual(set(configuracao), {"framework", "rewrites"})

    def test_api_tem_somente_a_funcao_index(self):
        conteudo = [n for n in os.listdir(os.path.join(RAIZ, "api")) if n != "__pycache__"]
        self.assertEqual(conteudo, ["index.py"])

    def test_nao_ha_arquivos_de_dependencia_que_ativariam_um_preset_de_framework(self):
        presentes = [n for n in ARQUIVOS_DE_DEPENDENCIA if os.path.exists(os.path.join(RAIZ, n))]
        self.assertEqual(presentes, [])

    def test_api_index_expoe_handler_e_nao_define_app_nem_application(self):
        import importlib.util
        especificacao = importlib.util.spec_from_file_location("api_index_teste", os.path.join(RAIZ, "api", "index.py"))
        modulo = importlib.util.module_from_spec(especificacao)
        especificacao.loader.exec_module(modulo)
        self.assertTrue(issubclass(modulo.handler, BaseHTTPRequestHandler))
        self.assertFalse(hasattr(modulo, "app"))
        self.assertFalse(hasattr(modulo, "application"))

    def test_todos_os_modulos_usam_somente_a_biblioteca_padrao(self):
        projeto = {f[:-3] for f in _modulos_do_projeto()}
        externos = {}
        for arquivo in _modulos_do_projeto():
            fora = {m for m in _importados(os.path.join(RAIZ, arquivo))
                    if m not in sys.stdlib_module_names and m not in projeto}
            if fora:
                externos[arquivo] = sorted(fora)
        self.assertEqual(externos, {})

    def test_nenhum_arquivo_do_projeto_cita_framework_ou_armazenamento_externo(self):
        for arquivo in _modulos_do_projeto():
            with open(os.path.join(RAIZ, arquivo), encoding="utf-8") as f:
                texto = f.read().lower()
            for nome in FRAMEWORKS:
                self.assertNotIn("import " + nome, texto, arquivo)
                self.assertNotIn("from " + nome, texto, arquivo)

    def test_arquivo_de_exemplo_de_variaveis_nao_contem_valores(self):
        with open(os.path.join(RAIZ, ".env.example"), encoding="utf-8") as f:
            linhas = [l for l in f.read().splitlines() if l.strip()]
        self.assertIn("SOMBRA_SEGREDO=", linhas)
        for linha in linhas:
            self.assertTrue(linha.endswith("="), linha)


if __name__ == "__main__":
    unittest.main(verbosity=2)
