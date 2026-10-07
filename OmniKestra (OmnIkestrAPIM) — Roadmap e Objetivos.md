# OmniKestra (OmnIkestrAPIM) — Roadmap e Objetivos

Atualizado em 07/10/2026. Documento de acompanhamento: o que já foi feito, o que falta e em que ordem. Os itens marcados como *proposta* são sugestões de priorização e ainda não foram confirmados.

## 1. Visão e princípios

O OmnIkestrAPIM é uma ferramenta self-hosted de gestão de inventário e de anúncios em marketplaces. Os princípios que orientam todas as decisões são:

- **Filesystem-first:** os arquivos YAML são a fonte da verdade e o banco de dados serve apenas como índice. Toda decisão deve reforçar esse padrão, não enfraquecê-lo.
- **Interface comum entre marketplaces:** uma abstração única sobre Mercado Livre, OLX e futuros canais.
- **Camada orientada a eventos, introduzida de forma progressiva.**
- **Market Intelligence honesto:** dados observados ficam separados de estimativas geradas por IA.
- **Isolamento por workspace** desde o início, pois retrofit de multi-tenancy é caro.
- **Segurança na precificação:** piso de preço aplicado no próprio motor, não só na interface.
- **Self-hosted em Linux:** destino inicial é um NAS com TrueNAS, rodando em container.

## 2. O que já foi feito

### Planejamento e arquitetura

- Documento de arquitetura detalhado.
- Crítica estruturada da arquitetura, com pontos fortes e lacunas mapeados.
- 2 ADRs escritos: resolução de conflitos YAML/banco e isolamento por workspace.

### Etapa A (implementada e ativa em 06/10/2026)

- Schema v1 do `product.yaml` com UUID persistente e escrita atômica.
- Watcher com debounce de 1,5 s e reindexação incremental.
- Reconciliação a cada 15 minutos para cobrir eventos perdidos.
- Workspace padrão.
- Migrações com Alembic.
- Token no helper do Windows.
- Limitação conhecida: a aplicação ainda usa uma única raiz de produtos.

### Aplicação local

- Container, página de configurações e página inicial configurados.
- Seletor de pasta do Windows funcionando (solução temporária, ver seção 4).
- Scripts `start-local.cmd` e `start-local.ps1` para subir o app no Windows (VS Code).

## 3. Decisões de arquitetura recentes

### 3.1 Implantação no TrueNAS (Linux)

O seletor nativo de pastas do Windows não serve em plataforma Linux/container. Direção adotada em discussão:

- A raiz dos anúncios passa a ser definida por **configuração e volume montado** (por exemplo, variável de ambiente apontando para o dataset montado no container).
- Se for desejável escolher pastas pela interface, usar um **navegador de pastas na própria interface web**, restrito por allowlist aos pontos de montagem permitidos.
- O script do Windows fica apenas como modo de desenvolvimento.
- **Watcher em NAS:** compartilhamentos SMB/NFS não geram eventos de arquivo de forma confiável quando a alteração vem de outra máquina. A reconciliação periódica é o mecanismo de segurança; prever intervalo configurável e um botão de reindexar sob demanda.
- **Permissões:** o usuário (UID/GID) do container deve coincidir com o dono do dataset para permitir a escrita atômica dos YAMLs.

### 3.2 Estrutura de pastas dos anúncios

A árvore de pastas guarda hoje não só o anúncio, mas também fotos originais, edições do Photoshop e documentos de cada produto. Para não sobrecarregar o watcher e o índice, a direção em avaliação é:

- Uma **pasta reservada dentro de cada anúncio**, ignorada pelo watcher (por exemplo, `_arquivos/` com `originais/`, `psd/` e `documentos/`).
- Regra de ignorar **configurável**: prefixo `_` mais uma lista de padrões (`*.psd`, `~$*`, `*.tmp`, `Thumbs.db`).
- O app indexa apenas o `product.yaml` e as fotos finais do anúncio.
- Alternativa considerada: árvore paralela em dataset separado, ligada ao anúncio pelo UUID. Só compensa se os originais forem muito pesados ou se o backup/snapshot precisar ser diferente.

### 3.3 Categorias em subpastas

