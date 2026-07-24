const clamp01 = (value) => Math.max(0, Math.min(1, Number(value) || 0));

export class AudioVisemePlayer {
  constructor(renderer) {
    this.renderer = renderer;
    this.frameHandle = null;
    this.generation = 0;
  }

  start(profile = {}) {
    this.stop();
    if (!this.renderer) return false;

    const fps = Math.max(1, Math.min(120, Number(profile.fps) || 30));
    const values = Array.isArray(profile.values)
      ? profile.values.map(clamp01)
      : [];
    if (!values.length) return false;

    const generation = ++this.generation;
    const startedAt = performance.now();
    const frameDuration = 1000 / fps;

    const tick = (now) => {
      if (generation !== this.generation) return;
      const index = Math.floor((now - startedAt) / frameDuration);
      if (index >= values.length) {
        this.frameHandle = null;
        this.renderer.closeMouth();
        return;
      }

      const open = values[Math.max(0, index)];
      this.renderer.setMouthVisemes({
        aa: open,
        ih: open * 0.05,
        ou: open * 0.08,
        ee: open * 0.03,
        oh: open * 0.08,
      });
      this.frameHandle = window.requestAnimationFrame(tick);
    };

    this.frameHandle = window.requestAnimationFrame(tick);
    return true;
  }

  stop() {
    this.generation += 1;
    if (this.frameHandle !== null) {
      window.cancelAnimationFrame(this.frameHandle);
      this.frameHandle = null;
    }
    if (this.renderer && typeof this.renderer.closeMouth === "function") {
      this.renderer.closeMouth();
    }
  }
}
