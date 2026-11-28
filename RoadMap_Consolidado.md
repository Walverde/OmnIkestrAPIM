# OmniKestra (OmnIkestrAPIM) — Roadmap Oficial

**Última atualização:** 28/11/2026

**Status geral do projeto:** Em desenvolvimento ativo — estabilização e segurança propostas como OmnIkestrAPIM 0.2.1. A autenticação, o alias de compatibilidade de `main.py`, documentação, testes locais, instalação parametrizada no TrueNAS e scripts de backup/restauração estão implementados no checkout; CI migrada, mas validação operacional no TrueNAS continua pendente.

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
| **Isolamento por workspace** | O modelo de dados é workspace-aware desde o início; toda seleção de workspace deve ser vinculada à identidade autenticada. Até existir autorização multiusuário, a API aceita somente o workspace `default`. |
| **Segurança na precificação** | Piso de preço (*hard floor*) aplicado **diretamente no motor**, não apenas na interface. |
| **Self-hosted em Linux** | Destino principal: NAS com TrueNAS, rodando em containers Docker. |

---

## 2. Avaliação Técnica do Código Atual

### Capacidade implementada

- Backend FastAPI modularizado em banco, modelos, catálogo, mídia e routers por domínio; `app.py` é o ponto de entrada atual e `main.py` é apenas alias de compatibilidade.
- `product.yaml` é lido e gravado atomicamente; o catálogo PostgreSQL é um índice reconstruível. Edições preservam chaves YAML desconhecidas.
- Catálogo isolado por workspace, identidade estável por UUID e suporte configurável a múltiplas raízes.
- Watcher com debounce, regras de exclusão configuráveis, reindexação manual e reconciliação periódica.
- Login de administrador com sessão assinada, chave de serviço restrita a eventos do watcher, rejeição de workspace fora de `default`, limites de IP configuráveis atrás de proxy confiável e aviso de setup quando raízes salvas deixam a allowlist.
- Compose TrueNAS configura o volume de produtos como somente leitura para o watcher; backend mantém acesso de escrita.
- Jobs de publicação usam chave idempotente única enquanto ativos; falhas de conexão tentam reconciliar pelo UUID exato em `seller_custom_field`; resultados sem correspondência verificável e reinícios ambíguos continuam exigindo reconciliação manual.
- Índice vazio só remove registros com ação explícita de limpeza forçada; essa ação não apaga arquivos.
- Métricas `/metrics` estão acessíveis sem login; logs de aplicação do backend são emitidos em JSON; epochs de autenticação usam `BigInteger`.
- Imagens podem ser entregues diretamente ou processadas como thumbnails WebP.
- Suítes locais após as correções: 54 testes do backend e 8 do watcher aprovados em 26/11/2026.

### Lacunas e verificações pendentes

- Revisar a implementação de autenticação/autorização antes de produção; a versão atual é de administrador único e não constitui gestão de usuários multi-tenant.
- Aplicar o Compose atualizado no TrueNAS e validar no container que o watcher inicia, observa e notifica com o dataset montado como somente leitura, enquanto o backend consegue atualizar manifestos.
- Confirmar no TrueNAS a configuração de secrets, HTTPS, firewall/LAN/VPN e restrições efetivas de exposição.
- Executar e validar backup, restauração e rollback no TrueNAS.
- Confirmar as suítes locais na pipeline CI após integração das alterações.
- Validar instalação limpa do Zero-Config/wizard e limites da allowlist em volumes montados no TrueNAS.
- O schema v2 está implementado e a suíte local passou; migrações futuras ainda dependem de requisito e aprovação explícitos.

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

- [x] Separar API, modelos, acesso ao banco, catálogo e mídia por domínio; `app.py` é o ponto de entrada atual e `main.py` permanece apenas como alias `from app import app` para compatibilidade (concluído em 24/11/2026).

### 3.5 Segurança da aplicação e implantação

- [x] Exigir senha de administrador, segredo de sessão e chave de serviço; usar sessão assinada com cookie `HttpOnly`/`SameSite=Strict`, autenticar a interface e APIs, restringir a chave do watcher à ingestão de eventos e rejeitar workspaces não autorizados (implementado e testado localmente em 24/11/2026; validação operacional e revisão externa pendentes).
- [x] Manter o volume de produtos com escrita para o backend e configurá-lo como somente leitura para o watcher no Compose TrueNAS (configuração e testes locais validados em 24/11/2026; implantação no NAS pendente).
- [x] Documentar limitações da autenticação, segredos obrigatórios, uso de HTTPS e reconciliação de publicação nos documentos de operação (concluído em 24/11/2026).