Parte dos anúncios fica em subpastas que funcionam como categorias. Direção sugerida: o produto é identificado pelo **UUID do `product.yaml`**, e a categoria é um campo opcional (podendo ser inferida da pasta pai). Assim, mover um produto de pasta não quebra nada. Decisão final entre manter a organização por pastas ou colocar tudo na raiz ainda está em aberto.

## 4. Roadmap — o que falta

A numeração das etapas após a A é uma *proposta* de ordenação.

### Etapa B — Fundação para o NAS e organização de pastas *(proposta)*

Objetivo: deixar o app pronto para rodar no TrueNAS e resolver a estrutura de pastas antes de avançar.

- [ ] Decidir a estrutura de pastas (categorias em subpastas × tudo na raiz).
- [ ] Escrever ADR cobrindo: raiz via configuração/volume, regra de ignorar do watcher, produto identificado por UUID com categoria opcional.
- [ ] Substituir o seletor nativo do Windows por raiz configurada por variável/volume.
- [ ] Navegador de pastas na interface web com allowlist (opcional).
- [ ] Implementar pasta ignorada pelo watcher (prefixo `_` + padrões configuráveis).
- [ ] Ajustar permissões (UID/GID) e empacotamento para TrueNAS.
- [ ] Intervalo de reconciliação configurável e botão de reindexar agora.
- [ ] Tratar casos de borda do watcher: renomeação de arquivos e pastas, debounce e eventos perdidos.
- [ ] Suporte a múltiplas raízes de produtos.

### Etapa C — Robustez do schema e do armazenamento *(proposta)*

- [ ] Estratégia de versionamento do schema do `product.yaml`, com migração entre versões.
- [ ] Limites e política de armazenamento de imagens.
- [ ] Revisão do escopo da Fase 1 (reduzir ou reestruturar os marcos, apontada como ampla demais).

### Etapa D — Interface comum e conector do Mercado Livre *(proposta)*

- [ ] Interface comum de abstração entre marketplaces.
- [ ] Conector do Mercado Livre: autenticação, gestão segura de credenciais e tratamento de rate limit.
- [ ] Publicação e sincronização de anúncios a partir dos YAMLs.

### Etapa E — Precificação com salvaguardas *(proposta)*

- [ ] Piso de preço (hard floor) aplicado no motor de precificação.
- [ ] Regras de precificação automática com limites verificáveis e trilha de auditoria.

### Etapa F — Camada de eventos e observabilidade *(proposta)*

- [ ] Introdução progressiva da camada orientada a eventos.
- [ ] Observabilidade: métricas, logs e limites de alerta.

### Etapa G — Market Intelligence e novos canais *(proposta)*

- [ ] Módulo de Market Intelligence com separação explícita entre dados observados e estimativas de IA.
- [ ] Conector da OLX sobre a abstração comum.

## 5. Decisões em aberto

- Categorias em subpastas ou todos os anúncios na raiz.
- Pasta ignorada dentro de cada anúncio (recomendada) ou árvore paralela de material de trabalho.
- Navegador de pastas na interface ou apenas configuração por volume.
- Quais etapas após a A já estão definidas no plano original, para alinhar este roadmap.
- Escopo final da Fase 1.

## 6. Riscos principais

- **Conflito YAML/banco:** a resolução de conflitos de write-back é a decisão mais fundamental e precisa permanecer coerente com o ADR já escrito.
- **Watcher em compartilhamento de rede:** eventos de arquivo não confiáveis no NAS exigem depender da reconciliação.
- **Multi-tenancy tardia:** qualquer dado sem workspace no schema vira retrabalho caro.
- **Precificação automática sem piso no motor:** risco financeiro direto em contexto de revenda.
- **Credenciais e rate limit do Mercado Livre:** subespecificados hoje.
- **Material pesado na árvore monitorada:** arquivos de Photoshop e originais podem degradar o watcher e o índice se não forem isolados.

## 7. Próximos passos imediatos

1. Decidir a estrutura de pastas e a regra de ignorar do watcher.
2. Escrever o ADR dessas decisões, junto com a raiz via configuração/volume.
3. Remover a dependência do seletor do Windows no caminho principal e testar no TrueNAS.
4. Fechar o escopo da Fase 1.
5. Iniciar a abstração de marketplaces e o conector do Mercado Livre, já com o piso de preço previsto no motor.
