const DEG_TO_RAD = Math.PI / 180;

const BONE_NAMES = Object.freeze([
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

function neutralBones() {
  return Object.fromEntries(BONE_NAMES.map((name) => [name, rotation()]));
}

export function createNeutralIdleSample() {
  return {
    rootPosition: { x: 0, y: 0, z: 0 },
    bones: neutralBones(),
  };
}

export function normalizeIdleSettings(settings = {}) {
  return {
    enabled: settings.enabled !== false && settings.auto_start_idle !== false,
    poseFps: clamp(finiteNumber(settings.poseFps ?? settings.pose_fps, 18), 1, 240),
    strength: clamp(
      finiteNumber(settings.strength ?? settings.body_idle_strength, 1),
      0,
      3,
    ),
    rootBobMeters: clamp(
      finiteNumber(settings.rootBobMeters ?? settings.body_root_bob_meters, 0.012),
      0,
      0.08,
    ),
    swayMeters: clamp(
      finiteNumber(settings.swayMeters ?? settings.body_sway_meters, 0.010),
      0,
      0.08,
    ),
    breathDegrees: clamp(
      finiteNumber(settings.breathDegrees ?? settings.body_breath_degrees, 2.2),
      0,
      12,
    ),
    headYawDegrees: clamp(
      finiteNumber(settings.headYawDegrees ?? settings.body_head_yaw_degrees, 2.4),
      0,
      15,
    ),
    armSwayDegrees: clamp(
      finiteNumber(settings.armSwayDegrees ?? settings.body_arm_sway_degrees, 1.8),
      0,
      12,
    ),
    speakingMotionBoost: clamp(
      finiteNumber(
        settings.speakingMotionBoost ?? settings.body_speaking_motion_boost,
        0.28,
      ),
      0,
      2,
    ),
    talkPulseDegrees: clamp(
      finiteNumber(settings.talkPulseDegrees ?? settings.body_talk_pulse_degrees, 1.35),
      0,
      8,
    ),
  };
}

export function sampleIdleMotion(timeSeconds, settings, speaking = false) {
  const config = normalizeIdleSettings(settings);
  if (!config.enabled || config.strength <= 0) return createNeutralIdleSample();

  const time = Math.max(0, finiteNumber(timeSeconds, 0));
  const motionBoost = speaking ? 1 + config.speakingMotionBoost : 1;
  const strength = config.strength * motionBoost;

  // Multiple slow frequencies keep the motion from reading as a single looping
  // sine wave while remaining deterministic and calm.
  const breath = (
    Math.sin(time * 1.08) * 0.72
    + Math.sin(time * 0.49 + 1.7) * 0.28
  ) * strength;
  const sway = (
    Math.sin(time * 0.47 + 0.8) * 0.64
    + Math.sin(time * 0.21 + 2.4) * 0.36
  ) * strength;
  const drift = (
    Math.sin(time * 0.31 + 2.3) * 0.58
    + Math.sin(time * 0.13 + 0.2) * 0.42
  ) * strength;
  const arm = (
    Math.sin(time * 0.71 + 0.4) * 0.67
    + Math.sin(time * 0.29 + 2.0) * 0.33
  ) * strength;
  const nod = (
    Math.sin(time * 0.23 + 0.7) * 0.65
    + Math.sin(time * 0.11 + 2.8) * 0.35
  ) * strength;
  const talk = speaking
    ? Math.sin(time * 4.6) * (0.70 + Math.sin(time * 0.63 + 0.5) * 0.30)
    : 0;

  const breathDeg = config.breathDegrees * DEG_TO_RAD;
  const headYaw = config.headYawDegrees * DEG_TO_RAD;
  const armSway = config.armSwayDegrees * DEG_TO_RAD;
  const talkPulse = config.talkPulseDegrees * DEG_TO_RAD;

  return {
    rootPosition: {
      x: config.swayMeters * 0.32 * sway,
      y: config.rootBobMeters * breath,
      z: config.swayMeters * 0.18 * drift,
    },
    bones: {
      hips: rotation(0, headYaw * 0.08 * sway, headYaw * 0.05 * drift),
      spine: rotation(
        breathDeg * 0.38 * breath,
        headYaw * 0.08 * sway,
        headYaw * 0.10 * drift,
      ),
      chest: rotation(
        breathDeg * 0.68 * breath + talkPulse * 0.18 * talk,
        headYaw * 0.12 * sway,
        headYaw * 0.16 * drift,
      ),
      upperChest: rotation(
        breathDeg * breath + talkPulse * 0.30 * talk,
        headYaw * 0.18 * sway,
        headYaw * 0.22 * drift,
      ),
      neck: rotation(
        breathDeg * 0.22 * breath + headYaw * 0.10 * nod,
        headYaw * 0.48 * sway,
        headYaw * 0.16 * drift,
      ),
      head: rotation(
        breathDeg * 0.30 * breath + headYaw * 0.16 * nod + talkPulse * 0.10 * talk,
        headYaw * (0.70 * sway + 0.30 * drift),
        headYaw * 0.24 * drift,
      ),
      leftShoulder: rotation(armSway * 0.10 * arm, 0, armSway * 0.16 * drift),
      rightShoulder: rotation(armSway * 0.10 * arm, 0, -armSway * 0.16 * drift),
      leftUpperArm: rotation(
        armSway * 0.25 * arm + talkPulse * 0.18 * talk,
        armSway * 0.08 * sway,
        armSway * 0.55 * drift,
      ),
      rightUpperArm: rotation(
        armSway * 0.25 * arm + talkPulse * 0.18 * talk,
        -armSway * 0.08 * sway,
        -armSway * 0.55 * drift,
      ),
      leftLowerArm: rotation(
        armSway * 0.18 * arm,
        0,
        armSway * 0.30 * sway,
      ),
      rightLowerArm: rotation(
        armSway * 0.18 * arm,
        0,
        -armSway * 0.30 * sway,
      ),
    },
  };
}

function lerpNumber(current, target, alpha) {
  return current + (target - current) * alpha;
}

function interpolateSample(current, target, alpha) {
  const result = createNeutralIdleSample();
  for (const axis of ["x", "y", "z"]) {
    result.rootPosition[axis] = lerpNumber(
      current.rootPosition[axis],
      target.rootPosition[axis],
      alpha,
    );
  }
  for (const name of BONE_NAMES) {
    for (const axis of ["x", "y", "z"]) {
      result.bones[name][axis] = lerpNumber(
        current.bones[name][axis],
        target.bones[name][axis],
        alpha,
      );
    }
  }
  return result;
}

export class EmbeddedIdleMotion {
  constructor(settings = {}) {
    this.settings = normalizeIdleSettings(settings);
    this.speaking = false;
    this.elapsed = 0;
    this.sampleAccumulator = 1 / this.settings.poseFps;
    this.current = createNeutralIdleSample();
    this.target = createNeutralIdleSample();
  }

  configure(settings = {}) {
    this.settings = normalizeIdleSettings(settings);
    if (!this.settings.enabled || this.settings.strength <= 0) {
      this.target = createNeutralIdleSample();
    }
  }

  setSpeaking(speaking) {
    this.speaking = Boolean(speaking);
  }

  reset() {
    this.speaking = false;
    this.elapsed = 0;
    this.sampleAccumulator = 1 / this.settings.poseFps;
    this.current = createNeutralIdleSample();
    this.target = createNeutralIdleSample();
  }

  update(deltaSeconds) {
    const delta = clamp(finiteNumber(deltaSeconds, 0), 0, 0.1);
    this.elapsed += delta;
    this.sampleAccumulator += delta;

    const interval = 1 / this.settings.poseFps;
    if (this.sampleAccumulator >= interval) {
      this.sampleAccumulator %= interval;
      this.target = sampleIdleMotion(
        this.elapsed,
        this.settings,
        this.speaking,
      );
    }

    const alpha = 1 - Math.exp(-delta * 10);
    this.current = interpolateSample(this.current, this.target, alpha);
    return this.current;
  }
}
