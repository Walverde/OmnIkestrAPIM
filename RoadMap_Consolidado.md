# OmniKestra (OmnIkestrAPIM) — Roadmap Oficial

**Última atualização:** 22/11/2026

**Status geral do projeto:** Em desenvolvimento ativo — Etapa B funcional concluída no TrueNAS SCALE em 22/11/2026; hardening operacional do NAS e fluxo Zero-Config/onboarding continuam em andamento.

**Fonte única de planejamento e decisões:** este documento consolida o roadmap oficial anterior, os rascunhos de roadmap e as decisões dos ADRs 0001 e 0002. Novas tarefas, status e decisões devem ser mantidos somente aqui. `docs/ARCHITECTURE.md` continua como referência descritiva de arquitetura, sem substituir este registro.

---

## 1. Visão e Princípios Fundamentais

O **OmnIkestrAPIM** é uma ferramenta *self-hosted* de gestão de inventário e anúncios em marketplaces, orientada a autonomia e inteligência artificial.

Este roadmap controla o escopo, prioridades, decisões e progresso. A implementação existente deve ser verificada no código e nos testes; os itens marcados como concluídos registram trabalho validado, enquanto decisões em aberto permanecem explicitamente na seção 5.

**Princípios que orientam todas as decisões:**

| Princípio | Descrição |
|-----------|-----------|
| **Filesystem-first** | Os arquivos YAML (`product.yaml`) são a **fonte da verdade**. O banco de dados serve apenas como índice. Toda decisão deve reforçar esse padrão. |
| **Interface comum entre marketplaces** | Abstração única sobre Mercado Livre, OLX e futuros canais. |
| **Camada orientada a eventos** | Introduzida de forma progressiva (não tudo de uma vez). |
| **Market Intelligence honesto** | Dados observados ficam **estritamente separados** de estimativas geradas por IA. |
| **Isolamento por workspace** | Nativo desde o início. Retrofit de multi-tenancy é caro e proibido. |
| **Segurança na precificação** | Piso de preço (*hard floor*) aplicado **diretamente no motor**, não apenas na interface. |
| **Self-hosted em Linux** | Destino principal: NAS com TrueNAS, rodando em containers Docker. |

---

## 2. Avaliação Técnica do Código Atual

### Capacidade implementada

- Backend FastAPI modularizado em banco, modelos, catálogo, mídia e routers por domínio; `main.py` permanece como ponto de entrada.
- `product.yaml` é lido e gravado atomicamente; o catálogo PostgreSQL é um índice reconstruível. Edições preservam chaves YAML desconhecidas.
- Catálogo isolado por workspace, identidade estável por UUID e suporte configurável a múltiplas raízes.
- Watcher com debounce, regras de exclusão configuráveis, reindexação manual e reconciliação periódica.
- Imagens podem ser entregues diretamente ou processadas como thumbnails WebP.
- Testes locais mais recentes: 22 testes do backend aprovados em 06/11/2026.

### Lacunas e verificações pendentes

- Os testes funcionais de catálogo, permissões, movimentação e reconciliação foram reportados como concluídos no TrueNAS em 22/11/2026; a nova configuração de hardening do Compose ainda não foi implantada no host.
- Aplicar e validar no TrueNAS a rotação do segredo, as regras de firewall/LAN/VPN e o procedimento de backup/restauração/rollback.
- Validar a montagem do volume do watcher como somente leitura em um dataset descartável antes de aplicar a restrição.
- Validar instalação limpa do Zero-Config/wizard e limites da allowlist em volumes montados no TrueNAS.
- Migrações de manifests para versões futuras ainda dependem da definição e aprovação de um schema v2.

---

## 3. Decisões Consolidadas

Decisões aceitas são registradas aqui com a data. A documentação antiga de ADR foi incorporada nesta seção e deixou de ser uma fonte independente.

### 3.1 Dados de produto e schema

- [x] `product.yaml` é a fonte de verdade; o banco de dados é índice operacional reconstruível. Atualizações de produto são gravadas no YAML por substituição atômica, preservando campos desconhecidos (concluído em 06/10/2026).
- [x] Cada produto é identificado pelo UUID persistido no YAML; mudar a pasta não deve mudar sua identidade (concluído em 06/10/2026).
- [x] Schema v1 exige `title`; inclui `schema_version`, `id` UUID, `sku`, `category`, `price`, `cost`, `condition`, `status`, `marketplaces` e `description`. Um produto inválido é reportado sem impedir a indexação de outras pastas (concluído em 06/10/2026).
- [x] Manifests sem `schema_version` são tratados como schema 0 e migrados para v1, preservando campos desconhecidos. Versões futuras não suportadas são reportadas sem alteração do arquivo (concluído em 06/11/2026).
- [x] Cada nova versão do schema deverá ter migração explícita e sequencial a partir de todas as versões anteriores; não inferir nem descartar campos desconhecidos (concluído em 06/11/2026).
- [x] Schema v2 representa `marketplaces` como mapa por canal, com configuração `enabled`; migra listas v1 preservando canais habilitados, UUID e campos extras (concluído em 22/11/2026).