### 3.6 Inteligência Artificial

- [x] Usar exclusivamente o Ollama local instalado como App no TrueNAS, sem
  fallback para provedores em nuvem; configurar URL e modelo por instalação
  (decisão registrada em 22/11/2026).

### 3.7 Movimentação e cópia de produtos

- [x] Movimentação que preserva identidade não gera novo UUID; cópia verdadeira
  recebe identidade nova apenas na reconciliação. A pasta conflitante só é
  alterada após confirmar que o caminho antigo deixou de existir
  (implementado em 28/11/2026).

### 3.8 Formato de preço

- [ ] A representação numérica canônica ainda não foi fechada: YAML usa string
  JSON, banco usa `Numeric` e API pode expor decimal/float. A decisão exige teste
  de ida e volta PATCH → YAML → leitura antes de alterar o formato.

---

## 4. Roadmap de Desenvolvimento (Oficial)

> **Regra de atualização de status:**  
> Após concluir qualquer item, marcar como `[x]` e adicionar data de conclusão no formato `(concluído em DD/MM/AAAA)`.  
> Itens em andamento devem ser marcados com `[-]` e nota de progresso.

### Sequenciamento de versões proposto

As versões abaixo organizam prioridades; são propostas de planejamento, não compromissos de lançamento.

#### OmnIkestrAPIM 0.2.1 — Estabilização e segurança

1. [x] Eliminar duplicação do backend antigo; `services/backend/main.py` agora é somente alias de `app:app` (concluído e validado em 24/11/2026).
2. [-] Revisar autenticação e autorização. Sessão de administrador único, chave de serviço restrita e limite ao workspace `default` foram implementados e cobertos por testes locais; revisão independente, HTTPS e validação no TrueNAS continuam pendentes.
3. [x] Corrigir documentação desatualizada sobre autenticação, limitações de acesso e configuração segura (concluído em 24/11/2026).
4. [x] Executar integralmente as suítes locais: 54 testes do backend e 8 do watcher aprovados em 26/11/2026; confirmar também a execução na pipeline CI após integração.
5. [-] Validar mecanismos de proteção do TrueNAS: chave obrigatória, secrets, rede e volume do watcher somente leitura. Compose e configuração resolvida foram verificados localmente em 24/11/2026; falta implantar e testar no host.
6. [ ] Testar backup e restauração, incluindo consistência do banco e do dataset de produtos, atualização e rollback no TrueNAS.
7. [-] Disponibilizar instalação inicial no TrueNAS sem secrets pessoais no Compose: script idempotente, Compose parametrizado por `APPS_ROOT`, `BIND_IP` e `OMNIKESTRAPIM_VERSION`, watcher com heartbeat e suporte a `_FILE`, health/liveness separado e scripts de backup/restauração implementados; falta executar em host real.
8. [-] Separar a integração Mercado Livre em Compose opcional e validar a instalação básica sem arquivos de credential (arquivo opcional criado; validação no host pendente).

#### OmnIkestrAPIM 0.3 — Marketplace confiável

7. [-] Tornar a publicação idempotente: chave única para jobs ativos e reconciliação automática por UUID exato após falhas de conexão implementadas; sem correspondência exata permanece a reconciliação manual; validar no TrueNAS antes de concluir.
8. [-] Melhorar a recuperação de jobs interrompidos: jobs sem ID remoto conhecido exigem reconciliação manual; validar outros pontos de falha e recuperação operacional.
9. [ ] Sincronizar alterações de anúncios existentes com o marketplace.
10. [ ] Implementar gerenciamento de anúncios existentes (consulta, atualização e ciclo de vida).
11. [ ] Padronizar erros de publicação, estados e respostas exibidos ao operador.

#### OmnIkestrAPIM 0.4 — Gestão comercial

12. [ ] Completar o motor de precificação, preservando o piso mínimo obrigatório.
13. [ ] Registrar custos e taxas por canal.
14. [ ] Expandir o histórico de preços.
15. [ ] Implementar controle de estoque.
16. [ ] Implementar gestão de vendas.
17. [ ] Criar relatórios de rentabilidade.

#### OmnIkestrAPIM 0.5 — Inteligência artificial

18. [ ] Integrar com Ollama local, sem fallback para provedores em nuvem.
19. [ ] Identificar produtos por imagem.
20. [ ] Gerar sugestões de títulos e descrições.
21. [ ] Sugerir categorias.
22. [ ] Analisar qualidade das fotografias.
23. [ ] Sugerir preços respeitando o piso configurado.

#### OmnIkestrAPIM 0.6 — Market Intelligence

