/**
 * External settings API for kiosk apps.
 *
 * Kiosk Satellite (and any other host with script access to the page)
 * changes the browser-local panel settings through this hook instead of
 * poking localStorage: a bare localStorage write is invisible to the
 * running session and gets overwritten by the server profile on the next
 * page load. `apply()` does what the sidebar panel's _onSettingsChange
 * does — merge, persist locally, push the server profile, and hand the
 * full config to the session with `fromPanel` so skins, theme, text
 * scale and the reactive bar re-apply live.
 *
 * Installed unconditionally from the engine bootstrap so it exists even
 * before a session starts (auto_start off, no satellite picked yet).
 */

import { DEFAULT_CONFIG } from '../constants.js';
import { getSkinOptions } from '../skins/index.js';
import { getStoredEntity } from './entity-picker.js';
import { savePanelConfig } from './server-settings.js';

const CONFIG_KEY = 'vs-panel-config';

/** The keys an external host may read and write through this hook. */
const EXTERNAL_KEYS = [
  'auto_start',
  'disable_muted_microphone_warning',
  'debug',
  'skin',
  'theme_mode',
  'reactive_bar',
  'reactive_bar_update_interval_ms',
  'text_scale',
];

function getStoredConfig() {
  try {
    const raw = localStorage.getItem(CONFIG_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch (_) {
    return {};
  }
}

function setStoredConfig(config) {
  try {
    localStorage.setItem(CONFIG_KEY, JSON.stringify(config));
  } catch (_) { /* private browsing */ }
}

function pickExternal(config) {
  const out = {};
  for (const key of EXTERNAL_KEYS) {
    out[key] = config[key] !== undefined ? config[key] : DEFAULT_CONFIG[key];
  }
  return out;
}

function currentHass() {
  return document.querySelector('home-assistant')?.hass || null;
}

export function installExternalSettings() {
  if (window.__vsExternalSettings) return;

  window.__vsExternalSettings = {
    /**
     * Current externally-settable config, the skin catalog for pickers,
     * and the satellite binding. Plain JSON-safe object.
     */
    get() {
      const config = Object.assign({}, DEFAULT_CONFIG, getStoredConfig());
      const session = window.__vsSession;
      const satellite = getStoredEntity() || config.satellite_entity || null;
      return {
        config: pickExternal(config),
        skins: getSkinOptions(),
        satellite,
        engine: {
          running: !!(session && session.isStarted),
          canStart: !!(session && satellite),
        },
      };
    },

    /**
     * Start the engine, exactly like the panel's Start button: ensure an
     * engine card exists for UI rendering, clear the stop flags, start on
     * the next frame. No-op without a session or satellite binding.
     */
    startEngine() {
      const session = window.__vsSession;
      const stored = Object.assign({}, DEFAULT_CONFIG, getStoredConfig());
      const entityId = getStoredEntity() || stored.satellite_entity || null;
      if (!session || session.isStarted || !entityId) {
        return { ok: false, running: !!(session && session.isStarted) };
      }
      if (session._cards.size === 0) {
        const card = document.createElement('voice-satellite-card');
        card._engineOwned = true;
        card.setConfig(Object.assign({}, stored, { satellite_entity: entityId }));
        card.style.display = 'none';
        document.body.appendChild(card);
        const hass = currentHass();
        if (hass) card.hass = hass;
      }
      session._userStopped = false;
      session._startAttempted = false;
      requestAnimationFrame(() => {
        if (!session.isStarted) session.start();
      });
      return { ok: true, running: true };
    },

    /** Stop the engine, exactly like the panel's Stop button. */
    stopEngine() {
      const session = window.__vsSession;
      if (!session || !session.isStarted) return { ok: false, running: false };
      session._userStopped = true;
      session.teardown();
      return { ok: true, running: false };
    },

    /**
     * Merge a partial config (external keys only), persist it locally,
     * push the server profile, and live-apply through the session.
     * Synchronous on the local half; the server save runs in the
     * background. Returns the resulting external config.
     */
    apply(partial) {
      const filtered = {};
      for (const key of EXTERNAL_KEYS) {
        if (partial && partial[key] !== undefined) filtered[key] = partial[key];
      }
      const stored = Object.assign(getStoredConfig(), filtered);
      setStoredConfig(stored);

      const entityId = getStoredEntity() || stored.satellite_entity || null;
      const hass = currentHass();
      if (entityId && hass) {
        savePanelConfig(hass, entityId, stored).catch(() => { /* logged */ });
      }

      const session = window.__vsSession;
      if (session) {
        const full = Object.assign({}, DEFAULT_CONFIG, stored);
        if (entityId) full.satellite_entity = entityId;
        try {
          session.updateConfig(full, { fromPanel: true });
        } catch (_) { /* session mid-teardown */ }
      }

      return { ok: true, config: pickExternal(stored) };
    },
  };
}
