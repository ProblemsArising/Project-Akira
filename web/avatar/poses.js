const DEG_TO_RAD = Math.PI / 180;

export const BODY_POSE_BONES = Object.freeze([
  "hips",
  "spine",
  "chest",
  "upperChest",
  "neck",
  "head",
  "leftShoulder",
  "rightShoulder",
  "leftUpperArm",
  "rightUpperArm",
  "leftLowerArm",
  "rightLowerArm",
  "leftHand",
  "rightHand",
]);

function finiteNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function rotation(x = 0, y = 0, z = 0) {
  return { x, y, z };
}

function degrees(x = 0, y = 0, z = 0) {
  return rotation(x * DEG_TO_RAD, y * DEG_TO_RAD, z * DEG_TO_RAD);
}

function neutralBones() {
  return Object.fromEntries(BODY_POSE_BONES.map((name) => [name, rotation()]));
}

export function createNeutralBodyPose() {
  return {
    rootPosition: { x: 0, y: 0, z: 0 },
    bones: neutralBones(),
  };
}

function definePose({ rootPosition = {}, bones = {} } = {}) {
  const pose = createNeutralBodyPose();
  for (const axis of ["x", "y", "z"]) {
    pose.rootPosition[axis] = finiteNumber(rootPosition[axis], 0);
  }
  for (const [name, value] of Object.entries(bones)) {
    if (!Object.prototype.hasOwnProperty.call(pose.bones, name)) continue;
    pose.bones[name] = degrees(value.x, value.y, value.z);
  }
  return pose;
}

// Normalized VRM humanoids expose a model-independent T-pose. The soft pose
// lowers the arms into a conservative standing posture before emotional offsets
// are layered on top. Values stay intentionally modest across different models.
export const STANDING_BODY_POSE = Object.freeze(definePose({
  bones: {
    leftShoulder: { z: 2 },
    rightShoulder: { z: -2 },
    leftUpperArm: { z: 68 },
    rightUpperArm: { z: -68 },
    leftLowerArm: { z: 7 },
    rightLowerArm: { z: -7 },
  },
}));

export const BODY_POSE_PRESETS = Object.freeze({
  soft: Object.freeze(definePose()),
  happy: Object.freeze(definePose({
    rootPosition: { y: 0.008, z: -0.004 },
    bones: {
      chest: { x: -1.5 },
      upperChest: { x: -2.0 },
      head: { x: -1.5, z: 1.0 },
      leftUpperArm: { x: -3, z: -28 },
      rightUpperArm: { x: -3, z: 28 },
      leftLowerArm: { z: 15 },
      rightLowerArm: { z: -15 },
    },
  })),
  playful: Object.freeze(definePose({
    rootPosition: { x: 0.004, z: -0.002 },
    bones: {
      spine: { z: 1.5 },
      chest: { z: 2.4 },
      upperChest: { z: 2.8 },
      head: { x: -0.5, y: 5.0, z: 4.0 },
      rightUpperArm: { x: -7, z: 18 },
      rightLowerArm: { x: -4, z: -28 },
      rightHand: { z: -5 },
    },
  })),
  surprised: Object.freeze(definePose({
    rootPosition: { y: 0.014, z: 0.004 },
    bones: {
      spine: { x: -1.0 },
      chest: { x: -2.5 },
      upperChest: { x: -3.0 },
      neck: { x: -1.0 },
      head: { x: -3.2 },
      leftUpperArm: { x: -5, z: -48 },
      rightUpperArm: { x: -5, z: 48 },
      leftLowerArm: { z: 38 },
      rightLowerArm: { z: -38 },
    },
  })),
  concerned: Object.freeze(definePose({
    rootPosition: { y: -0.006, z: 0.003 },
    bones: {
      spine: { x: 1.5 },
      chest: { x: 2.5 },
      upperChest: { x: 3.0 },
      neck: { x: 2.0 },
      head: { x: 5.5, y: -1.0, z: -1.5 },
      leftUpperArm: { x: 3, z: 6 },
      rightUpperArm: { x: 3, z: -6 },
      leftLowerArm: { z: 8 },
      rightLowerArm: { z: -8 },
    },
  })),
  annoyed: Object.freeze(definePose({
    rootPosition: { x: -0.004 },
    bones: {
      spine: { z: -1.5 },
      chest: { z: -2.6 },
      upperChest: { z: -3.0 },
      head: { x: 1.0, y: -6.0, z: -4.0 },
      rightUpperArm: { x: -7, z: 30 },
      rightLowerArm: { x: -4, z: -31 },
      rightHand: { z: -6 },
    },
  })),
  shy: Object.freeze(definePose({
    rootPosition: { z: 0.003 },
    bones: {
      spine: { x: 0.8 },
      chest: { x: 1.8 },
      upperChest: { x: 2.2 },
      head: { x: 5.0, y: 1.0, z: 3.0 },
      leftUpperArm: { x: 4, z: -7 },
      rightUpperArm: { x: 4, z: 7 },
      leftLowerArm: { x: -3, z: 20 },
      rightLowerArm: { x: -3, z: -20 },
    },
  })),
});

