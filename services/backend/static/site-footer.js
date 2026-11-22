(() => {
  const styleId = "omnikestr-site-footer-style";
  if (!document.querySelector(`#${styleId}`)) {
    const style = document.createElement("style");
    style.id = styleId;
    style.textContent = `
      body {
        min-height: 100vh;
        display: flex;
        flex-direction: column;
      }
      body > main {
        flex: 1 0 auto;
      }
      .app-footer {
        width: 100%;
        margin-top: auto;
        min-height: 40px;
        padding: 8px max(20px, 5vw);
        border-top: 1px solid #d8e0da;
        background: rgba(255, 255, 255, 0.92);
        color: #63766d;
        font-size: 11px;
        line-height: 1.5;
      }
      .app-footer__inner {
        width: min(1240px, 100%);
        margin: 0 auto;
        display: flex;
        align-items: center;
        justify-content: space-between;
        flex-wrap: wrap;
        gap: 6px 18px;
      }
      .app-footer strong { color: #1d3029; font-weight: 800; }
      @media (max-width: 620px) {
        .app-footer { padding-inline: 14px; }
        .app-footer__inner { align-items: flex-start; flex-direction: column; }
      }
    `;
    document.head.append(style);
  }

  if (!document.querySelector(".app-footer")) {
    const footer = document.createElement("footer");
    footer.className = "app-footer";
    footer.innerHTML = `
      <div class="app-footer__inner">
        <span id="app-build-info">Carregando versão...</span>
        <span id="app-catalog-info">Última atualização do catálogo: --</span>
      </div>
    `;
    document.body.append(footer);
  }

  function formatDate(value) {
    if (!value || value === "unknown") return null;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return null;
    return new Intl.DateTimeFormat("pt-BR", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  async function loadFooterInfo() {
    try {
      const healthResponse = await fetch("/api/health", { cache: "no-store" });
      if (!healthResponse.ok) throw new Error("health unavailable");
      const health = await healthResponse.json();

      const version = health.version && health.version !== "unknown" ? health.version : "0.2.0";
      const commit = health.commit && health.commit !== "unknown" ? health.commit.slice(0, 7) : "commit não informado";
      const builtAt = formatDate(health.built_at);
      const catalogUpdatedAt = formatDate(health.catalog_updated_at);

      document.querySelector("#app-build-info").innerHTML =
        `<strong>OmnIkestrAPIM ${version}</strong> · ${commit}` +
        (builtAt ? ` · build ${builtAt}` : " · build não informado");
      document.querySelector("#app-catalog-info").innerHTML =
        `<strong>Catálogo atualizado:</strong> ` +
        (catalogUpdatedAt ? catalogUpdatedAt : "ainda não indexado");
    } catch (_) {
      document.querySelector("#app-build-info").innerHTML =
        "<strong>OmnIkestrAPIM</strong> · versão indisponível";
      document.querySelector("#app-catalog-info").innerHTML =
        "<strong>Catálogo atualizado:</strong> indisponível";
    }
  }

  loadFooterInfo();
})();