### 3.2 Workspace e catálogo

- [x] Produtos e eventos pertencem a um workspace; a instalação inicial usa `default`. UUIDs e caminhos são únicos dentro do workspace (concluído em 06/10/2026).
- [x] Dados existentes e eventos recebidos durante a migração inicial são atribuídos ao workspace `default`; eventos são consultados por workspace, paginados e sujeitos a limites de retenção por quantidade e idade (concluído em 06/10/2026).
- [x] A configuração de raízes é global à instalação; um `workspace_id` não seleciona implicitamente outra raiz. Alterar essa regra exige uma decisão e mudança explícitas (concluído em 06/10/2026).

### 3.3 Deploy e filesystem

- [x] Linux/TrueNAS é o destino principal; roots são configuradas por variáveis de ambiente e volumes. O seletor PowerShell é ferramenta opcional de desenvolvimento, não requisito do fluxo padrão (concluído em 13/10/2026).
- [x] Raízes de produto podem ser configuradas via `PRODUCT_ROOTS`, mantendo `PRODUCTS_ROOT=/data/products` como compatibilidade/default (concluído em 13/10/2026).
- [x] Eventos SMB/NFS não são considerados confiáveis: combinar watcher com reconciliação configurável e reindexação manual (concluído em 13/10/2026).
- [x] O container deve usar permissões compatíveis com o dono do dataset (PUID/PGID) para permitir escrita atômica (concluído em 13/10/2026).
- [x] Pastas cujo nome começa com `_` são reservadas e ignoradas pelo watcher; padrões temporários/auxiliares também são configuráveis. A convenção comporta `_arquivos/`, `_draft/` e `_cache/` (concluído em 13/10/2026).
- [x] Categorias em subpastas são representadas por `folder_group`; categoria no YAML é opcional, e sugestão derivada da pasta não sobrescreve o YAML (concluído em 13/10/2026).

### 3.4 Organização do backend

- [x] Separar API, modelos, acesso ao banco, catálogo e mídia por domínio, mantendo `main.py` como ponto de entrada compatível com execução, testes e migrações existentes (concluído em 05/11/2026).

### 3.5 Inteligência Artificial

- [x] Usar exclusivamente o Ollama local instalado como App no TrueNAS, sem
  fallback para provedores em nuvem; configurar URL e modelo por instalação
  (decisão registrada em 22/11/2026).

---

## 4. Roadmap de Desenvolvimento (Oficial)

> **Regra de atualização de status:**  
> Após concluir qualquer item, marcar como `[x]` e adicionar data de conclusão no formato `(concluído em DD/MM/AAAA)`.  
> Itens em andamento devem ser marcados com `[-]` e nota de progresso.

### Etapa A — Fundação e Core
**Status:** Concluída (06/10/2026)

- [x] Schema v1 do `product.yaml` com UUID persistente e escrita atômica (concluído em 06/10/2026)
- [x] Serviço de Watcher com debounce (1,5 s) e reconciliação automática (15 min) (concluído em 06/10/2026)
- [x] Catalogação e isolamento de dados por Workspace (concluído em 06/10/2026)
- [x] Mapeamento de categorias via subpastas (`folder_group`) (concluído em 06/10/2026)
- [x] Scripts de execução local para Windows (`start-local.cmd` / `start-local.ps1`) (concluído em 06/10/2026)
- [x] Migrações com Alembic (concluído em 06/10/2026)
- [x] Token no helper do Windows (concluído em 06/10/2026)

---

### Etapa B — Adaptação para Linux / TrueNAS & Tratamento de Pastas
**Status:** Concluída — validação funcional no TrueNAS em 22/11/2026
**Objetivo:** Deixar o app pronto para rodar em TrueNAS e otimizar I/O em discos de rede.