export function normalizeBodyPoseSettings(settings = {}) {
  return {
    standingEnabled: settings.standing_pose_replay_enabled !== false,
    expressionsEnabled: settings.body_expressions_enabled !== false,
    poseFps: clamp(finiteNumber(settings.pose_fps, 18), 1, 240),
    expressionStrength: clamp(
      finiteNumber(settings.body_expression_strength, 1),
      0,
      2,
    ),
    poseStrength: clamp(finiteNumber(settings.body_pose_strength, 1), 0, 2),
    fadeSpeed: clamp(
      finiteNumber(settings.body_expression_fade_speed, 0.055),
      0,
      1,
    ),
    armStrength: clamp(
      finiteNumber(settings.arm_gesture_strength, 1)
        * finiteNumber(settings.arm_bone_rotation_strength, 1),
      0,
      3,
    ),
    reduceIdleDuringExpressions:
      settings.disable_idle_during_expressions !== false,
    idleStrengthDuringExpressions: clamp(
      finiteNumber(settings.idle_strength_during_expressions, 0.2),
      0,
      1,
    ),
  };
}

function clonePose(pose) {
  const result = createNeutralBodyPose();
  for (const axis of ["x", "y", "z"]) {
    result.rootPosition[axis] = pose.rootPosition[axis];
  }
  for (const name of BODY_POSE_BONES) {
    for (const axis of ["x", "y", "z"]) {
      result.bones[name][axis] = pose.bones[name][axis];
    }
  }
  return result;
}

function isArmBone(name) {
  return name.includes("Shoulder")
    || name.includes("UpperArm")
    || name.includes("LowerArm")
    || name.includes("Hand");
}

export function buildBodyPose(expression = {}, settings = {}) {
  const config = normalizeBodyPoseSettings(settings);
  const result = createNeutralBodyPose();
  const standingScale = config.standingEnabled ? config.poseStrength : 0;

  for (const axis of ["x", "y", "z"]) {
    result.rootPosition[axis] = STANDING_BODY_POSE.rootPosition[axis] * standingScale;
  }
  for (const name of BODY_POSE_BONES) {
    for (const axis of ["x", "y", "z"]) {
      result.bones[name][axis] = STANDING_BODY_POSE.bones[name][axis]
        * standingScale
        * (isArmBone(name) ? config.armStrength : 1);
    }
  }

  if (!config.expressionsEnabled) return result;

  const presetName = typeof expression === "string"
    ? expression
    : String(expression.preset || "soft");
  const score = Math.max(0, Math.floor(finiteNumber(expression.score, 0)));
  const shape = BODY_POSE_PRESETS[presetName] || BODY_POSE_PRESETS.soft;
  let expressionScale = config.expressionStrength * config.poseStrength;
  if (score >= 4) expressionScale *= 1.10;
  else if (score === 1) expressionScale *= 0.92;

  for (const axis of ["x", "y", "z"]) {
    result.rootPosition[axis] += shape.rootPosition[axis] * expressionScale;
  }
  for (const name of BODY_POSE_BONES) {
    const armScale = isArmBone(name) ? config.armStrength : 1;
    for (const axis of ["x", "y", "z"]) {
      result.bones[name][axis] += shape.bones[name][axis]
        * expressionScale
        * armScale;
    }
  }
  return result;
}

