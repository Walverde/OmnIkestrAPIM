# TrueNAS SCALE 25.10 (Goldeye) — implantação e operação

Este procedimento usa **Custom App com Compose YAML**. A aplicação exige
autenticação de administrador, sessão e chave de serviço. O Compose atual
vincula a porta ao IP LAN e usa HTTP; mantenha-o em rede confiável até configurar
HTTPS e `OMNIKESTRAPIM_COOKIE_SECURE=true`. Não exponha a API à internet sem
HTTPS e uma revisão da configuração de implantação.

## Pré-requisitos e caminhos

- TrueNAS SCALE 25.10.x com Apps habilitado.
- Acesso ao GHCR para baixar as imagens privadas, caso o registry exija login.
- Dataset de produtos e diretório/dataset persistente do PostgreSQL.
- Endereço LAN reservado para o NAS; o YAML atual usa `192.168.100.58`.
- UID/GID com acesso de leitura e gravação aos produtos; o exemplo validado
  usa `568:568`.

O Compose versionado está configurado para:

- `/mnt/gbZaid/OmnIkestrAPIM-Products` — dataset persistente dos produtos,
  separado do checkout Git e mantido entre atualizações. O backend monta esse
  volume com escrita para atualizar manifestos; o watcher o monta como
  somente leitura, pois apenas observa arquivos e notifica a API.
- `/mnt/gbZaid/OmnIkestrAPIM/postgres` — dados persistentes do PostgreSQL.
- `/mnt/gbZaid/OmnIkestrAPIM/secrets/postgres_password` — arquivo secreto no
  host, fora do Git.

Crie o dataset de produtos uma única vez pela interface do TrueNAS ou pelo
procedimento de armazenamento do operador, atribuindo acesso de leitura e
gravação a `568:568`. Não o crie dentro do checkout. O Compose usa
`create_host_path: false`: se o dataset não estiver montado no caminho
esperado, a inicialização deve falhar em vez de criar uma pasta vazia.
Se o pool, IP ou nomes forem diferentes, ajuste os caminhos no
`docker/docker-compose.truenas.yml`.

## Preservar produtos durante atualizações

Os arquivos de produtos são dados do operador, não código da aplicação. No
TrueNAS, mantenha-os no dataset dedicado
`/mnt/gbZaid/OmnIkestrAPIM-Products`, montado como `/data/products` nos
containers. O dataset é criado e populado uma vez; atualizações do código
recriam apenas os containers e não copiam, apagam ou substituem o dataset.
Não coloque o dataset dentro do checkout usado para `git pull`. No Compose de
desenvolvimento, `PRODUCTS_DIR` seleciona um caminho persistente externo ao
checkout e o destino dentro dos containers permanece `/data/products`.

### Migração única do diretório existente

O caminho anterior citado em instruções e relatos variou entre `Products` e
`products`. TrueNAS é sensível a maiúsculas/minúsculas; confirme qual diretório
contém os manifestos antes de copiar. Não inicie a nova stack até que o novo
dataset esteja criado, montado e validado:

1. Pare backend e watcher e, no TrueNAS, crie o dataset
   `/mnt/gbZaid/OmnIkestrAPIM-Products` com ACL/permissões para UID/GID
   `568:568`. Confirme que está montado nesse caminho e vazio.
2. No Shell, inspecione as duas origens candidatas. Escolha **somente** a que
   contém os diretórios de produtos e `product.yaml`; não crie a origem se ela
   não existir:

   ```bash
   sudo ls -la /mnt/gbZaid/OmnIkestrAPIM/Products
   sudo ls -la /mnt/gbZaid/OmnIkestrAPIM/products
   sudo find /mnt/gbZaid/OmnIkestrAPIM/Products /mnt/gbZaid/OmnIkestrAPIM/products \
     -type f -name product.yaml -print 2>/dev/null
   ```