24. [ ] Coletar preços observados no mercado.
25. [ ] Comparar concorrentes, separando dados observados de estimativas.
26. [ ] Estimar tempo de venda.
27. [ ] Projetar rentabilidade.
28. [ ] Recomendar compras.
29. [ ] Sugerir estratégias de reajuste.

#### OmnIkestrAPIM 0.7 — Automação e expansão

30. [ ] Avaliar conector OLX somente conforme disponibilidade de integração autorizada.
31. [ ] Republicar anúncios inativos com regras e autorização explícitas.
32. [ ] Criar regras de automação auditáveis.
33. [ ] Implementar notificações.
34. [ ] Implementar operações em lote.
35. [ ] Avaliar aplicativo mobile.

O detalhamento técnico por etapas A–H abaixo permanece como mapa de implementação para as versões propostas.

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
**Status:** Funcionalidade concluída — validação funcional no TrueNAS em 22/11/2026; hardening operacional em andamento.
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
- [-] Validar no TrueNAS o acesso de rede após a implementação de sessão de administrador, chave de serviço e autorização do workspace (Compose limitado ao IP LAN `192.168.100.58`; falta aplicar/verificar HTTPS e regras no roteador/firewall do host e confirmar VPN).
- [-] Montar o volume de produtos do watcher como somente leitura (configurado no Compose TrueNAS e estado read-write/read-only conferido localmente em 24/11/2026; falta implantar no host e validar observação/notificação, mantendo o backend gravável).
- [-] Documentar e validar procedimento seguro de atualização: digests e instruções de atualização, backup e rollback registrados no guia em 22/11/2026; ainda falta executar backup/restauração, atualização e rollback no TrueNAS, inclusive verificar compatibilidade das migrações.
- [-] Bootstrap local Zero-Config e wizard inicial: defaults, seleção persistente, navegação allowlist e testes de limites já estão implementados; falta validar instalação limpa e seleção em volumes montados no TrueNAS.

#### B.4 Modularização do Backend
- [x] Separar modelos (`backend_core/models.py`), acesso ao banco (`backend_core/database.py`), lógica de catálogo (`backend_core/catalog.py`) e processamento de mídia (`backend_core/media.py`), mantendo `main.py` como alias fino de compatibilidade para `app.py` (concluído em 05/11/2026; alias simplificado e validado em 24/11/2026)
- [x] Validar a compatibilidade das rotas, migrações e imports existentes com a suíte do backend (20 testes aprovados em 05/11/2026; concluído em 05/11/2026)
- [x] Separar endpoints HTTP em routers de sistema, configuração, produtos, eventos, mídia e integração local, mantendo os caminhos HTTP existentes (concluído em 05/11/2026)
- [x] Validar a integração dos routers com a suíte do backend (20 testes aprovados em 05/11/2026; concluído em 05/11/2026)

**Critério de aceite da Etapa B:**  
Aplicação sobe em TrueNAS apenas com volumes e variáveis de ambiente, sem qualquer dependência do seletor Windows, com regras de ignore ativas e reindexação manual funcional.  
Status funcional concluído em 22/11/2026, conforme validação reportada no ambiente real e confirmação visual: indexação/leitura e gravação de `product.yaml` com `PUID=568`/`PGID=568`; movimentação preservando o UUID e atualizando caminhos de mídia; reconciliação periódica; reindexação manual com HTTP 200; e pasta `_Paste de teste` ignorada sem aparecer como produto adicional na interface. O hardening operacional permanece em andamento: aplicar os digests e secrets, confirmar HTTPS/rede, implantar e testar o mount somente leitura do watcher, executar backup/restauração/rollback e validar instalação limpa do wizard.

---

### Etapa C — Robustez do Schema e Imagens
**Status:** Implementada e validada localmente — schema v2 e testes da suíte concluídos em 24/11/2026; validação operacional de migrações permanece pendente.

- [x] Criar registry de migrações do `product.yaml`: manifests legados sem versão migram para v1 com preservação de campos desconhecidos; versões futuras são sinalizadas sem alteração do arquivo (22 testes do backend aprovados em 06/11/2026; concluído em 06/11/2026)
- [x] Introduzir configuração de habilitação por canal no `marketplaces` do `product.yaml`; migrar schema v1 (lista de nomes) para schema v2 (mapa de configurações), preservando campos desconhecidos (implementado em 22/11/2026; suíte backend aprovada em 24/11/2026).
- [x] Processar imagens para entrega na Web UI com thumbnails e conversão WebP, preservando suporte à entrega direta (concluído em 06/11/2026)

---

