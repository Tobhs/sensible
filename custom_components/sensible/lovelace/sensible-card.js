/**
 * Sensible - custom Lovelace card
 * Renders any Sensible sensor richly: optional image, a category chip, the
 * state, a detail line, and fact chips. Dependency-free custom element.
 */

const CARD_VERSION = "1.0.0";

const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/[&<>"']/g, (c) => ESC_MAP[c]);
}
function httpsUrl(value) {
  return typeof value === "string" && value.startsWith("https://") ? value : null;
}
function levelClass(level) {
  return ["good", "warn", "bad", "info"].includes(level) ? level : "";
}

class SensibleCard extends HTMLElement {
  setConfig(config) {
    this._config = { entity: config.entity || null, title: config.title || "" };
    if (!this._built) {
      this.attachShadow({ mode: "open" });
      this._built = true;
    }
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 3;
  }

  static getStubConfig() {
    return {};
  }

  _entityId() {
    if (this._config.entity) return this._config.entity;
    const states = this._hass ? this._hass.states : {};
    for (const id of Object.keys(states)) {
      if (!id.startsWith("sensor.")) continue;
      const a = states[id].attributes || {};
      if (a.category && Array.isArray(a.facts)) return id;
    }
    return null;
  }

  _render() {
    if (!this._hass || !this._config) return;
    const id = this._entityId();
    const stateObj = id ? this._hass.states[id] : null;

    if (!stateObj) {
      this.shadowRoot.innerHTML = `<style>${this._styles()}</style>
        <ha-card><div class="empty">Set <code>entity:</code> to a Sensible sensor.</div></ha-card>`;
      return;
    }

    const a = stateObj.attributes || {};
    const name = esc(this._config.title || a.friendly_name || id);
    const category = esc(a.category || "");
    const catClass = levelClass(a.category_level);
    const state = esc(stateObj.state);
    const detail = esc(a.detail || "");
    const facts = (Array.isArray(a.facts) ? a.facts : []).map((f) =>
      f && typeof f === "object"
        ? { text: f.text, level: f.level }
        : { text: f, level: null }
    );
    const picture = httpsUrl(a.entity_picture);
    const url = httpsUrl(a.url);
    const clickable = !!url;

    const cover = picture
      ? `<div class="cover"><img src="${esc(picture)}" alt="" loading="lazy"></div>`
      : "";

    this.shadowRoot.innerHTML = `
      <style>${this._styles()}</style>
      <ha-card class="${clickable ? "clickable" : ""}">
        ${cover}
        <div class="body">
          <div class="head">
            <span class="name">${name}</span>
            ${category ? `<span class="chip ${catClass}">${category}</span>` : ""}
          </div>
          <div class="state ${catClass}">${state}</div>
          ${detail ? `<div class="detail">${detail}</div>` : ""}
          ${facts.length ? `<div class="chips">${facts.map((f) => `<span class="${levelClass(f.level)}">${esc(f.text)}</span>`).join("")}</div>` : ""}
        </div>
      </ha-card>`;

    if (clickable) {
      this.shadowRoot
        .querySelector("ha-card")
        .addEventListener("click", () => window.open(url, "_blank", "noopener"));
    }
  }

  _styles() {
    return `
      ha-card { overflow: hidden; }
      ha-card.clickable { cursor: pointer; }
      ha-card.clickable:hover { background: var(--secondary-background-color); }
      .cover { width: 100%; max-height: 190px; overflow: hidden; }
      .cover img { width: 100%; height: 190px; object-fit: cover; display: block; }
      .body { padding: 16px; }
      .head {
        display: flex; align-items: center; justify-content: space-between; gap: 10px;
      }
      .name {
        font-size: 1.05rem; font-weight: 500; min-width: 0;
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        color: var(--primary-text-color);
      }
      .chip {
        flex: none; font-size: 0.7rem; font-weight: 600; letter-spacing: 0.03em;
        padding: 3px 10px; border-radius: 12px; white-space: nowrap;
        background: var(--secondary-background-color); color: var(--primary-color);
      }
      .state {
        margin-top: 6px; font-size: 1.7rem; font-weight: 700; line-height: 1.15;
        color: var(--primary-text-color);
      }
      .detail {
        margin-top: 8px; font-size: 0.92rem; line-height: 1.5;
        color: var(--secondary-text-color);
      }
      .chips {
        margin-top: 12px; display: flex; flex-wrap: wrap; gap: 6px;
        justify-content: center;
      }
      .chips span {
        font-size: 0.75rem; padding: 3px 9px; border-radius: 10px; white-space: nowrap;
        background: var(--secondary-background-color); color: var(--secondary-text-color);
      }
      .chip.good, .chips span.good {
        color: var(--success-color);
        background: color-mix(in srgb, var(--success-color) 16%, transparent);
      }
      .chip.warn, .chips span.warn {
        color: var(--warning-color);
        background: color-mix(in srgb, var(--warning-color) 18%, transparent);
      }
      .chip.bad, .chips span.bad {
        color: var(--error-color);
        background: color-mix(in srgb, var(--error-color) 16%, transparent);
      }
      .chip.info, .chips span.info {
        color: var(--info-color);
        background: color-mix(in srgb, var(--info-color) 16%, transparent);
      }
      .state.good { color: var(--success-color); }
      .state.warn { color: var(--warning-color); }
      .state.bad { color: var(--error-color); }
      .empty { padding: 24px 16px; text-align: center; color: var(--secondary-text-color); }
      code { font-family: ui-monospace, Menlo, monospace; font-size: 0.85em; }
    `;
  }
}

customElements.define("sensible-card", SensibleCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "sensible-card",
  name: "Sensible Card",
  description: "Rich card for any Sensible sensor (state, detail, and fact chips).",
  preview: false,
});

// eslint-disable-next-line no-console
console.info(
  `%c SENSIBLE-CARD %c v${CARD_VERSION} `,
  "color:#fff;background:#0d9488;font-weight:700;",
  "color:#0d9488;background:#fff;"
);
