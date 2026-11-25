# Sombra — dashboard de demonstração (versão para a Vercel)

Dashboard de consulta e registro de atendimentos (Offers, contatos, histórico) com painel de supervisão.
**Todos os dados são fictícios.** É uma demonstração para validar a ideia, não um sistema de produção.

Sem Flask, sem banco de dados e sem serviços externos: só a biblioteca padrão do Python.

## Como funciona

- **Interface:** `public/index.html` (único arquivo servido de forma estática).
- **Backend:** `api/index.py` é a única função da Vercel. Ela reexporta o `Handler` de `app.py`
  (`BaseHTTPRequestHandler`, o mesmo servidor do projeto original). O `vercel.json` envia `/api/*` para ela.
- **Login:** cookie assinado com HMAC-SHA256 (validade de 8 horas). A senha é conferida com PBKDF2-SHA256.
- **Dados criados no dashboard** (atendimentos, contatos alternativos, classificações): ficam num segundo
  cookie assinado, no navegador de quem os criou. O servidor não guarda nada, então funciona em qualquer
  instância da Vercel.

| Arquivo | Para que serve |
|---|---|
| `app.py` | `Handler` com todas as rotas; também inicia o servidor local |
| `api/index.py` | Função da Vercel (reexporta o `Handler`) |
| `public/index.html` | Interface |
| `usuarios.py` | Credenciais, login, cookie de sessão e limite de tentativas |
| `estado.py` | Cookie de estado da demonstração |
| `assinatura.py` | Assinatura HMAC usada pelos dois cookies |
| `salesforce_simulado.py` | Dados fictícios (simulam o Salesforce) |
| `analistas.py`, `busca.py`, `contatos_alternativos.py`, `mascaramento.py`, `registros.py`, `supervisor.py` | Regras de negócio |
| `vercel.json` | `framework: null` e a reescrita de `/api/*` |

Não crie `requirements.txt`, `pyproject.toml` nem `Pipfile`: se existir um framework declarado, a Vercel passa a
ignorar a pasta `api/`. Não coloque outros `.py` dentro de `api/`: cada um viraria uma função.

## Variáveis de ambiente

| Variável | Obrigatória na Vercel? | Descrição |
|---|---|---|
| `SOMBRA_SEGREDO` | Sim | Chave dos cookies assinados. Mínimo de 32 caracteres, aleatória |
| `SOMBRA_ANALISTA_EMAIL`, `SOMBRA_ANALISTA_SENHA` | Sim | Conta do analista. Senha com 8 caracteres ou mais |
| `SOMBRA_SUPERVISOR_EMAIL`, `SOMBRA_SUPERVISOR_SENHA` | Sim | Conta do supervisor. Senha com 8 caracteres ou mais |
| `VERCEL` | Não configure | A Vercel define. Faz o app exigir as variáveis acima |

Para gerar o segredo: `py -c "import secrets; print(secrets.token_urlsafe(48))"`

- Use valores **novos**. As contas de teste de versões anteriores devem ser consideradas expostas.
- Trocar `SOMBRA_SEGREDO` encerra todas as sessões e invalida os dados guardados nos cookies.
- Na Vercel não existe valor padrão: sem as variáveis, o login responde 503 ou 401 e `/api/saude` avisa.
- O app não lê arquivo `.env`. Configure no painel da Vercel. O `.env.example` só lista os nomes.

## Rodar no seu computador

1. Python 3.12 ou mais novo (sem instalar nada).
2. Na pasta do projeto: `py app.py`
3. Abra http://127.0.0.1:8000

Localmente, sem variáveis, o app usa contas de desenvolvimento (definidas em `usuarios.py`) e gera um segredo novo a cada execução.

## Testes

`py -m unittest discover -s testes -t .`

Rode sem a variável `VERCEL` definida. Os testes sobem servidores reais (inclusive vários processos, simulando
instâncias diferentes). Eles **não** provam o comportamento da plataforma Vercel: isso só o deploy confirma.

## Publicar na Vercel

A demonstração é publicada como **Preview Deployment** de uma branch exclusiva. A `main` e a Production Branch
atual do projeto **não são alteradas**: não mude Settings → Git → Production Branch e não envie o domínio de
produção ao coordenador.

1. **Crie a branch da demonstração** a partir da `main` (versão original), com um nome diferente do da Production
   Branch atual (por exemplo, `demo-sombra`), e aplique estes arquivos nela. Não reaproveite uma branch com
   `requirements.txt`, `index.py`, `manipulador.py` ou `vercel.json` antigos.
2. **Use o projeto que já existe na Vercel**, conectado a este repositório (não crie outro). Em Settings → Build &
   Development, confirme que o Framework Preset é **Other** (o `vercel.json` já pede isso) e que Build Command e
   Output Directory estão vazios. Não mexa em Settings → Git.
3. **Cadastre as variáveis antes do primeiro push.** Em Settings → Environment Variables, adicione as 5 variáveis da
   seção "Variáveis de ambiente" marcando **somente o ambiente Preview** (se quiser, restrinja à branch da
   demonstração). Não altere as variáveis de Production. Use valores próprios para o Preview, em especial no
   `SOMBRA_SEGREDO`. Variáveis novas só valem em deploys criados depois delas.
4. **Envie a branch** para o repositório (`git push -u origin demo-sombra`). Como ela não é a Production Branch, a
   Vercel cria um Preview Deployment sozinha. Em Deployments, filtre pela branch e espere o status **Ready**.
5. **Valide o Preview você mesmo**, seguindo a seção "Validar o deploy" com o endereço do Preview no lugar de
   `URL-DO-PREVIEW`.
