# OmniKestra (OmnIkestrAPIM) — Roadmap Oficial

**Última atualização:** 07/10/2026  
**Status geral do projeto:** Em desenvolvimento ativo — Etapa B em andamento  
**Documento de referência:** Este é o roadmap único e oficial. Todas as decisões, tarefas e atualizações de status devem ser registradas aqui.

---

## 1. Visão e Princípios Fundamentais

O **OmnIkestrAPIM** é uma ferramenta *self-hosted* de gestão de inventário e anúncios em marketplaces, orientada a autonomia e inteligência artificial.

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

### Pontos Fortes (já implementados)

- **Escrita atômica** do `product.yaml` (arquivo temporário + `replace`) — previne corrupção.
- **Preservação de campos não mapeados** no YAML (carga/dump direto).
- **Watcher resiliente**: debounce de 1,5 s + reconciliação periódica a cada 15 minutos.
- **Identidade por UUID**: produto identificado pelo UUID do YAML, independente da pasta (`folder_group`).
- **Multi-tenancy nativo**: isolamento por workspace já presente na API e nas migrações (`WorkspaceProductCatalog`).

### Oportunidades de Melhoria (prioritárias)

- Remover dependência do seletor nativo do Windows (`windows_folder_picker.ps1`).
- Implementar filtros de exclusão no watcher (arquivos pesados e temporários).
- Suporte nativo a múltiplas raízes de produtos.
- Ajustes de permissões (PUID/PGID) e empacotamento para TrueNAS.

---

## 3. Decisões de Arquitetura Consolidadas

### 3.1 Implantação no TrueNAS / Linux

| Decisão | Status | Detalhe |
|---------|--------|---------|
| Raiz dos produtos via configuração/volume | Pendente | Variáveis de ambiente (`PRODUCT_ROOTS` ou similar) + volumes montados. |
| Navegador de pastas na Web UI | Opcional | Restrito por *allowlist* aos pontos de montagem permitidos. |
| Script Windows | Apenas dev | Fica apenas para desenvolvimento local. |
| Watcher em SMB/NFS | Mitigado | Eventos não confiáveis → depender de reconciliação periódica configurável + botão de reindexação manual. |
| Permissões | Pendente | Container deve rodar com UID/GID do dono do dataset (PUID/PGID). |

### 3.2 Estrutura de Pastas dos Anúncios

**Direção adotada (recomendada):**

- Pasta reservada **dentro de cada anúncio**, ignorada pelo watcher: `_arquivos/`  
  Contendo subpastas: `originais/`, `psd/`, `documentos/`.
- Regras de ignore **configuráveis**:
  - Prefixo `_`
  - Padrões: `*.psd`, `~$*`, `*.tmp`, `Thumbs.db`, `.DS_Store`
- O sistema indexa **apenas** o `product.yaml` e as fotos finais do anúncio.
- Alternativa (não prioritária): árvore paralela em dataset separado (só se originais forem extremamente pesados).

### 3.3 Categorias e Organização

- Produto identificado **sempre** pelo UUID do `product.yaml`.
- Categoria é campo **opcional** (pode ser inferida da pasta pai).
- Mover produto de pasta **não quebra** a identidade.
- Decisão final (subpastas vs raiz única) ainda aberta → ver seção 5.

---

## 4. Roadmap de Desenvolvimento (Oficial)

> **Regra de atualização de status:**  
> Após concluir qualquer item, marcar como `[x]` e adicionar data de conclusão no formato `(concluído em DD/MM/AAAA)`.  
> Itens em andamento devem ser marcados com `[-]` e nota de progresso.

### Etapa A — Fundação e Core
**Status:** Concluída (06/10/2026)

- [x] Schema v1 do `product.yaml` com UUID persistente e escrita atômica
- [x] Serviço de Watcher com debounce (1,5 s) e reconciliação automática (15 min)
- [x] Catalogação e isolamento de dados por Workspace
- [x] Mapeamento de categorias via subpastas (`folder_group`)
- [x] Scripts de execução local para Windows (`start-local.cmd` / `start-local.ps1`)
- [x] Migrações com Alembic
- [x] Token no helper do Windows

---

### Etapa B — Adaptação para Linux / TrueNAS & Tratamento de Pastas
**Status:** Em andamento — Prioridade 1  
**Objetivo:** Deixar o app pronto para rodar em TrueNAS e otimizar I/O em discos de rede.

#### B.1 Configuração de Raiz por Volume/Ambiente
- [ ] Remover dependência do `windows_folder_picker.ps1` no fluxo principal
- [ ] Definir raízes de anúncios por variáveis de ambiente (`PRODUCT_ROOTS` / `/data/products`) e volumes montados
- [ ] Implementar navegador de diretórios restrito por *allowlist* na Web UI (opcional, mas recomendado)

#### B.2 Filtros e Regras de Exclusão no Watcher
- [ ] Ignorar pasta reservada `_arquivos/` (com `originais/`, `psd/`, `documentos/`)
- [ ] Aplicar padrões configuráveis de exclusão: `*.psd`, `~$*`, `*.tmp`, `Thumbs.db`, `.DS_Store`
- [ ] Tornar as regras de ignore configuráveis (arquivo de configuração ou variáveis de ambiente)

