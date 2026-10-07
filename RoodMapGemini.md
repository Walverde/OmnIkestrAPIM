# OmniKestra (OmnIkestrAPIM) — Roadmap e Objetivos

*Atualizado em 07/10/2026 com base na auditoria do código-fonte e reuniões de arquitetura.*[cite: 1]

---

## 1. Visão e Princípios

O **OmnIkestrAPIM** é uma ferramenta *self-hosted* de gestão de inventário e de anúncios em marketplaces[cite: 1]. Os princípios que orientam todas as decisões do sistema são:

- **Filesystem-first:** Os arquivos YAML são a fonte da verdade e o banco de dados serve apenas como índice[cite: 1]. Toda decisão deve reforçar esse padrão, não enfraquecê-lo[cite: 1].
- **Interface comum entre marketplaces:** Uma abstração única sobre Mercado Livre, OLX e futuros canais[cite: 1].
- **Camada orientada a eventos:** Introduzida de forma progressiva[cite: 1].
- **Market Intelligence honesto:** Dados observados ficam estritamente separados de estimativas geradas por IA[cite: 1].
- **Isolamento por workspace:** Nativo desde o início, evitando o custo elevado de retrofit de *multi-tenancy*[cite: 1].
- **Segurança na precificação:** Piso de preço (*hard floor*) aplicado diretamente no motor de precificação, não apenas na interface[cite: 1].
- **Self-hosted em Linux:** O destino principal é um servidor NAS com TrueNAS, rodando em contêineres Docker[cite: 1].

---

## 2. Avaliação Técnica do Código-Fonte

### Pontos Fortes Identificados
- **Escrita Atômica:** A gravação do `product.yaml` é feita via arquivo temporário (`.tmp`) seguido de substituição atômica (`replace`), prevenindo corrupção de dados em quedas do sistema[cite: 1, 2].
- **Preservação do YAML:** Estrutura preserva campos não mapeados no formulário/banco por meio de carga/dump direto[cite: 2].
- **Mecanismo de Resiliência:** Watcher com *debounce* de 1,5s e reconciliação periódica a cada 15 minutos para tratar eventos perdidos no File System[cite: 1, 2].
- **Desacoplamento de Diretório:** Suporte a subpastas/categorias (`folder_group`) vinculadas ao UUID do produto, permitindo mover pastas sem perder a identidade[cite: 1, 2].
- **Multi-tenancy Nativo:** Isolamento por workspace já aplicado na API e nas migrações do banco de dados (`WorkspaceProductCatalog`)[cite: 1, 2].

### Oportunidades de Melhoria
- **Dependência do Windows:** Substituir a seleção de pastas via PowerShell (`windows_folder_picker.ps1`) por mapeamento de volume/variável de ambiente no Linux[cite: 1, 2].
- **Filtro de Arquivos do Watcher:** Ignorar arquivos pesados e temporários (`.psd`, `Thumbs.db`, `~$*`) diretamente no motor do watcher[cite: 1].
- **Múltiplas Raízes:** Expandir o suporte para escnear múltiplos volumes e diretórios raízes dinamicamente[cite: 1].

---

## 3. Roadmap de Desenvolvimento

### 🟢 Etapa A — Fundação e Core (Concluída)
- [x] Schema v1 do `product.yaml` com UUID persistente e escrita atômica[cite: 1, 2].
- [x] Serviço de Watcher com debounce (1.5s) e reconciliação automática (15 min)[cite: 1, 2].
- [x] Catalogação e isolamento de dados por Workspace[cite: 1, 2].
- [x] Mapeamento de categorias e organização via subpastas (`folder_group`)[cite: 1, 2].
- [x] Scripts de execução local para desenvolvimento em Windows (`start-local.cmd` / `start-local.ps1`)[cite: 1].

---

### 🟡 Etapa B — Adaptação para Linux / TrueNAS & Tratamento de Pastas *(Em Andamento / Prioridade 1)*
*Objetivo: Deixar o app pronto para rodar no TrueNAS e otimizar o consumo de I/O em discos do NAS.*[cite: 1]