#### B.1 Configuração de Raiz por Volume/Ambiente
- [x] Remover dependência do `windows_folder_picker.ps1` no fluxo principal (concluído em 13/10/2026)
- [x] Definir raízes de anúncios por variáveis de ambiente (`PRODUCT_ROOTS` / `/data/products`) e volumes montados (concluído em 13/10/2026)
- [x] Normalizar caminhos de catálogo e arquivos para representação POSIX-safe em runtime Linux/NAS, preservando a identidade do produto ao mover ou renomear diretórios (implementação e testes de caminho ajustados em 05/11/2026; no TrueNAS, movimentação preservou o UUID e atualizou os caminhos de mídia em 22/11/2026)
- [x] Implementar navegador de diretórios restrito por *allowlist* na Web UI (endpoint e wizard limitados aos volumes permitidos; teste de traversal em `services/backend/test_main.py`; implementação já existente em 22/11/2026)

#### B.2 Filtros e Regras de Exclusão no Watcher
- [x] Ignorar qualquer pasta cujo nome comece com `_` (incluindo `_arquivos/`, `_draft`, `_cache` e similares) (implementado em 13/10/2026; confirmado no TrueNAS em 22/11/2026: pasta `_Paste de teste` presente no produto, sem criar produto extra na listagem)
- [x] Aplicar padrões configuráveis de exclusão: `*.psd`, `~$*`, `*.tmp`, `Thumbs.db`, `.DS_Store` (concluído em 13/10/2026)
- [x] Tornar as regras de ignore configuráveis (arquivo de configuração ou variáveis de ambiente) (concluído em 13/10/2026)

#### B.3 Ajustes de Infraestrutura para NAS
- [x] Intervalo de reconciliação configurável (SMB/NFS) em estrutura do watcher e env vars (concluído em 13/10/2026)
- [x] Botão / endpoint de reindexação manual sob demanda (concluído em 13/10/2026)
- [x] Configuração de permissões PUID/PGID no Docker para escrita atômica (concluído em 13/10/2026; leitura e gravação do `product.yaml` validadas no TrueNAS com `568:568` em 22/11/2026)
- [x] Tratar casos de borda: renomeação de arquivos/pastas, debounce e eventos perdidos (concluído em 13/10/2026)
- [x] Suporte a múltiplas raízes de produtos (concluído em 13/10/2026)
- [x] Preparar e publicar a primeira stack para TrueNAS SCALE 25.10: autenticação do host no GHCR configurada; workflow executado com sucesso para publicar backend e watcher; Compose atualizado e `docker compose pull`/subida integrada de `db`, `backend` e `watcher` validados no TrueNAS (concluído em 08/11/2026). Indexação/leitura, persistência de edições no manifesto, movimentação/renomeação, reconciliação periódica e reindexação manual validadas por logs e interface em 22/11/2026.
- [x] Automatizar publicação de imagens para pushes em `main` com tag mutável `latest`, mantendo publicação versionada por tags SemVer `v*` (concluído em 08/11/2026).
- [-] Fixar as imagens usadas no TrueNAS em digests imutáveis validados; documentar como atualizar backend, watcher e banco conjuntamente e como voltar às referências anteriores (Compose/guia atualizados e configuração validada em 22/11/2026; aplicar e confirmar no host ainda pendente).
- [-] Definir armazenamento da senha do PostgreSQL por arquivo secreto fora do Git e configurar o Compose/guia sem credencial versionada (implementado no repositório em 22/11/2026; falta criar o arquivo, rotacionar a senha do banco existente e validar no TrueNAS).
- [-] Restringir e documentar o acesso de rede à API no TrueNAS enquanto não houver autenticação nativa (Compose limitado ao IP LAN `192.168.100.58` e orientação para ACL/firewall em 22/11/2026; falta aplicar/verificar regras no roteador/firewall do host e confirmar VPN).
- [ ] Validar o watcher com o volume de produtos montado como somente leitura; a restrição ainda não foi aplicada porque o Docker daemon de validação não estava disponível em 22/11/2026.
- [-] Documentar e validar procedimento seguro de atualização: digests e instruções de atualização, backup e rollback registrados no guia em 22/11/2026; ainda falta executar backup/restauração, atualização e rollback no TrueNAS, inclusive verificar compatibilidade das migrações.
- [-] Bootstrap local Zero-Config e wizard inicial: defaults, seleção persistente, navegação allowlist e testes de limites já estão implementados; falta validar instalação limpa e seleção em volumes montados no TrueNAS.

#### B.4 Modularização do Backend
- [x] Separar modelos (`backend_core/models.py`), acesso ao banco (`backend_core/database.py`), lógica de catálogo (`backend_core/catalog.py`) e processamento de mídia (`backend_core/media.py`), mantendo `main.py` como ponto de entrada compatível (concluído em 05/11/2026)
- [x] Validar a compatibilidade das rotas, migrações e imports existentes com a suíte do backend (20 testes aprovados em 05/11/2026; concluído em 05/11/2026)
- [x] Separar endpoints HTTP em routers de sistema, configuração, produtos, eventos, mídia e integração local, mantendo os caminhos HTTP existentes (concluído em 05/11/2026)
- [x] Validar a integração dos routers com a suíte do backend (20 testes aprovados em 05/11/2026; concluído em 05/11/2026)

