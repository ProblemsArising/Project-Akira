"use strict";

export const FACE_EXPRESSION_NAMES = Object.freeze([
  "happy",
  "relaxed",
  "sad",
  "angry",
  "surprised",
]);

const ZERO_FACE = Object.freeze({
  happy: 0,
  relaxed: 0,
  sad: 0,
  angry: 0,
  surprised: 0,
});

// These values intentionally exceed the old subtle 0.2-0.4 range. Some VRM
// creators mark face clips as binary, and values below 0.5 appear completely
// neutral on those avatars.
export const FACE_EXPRESSION_PRESETS = Object.freeze({
  neutral: ZERO_FACE,
  soft: Object.freeze({ happy: 0.18, relaxed: 0.12, sad: 0, angry: 0, surprised: 0 }),
  happy: Object.freeze({ happy: 0.86, relaxed: 0.14, sad: 0, angry: 0, surprised: 0 }),
  playful: Object.freeze({ happy: 0.46, relaxed: 0.78, sad: 0, angry: 0, surprised: 0 }),
  surprised: Object.freeze({ happy: 0.05, relaxed: 0, sad: 0, angry: 0, surprised: 0.92 }),
  concerned: Object.freeze({ happy: 0, relaxed: 0, sad: 0.82, angry: 0, surprised: 0.14 }),
  annoyed: Object.freeze({ happy: 0, relaxed: 0.06, sad: 0, angry: 0.76, surprised: 0 }),
  shy: Object.freeze({ happy: 0.42, relaxed: 0.62, sad: 0.10, angry: 0, surprised: 0.04 }),
});

const DEFAULT_SETTINGS = Object.freeze({
  enabled: true,
  idleEnabled: true,
  faceFps: 120,
  idleStrength: 0.30,
  speakingStrength: 0.72,
  fadeSpeed: 0.12,
});

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function finiteOr(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

export function normalizeExpressionSettings(value = {}) {
  const settings = value && typeof value === "object" ? value : {};
  return {
    enabled: settings.expressions_enabled ?? settings.enabled ?? DEFAULT_SETTINGS.enabled,
    idleEnabled: settings.idle_face_enabled ?? settings.idleEnabled ?? DEFAULT_SETTINGS.idleEnabled,
    faceFps: Math.round(clamp(
      finiteOr(settings.face_blend_fps ?? settings.faceFps, DEFAULT_SETTINGS.faceFps),
      1,
      240,
    )),
    idleStrength: clamp(
      finiteOr(
        settings.idle_expression_strength ?? settings.idleStrength,
        DEFAULT_SETTINGS.idleStrength,
      ),
      0,
      2,
    ),
    speakingStrength: clamp(
      finiteOr(
        settings.speaking_expression_strength ?? settings.speakingStrength,
        DEFAULT_SETTINGS.speakingStrength,
      ),
      0,
      2,
    ),
    fadeSpeed: clamp(
      finiteOr(
        settings.expression_fade_speed ?? settings.fadeSpeed,
        DEFAULT_SETTINGS.fadeSpeed,
      ),
      0,
      1,
    ),
  };
}

function scaleFace(shape, strength) {
  const result = {};
  for (const name of FACE_EXPRESSION_NAMES) {
    result[name] = clamp(finiteOr(shape[name], 0) * strength, 0, 1);
  }
  return result;
}

export function buildIdleExpression(settings = {}) {
  const normalized = normalizeExpressionSettings(settings);
  if (!normalized.enabled || !normalized.idleEnabled) return { ...ZERO_FACE };
  return scaleFace(FACE_EXPRESSION_PRESETS.soft, normalized.idleStrength);
}

export function buildSpeakingExpression(expression = {}, settings = {}) {
  const normalized = normalizeExpressionSettings(settings);
  if (!normalized.enabled) return { ...ZERO_FACE };

  const presetName = typeof expression === "string"
    ? expression
    : String(expression.preset || "soft");
  const score = Math.max(0, Math.floor(finiteOr(expression.score, 0)));
  const shape = FACE_EXPRESSION_PRESETS[presetName] || FACE_EXPRESSION_PRESETS.soft;

  // Smoothly lift ordinary 0-1 settings into a more visible range while still
  // respecting 0 as fully disabled and retaining stronger user values.
  let strength = 1 - Math.pow(1 - clamp(normalized.speakingStrength, 0, 1), 1.7);
  if (normalized.speakingStrength > 1) {
    strength = Math.min(1, strength * normalized.speakingStrength);
  }
  if (score >= 4) strength = Math.min(1, strength * 1.10);
  else if (score === 1) strength *= 0.92;

  return scaleFace(shape, strength);
}

export function expressionHoldMs(text) {
  return Math.round(clamp(String(text || "").length * 35, 2000, 6000));
}

export class TextExpressionPlayer {
  constructor(renderer, settings = {}) {
    this.renderer = renderer;
    this.settings = normalizeExpressionSettings(settings);
    this.timer = null;
    this.spokenExpression = false;
    this.configure(settings);
  }

  configure(settings = {}) {
    this.settings = normalizeExpressionSettings(settings);
    if (this.renderer && typeof this.renderer.configureFace === "function") {
      this.renderer.configureFace({
        faceFps: this.settings.faceFps,
        fadeSpeed: this.settings.fadeSpeed,
        idle: buildIdleExpression(this.settings),
      });
    }
  }

  start(expression, text, willSpeak = false) {
    this._clearTimer();
    this.spokenExpression = Boolean(willSpeak);
    if (!this.renderer || !this.settings.enabled) {
      this.cancel();
      return false;
    }

    this.renderer.setFaceExpression(
      buildSpeakingExpression(expression, this.settings),
    );

    if (!this.spokenExpression) {
      this.timer = window.setTimeout(
        () => this._returnToIdle(),
        expressionHoldMs(text),
      );
    }
    return true;
  }

  complete() {
    if (this.spokenExpression) this._returnToIdle();
    this.spokenExpression = false;
  }

  cancel(immediate = false) {
    this._clearTimer();
    this.spokenExpression = false;
    if (this.renderer && typeof this.renderer.clearFaceExpression === "function") {
      this.renderer.clearFaceExpression(immediate);
    }
  }

  _returnToIdle() {
    this._clearTimer();
    this.spokenExpression = false;
    if (this.renderer && typeof this.renderer.clearFaceExpression === "function") {
      this.renderer.clearFaceExpression();
    }
  }

  _clearTimer() {
    if (this.timer !== null) {
      window.clearTimeout(this.timer);
      this.timer = null;
    }
  }
}
