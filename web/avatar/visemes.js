"use strict";

export const MOUTH_EXPRESSION_NAMES = Object.freeze([
  "aa",
  "ih",
  "ou",
  "ee",
  "oh",
]);

const CLOSED_SHAPE = Object.freeze({ aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 });

const VISEME_SHAPES = Object.freeze({
  a: Object.freeze({ aa: 0.85, ih: 0.00, ou: 0.00, ee: 0.05, oh: 0.00 }),
  i: Object.freeze({ aa: 0.12, ih: 0.72, ou: 0.00, ee: 0.10, oh: 0.00 }),
  u: Object.freeze({ aa: 0.04, ih: 0.00, ou: 0.78, ee: 0.00, oh: 0.12 }),
  e: Object.freeze({ aa: 0.22, ih: 0.10, ou: 0.00, ee: 0.72, oh: 0.00 }),
  o: Object.freeze({ aa: 0.10, ih: 0.00, ou: 0.18, ee: 0.00, oh: 0.78 }),
  closed: CLOSED_SHAPE,
  small: Object.freeze({ aa: 0.18, ih: 0.00, ou: 0.00, ee: 0.00, oh: 0.00 }),
});

const DEFAULT_SETTINGS = Object.freeze({
  mouthFps: 28,
  startDelayMs: 1150,
  mouthScale: 0.95,
  randomAmount: 0.08,
  attackSpeed: 0.60,
  releaseSpeed: 0.42,
});

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, Number(value)));
}

function finiteOr(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

export function normalizeMouthSettings(value = {}) {
  const settings = value && typeof value === "object" ? value : {};
  return {
    mouthFps: Math.round(clamp(
      finiteOr(settings.mouth_fps ?? settings.mouthFps, DEFAULT_SETTINGS.mouthFps),
      1,
      240,
    )),
    startDelayMs: Math.round(clamp(
      finiteOr(
        settings.mouth_start_delay_seconds,
        DEFAULT_SETTINGS.startDelayMs / 1000,
      ) * 1000,
      0,
      10000,
    )),
    mouthScale: clamp(
      finiteOr(settings.mouth_scale ?? settings.mouthScale, DEFAULT_SETTINGS.mouthScale),
      0,
      2,
    ),
    randomAmount: clamp(
      finiteOr(
        settings.mouth_random_amount ?? settings.randomAmount,
        DEFAULT_SETTINGS.randomAmount,
      ),
      0,
      1,
    ),
    attackSpeed: clamp(
      finiteOr(
        settings.mouth_attack_speed ?? settings.attackSpeed,
        DEFAULT_SETTINGS.attackSpeed,
      ),
      0,
      1,
    ),
    releaseSpeed: clamp(
      finiteOr(
        settings.mouth_release_speed ?? settings.releaseSpeed,
        DEFAULT_SETTINGS.releaseSpeed,
      ),
      0,
      1,
    ),
  };
}

function randomBetween(minimum, maximum, random) {
  return minimum + (maximum - minimum) * random();
}

function scaleShape(shape, settings, random) {
  const result = {};
  for (const name of MOUTH_EXPRESSION_NAMES) {
    let value = finiteOr(shape[name], 0);
    if (value > 0) {
      value += randomBetween(-settings.randomAmount, settings.randomAmount, random);
    }
    result[name] = clamp(value * settings.mouthScale, 0, 1);
  }
  return result;
}

function event(shapeName, minimumMs, maximumMs, settings, random) {
  return {
    blends: scaleShape(VISEME_SHAPES[shapeName], settings, random),
    durationMs: Math.round(randomBetween(minimumMs, maximumMs, random)),
  };
}

export function buildVisemeEvents(text, options = {}) {
  const settings = normalizeMouthSettings(options.settings || options);
  const random = typeof options.random === "function" ? options.random : Math.random;
  const normalized = String(text || "")
    .toLowerCase()
    .replace(/[^a-z0-9 .,!?;:'-]/g, " ");

  const events = [];
  let lastWasVowel = false;

  for (const character of normalized) {
    if ("aeiou".includes(character)) {
      events.push(event(character, 55, 95, settings, random));
      lastWasVowel = true;
    } else if ("mnmbpfv".includes(character)) {
      events.push(event("closed", 30, 55, settings, random));
      lastWasVowel = false;
    } else if ("bcdfghjklpqrstvwxyz".includes(character)) {
      const chance = random();
      if (lastWasVowel && chance < 0.65) {
        events.push(event("closed", 20, 45, settings, random));
      } else if (chance < 0.35) {
        events.push(event("small", 25, 50, settings, random));
      }
      lastWasVowel = false;
    } else if (character === " ") {
      if (events.length && random() < 0.45) {
        events.push(event("closed", 35, 75, settings, random));
      }
      lastWasVowel = false;
    } else if (",;:".includes(character)) {
      events.push(event("closed", 75, 130, settings, random));
      lastWasVowel = false;
    } else if (".!?".includes(character)) {
      events.push(event("closed", 120, 220, settings, random));
      lastWasVowel = false;
    }
  }

  if (!events.length) {
    for (let index = 0; index < 20; index += 1) {
      const vowel = "aeiou"[Math.floor(random() * 5)];
      events.push(event(vowel, 55, 95, settings, random));
      events.push(event("closed", 25, 55, settings, random));
    }
  }

  return events;
}

export class TextVisemePlayer {
  constructor(renderer, settings = {}) {
    this.renderer = renderer;
    this.settings = normalizeMouthSettings(settings);
    this.events = [];
    this.eventIndex = 0;
    this.timer = null;
    this.generation = 0;
    this.configure(settings);
  }

  configure(settings = {}) {
    this.settings = normalizeMouthSettings(settings);
    if (this.renderer && typeof this.renderer.configureMouth === "function") {
      this.renderer.configureMouth(this.settings);
    }
  }

  start(text) {
    this.stop();
    if (!this.renderer) return false;

    const generation = ++this.generation;
    this.events = buildVisemeEvents(text, { settings: this.settings });
    this.eventIndex = 0;

    const begin = () => {
      if (generation !== this.generation) return;
      this._playNext(generation);
    };

    if (this.settings.startDelayMs > 0) {
      this.timer = window.setTimeout(begin, this.settings.startDelayMs);
    } else {
      begin();
    }
    return true;
  }

  _playNext(generation) {
    if (generation !== this.generation || !this.events.length) return;

    const current = this.events[this.eventIndex];
    this.eventIndex = (this.eventIndex + 1) % this.events.length;
    this.renderer.setMouthVisemes(current.blends);
    this.timer = window.setTimeout(
      () => this._playNext(generation),
      Math.max(10, current.durationMs),
    );
  }

  stop() {
    this.generation += 1;
    if (this.timer !== null) {
      window.clearTimeout(this.timer);
      this.timer = null;
    }
    this.events = [];
    this.eventIndex = 0;
    if (this.renderer && typeof this.renderer.closeMouth === "function") {
      this.renderer.closeMouth();
    }
  }
}