**Critério de aceite da Etapa B:**  
Aplicação sobe em TrueNAS apenas com volumes e variáveis de ambiente, sem qualquer dependência do seletor Windows, com regras de ignore ativas e reindexação manual funcional.  
Status: concluído em 22/11/2026, conforme validação reportada no ambiente real e confirmação visual: indexação/leitura e gravação de `product.yaml` com `PUID=568`/`PGID=568`; movimentação preservando o UUID e atualizando caminhos de mídia; reconciliação periódica; reindexação manual com HTTP 200; e pasta `_Paste de teste` ignorada sem aparecer como produto adicional na interface. Permanecem pendentes no host a aplicação dos digests, rotação e validação do segredo, restrição efetiva de rede, teste do watcher somente leitura e execução do backup/restauração/rollback. O navegador de diretórios e o wizard estão implementados; a validação de instalação limpa permanece pendente.

---

### Etapa C — Robustez do Schema e Imagens
**Status:** Em validação — schema v2 implementado em 22/11/2026; testes automatizados ainda precisam ser executados

- [x] Criar registry de migrações do `product.yaml`: manifests legados sem versão migram para v1 com preservação de campos desconhecidos; versões futuras são sinalizadas sem alteração do arquivo (22 testes do backend aprovados em 06/11/2026; concluído em 06/11/2026)
- [-] Introduzir configuração de habilitação por canal no `marketplaces` do `product.yaml`; migrar schema v1 (lista de nomes) para schema v2 (mapa de configurações), preservando campos desconhecidos. Implementação e testes adicionados em 22/11/2026; execução automatizada pendente porque o ambiente bloqueou a inicialização do Python.
- [x] Processar imagens para entrega na Web UI com thumbnails e conversão WebP, preservando suporte à entrega direta (concluído em 06/11/2026)

---

### Etapa D — Abstração de Marketplaces e Conector Mercado Livre
**Status:** Em implementação — base do conector e publicação manual implementadas em 22/11/2026; validação externa pendente

- [x] Construção da camada de abstração comum por interface de conector e registry; validar novos adaptadores ao adicioná-los
- [ ] Conector Mercado Livre:
  - [-] Autenticação OAuth2 com estado de uso único, tokens cifrados no PostgreSQL e renovação automática; falta validar com credenciais reais no TrueNAS
  - [-] Publicação/sincronização manual explícita a partir do manifesto, com enfileiramento persistente e ID externo salvo; automação de sincronização permanece fora do escopo atual e o fluxo real depende de validação de conta/categoria
  - [x] Intervalo mínimo entre chamadas, fila persistente no PostgreSQL, backoff/retry de erros transitórios e endpoint de estado da tarefa

---

### Etapa E — Motor de Precificação e Salvaguardas
**Status:** Em implementação — piso e auditoria implementados em 22/11/2026; execução dos testes automatizados pendente

- [-] Piso explícito por produto (`price_floor`) aplicado na validação do backend e exigido antes da publicação; validar suíte e migração em runtime
- [-] Rejeitar preços abaixo do piso e registrar alterações aceitas em `price_audits`; testes de regressão adicionados, mas ainda não executados
- [ ] Regras adicionais de precificação automática com limites verificáveis; definir algoritmo/política antes de automatizar sugestões ou alterações

---

### Etapa F — Camada de Eventos e Observabilidade
**Status:** Em implementação — eventos, fila e telemetria locais implementados em 22/11/2026; validação operacional pendente

- [-] Eventos de catálogo, preço e integração persistidos em `domain_events`, com consulta via API; validar migrações e retenção em runtime
- [-] Logs JSON e métricas Prometheus HTTP/fila disponíveis em `/metrics`; testes de integração ainda pendentes
- [ ] Centralização externa de logs e configuração de alertas no coletor Prometheus/stack do operador

---

### Etapa G — Market Intelligence e Novos Canais
**Status:** Proposta

- [ ] Módulo de Market Intelligence com separação rigorosa entre dados observados e estimativas de IA
- [ ] Conector OLX sobre a abstração comum
- [ ] Conector Facebook Marketplace sobre a abstração comum
- [ ] Preparação para novos canais futuros

---

### Etapa H — AI Enrichment (Local-first)
**Status:** Proposta — Ollama local no TrueNAS definido como provedor exclusivo; funcionalidades de IA ainda não implementadas