#### B.3 Ajustes de Infraestrutura para NAS
- [ ] Intervalo de reconciliação configurável (SMB/NFS)
- [ ] Botão / endpoint de reindexação manual sob demanda
- [ ] Configuração de permissões PUID/PGID no Docker para escrita atômica
- [ ] Tratar casos de borda: renomeação de arquivos/pastas, debounce e eventos perdidos
- [ ] Suporte a múltiplas raízes de produtos

**Critério de aceite da Etapa B:**  
Aplicação sobe em TrueNAS apenas com volumes e variáveis de ambiente, sem qualquer dependência do seletor Windows, com regras de ignore ativas e reindexação manual funcional.

---

### Etapa C — Robustez do Schema, Imagens e Múltiplas Raízes
**Status:** Proposta (após conclusão da Etapa B)

- [ ] Versionamento do schema do `product.yaml` com migração transparente entre versões
- [ ] Política de limites e otimização de imagens (thumbnails + conversão WebP para a Web UI)
- [ ] Suporte nativo completo para cadastro e monitoramento de múltiplas raízes

---

### Etapa D — Abstração de Marketplaces e Conector Mercado Livre
**Status:** Proposta

- [ ] Construção da camada de abstração comum entre múltiplos marketplaces
- [ ] Conector Mercado Livre:
  - [ ] Autenticação OAuth2 + gestão segura de credenciais e renovação de tokens
  - [ ] Publicação e sincronização automática de anúncios a partir dos YAMLs
  - [ ] Controle de *rate limit* e gerenciamento de filas de requisição

---

### Etapa E — Motor de Precificação e Salvaguardas
**Status:** Proposta

- [ ] Implantação do piso de preço (*hard floor*) aplicado **diretamente no motor** do backend
- [ ] Regras de precificação automática com limites verificáveis e trilha de auditoria

---

### Etapa F — Camada de Eventos e Observabilidade
**Status:** Proposta

- [ ] Introdução progressiva da camada orientada a eventos
- [ ] Observabilidade: métricas, logs centralizados e alertas

---

### Etapa G — Market Intelligence e Novos Canais
**Status:** Proposta

- [ ] Módulo de Market Intelligence com separação rigorosa entre dados observados e estimativas de IA
- [ ] Conector OLX sobre a abstração comum
- [ ] Preparação para novos canais futuros

---

### Etapa H — AI Enrichment (Local-first)
**Status:** Proposta (após Etapa C ou D)

- [ ] Serviço de IA local via Ollama (ou LiteLLM como proxy)
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

Estas decisões devem ser fechadas preferencialmente ainda na Etapa B:

| # | Decisão | Opções | Recomendação atual | Status |
|---|---------|--------|--------------------|--------|
| 1 | Organização de categorias | Subpastas vs tudo na raiz | Manter subpastas + categoria opcional no YAML | Aberta |
| 2 | Material de trabalho | Pasta `_arquivos/` dentro do anúncio vs árvore paralela | Pasta `_arquivos/` (mais simples) | Quase fechada |
| 3 | Seleção de pastas | Navegador web com allowlist vs apenas volume | Ambos (volume obrigatório + navegador opcional) | Aberta |
| 4 | Escopo final da Fase 1 | Definir claramente o que entra na primeira versão utilizável | A definir após Etapa B | Aberta |

---

## 6. Riscos Principais e Mitigações

| Risco | Impacto | Mitigação |
|-------|---------|-----------|
| Conflito YAML × Banco | Alto | Fonte da verdade **sempre** é o YAML. Write-back deve seguir o ADR já existente. |
| Eventos não confiáveis em SMB/NFS | Alto | Reconciliação periódica configurável + reindexação manual. |
| Arquivos pesados (PSD/RAW) na árvore | Médio-Alto | Isolamento em `_arquivos/` + padrões de ignore. |
| Precificação automática sem piso | Crítico | *Hard floor* obrigatório no motor (Etapa E). |
| Credenciais e rate limit do Mercado Livre | Alto | Tratar na Etapa D com gestão segura de tokens e filas. |
| Multi-tenancy tardia | Alto | Já mitigado (workspace nativo desde a Etapa A). |

---

## 7. Próximos Passos Imediatos (Ordem de Execução)

1. **Fechar decisões de estrutura de pastas** (seção 5 — itens 1 e 2).
2. Escrever ADR cobrindo:
   - Raiz via configuração/volume
   - Regras de ignore do watcher
   - Identificação por UUID + categoria opcional
3. Remover a dependência do seletor Windows do fluxo principal.
4. Implementar regras de exclusão (`ignore_patterns`) no Watcher.
5. Criar endpoint/botão de reindexação manual.
6. Configurar PUID/PGID e testar no TrueNAS.
7. Após conclusão da Etapa B → revisar e fechar o escopo da Fase 1.

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