function interpolatePose(current, target, alpha) {
  const result = createNeutralBodyPose();
  for (const axis of ["x", "y", "z"]) {
    result.rootPosition[axis] = current.rootPosition[axis]
      + (target.rootPosition[axis] - current.rootPosition[axis]) * alpha;
  }
  for (const name of BODY_POSE_BONES) {
    for (const axis of ["x", "y", "z"]) {
      result.bones[name][axis] = current.bones[name][axis]
        + (target.bones[name][axis] - current.bones[name][axis]) * alpha;
    }
  }
  return result;
}

export function bodyPoseHoldMs(text) {
  return Math.round(clamp(String(text || "").length * 34, 2200, 6500));
}

export class EmbeddedBodyPose {
  constructor(settings = {}) {
    this.settings = normalizeBodyPoseSettings(settings);
    this.active = false;
    this.preset = "soft";
    this.current = buildBodyPose("soft", this.settings);
    this.target = clonePose(this.current);
  }

  configure(settings = {}) {
    this.settings = normalizeBodyPoseSettings(settings);
    this.target = buildBodyPose(
      this.active ? { preset: this.preset, score: 2 } : "soft",
      this.settings,
    );
  }

  setExpression(expression = {}) {
    this.preset = typeof expression === "string"
      ? expression
      : String(expression.preset || "soft");
    this.active = this.preset !== "soft" && this.preset !== "neutral";
    this.target = buildBodyPose(expression, this.settings);
  }

  clear(immediate = false) {
    this.active = false;
    this.preset = "soft";
    this.target = buildBodyPose("soft", this.settings);
    if (immediate) this.current = clonePose(this.target);
  }

  reset() {
    this.clear(true);
  }

  idleScale() {
    if (!this.active || !this.settings.reduceIdleDuringExpressions) return 1;
    return this.settings.idleStrengthDuringExpressions;
  }

  update(deltaSeconds) {
    const delta = clamp(finiteNumber(deltaSeconds, 0), 0, 0.1);
    const frameScale = Math.max(0.05, delta * this.settings.poseFps);
    const alpha = 1 - Math.pow(1 - this.settings.fadeSpeed, frameScale);
    this.current = interpolatePose(this.current, this.target, alpha);
    return this.current;
  }
}

export class TextBodyPosePlayer {
  constructor(renderer, settings = {}) {
    this.renderer = renderer;
    this.timer = null;
    this.spokenPose = false;
    this.configure(settings);
  }

  configure(settings = {}) {
    if (this.renderer && typeof this.renderer.configureBodyPose === "function") {
      this.renderer.configureBodyPose(settings);
    }
  }

  start(expression, text, willSpeak = false) {
    this._clearTimer();
    this.spokenPose = Boolean(willSpeak);
    if (!this.renderer || typeof this.renderer.setBodyPose !== "function") {
      return false;
    }
    this.renderer.setBodyPose(expression || { preset: "soft", score: 0 });
    if (!this.spokenPose) {
      this.timer = window.setTimeout(
        () => this._returnToStanding(),
        bodyPoseHoldMs(text),
      );
    }
    return true;
  }

  complete() {
    if (this.spokenPose) this._returnToStanding();
    this.spokenPose = false;
  }

  cancel(immediate = false) {
    this._clearTimer();
    this.spokenPose = false;
    if (this.renderer && typeof this.renderer.clearBodyPose === "function") {
      this.renderer.clearBodyPose(immediate);
    }
  }

  _returnToStanding() {
    this._clearTimer();
    this.spokenPose = false;
    if (this.renderer && typeof this.renderer.clearBodyPose === "function") {
      this.renderer.clearBodyPose();
    }
  }

  _clearTimer() {
    if (this.timer !== null) {
      window.clearTimeout(this.timer);
      this.timer = null;
    }
  }
}