- [ ] Integrar ao serviço Ollama instalado no TrueNAS; sem LiteLLM,
  provedores de nuvem ou fallback externo
- [ ] Configurar URL da API e nome do modelo local por ambiente; validar conectividade e disponibilidade do modelo
- [ ] Capacidade de gerar/sugerir:
  - Título otimizado
  - Descrição completa
  - Atributos faltantes
  - Categoria sugerida
  - Preço sugerido (sempre respeitando hard floor)
- [ ] Campos no `product.yaml` claramente separados:
  - `title` / `title_ai`
  - `description` / `description_ai`
  - `price` / `price_suggested_ai`
  - `ai_metadata` (modelo usado, data, prompt version, confiança)
- [ ] Modos de operação:
  - Manual (usuário pede e aprova)
  - Semi-automático (sugere e espera confirmação)
  - Automático (com regras de confiança mínima)
- [ ] Integração com Vision (opcional): analisar fotos do produto para gerar descrição
- [ ] Fila de processamento + rate limit local (para não matar o NAS)
      

## 5. Decisões em Aberto

| # | Decisão | Opções / critério | Estado |
|---|---------|------------------|--------|
| 2 | Escopo da primeira versão utilizável | Definir critérios objetivos de entrega e quais integrações entram no primeiro release. | Aberta; fechar após Etapa B |
| 3 | Próxima mudança funcional do manifest | Identificar requisito que demande schema v2; não criar uma versão sem mudança real que a justifique. | Aberta; dependente da evolução funcional |

---

## 6. Riscos Principais e Mitigações

| Risco | Impacto | Mitigação |
|-------|---------|-----------|
| Conflito YAML × Banco | Alto | Fonte da verdade **sempre** é o YAML; o banco é índice reconstruível e nunca substitui silenciosamente o manifest. |
| Eventos não confiáveis em SMB/NFS | Alto | Reconciliação periódica configurável + reindexação manual. |
| Arquivos pesados (PSD/RAW) na árvore | Médio-Alto | Isolamento em `_arquivos/` + padrões de ignore. |
| Precificação automática sem piso | Crítico | *Hard floor* obrigatório no motor (Etapa E). |
| Credenciais e rate limit do Mercado Livre | Alto | Tratar na Etapa D com gestão segura de tokens e filas. |
| Multi-tenancy tardia | Alto | Já mitigado (workspace nativo desde a Etapa A). |

---

## 7. Próximos Passos Imediatos (Ordem de Execução)

1. [x] Validar publicação das imagens e inicialização integrada de `db`, `backend` e `watcher` no TrueNAS (concluído em 08/11/2026); indexação/leitura e persistência de edição no manifesto também validadas em 22/11/2026.
2. [x] Executar testes funcionais no TrueNAS: indexação/leitura, gravação no manifesto, movimentação/renomeação com preservação de UUID, reconciliação periódica, reindexação manual e confirmação visual da pasta ignorada não aparecer no catálogo (validação reportada em 22/11/2026).
3. [-] Aplicar no TrueNAS a configuração versionada de segredos e digests; rotacionar a senha persistente e confirmar saúde dos serviços.
4. Restringir a porta TCP/8000 no firewall/roteador a LAN/VPN e validar a partir de redes permitidas e não permitidas.
5. Testar o watcher com volume somente leitura em dataset descartável; só então considerar aplicar a restrição.
6. [-] Procedimento de backup/restauração, atualização e rollback documentado; executar e validar no TrueNAS, incluindo efeitos das migrações.
7. [x] Confirmar na interface a listagem final e o funcionamento das regras de ignore; formalizar o aceite funcional da Etapa B (concluído em 22/11/2026).
8. [-] Executar os testes do backend para validar a migração v1 → v2, a edição de canais na API/UI e a preservação dos dados antes de marcar a Etapa C como concluída.

---

## 8. Como Atualizar este Roadmap

Sempre que uma tarefa for concluída ou uma decisão for tomada:

1. Marque o item com `[x]` e adicione a data: `(concluído em DD/MM/AAAA)`.
2. Se estiver em andamento: use `[-]` e descreva o progresso.
3. Atualize a data de “Última atualização” no topo do documento.
4. Se uma decisão em aberto for fechada, mova-a para a seção de decisões consolidadas (seção 3) e registre a data.
5. Mantenha este arquivo como a **única fonte de verdade** do planejamento.

---

**Fim do Roadmap Oficial**  
Este documento substitui todos os roadmaps anteriores. Qualquer alteração futura deve ser feita diretamente neste arquivo.
