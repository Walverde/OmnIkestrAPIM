# TrueNAS SCALE 25.10 (Goldeye) — primeira instalação

Este procedimento instala a aplicação como **Custom App usando Compose YAML**.
Use uma versão publicada das imagens e um dataset de teste na primeira
instalação. Não aponte para os dados únicos de produção: a API ainda não tem
autenticação e o catálogo pode atualizar manifests legados durante a
reconciliação.

## Pré-requisitos

- TrueNAS SCALE 25.10.x com Apps habilitado.
- Acesso à interface Web do TrueNAS e a um registry que possa baixar as imagens
  `ghcr.io/walverde/omnikestrapim-backend` e
  `ghcr.io/walverde/omnikestrapim-watcher`.
- Um dataset para os produtos e outro diretório/dataset para os dados do
  PostgreSQL.
- UID e GID que terão acesso de leitura e escrita ao dataset de produtos.

O workflow de GitHub Actions em `.github/workflows/publish-containers.yml`
publica as imagens do backend e watcher no GHCR em pushes para `main` e em
tags SemVer (`vMAJOR.MINOR.PATCH`). Cada push para `main` atualiza a tag
mutável `latest`; tags SemVer publicam tags de versão, como `0.1.0`. O workflow
foi executado com sucesso em 08/11/2026
([execução no GitHub Actions](https://github.com/Walverde/OmnIkestrAPIM/actions/runs/42518491139)).

As imagens do GHCR podem exigir autenticação. No TrueNAS, faça login no
registry com um Personal Access Token (PAT) que tenha permissão de leitura dos
pacotes (`read:packages`). Informe o token apenas no prompt seguro do comando
de login; não o salve neste Compose, no Git ou em documentação. O login bem
sucedido permite que o Docker do host baixe as imagens privadas.

## Preparar os datasets

Crie os diretórios/datasets antes de instalar o app. O Compose versionado está
preenchido para os caminhos informados para este TrueNAS:

- `/mnt/gbZaid/Omnikestrapm/products` — dataset montado para os produtos.
- `/mnt/gbZaid/Omnikestrapm/postgres` — diretório persistente exclusivo do
  PostgreSQL.

Se o pool ou os nomes dos datasets forem diferentes no seu servidor, atualize
as ocorrências correspondentes em
`docker/docker-compose.truenas.yml`. Faça a primeira instalação com uma cópia
de teste dos produtos; não use dados únicos de produção antes de validar o
backup e a restauração.

O Compose usa `create_host_path: false` para que caminhos incorretos não criem
diretórios vazios silenciosamente. Configure ACL/permissões do dataset de produtos para que o usuário do container
consiga ler e gravar os manifests. O exemplo usa `568:568`, mas confirme os
IDs/permissões no seu sistema; altere `user`, `PUID` e `PGID` nos serviços
`backend` e `watcher` se necessário. O dataset PostgreSQL deve permitir que o
processo `postgres` da imagem oficial inicialize e grave os dados; não aplique
automaticamente a ele o mesmo UID/GID escolhido para os produtos.

## Publicar e atualizar as imagens

Para a atualização contínua, cada push para `main` executa o workflow e publica
as imagens com a tag `latest`. Para uma versão fixa, crie e envie uma tag
SemVer, por exemplo `v0.1.0`; isso publica:

- `ghcr.io/walverde/omnikestrapim-backend:0.1.0`
- `ghcr.io/walverde/omnikestrapim-watcher:0.1.0`

Confirme que a execução do workflow terminou com sucesso antes de atualizar o
TrueNAS. O Compose versionado neste repositório usa `latest` para consumir
builds da branch principal. Essa tag é mutável; para implantação reproduzível,
prefira fixar as duas imagens numa tag SemVer já validada.

## Configurar e instalar

1. Abra **Apps** no TrueNAS e escolha a opção de instalar um **Custom App** via
   YAML/Compose.
2. Cole o conteúdo de `docker/docker-compose.truenas.yml` no editor do app.
3. Confirme que `/mnt/gbZaid/Omnikestrapm/products` e
   `/mnt/gbZaid/Omnikestrapm/postgres` existem no TrueNAS e correspondem aos
   datasets preparados. Se necessário, ajuste os caminhos no YAML antes de
   colá-lo.
4. Gere uma senha aleatória hexadecimal com 32 bytes (por exemplo,
   `openssl rand -hex 32`) em uma máquina confiável. Substitua
   `REPLACE_WITH_RANDOM_HEX_PASSWORD` pelas mesmas 64 letras/números nas duas
   ocorrências do Compose. Não use a senha de exemplo nem a reutilize em outros
   serviços.
5. Se as imagens forem privadas, autentique o Docker do TrueNAS no GHCR com um
   PAT de leitura de pacotes. Não coloque o valor do token no Compose.
6. Revise as permissões do dataset e o UID/GID configurado. No diretório do
   Compose, baixe as imagens e suba/atualize a stack:
   `docker compose -f docker-compose.yml pull` e depois
   `docker compose -f docker-compose.yml up -d`.
   Se estiver usando o editor de Custom App do TrueNAS, aplique as mesmas
   alterações e use a ação de atualizar/reimplantar do app.
7. Acompanhe os eventos/logs até `db`, `backend` e `watcher` estarem em
   execução. Abra `http://IP_DO_TRUENAS:8000/settings` na rede local e valide o
   catálogo de teste, depois faça uma reindexação e teste mover/renomear uma
   pasta de produto.

O primeiro início do backend aguarda o PostgreSQL saudável, aplica as
migrações Alembic automaticamente e reconcilia o catálogo. Não execute
migrações SQL manualmente.

## Segurança e operação

- A porta `8000` é publicada para acesso na rede. Como a API ainda não tem
  autenticação, **não encaminhe essa porta pela internet**; limite o acesso à
  LAN/VPN com as regras de rede apropriadas.
- Backend e watcher precisam de acesso de escrita ao dataset de produtos:
  o backend grava manifests atomicamente e migra manifests legados; o watcher
  processa eventos/reconciliações.
- O PostgreSQL usa bind mount persistente. Inclua esse dataset nos backups e
  teste a restauração antes de usar dados de produção.
- Para atualizar, publique uma nova tag de imagens, altere as duas tags no
  Compose (ou atualize `latest` com `pull` e recriação dos containers) e
  aplique a atualização do Custom App. Faça backup do PostgreSQL e dos
  manifests antes de atualizar.
- Para voltar atrás, restaure conjuntamente a versão anterior do app e os
  backups correspondentes. Não apague o dataset de PostgreSQL ao remover ou
  recriar o Custom App.

Esta primeira versão não monta automaticamente uma pasta escolhida pelo
usuário na Web UI. O caminho do dataset é configurado no Compose do Custom App.
