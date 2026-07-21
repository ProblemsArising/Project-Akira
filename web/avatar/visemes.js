"use strict";

export const MOUTH_EXPRESSION_NAMES = Object.freeze([
  "aa",
  "ih",
  "ou",
  "ee",
  "oh",
]);

const CLOSED_SHAPE = Object.freeze({ aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 });

// Embedded avatars generally look more natural with modest, blended mouth
// shapes than with full-strength letter-by-letter poses.
const VISEME_SHAPES = Object.freeze({
  a: Object.freeze({ aa: 0.72, ih: 0.00, ou: 0.00, ee: 0.06, oh: 0.00 }),
  i: Object.freeze({ aa: 0.14, ih: 0.48, ou: 0.00, ee: 0.16, oh: 0.00 }),
  u: Object.freeze({ aa: 0.08, ih: 0.00, ou: 0.52, ee: 0.00, oh: 0.12 }),
  e: Object.freeze({ aa: 0.18, ih: 0.10, ou: 0.00, ee: 0.48, oh: 0.00 }),
  o: Object.freeze({ aa: 0.18, ih: 0.00, ou: 0.12, ee: 0.00, oh: 0.52 }),
  closed: CLOSED_SHAPE,
  small: Object.freeze({ aa: 0.11, ih: 0.04, ou: 0.00, ee: 0.00, oh: 0.00 }),
});

const DEFAULT_SETTINGS = Object.freeze({
  mouthFps: 28,
  startDelayMs: 90,
  mouthScale: 0.78,
  randomAmount: 0.025,
  attackSpeed: 0.46,
  releaseSpeed: 0.62,
  speechRate: 175,
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
  const legacyDelayMs = finiteOr(
    settings.mouth_start_delay_seconds,
    DEFAULT_SETTINGS.startDelayMs / 1000,
  ) * 1000;
  const configuredScale = finiteOr(
    settings.mouth_scale ?? settings.mouthScale,
    DEFAULT_SETTINGS.mouthScale,
  );

  return {
    mouthFps: Math.round(clamp(
      finiteOr(settings.mouth_fps ?? settings.mouthFps, DEFAULT_SETTINGS.mouthFps),
      1,
      240,
    )),
    // The original 1.15 second delay was tuned for OSC/VSeeFace latency. It
    // makes an in-process renderer visibly late, so cap it unless a dedicated
    // embedded delay is supplied in the future.
    startDelayMs: Math.round(clamp(
      finiteOr(
        settings.embedded_mouth_start_delay_ms,
        Math.min(legacyDelayMs, 180),
      ),
      0,
      1000,
    )),
    mouthScale: clamp(
      finiteOr(settings.embedded_mouth_scale, configuredScale * 0.82),
      0,
      1.25,
    ),
    randomAmount: clamp(
      finiteOr(
        settings.embedded_mouth_random_amount,
        Math.min(
          finiteOr(settings.mouth_random_amount ?? settings.randomAmount, DEFAULT_SETTINGS.randomAmount),
          0.04,
        ),
      ),
      0,
      0.15,
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
    speechRate: Math.round(clamp(
      finiteOr(settings.tts_rate ?? settings.speechRate, DEFAULT_SETTINGS.speechRate),
      60,
      400,
    )),
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

function shapeForVowelGroup(group) {
  const normalized = String(group || "").toLowerCase();
  if (normalized.includes("o")) return "o";
  if (normalized.includes("u")) return "u";
  if (normalized.includes("e")) return "e";
  if (normalized.includes("i") || normalized.includes("y")) return "i";
  return "a";
}

export function estimateSpeechDurationMs(text, speechRate = DEFAULT_SETTINGS.speechRate) {
  const normalized = String(text || "").trim();
  if (!normalized) return 0;

  const words = normalized.match(/[a-z0-9']+/gi) || [];
  const rate = clamp(finiteOr(speechRate, DEFAULT_SETTINGS.speechRate), 60, 400);
  const spokenMs = (Math.max(1, words.length) / rate) * 60000;
  const commaPauseMs = (normalized.match(/[,;:]/g) || []).length * 85;
  const sentencePauseMs = (normalized.match(/[.!?]/g) || []).length * 150;

  return Math.round(clamp(
    spokenMs + commaPauseMs + sentencePauseMs + 180,
    420,
    45000,
  ));
}

function buildWeightedUnits(text) {
  const tokens = String(text || "")
    .toLowerCase()
    .match(/[a-z0-9']+|[,.!?;:]/g) || [];
  const units = [];

  for (const token of tokens) {
    if (/^[,.!?;:]$/.test(token)) {
      units.push({
        shapeName: "closed",
        weight: ".!?".includes(token) ? 2.4 : 1.35,
      });
      continue;
    }

    const vowelGroups = token.match(/[aeiouy]+/g) || [];
    if (!vowelGroups.length) {
      units.push({ shapeName: "small", weight: Math.max(0.75, token.length * 0.22) });
    } else {
      for (const group of vowelGroups) {
        units.push({
          shapeName: shapeForVowelGroup(group),
          weight: Math.max(0.85, group.length * 0.72),
        });
        if (vowelGroups.length > 1) {
          units.push({ shapeName: "small", weight: 0.38 });
        }
      }
    }

    // A short closure between words prevents the constant puppet-like flap
    // produced by the old per-character loop.
    units.push({ shapeName: "closed", weight: 0.58 });
  }

  return units;
}

export function buildVisemeEvents(text, options = {}) {
  const settings = normalizeMouthSettings(options.settings || options);
  const random = typeof options.random === "function" ? options.random : Math.random;
  const units = buildWeightedUnits(text);

  if (!units.length) return [];

  const totalDurationMs = estimateSpeechDurationMs(text, settings.speechRate);
  const totalWeight = units.reduce((sum, unit) => sum + unit.weight, 0) || 1;

  return units.map((unit) => ({
    blends: scaleShape(VISEME_SHAPES[unit.shapeName], settings, random),
    durationMs: Math.round(clamp(
      (totalDurationMs * unit.weight) / totalWeight,
      42,
      360,
    )),
  }));
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

    if (!this.events.length) return false;

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
    if (generation !== this.generation) return;

    if (this.eventIndex >= this.events.length) {
      this.timer = null;
      this.renderer.closeMouth();
      return;
    }

    const current = this.events[this.eventIndex];
    this.eventIndex += 1;
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