### Etapa D — Abstração de Marketplaces e Conector Mercado Livre
**Status:** Em implementação — base do conector e publicação manual implementadas em 22/11/2026; autenticação, reconciliação e idempotência local cobertas por testes em 24/11/2026; validação externa pendente.

- [x] Construção da camada de abstração comum por interface de conector e registry; validar novos adaptadores ao adicioná-los
- [ ] Conector Mercado Livre:
  - [-] Autenticação OAuth2 com estado de uso único, tokens cifrados no PostgreSQL e renovação automática; falta validar com credenciais reais no TrueNAS
  - [-] Publicação manual explícita a partir do manifesto, com fila persistente e ID externo salvo; publicação está implementada, sincronização de alterações e validação de conta/categoria reais permanecem pendentes
  - [x] Intervalo mínimo entre chamadas, fila persistente no PostgreSQL, backoff/retry de erros transitórios e endpoint de estado da tarefa
  - [-] Bloquear duplicatas de jobs ativos e interrompidos quando o resultado remoto for ambíguo; chave idempotente, estado de reconciliação manual e endpoint de reconciliação implementados e cobertos por testes locais; validar no TrueNAS antes de concluir

---

### Etapa E — Motor de Precificação e Salvaguardas
**Status:** Em implementação — piso e auditoria implementados em 22/11/2026; comportamento coberto pela suíte backend aprovada em 24/11/2026.

- [x] Piso explícito por produto (`price_floor`) aplicado na validação do backend e exigido antes da publicação; comportamento validado localmente em 24/11/2026
- [x] Rejeitar preços abaixo do piso e registrar alterações aceitas em `price_audits`; regressões cobertas pela suíte backend aprovada em 24/11/2026
- [ ] Regras adicionais de precificação automática com limites verificáveis; definir algoritmo/política antes de automatizar sugestões ou alterações

---

### Etapa F — Camada de Eventos e Observabilidade
**Status:** Em implementação — eventos, fila e telemetria locais implementados; testes da suíte aprovados em 24/11/2026; validação operacional pendente

- [x] Eventos de catálogo, preço e integração persistidos em `domain_events`, com consulta via API; cobertos pela suíte backend aprovada em 24/11/2026
- [x] Logs JSON e métricas Prometheus HTTP/fila disponíveis em `/metrics` (endpoint público para scraping); testes locais aprovados em 26/11/2026
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
| 2 | Escopo da primeira versão utilizável | Definir critérios objetivos de entrega e quais integrações entram no primeiro release. | Aberta; revisar após estabilização e validação operacional da 0.2.1 |
| 3 | Próximas mudanças funcionais do manifest | Exigir requisito concreto antes de criar nova versão sequencial do schema; preservar campos desconhecidos. | Aberta; schema v2 por canal já foi implementado |

---

## 6. Riscos Principais e Mitigações

| Risco | Impacto | Mitigação |
|-------|---------|-----------|
| Conflito YAML × Banco | Alto | Fonte da verdade **sempre** é o YAML; o banco é índice reconstruível e nunca substitui silenciosamente o manifest. |
| Eventos não confiáveis em SMB/NFS | Alto | Reconciliação periódica configurável + reindexação manual. |
| Arquivos pesados (PSD/RAW) na árvore | Médio-Alto | Isolamento em `_arquivos/` + padrões de ignore. |
| Precificação automática sem piso | Crítico | *Hard floor* obrigatório no motor (Etapa E). |
| Credenciais e rate limit do Mercado Livre | Alto | Tratar na Etapa D com gestão segura de tokens e filas. |
| Acesso entre workspaces | Alto | A versão atual só autoriza o workspace `default`; não habilitar outros workspaces antes de vincular cada identidade aos workspaces permitidos. |

---

## 7. Próximos Passos Imediatos (Ordem de Execução)

1. [-] Concluir a revisão da autenticação e autorização e validar HTTPS, secrets e regras de rede no TrueNAS (implementação e testes locais feitos; validação operacional pendente).
2. [-] Implantar o Compose atualizado e confirmar o watcher read-only e o backend read-write no TrueNAS, com observação, notificação e edição de manifestos funcionais.
3. [ ] Executar e validar backup/restauração, atualização e rollback no TrueNAS.
4. [x] Executar localmente as suítes completas do backend (54 testes) e watcher (8 testes), ambas aprovadas em 26/11/2026; confirmar a pipeline CI após integração.
5. [-] Validar migrações e recuperação idempotente da fila com a stack real antes de concluir a etapa Marketplace 0.3.
6. [-] Validar instalação limpa do Zero-Config/wizard e seleção de raízes permitidas em volumes montados no TrueNAS.

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