- [x] **Organização por Subpastas:** Mantida a identificação contínua do produto pelo UUID do YAML, independente de mudanças no caminho[cite: 1, 2].
- [ ] **Configuração de Raiz por Volume/Ambiente:**
  - [ ] Remover dependência do `windows_folder_picker.ps1` no fluxo principal[cite: 1, 2].
  - [ ] Definir raízes de anúncios por variáveis de ambiente (`PRODUCT_ROOTS` / `/data/products`) e volumes montados[cite: 1].
  - [ ] Implementar navegador de diretórios restrito por *allowlist* na Web UI[cite: 1].
- [ ] **Filtros e Regras de Exclusão no Watcher:**
  - [ ] Ignorar pasta reservada de arquivos pesados dentro do anúncio (`_arquivos/` contendo `originais/`, `psd/` e `documentos/`)[cite: 1].
  - [ ] Aplicar padrão configurável de exclusão para arquivos temporários e do sistema (`*.psd`, `~$*`, `*.tmp`, `Thumbs.db`, `.DS_Store`)[cite: 1].
- [ ] **Ajustes de Infraestrutura para NAS:**
  - [ ] Intervalo de reconciliação de rede SMB/NFS configurável[cite: 1].
  - [ ] Botão de reindexação manual na interface web[cite: 1].
  - [ ] Configuração de permissões PUID/PGID no Docker para escrita atômica nos YAMLs[cite: 1].

---

### 🔵 Etapa C — Robustez do Schema, Imagens e Múltiplas Raízes *(Proposta)*
- [ ] Versionamento do schema do `product.yaml` com migração transparente entre versões[cite: 1].
- [ ] Política de limites e otimização de imagens (geração de thumbnails e conversão WebP para a Web UI)[cite: 1].
- [ ] Suporte nativo para cadastro e monitoramento de múltiplas raízes de produtos[cite: 1].

---

### 🟣 Etapa D — Abstração de Marketplaces e Conector Mercado Livre *(Proposta)*
- [ ] Construção da camada de abstração comum entre múltiplos marketplaces[cite: 1].
- [ ] Conector Mercado Livre:
  - [ ] Autenticação OAuth2, gestão segura de credenciais e renovação de tokens[cite: 1].
  - [ ] Publicação e sincronização automática de anúncios direcionada pelos arquivos YAML[cite: 1].
  - [ ] Controle de *rate limit* e gerenciamento de filas de requisição[cite: 1].

---

### 🔴 Etapa E — Motor de Precificação e Salvaguardas *(Proposta)*
- [ ] Implantação do piso de preço (*hard floor*) aplicado diretamente no motor do backend[cite: 1].
- [ ] Regras de precificação automática com limites verificáveis e trilha de auditoria[cite: 1].

---

### ⚪ Etapas Futuras (F & G)
- [ ] **Camada de Eventos e Observabilidade:** Mapeamento de métricas, logs centralizados e alertas[cite: 1].
- [ ] **Market Intelligence:** Módulo de inteligência de mercado com separação rigorosa entre dados observados e estimativas de IA[cite: 1].
- [ ] **Conector OLX e Novos Canais:** Expansão da abstração comum para novos marketplaces[cite: 1].

---

## 4. Riscos Mapeados e Mitigações

1. **Conflito YAML x Banco de Dados:** A fonte da verdade é sempre o YAML. Escreveu no banco, reflete no YAML atomaticamente[cite: 1].
2. **Eventos Não Confiáveis em Rede (SMB/NFS):** Reconciliação periódica ajustável e reindexação manual cobrem falhas do watcher do NAS[cite: 1].
3. **Inclusão de Arquivos Pesados (PSD/RAW):** Isolamento em pasta `_arquivos/` e padrões de ignorar garantem a performance da reindexação[cite: 1].
4. **Variações Indevidas de Preço:** Trava do piso de preço (*hard floor*) no próprio motor previne prejuízos em integrações automáticas[cite: 1].

---

## 5. Próximos Passos Imediatos

1. Remover a chamada do script `.ps1` do Windows no fluxo principal e migrar a configuração para variáveis de ambiente Docker (`PRODUCT_ROOTS`)[cite: 1, 2].
2. Adicionar as regras de exclusão de arquivos (`ignore_patterns`) no serviço do Watcher[cite: 1, 2].
3. Criar a rota na API do backend para acionar a reindexação manual sob demanda[cite: 1, 2].