3. Após identificar a origem real, copie seu **conteúdo** para o dataset novo
   com metadados preservados. Substitua `<origem-confirmada>` pelo caminho
   existente (`Products` ou `products`). Não use `--delete` e não apague a
   origem:

   ```bash
   sudo rsync -aHAX --numeric-ids \
     "/mnt/gbZaid/OmnIkestrAPIM/<origem-confirmada>/" \
     /mnt/gbZaid/OmnIkestrAPIM-Products/
   sudo find /mnt/gbZaid/OmnIkestrAPIM-Products -type f -name product.yaml -print
   ```

4. Compare a quantidade de manifestos e alguns arquivos/mídias na origem e no
   destino; confirme ACL/permissões no destino. Mantenha a origem intacta
   como cópia de segurança até validar catálogo, mídia e edição de um produto
   pelo aplicativo. Se o destino não tiver os dados esperados, pare e não
   reimplante.
5. Aplique o Compose atualizado e confirme que backend e watcher montaram
   `/mnt/gbZaid/OmnIkestrAPIM-Products` em `/data/products`. Só depois de
   validar o catálogo e um backup independente, decida manualmente o destino
   da cópia antiga.

Por segurança, o `.gitignore` da raiz exclui `Products/` e `products/` na
raiz do repositório, além de `/data/`. Isso protege pastas locais não
rastreadas contra um `git clean -fd` comum, mas não protege arquivos já
rastreados nem um `git clean -fdx` (que também remove itens ignorados).
Confira o caminho antes de limpar o checkout e não use `git clean -fdx` no
diretório que contém dados:

```bash
git status --short
git clean -nd
```

O segundo comando apenas pré-visualiza o que um `git clean -fd` removeria.
Se for necessário executar uma limpeza, preserve explicitamente dados locais
e configuração:

```bash
git clean -fd -e Products/ -e products/ -e data/ -e docker/.env
```

Não use `docker compose down -v` para atualização: a opção `-v` remove volumes
nomeados do Docker. Use o bind mount persistente configurado no Compose e
confira o `source` antes de recriar containers. Um `git pull` não remove dados
fora do checkout; se os arquivos sumiram, verifique primeiro se o dataset
continua no host e se o container está montando o caminho correto:

```bash
sudo ls -la /mnt/gbZaid/OmnIkestrAPIM-Products
sudo docker compose -f docker-compose.truenas.yml config
```

Se os produtos e `product.yaml` ainda existirem no dataset, mas não estiverem
listados, o problema pode ser somente o índice PostgreSQL. Refaça a
reindexação pelo painel/configuração ou pela API `POST /api/products/scan`;
isso reconstrói o catálogo a partir dos manifestos, mas não recupera arquivos
apagados do dataset.

## Imagens fixadas

O Compose fixa backend, watcher e PostgreSQL por digest, evitando que um
`pull` troque silenciosamente a versão em execução:

- Backend e watcher: artefatos da execução bem-sucedida [Publish TrueNAS
  container images #4](https://github.com/Walverde/OmnIkestrAPIM/actions/runs/45040597590),
  no commit `df5ac54cc6de4c463b32adfd85ea1a1c7813a023`.
- PostgreSQL: digest da imagem oficial `postgres:16-alpine` para `linux/amd64`.
  TrueNAS SCALE requer arquitetura x86-64.

Após qualquer build novo, atualize **os dois digests do GHCR em conjunto**
somente depois de a execução do workflow terminar com sucesso. Atualize
também o digest do PostgreSQL apenas quando deliberadamente selecionar outra
versão. Registre os três valores anteriores em local seguro para rollback.
Não substitua os digests por `latest`.

O backend publicado inclui `BUILD_VERSION`, `BUILD_COMMIT` e `BUILD_DATE`,
injetados pelo workflow a cada build. Consulte `/api/health` para ver esses
metadados ou confira o rodapé **Versão do servidor** no painel. Compare o
commit exibido com o commit esperado antes de considerar a atualização
aplicada; a resposta do endpoint não é armazenada em cache.

Os digests atualmente registrados são os da implantação anterior e ainda não
incluem as alterações D/E/F deste checkout. Não considere essas funções
implantadas no NAS até publicar e validar novas imagens backend/watcher e
atualizar os dois digests juntos.

## Senha do PostgreSQL

O Compose não contém a senha. Crie uma senha hexadecimal aleatória de 32 bytes
(64 caracteres), guarde-a em um gerenciador de senhas e escreva o mesmo valor
no arquivo secreto sem incluí-lo em Git, ticket ou histórico de comandos:

```bash
sudo install -d -o root -g 568 -m 0750 /mnt/gbZaid/OmnIkestrAPIM/secrets
sudo install -o root -g 568 -m 0440 /dev/null /mnt/gbZaid/OmnIkestrAPIM/secrets/postgres_password
sudo nano /mnt/gbZaid/OmnIkestrAPIM/secrets/postgres_password
```

Use um gerador seguro para criar a senha e cole-a no editor; por exemplo,
`openssl rand -hex 32` exibe um valor aleatório que deve ser salvo diretamente
no gerenciador de senhas. Confirme que o arquivo contém apenas a senha
hexadecimal e uma quebra de linha final opcional. O grupo `568` permite que o
backend, executado como `568:568`, leia o secret. Verifique a ACL do arquivo no
TrueNAS caso o bind mount não preserve os modos POSIX.

`POSTGRES_PASSWORD_FILE` é usado pelo PostgreSQL **somente ao inicializar um
diretório de dados vazio**. Como a instalação existente já tem um banco
inicializado, sincronize a senha da role com o arquivo antes de recriar o
backend:

1. Faça backup do banco e dos produtos conforme o procedimento abaixo e
   confirme que a senha atual está preservada no gerenciador de senhas.
2. No serviço `db` que já está em execução, abra `psql`:

   ```bash
   sudo docker compose -f docker-compose.truenas.yml exec db psql -U omnikestr -d omnikestr
   ```

3. No prompt interativo do `psql`, execute `\password omnikestr`. Digite a
   senha armazenada no arquivo secreto nas duas solicitações, depois `\q`.
   O prompt oculta a senha e evita colocá-la no histórico de shell.
4. Recrie o Compose e verifique a saúde do banco e a conexão do backend. Não
   apague o diretório persistente do PostgreSQL.

O backend lê o arquivo secreto no início e constrói `DATABASE_URL` dentro do
container. Não copie a senha para YAML, `.env`, argumentos de linha de
comando ou logs. Mantenha uma cópia protegida da senha no gerenciador de
senhas; um dump lógico do PostgreSQL não inclui a senha da role.

## Instalação / aplicação do Compose

1. Confirme os caminhos, endereço LAN, UID/GID e ACLs dos datasets.
2. Crie o arquivo de segredo acima. Se esta for uma instalação existente,
   primeiro faça a rotação interativa da senha da role.
3. Configure no ambiente do Custom App (ou no `.env` local protegido e fora do
   Git) os valores obrigatórios `OMNIKESTRAPIM_API_KEY`,
   `OMNIKESTRAPIM_ADMIN_PASSWORD` e `OMNIKESTRAPIM_SESSION_SECRET`. Gere chaves
   aleatórias, guarde-as em um gerenciador de senhas e não as inclua no YAML,
   Git ou histórico de comandos. A senha do administrador precisa ter ao menos
   12 caracteres; a chave de API e o segredo de sessão, 32 caracteres cada.
   Defina `OMNIKESTRAPIM_COOKIE_SECURE=false` somente enquanto
   usar HTTP na LAN; mude para `true` ao servir por HTTPS. A aplicação usa um
   único administrador vinculado ao workspace `default`; a chave de API é
   aceita apenas para ingestão de eventos do watcher.
4. Se necessário, autentique o Docker do TrueNAS no GHCR com um PAT de leitura
   de pacotes. Informe o token somente no prompt seguro de `docker login`.
5. No diretório `docker` do checkout, valide e aplique o YAML:

   ```bash
   cd /mnt/gbZaid/OmnIkestrAPIM/docker
   sudo docker compose -f docker-compose.truenas.yml config --quiet
   sudo docker compose -f docker-compose.truenas.yml pull
   sudo docker compose -f docker-compose.truenas.yml up -d --force-recreate
   ```

   No Custom App, aplique as mesmas alterações usando a ação de atualizar/
   reimplantar do TrueNAS.
6. Confirme que `db`, `backend` e `watcher` estão saudáveis, abra
   `http://192.168.100.58:8000/` a partir da LAN permitida, entre com a senha do
   administrador configurada e teste a reindexação com um dataset de teste.

O backend aguarda o PostgreSQL saudável e aplica migrações Alembic ao iniciar.
Não execute SQL de migração manualmente.

## Etapas D, E e F — integrações, preço e observabilidade

### Hard floor por produto

Cada `product.yaml` pode declarar `price_floor`. A API rejeita qualquer
alteração que deixe `price` abaixo desse valor; a regra é validada no backend,
não apenas na interface. Se o piso for aumentado, envie também um preço igual
ou superior no mesmo PATCH quando já existir um preço abaixo do novo piso.
Alterações de preço aceitas ficam em `GET /api/products/{id}/price-audit`;
tentativas rejeitadas não alteram o manifesto nem criam auditoria de sucesso.

Exemplo:

```yaml
price: 250
price_floor: 200
```

### OAuth e publicação manual no Mercado Livre

Antes de habilitar o conector, registre no aplicativo de desenvolvedor do
Mercado Livre o callback exato configurado em `MELI_REDIRECT_URI`. Ele deve
ser alcançável pelo navegador durante a autorização e atender aos requisitos
do provedor; não exponha a API diretamente à internet para isso. Prepare os
segredos abaixo em `/mnt/gbZaid/OmnIkestrAPIM/secrets/`, com proprietário
`root:568` e modo `0440` para leitura pelo backend:

- `mercadolivre_client_id` — Client ID do app Mercado Livre.
- `mercadolivre_client_secret` — Client Secret do app.
- `marketplace_token_key` — chave Fernet URL-safe de 32 bytes para cifrar
  tokens OAuth no PostgreSQL.
- `marketplace_action_token` — segredo aleatório hexadecimal usado como
  confirmação adicional para publicar pela API.

Crie os quatro arquivos; preencha o Client ID e o Client Secret pelo editor
interativo, sem colocá-los em argumentos de shell:

```bash
sudo install -o root -g 568 -m 0440 /dev/null /mnt/gbZaid/OmnIkestrAPIM/secrets/mercadolivre_client_id
sudo install -o root -g 568 -m 0440 /dev/null /mnt/gbZaid/OmnIkestrAPIM/secrets/mercadolivre_client_secret
sudo install -o root -g 568 -m 0440 /dev/null /mnt/gbZaid/OmnIkestrAPIM/secrets/marketplace_token_key
sudo install -o root -g 568 -m 0440 /dev/null /mnt/gbZaid/OmnIkestrAPIM/secrets/marketplace_action_token
sudo nano /mnt/gbZaid/OmnIkestrAPIM/secrets/mercadolivre_client_id
sudo nano /mnt/gbZaid/OmnIkestrAPIM/secrets/mercadolivre_client_secret
```

Gere a chave Fernet sem imprimi-la no terminal:

```bash
openssl rand -base64 32 | tr '+/' '-_' | sudo tee /mnt/gbZaid/OmnIkestrAPIM/secrets/marketplace_token_key >/dev/null
sudo chown root:568 /mnt/gbZaid/OmnIkestrAPIM/secrets/marketplace_token_key
sudo chmod 0440 /mnt/gbZaid/OmnIkestrAPIM/secrets/marketplace_token_key
```

Gere `marketplace_action_token` com `openssl rand -hex 32`; copie-o
diretamente para o gerenciador de senhas e para o editor interativo do arquivo,
sem inserir o valor no comando:

```bash
sudo nano /mnt/gbZaid/OmnIkestrAPIM/secrets/marketplace_action_token
```

Reaplique proprietário e modo `0440` depois de editar qualquer arquivo
secreto. Não troque
`marketplace_token_key` depois de vincular a conta: os tokens persistidos
deixarão de ser descriptografáveis; será necessário restaurar a chave antiga
ou revogar e autorizar novamente a conta. O Client ID/secret e a chave Fernet
nunca devem ser enviados ao navegador ou incluídos em logs.

Configure no ambiente do Custom App:

- `MELI_REDIRECT_URI` — exatamente o callback registrado no aplicativo.
- `MELI_SITE_ID` e `MELI_CURRENCY_ID` — padrão do Compose: `MLB` e `BRL`.
- `MELI_LISTING_TYPE_ID` — tipo de anúncio habilitado para a conta.
- `MELI_CATEGORY_ID` — categoria usada quando o produto não declara
  `marketplaces.mercadolivre.category_id`.
- `MELI_AVAILABLE_QUANTITY` — quantidade a anunciar, padrão `1`.
- `MELI_MIN_REQUEST_INTERVAL_SECONDS` — intervalo mínimo entre chamadas,
  padrão de um segundo.
- `TRUENAS_LAN_IP` — IP LAN reservado do NAS, padrão `192.168.100.58`;
  mantenha a publicação da porta vinculada apenas a esse IP.

Recrie o backend após instalar os secrets e parâmetros. No produto, habilite
Mercado Livre e preencha `price`, `price_floor`, `condition` e a categoria
adequada. A tela oferece a vinculação OAuth e a ação **Publicar no Mercado
Livre**; a publicação exige confirmação explícita e o token de ação secreto.
O backend enfileira a tarefa no PostgreSQL, envia as imagens válidas, cria ou
atualiza o anúncio e grava o ID externo no manifesto. Pastas iniciadas por `_`
continuam excluídas da mídia.

Se uma publicação ficar com status `reconciliation_required`, não a reenvie
diretamente. Confira o anúncio na conta do Mercado Livre e use
`POST /api/marketplaces/jobs/{job_id}/reconcile`: informe `item_id` se o anúncio
existir, ou `confirmed_not_published: true` somente após confirmar que não foi
criado. O primeiro caso associa o ID externo ao manifesto; o segundo libera uma
nova tentativa manual.

O adaptador usa OAuth2, guarda access/refresh tokens cifrados no banco e
renova tokens expirados. A fila persistente processa uma tarefa por vez no
backend, aplica intervalo mínimo entre chamadas e tenta novamente erros
transitórios com backoff limitado. Erros de validação não são repetidos. A
integração precisa ser validada com credenciais reais e um produto de teste
antes de publicar dados de produção; respostas/API do Mercado Livre podem
exigir ajustes por categoria, site ou conta.

### IA local — Ollama instalado no TrueNAS

A aplicação usará somente o App Ollama local mostrado no catálogo do TrueNAS;
não será configurado LiteLLM, chave de IA em nuvem ou fallback externo. O
Compose passa ao backend estas variáveis para a futura integração:

- `OLLAMA_BASE_URL` — URL HTTP da API Ollama alcançável **a partir do container
  backend**, normalmente `http://<IP-do-TrueNAS>:11434` se o App publicar a
  porta padrão. Use o endereço/porta apresentados na configuração do App.
- `OLLAMA_MODEL` — nome exato de um modelo já baixado no Ollama, por exemplo
  `llama3.2`; escolha conforme os recursos disponíveis no NAS.

Defina essas variáveis no ambiente do Custom App e confirme que o backend
consegue alcançar a API e que o modelo está disponível. Não presuma que
`localhost` dentro do backend aponta para o TrueNAS. A configuração apenas
prepara o endpoint e o modelo: geração de conteúdo, tela de IA e validação de
conectividade ainda dependem da implementação da Etapa H e não ficam ativos
por definir essas variáveis.

### Eventos, logs e métricas

- Eventos de alterações do catálogo, mudanças de preço e tarefas de
  marketplace ficam registrados em `domain_events`; consulte
  `GET /api/marketplaces/events`.
- A fila está disponível em `GET /api/marketplaces/jobs/{id}`. Estados
  `failed` e erros aparecem também nos eventos e logs do backend.
- Os logs do backend são emitidos em JSON, com evento/tarefa e erro sem
  registrar tokens ou segredos.
- `GET /metrics` expõe métricas Prometheus de requisições HTTP e resultado de
  tarefas. Restrinja esse endpoint à mesma rede operacional confiável; não o
  publique na internet.
- Alertas externos devem ser configurados no coletor Prometheus/stack de
  observabilidade do operador, por exemplo para crescimento de tarefas
  `failed` ou erros HTTP. O app não envia notificações externas por conta
  própria.

## Backup, atualização e rollback

Faça um backup imediatamente antes de cada atualização. Durante a cópia,
interrompa temporariamente as alterações pela interface e pare backend e
watcher; mantenha o PostgreSQL em execução para o `pg_dump`.
Antes de atualizar, exporte/salve também o YAML atualmente aplicado pelo
Custom App e anote os digests em execução. O YAML do checkout é a configuração
candidata da nova versão e não substitui uma cópia da configuração realmente
implantada.

```bash
cd /mnt/gbZaid/OmnIkestrAPIM/docker
BACKUP_DIR="/mnt/gbZaid/OmnIkestrAPIM/backups/$(date +%F-%H%M%S)"
sudo install -d -o root -g root -m 0700 "$BACKUP_DIR"
sudo docker compose -f docker-compose.truenas.yml stop backend watcher
set -o pipefail
sudo docker compose -f docker-compose.truenas.yml exec -T db \
  pg_dump -U omnikestr -d omnikestr -Fc | sudo tee "$BACKUP_DIR/catalog.dump" >/dev/null
sudo tar -C /mnt/gbZaid -czf "$BACKUP_DIR/products.tar.gz" OmnIkestrAPIM-Products
sudo docker compose -f docker-compose.truenas.yml start backend watcher
```

Guarde os backups fora do Git e, idealmente, em armazenamento criptografado
separado do NAS. Faça também snapshot TrueNAS dos datasets de produtos e
PostgreSQL, se disponíveis; inclua no snapshot/backup a versão do YAML e
preserve a senha correspondente no gerenciador de senhas. Confira os códigos
de saída e tamanhos dos artefatos. Teste restauração em datasets de teste
antes de depender deste procedimento em produção.

Atualização:

1. Registre os digests atualmente instalados e faça o backup acima.
2. Atualize no Compose os digests do backend e watcher para o mesmo build
   validado no workflow. Não atualize só um deles.
3. Valide o YAML, faça `pull` e recrie os serviços com os comandos da seção
   anterior.
4. Verifique saúde, logs sem exceções, leitura de produtos, edição de
   `product.yaml`, reindexação e eventos de movimentação.
5. Mantenha o backup e os digests anteriores até aceitar a atualização.

Rollback:

1. Pare backend e watcher e restaure **em conjunto** o Compose/digests,
   PostgreSQL e dataset de produtos do mesmo ponto anterior à atualização.
   Reaplique a cópia do YAML que estava realmente implantado antes da
   atualização, não o YAML candidato da nova versão.
   Para produtos, prefira restaurar o snapshot pareado do dataset; se usar o
   tarball, extraia primeiro para staging, valide o conteúdo e só então
   substitua o dataset pelo procedimento de restauração do TrueNAS. Não
   extraia sobre a árvore atual esperando remover arquivos novos.
2. Restaure o dump lógico no banco de teste/recuperação antes de produção;
   a forma de recriar o banco deve respeitar as roles e a versão do
   PostgreSQL. `pg_restore --clean` remove objetos existentes e só deve ser
   usado após conferir o alvo e possuir backup.
3. Reaplique os digests antigos, faça `pull` e suba a stack. Confirme a
   conectividade e a integridade antes de reabrir o acesso dos usuários.

Migrações podem tornar imagens antigas incompatíveis com o banco ou com
`product.yaml`. Não faça rollback somente das imagens depois de uma migração:
restaure também o banco e os manifests do backup anterior. Não use
`docker compose down -v` para atualização ou rollback; isso pode remover
volumes persistentes.

## Rede e volume somente leitura do watcher

A porta publicada no Compose está vinculada ao IP LAN
`${TRUENAS_LAN_IP:-192.168.100.58}`, não a todas as interfaces. O IP precisa
estar configurado e reservado no TrueNAS; se mudar, atualize a variável antes
de aplicar. Isso **não substitui firewall**:

- Permita TCP/8000 somente das sub-redes LAN/VPN confiáveis no firewall/ACL
  disponível no host e na rede.
- Não crie encaminhamento de porta pública no roteador e não exponha a API
  diretamente à internet.
- Se usar VPN, permita somente os clientes/sub-redes VPN necessários e
  verifique a rota até o IP LAN.
- Teste de uma rede não autorizada que a porta não esteja acessível. O
  Compose sozinho não configura nem comprova essas regras.

O volume do watcher permanece **gravável** até que a operação somente leitura
seja validada em um dataset descartável. Para avaliar, monte o dataset de
teste com `read_only: true` apenas no serviço `watcher`, confirme que a raiz
existe antes de iniciar e valide inicialização, reconciliação periódica,
eventos de criação/movimentação e logs sem `PermissionError`. Se algum teste
falhar, mantenha o volume gravável. Não aplique a restrição ao backend, que
precisa gravar manifests.

## Wizard Zero-Config e navegador de diretórios

O wizard inicial, a seleção persistente da raiz e o navegador restrito por
allowlist já estão implementados no backend/Web UI. A raiz selecionável deve
ser um volume montado e autorizado em `PRODUCT_ROOTS`/`PRODUCTS_ROOT`; o
wizard não monta datasets no host.

Antes de aceitar uma instalação limpa:

1. Use uma stack e dataset descartáveis e confirme a instalação sem dados
   previamente catalogados.
2. Selecione uma raiz montada permitida, reinicie o backend e confirme que a
   seleção persiste.
3. Tente navegar acima da raiz, selecionar um caminho externo e criar pasta
   fora da allowlist; essas operações devem ser recusadas.
4. Repita os testes com os volumes reais somente depois de validar o
   comportamento no dataset de teste.

Os testes automatizados correspondentes estão em
`services/backend/test_main.py`; a instalação limpa e os limites em volumes
montados ainda precisam de validação no TrueNAS.

## Registro de execução no TrueNAS

Os seguintes resultados foram reportados no ambiente real em 22/11/2026:

- Dataset de produtos pertencente a `apps` (`UID=568`, `GID=568`); configurar
  `PUID=568` e `PGID=568` resolveu os erros de permissão e importação.
- Stack recriada com PostgreSQL saudável e interface/API respondendo na porta
  `8000`; backend iniciado sem `--reload`.
- Leitura e gravação de `product.yaml`, incluindo título, estado, preço,
  custo, condição e descrição, foram validadas pela interface.
- Movimentação/renomeação preservou o UUID
  `51143590-6650-4667-b2b5-362a36aa5aef` e atualizou os caminhos de mídia.
- Reconciliação periódica, reindexação manual (`POST /api/products/scan`,
  HTTP 200) e remoção de `Teste Reconciliacao` foram reportadas sem erros
  HTTP 500 ou exceções.
- A confirmação visual mostrou somente o produto esperado; `_Paste de teste`
  foi ignorada como produto adicional.

Esses registros validam o fluxo funcional no estado implantado naquele
momento. Os digests, segredo, regras de rede, backup/rollback e montagem
somente leitura documentados acima ainda precisam ser aplicados ou testados
no TrueNAS; este workspace não tem acesso ao host.
