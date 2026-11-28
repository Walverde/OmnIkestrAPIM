# Implantação no TrueNAS SCALE

O deploy usa Docker Compose, não o menu Apps do TrueNAS.

## Pré-requisitos

- Docker Compose v2.
- Execução como root no shell do TrueNAS.
- Datasets ZFS recomendados para banco, produtos e backups.
- UID/GID `568:568` com gravação no dataset de produtos.

## Instalação

No checkout:

```bash
bash scripts/install-truenas.sh
bash scripts/install-truenas.sh --yes
bash scripts/install-truenas.sh --proxy-ip 192.168.100.10
bash scripts/install-truenas.sh --with-marketplace
```

`--yes` desativa prompts. `--proxy-ip` define a origem confiável do proxy.
Mercado Livre é opcional e só cria seus secrets com `--with-marketplace`.

Crie o dataset de produtos manualmente ou deixe o instalador criar no ZFS
detectado. Ajuste a ACL quando necessário e confirme:

```bash
setpriv --reuid=568 --regid=568 --clear-groups test -w /mnt/<pool>/OmnIkestrAPIM-Products
```

## Verificação

```bash
curl -fsS http://127.0.0.1:8000/api/health
curl -i http://127.0.0.1:8000/api/ready
docker compose --env-file docker/.env -f docker/docker-compose.truenas.yml ps
```

`/api/health` é liveness. `/api/ready` é readiness e devolve `503` sem uma raiz
de produtos montada e gravável.

## Backup e restauração

```bash
bash scripts/backup-truenas.sh
bash scripts/restore-truenas.sh /caminho/para/backup.dump
bash scripts/restore-truenas.sh --yes /caminho/para/backup.dump
```

Os backups são `pg_dump -Fc`, ficam no dataset configurado por `APPS_ROOT` e
mantêm 14 arquivos por padrão (`BACKUP_KEEP`). Agende
`scripts/backup-truenas.sh` em uma tarefa cron do sistema e replique o dataset
de backups para outro pool ou máquina.

## Atualização e rollback

```bash
bash scripts/install-truenas.sh --upgrade
bash scripts/install-truenas.sh --upgrade --skip-snapshot
```

O upgrade cria primeiro um backup lógico e snapshots ZFS dos datasets de banco
e produtos. Use `--skip-snapshot` somente em instalações sem ZFS.

## HTTPS e proxy reverso

Com HTTPS público, defina no `docker/.env`:

```text
OMNIKESTRAPIM_COOKIE_SECURE=true
FORWARDED_ALLOW_IPS=<IP-do-proxy>
UVICORN_PROXY_HEADERS=true
```

Não use `FORWARDED_ALLOW_IPS=*`.

## Mercado Livre

A integração usa `docker/docker-compose.marketplace.yml` e não é necessária para
o funcionamento básico. O instalador cria os arquivos de secret apenas com
`--with-marketplace`.

## Desinstalação

```bash
bash scripts/install-truenas.sh --uninstall
```

A desinstalação remove containers, não apaga banco, produtos ou configuração.