6. **Verifique se o coordenador consegue abrir o link.** Os Previews costumam estar protegidos (Standard Protection
   com Vercel Authentication): só usuários da Vercel com acesso os abrem. Abra o link do Preview numa **janela
   anônima**, sem login na Vercel:
   - Se aparecer a tela de login do Sombra, a proteção não está bloqueando: vá ao passo 8.
   - Se aparecer um pedido de login da Vercel, siga o passo 7.
7. **Gere um link compartilhável.** No deploy da branch (Deployments → botão **Share**), escolha **Anyone with the
   link**, troque o tipo do link de *branch* para *commit* (seta ao lado do link) e use **Copy Link**, sem copiar o
   endereço da barra do navegador. Abra o link copiado numa janela anônima e repita a validação do passo 5 por ele.
8. **Envie ao coordenador somente o link específico do Preview Deployment** que você validou (o do passo 6 ou o link
   compartilhável do passo 7). Não envie o domínio de produção nem links de outros deploys. Cada novo push ou
   Redeploy cria um deploy novo: valide-o e envie o link novo.

Observações:

- Se alterar uma variável, faça Redeploy do deploy da branch (Deployments → ⋯ → Redeploy). Links antigos continuam
  apontando para os deploys antigos.
- O convite por e-mail é o método que a Vercel recomenda para pessoas de fora, mas exige que o coordenador entre
  com uma conta da Vercel. No plano Hobby há limite de convidados e de links ativos.
- A proteção por senha da Vercel não está disponível no plano Hobby.
- Desativar a proteção do projeto (Settings → Deployment Protection) deixa todos os Previews abertos a quem tiver o
  endereço. Use só como último recurso: vale para todas as branches do projeto.

## Validar o deploy (2 minutos)

Use a **URL do Preview Deployment** da branch da demonstração, indicada abaixo como `URL-DO-PREVIEW` (se a
proteção da Vercel bloquear o acesso, siga o passo 7 de "Publicar na Vercel"). Não use o domínio de produção.

1. Abra `https://URL-DO-PREVIEW/api/saude`. Esperado: `"ok": true`, `"https_detectado": true`,
   `"em_vercel": true` e `"caminho_recebido": "/api/saude"`.
   - `"caminho_recebido": "/api/index"` significa que a plataforma entregou o caminho reescrito: me avise.
   - `"segredo_configurado": false` ou credenciais `false`: faltam variáveis no ambiente **Preview** da Vercel.
     `SOMBRA_SEGREDO` e as credenciais (`SOMBRA_ANALISTA_EMAIL`, `SOMBRA_ANALISTA_SENHA`,
     `SOMBRA_SUPERVISOR_EMAIL` e `SOMBRA_SUPERVISOR_SENHA`) devem estar cadastradas para Preview. Depois de corrigir,
     faça Redeploy.
2. Abra `https://URL-DO-PREVIEW/`: deve aparecer a tela de login. Entre como analista e como supervisor.
3. Como analista: abra uma Offer, registre um atendimento, adicione um contato, altere uma classificação e
   **recarregue a página** (F5): tudo deve continuar lá.
4. Saia, entre como supervisor **no mesmo navegador** e confira se o painel conta o registro feito.
5. Abra `/app.py`, `/usuarios.py`, `/vercel.json` e `/api/index.py`: nenhum pode mostrar código.

## Se algo não funcionar

| Sintoma | Causa provável |
|---|---|
| Tela de login avisa "código 404" | A função não está ativa: confira Framework Preset = Other, ausência de `requirements.txt`/`pyproject.toml` e se `api/index.py` está na raiz do repositório |
| Aviso "código 500" | Erro na função: veja os logs do deploy na Vercel |
| Login responde 503 | Falta `SOMBRA_SEGREDO` (32 caracteres ou mais) no ambiente **Preview** da Vercel. As demais variáveis (`SOMBRA_ANALISTA_*` e `SOMBRA_SUPERVISOR_*`) também precisam estar no Preview |
| Login diz "E-mail ou senha incorretos" | Credenciais diferentes das cadastradas na Vercel |
| Pede login da própria Vercel | O Preview está protegido. Gere o link compartilhável (passo 7 de "Publicar na Vercel") e abra-o numa janela anônima. Não use o domínio de produção |
| "Limite de registros da demonstração" | Cookie de estado cheio: limpe os cookies do site |

## Limitações conhecidas

- Os dados criados ficam **no navegador de quem os criou**: outro aparelho ou outra pessoa não os vê, e limpar os
  cookies os apaga. O painel do supervisor só enxerga o que foi criado no mesmo navegador.
- Capacidade limitada por navegador (cookie de 3.500 bytes): de 12 a 46 atendimentos com resumos de 150 a 300
  caracteres, e de 2 a 6 com resumos de 1.500 caracteres. Ao encher, a gravação é recusada com aviso.
- O logout apaga o cookie do navegador, mas uma cópia do cookie continua válida até expirar (8 horas). Não há
  revogação individual. Trocar `SOMBRA_SEGREDO` invalida todos.
- O bloqueio após 5 tentativas de login é por instância e não é compartilhado entre instâncias.
- Dois logins fixos de demonstração; sem cadastro de usuários nem recuperação de senha.
- A proteção contra CSRF usa cookie `SameSite=Strict` e a verificação de `Origin`; não há token dedicado.
- Dados fictícios e Salesforce simulado: nada aqui lê dados reais de clientes.
- Localmente, os dados criados também ficam no cookie do navegador e sobrevivem ao reinício do servidor.
