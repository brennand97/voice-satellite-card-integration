/** Hands timer presentation to Kiosk Satellite when its bridge accepts it. */
export class NativeTimerPills {
  constructor(manager) {
    this.manager = manager;
    this.active = false;
    this.alertTimers = [];
    this._tail = Promise.resolve();
    this._lastSnapshot = '';
    this._destroyed = false;
    this._pendingActions = new Set();
    this._onAction = (event) => this.control(event.detail);
    if (this.api) window.addEventListener('kiosksatellite:timer-action', this._onAction);
  }

  get api() {
    const api = typeof window !== 'undefined' && window.kioskSatellite;
    return api?.platform === 'kiosksatellite'
      && typeof api.setVoiceTimers === 'function'
      && typeof api.setVoiceTimerAlert === 'function' ? api : null;
  }

  get entityId() { return this.manager.card.config?.satellite_entity || ''; }

  serialize(timers) {
    return timers.map((t) => ({
      id: t.id,
      name: t.name || '',
      totalSeconds: t.totalSeconds || 0,
      startedAt: t.startedAt || 0,
      isActive: t.isActive !== false,
    }));
  }

  enqueue(callback) {
    const result = this._tail.then(callback).catch(() => false);
    this._tail = result;
    return result;
  }

  sync(timers) {
    if (!this.api || this._destroyed) return false;
    const snapshot = { entityId: this.entityId, timers: this.serialize(timers) };
    const key = JSON.stringify(snapshot);
    if (key === this._lastSnapshot) return this.active;
    this._lastSnapshot = key;
    this.enqueue(() => this.api.setVoiceTimers(snapshot)).then((accepted) => {
      if (this._destroyed || key !== this._lastSnapshot) return;
      this.active = accepted === true;
      if (this.active) {
        this.manager.card.ui.removeTimerContainer();
      } else {
        this._lastSnapshot = '';
        if (!this.manager.card.config?.hide_timer_pills) {
          this.manager.card.ui.syncTimerPills(this.manager.timers,
            (id) => () => this.manager.cancelTimer(id));
        }
      }
    });
    return this.active;
  }

  async showAlert(timers, muted) {
    if (!this.api || this._destroyed) return false;
    const snapshot = { entityId: this.entityId, timers: this.serialize(timers), muted };
    this.alertTimers = timers;
    return await this.enqueue(() => this.api.setVoiceTimerAlert(snapshot)) === true;
  }

  clearAlert() {
    this.alertTimers = [];
    if (!this.api) return;
    const snapshot = { entityId: this.entityId, timers: [] };
    this.enqueue(() => this.api.setVoiceTimerAlert(snapshot));
  }

  async control(detail) {
    if (this._destroyed || !detail || detail.entityId !== this.entityId) return;
    if (detail.action === 'dismiss') {
      if (this.alertTimers.some((t) => t.id === detail.id)) this.manager.clearAlert();
      return;
    }
    if (!['pause', 'resume', 'cancel'].includes(detail.action)
      || !this.manager.timers.some((t) => t.id === detail.id)
      || this._pendingActions.has(detail.id)) return;
    this._pendingActions.add(detail.id);
    try {
      const connection = this.manager.card.connection;
      if (!connection) throw new Error('Home Assistant is disconnected');
      await connection.sendMessagePromise({
        type: detail.action === 'cancel' ? 'voice_satellite/cancel_timer' : 'voice_satellite/pause_timer',
        entity_id: this.entityId,
        timer_id: detail.id,
        ...(detail.action === 'cancel' ? {} : { paused: detail.action === 'pause' }),
      });
    } catch (error) {
      this.manager.log.error('timer', `Timer action failed: ${error.message || error}`);
      this.api?.voiceTimerActionFailed?.(this.entityId);
    } finally {
      this._pendingActions.delete(detail.id);
    }
  }

  destroy() {
    if (this.api) {
      window.removeEventListener('kiosksatellite:timer-action', this._onAction);
      this.sync([]);
      this.clearAlert();
    }
    this._destroyed = true;
    this.active = false;
  }
}
