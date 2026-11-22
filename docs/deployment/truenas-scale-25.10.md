# TrueNAS SCALE 25.10 (Goldeye) — implantação e operação

Este procedimento usa **Custom App com Compose YAML**. Como a API ainda não
tem autenticação, exponha-a somente a uma rede confiável e faça a primeira
instalação com dados de teste. Não use dados únicos de produção antes de testar
backup e restauração.

## Pré-requisitos e caminhos

- TrueNAS SCALE 25.10.x com Apps habilitado.
- Acesso ao GHCR para baixar as imagens privadas, caso o registry exija login.
- Dataset de produtos e diretório/dataset persistente do PostgreSQL.
- Endereço LAN reservado para o NAS; o YAML atual usa `192.168.100.58`.
- UID/GID com acesso de leitura e gravação aos produtos; o exemplo validado
  usa `568:568`.

O Compose versionado está configurado para:

- `/mnt/gbZaid/OmnIkestrAPIM/products` — produtos.
- `/mnt/gbZaid/OmnIkestrAPIM/postgres` — dados persistentes do PostgreSQL.
- `/mnt/gbZaid/OmnIkestrAPIM/secrets/postgres_password` — arquivo secreto no
  host, fora do Git.

Crie os caminhos/datasets antes de instalar. `create_host_path: false` evita
que caminhos incorretos virem diretórios vazios silenciosamente. Se o pool,
IP ou nomes forem diferentes, ajuste os caminhos no
`docker/docker-compose.truenas.yml` e use o IP reservado do seu TrueNAS.

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
3. Se necessário, autentique o Docker do TrueNAS no GHCR com um PAT de leitura
   de pacotes. Informe o token somente no prompt seguro de `docker login`.
4. No diretório `docker` do checkout, valide e aplique o YAML:

   ```bash
   cd /mnt/gbZaid/OmnIkestrAPIM/docker
   sudo docker compose -f docker-compose.truenas.yml config --quiet
   sudo docker compose -f docker-compose.truenas.yml pull
   sudo docker compose -f docker-compose.truenas.yml up -d --force-recreate
   ```

   No Custom App, aplique as mesmas alterações usando a ação de atualizar/
   reimplantar do TrueNAS.
5. Confirme que `db`, `backend` e `watcher` estão saudáveis, abra
   `http://192.168.100.58:8000/` a partir da LAN permitida e teste a
   reindexação com um dataset de teste.

O backend aguarda o PostgreSQL saudável e aplica migrações Alembic ao iniciar.
Não execute SQL de migração manualmente.

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
sudo tar -C /mnt/gbZaid/OmnIkestrAPIM -czf "$BACKUP_DIR/products.tar.gz" products
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
