import * as THREE from "./vendor/three/three.module.min.js";
import { GLTFLoader } from "./vendor/three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "./vendor/three-vrm/three-vrm.module.min.js";
import { EmbeddedIdleMotion } from "./idle.js";

const FACE_EXPRESSION_ALIASES = Object.freeze({
  happy: Object.freeze(["happy", "joy", "smile"]),
  relaxed: Object.freeze(["relaxed", "fun"]),
  sad: Object.freeze(["sad", "sorrow"]),
  angry: Object.freeze(["angry"]),
  surprised: Object.freeze(["surprised", "surprise"]),
});

const MOUTH_EXPRESSION_ALIASES = Object.freeze({
  aa: Object.freeze(["aa", "a"]),
  ih: Object.freeze(["ih", "i"]),
  ou: Object.freeze(["ou", "u"]),
  ee: Object.freeze(["ee", "e"]),
  oh: Object.freeze(["oh", "o"]),
});

export class EmbeddedVRMRenderer {
  constructor(host) {
    if (!host) throw new Error("The embedded avatar renderer host is missing.");

    this.host = host;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(30, 1, 0.01, 100);
    this.camera.position.set(0, 1.35, 3);
    this.currentVrm = null;
    this.loadToken = 0;
    this.clock = new THREE.Clock();
    this.faceSettings = {
      faceFps: 120,
      fadeSpeed: 0.08,
    };
    this.faceIdle = { happy: 0.024, relaxed: 0.009, sad: 0, angry: 0, surprised: 0 };
    this.faceCurrent = { ...this.faceIdle };
    this.faceTarget = { ...this.faceIdle };
    this.faceActive = false;
    this.faceBindings = {};
    this.mouthBindings = {};
    this.mouthSettings = {
      mouthFps: 28,
      attackSpeed: 0.60,
      releaseSpeed: 0.42,
    };
    this.mouthCurrent = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 };
    this.mouthTarget = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 };
    this.idleMotion = new EmbeddedIdleMotion();
    this.idleRig = null;
    this.idleEuler = new THREE.Euler(0, 0, 0, "XYZ");
    this.idleQuaternion = new THREE.Quaternion();

    this.renderer = new THREE.WebGLRenderer({
      alpha: true,
      antialias: true,
      powerPreference: "high-performance",
    });
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.08;
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.domElement.setAttribute("aria-label", "Embedded VRM avatar");
    host.replaceChildren(this.renderer.domElement);

    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x25203b, 2.35));
    const keyLight = new THREE.DirectionalLight(0xffffff, 2.8);
    keyLight.position.set(1.8, 3.2, 2.4);
    this.scene.add(keyLight);

    const fillLight = new THREE.DirectionalLight(0xb9a7ff, 1.35);
    fillLight.position.set(-2.2, 1.4, 1.2);
    this.scene.add(fillLight);

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(host);
    this.resize();

    this.renderer.setAnimationLoop(() => {
      const delta = Math.min(this.clock.getDelta(), 0.1);
      if (this.currentVrm) {
        this._updateIdleMovement(delta);
        this._updateFace(delta);
        this._updateMouth(delta);
        this.currentVrm.update(delta);
      }
      this.renderer.render(this.scene, this.camera);
    });
  }

  async load(url, onProgress = null) {
    this.clear();
    const token = ++this.loadToken;

    const loader = new GLTFLoader();
    loader.register((parser) => new VRMLoaderPlugin(parser));

    let gltf;
    try {
      gltf = await new Promise((resolve, reject) => {
        loader.load(
          url,
          resolve,
          (progress) => {
            if (!onProgress || !progress.total) return;
            onProgress(Math.min(1, progress.loaded / progress.total));
          },
          reject,
        );
      });
    } catch (error) {
      if (token !== this.loadToken) return null;
      throw error;
    }

    if (token !== this.loadToken) {
      const abandoned = gltf.userData && gltf.userData.vrm;
      if (abandoned) VRMUtils.deepDispose(abandoned.scene);
      return null;
    }

    const vrm = gltf.userData && gltf.userData.vrm;
    if (!vrm) {
      throw new Error("The loaded file did not produce a VRM avatar.");
    }

    VRMUtils.rotateVRM0(vrm);
    this.currentVrm = vrm;
    this._captureIdleRig();
    this._refreshExpressionBindings();
    this.clearFaceExpression(true);
    this.closeMouth(true);
    this.scene.add(vrm.scene);
    vrm.scene.updateMatrixWorld(true);
    this.frame(vrm.scene);
    return vrm;
  }

  configureIdle(settings = {}) {
    this.idleMotion.configure(settings);
  }

  setSpeaking(speaking) {
    this.idleMotion.setSpeaking(speaking);
  }

  getIdleCapabilities() {
    if (!this.idleRig) return { enabled: false, bones: [] };
    return {
      enabled: this.idleMotion.settings.enabled,
      bones: Object.keys(this.idleRig.bones),
    };
  }

  _captureIdleRig() {
    const vrm = this.currentVrm;
    if (!vrm || !vrm.scene || !vrm.humanoid) {
      this.idleRig = null;
      return;
    }

    const boneNames = [
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
    ];
    const bones = {};
    for (const name of boneNames) {
      const node = vrm.humanoid.getNormalizedBoneNode(name);
      if (!node) continue;
      bones[name] = {
        node,
        position: node.position.clone(),
        quaternion: node.quaternion.clone(),
      };
    }

    this.idleRig = {
      root: {
        node: vrm.scene,
        position: vrm.scene.position.clone(),
        quaternion: vrm.scene.quaternion.clone(),
      },
      bones,
    };
    this.idleMotion.reset();
  }

  _updateIdleMovement(delta) {
    if (!this.idleRig) return;
    const sample = this.idleMotion.update(delta);
    const root = this.idleRig.root;
    root.node.position.set(
      root.position.x + sample.rootPosition.x,
      root.position.y + sample.rootPosition.y,
      root.position.z + sample.rootPosition.z,
    );
    root.node.quaternion.copy(root.quaternion);

    for (const [name, binding] of Object.entries(this.idleRig.bones)) {
      const offset = sample.bones[name];
      if (!offset) continue;
      binding.node.position.copy(binding.position);
      this.idleEuler.set(offset.x, offset.y, offset.z, "XYZ");
      this.idleQuaternion.setFromEuler(this.idleEuler);
      binding.node.quaternion
        .copy(binding.quaternion)
        .multiply(this.idleQuaternion);
    }
  }

  _resetIdleRig() {
    if (!this.idleRig) return;
    const root = this.idleRig.root;
    root.node.position.copy(root.position);
    root.node.quaternion.copy(root.quaternion);
    for (const binding of Object.values(this.idleRig.bones)) {
      binding.node.position.copy(binding.position);
      binding.node.quaternion.copy(binding.quaternion);
    }
    this.idleMotion.reset();
  }

  configureFace(settings = {}) {
    const fps = Number(settings.faceFps ?? settings.face_blend_fps);
    const fade = Number(settings.fadeSpeed ?? settings.expression_fade_speed);
    const idle = settings.idle && typeof settings.idle === "object"
      ? settings.idle
      : null;

    if (Number.isFinite(fps)) {
      this.faceSettings.faceFps = Math.max(1, Math.min(240, fps));
    }
    if (Number.isFinite(fade)) {
      this.faceSettings.fadeSpeed = Math.max(0, Math.min(1, fade));
    }
    if (idle) {
      for (const name of Object.keys(this.faceIdle)) {
        const value = Number(idle[name]);
        this.faceIdle[name] = Number.isFinite(value)
          ? Math.max(0, Math.min(1, value))
          : 0;
      }
      if (!this.faceActive) this.clearFaceExpression();
    }
  }

  setFaceExpression(blends = {}) {
    this.faceActive = true;
    for (const name of Object.keys(this.faceTarget)) {
      const value = Number(blends[name]);
      this.faceTarget[name] = Number.isFinite(value)
        ? Math.max(0, Math.min(1, value))
        : 0;
    }
    return Boolean(this.currentVrm && this.currentVrm.expressionManager);
  }

  clearFaceExpression(immediate = false) {
    this.faceActive = false;
    this.faceTarget = { ...this.faceIdle };
    if (immediate) {
      this.faceCurrent = { ...this.faceTarget };
      this._applyFaceValues();
    }
  }


  _updateFace(delta) {
    const frameScale = Math.max(0.05, delta * this.faceSettings.faceFps);
    const speed = 1 - Math.pow(1 - this.faceSettings.fadeSpeed, frameScale);

    for (const name of Object.keys(this.faceCurrent)) {
      const current = this.faceCurrent[name];
      const target = this.faceTarget[name];
      this.faceCurrent[name] = current + (target - current) * speed;
    }
    this._applyFaceValues();
  }

  _expressionNameLookup(manager) {
    const lookup = new Map();
    const expressionMap = manager && manager.expressionMap;
    if (!expressionMap || typeof expressionMap !== "object") return lookup;

    for (const name of Object.keys(expressionMap)) {
      lookup.set(String(name).toLowerCase(), name);
    }
    return lookup;
  }

  _resolveExpressionBindings(manager, aliases) {
    const lookup = this._expressionNameLookup(manager);
    const result = {};

    for (const [logicalName, candidates] of Object.entries(aliases)) {
      result[logicalName] = null;
      for (const candidate of candidates) {
        const exact = manager.getExpression(candidate);
        if (exact) {
          result[logicalName] = candidate;
          break;
        }
        const actualName = lookup.get(candidate.toLowerCase());
        if (actualName && manager.getExpression(actualName)) {
          result[logicalName] = actualName;
          break;
        }
      }
    }
    return result;
  }

  _refreshExpressionBindings() {
    const manager = this.currentVrm && this.currentVrm.expressionManager;
    if (!manager) {
      this.faceBindings = {};
      this.mouthBindings = {};
      return;
    }

    this.faceBindings = this._resolveExpressionBindings(
      manager,
      FACE_EXPRESSION_ALIASES,
    );
    this.mouthBindings = this._resolveExpressionBindings(
      manager,
      MOUTH_EXPRESSION_ALIASES,
    );
  }

  _expressionWeight(manager, name, value) {
    const expression = name ? manager.getExpression(name) : null;
    if (!expression) return 0;
    const normalized = Math.max(0, Math.min(1, Number(value) || 0));
    if (expression.isBinary) return normalized > 0.08 ? 1 : 0;
    return normalized;
  }

  _applyBoundValues(manager, currentValues, bindings) {
    const valuesByExpression = new Map();

    for (const [logicalName, value] of Object.entries(currentValues)) {
      const actualName = bindings[logicalName];
      if (!actualName) continue;
      const previous = valuesByExpression.get(actualName) || 0;
      valuesByExpression.set(actualName, Math.max(previous, value));
    }

    for (const [actualName, value] of valuesByExpression.entries()) {
      manager.setValue(
        actualName,
        this._expressionWeight(manager, actualName, value),
      );
    }
  }

  _applyFaceValues() {
    const manager = this.currentVrm && this.currentVrm.expressionManager;
    if (!manager) return;
    this._applyBoundValues(manager, this.faceCurrent, this.faceBindings);
  }

  getExpressionCapabilities() {
    const face = Object.entries(this.faceBindings)
      .filter(([, actualName]) => Boolean(actualName))
      .map(([logicalName]) => logicalName);
    const mouth = Object.entries(this.mouthBindings)
      .filter(([, actualName]) => Boolean(actualName))
      .map(([logicalName]) => logicalName);
    return { face, mouth };
  }

  configureMouth(settings = {}) {
    const fps = Number(settings.mouthFps ?? settings.mouth_fps);
    const attack = Number(settings.attackSpeed ?? settings.mouth_attack_speed);
    const release = Number(settings.releaseSpeed ?? settings.mouth_release_speed);
    if (Number.isFinite(fps)) this.mouthSettings.mouthFps = Math.max(1, Math.min(240, fps));
    if (Number.isFinite(attack)) this.mouthSettings.attackSpeed = Math.max(0, Math.min(1, attack));
    if (Number.isFinite(release)) this.mouthSettings.releaseSpeed = Math.max(0, Math.min(1, release));
  }

  setMouthVisemes(blends = {}) {
    for (const name of Object.keys(this.mouthTarget)) {
      const value = Number(blends[name]);
      this.mouthTarget[name] = Number.isFinite(value)
        ? Math.max(0, Math.min(1, value))
        : 0;
    }
    return Boolean(this.currentVrm && this.currentVrm.expressionManager);
  }

  closeMouth(immediate = false) {
    for (const name of Object.keys(this.mouthTarget)) {
      this.mouthTarget[name] = 0;
    }
    if (!this.currentVrm || !this.currentVrm.expressionManager) {
      for (const name of Object.keys(this.mouthCurrent)) this.mouthCurrent[name] = 0;
      return;
    }
    if (immediate) this._applyMouthValues(true);
  }

  _updateMouth(delta) {
    const currentOpen = Math.max(...Object.values(this.mouthCurrent));
    const targetOpen = Math.max(...Object.values(this.mouthTarget));
    const baseSpeed = targetOpen >= currentOpen
      ? this.mouthSettings.attackSpeed
      : this.mouthSettings.releaseSpeed;
    const frameScale = Math.max(0.05, delta * this.mouthSettings.mouthFps);
    const speed = 1 - Math.pow(1 - baseSpeed, frameScale);

    for (const name of Object.keys(this.mouthCurrent)) {
      const current = this.mouthCurrent[name];
      const target = this.mouthTarget[name];
      this.mouthCurrent[name] = current + (target - current) * speed;
    }
    this._applyMouthValues(false);
  }

  _applyMouthValues(forceClosed) {
    const manager = this.currentVrm && this.currentVrm.expressionManager;
    if (!manager) return;

    if (forceClosed) {
      for (const name of Object.keys(this.mouthCurrent)) {
        this.mouthCurrent[name] = 0;
      }
    }
    this._applyBoundValues(manager, this.mouthCurrent, this.mouthBindings);
  }

  frame(object) {
    const bounds = new THREE.Box3().setFromObject(object);
    if (bounds.isEmpty()) {
      this.camera.position.set(0, 1.35, 3);
      this.camera.lookAt(0, 1.25, 0);
      return;
    }

    const size = bounds.getSize(new THREE.Vector3());
    const center = bounds.getCenter(new THREE.Vector3());
    const fov = THREE.MathUtils.degToRad(this.camera.fov);
    const aspect = Math.max(0.25, this.camera.aspect);
    const verticalDistance = size.y / (2 * Math.tan(fov / 2));
    const horizontalDistance = size.x / (2 * Math.tan(fov / 2) * aspect);
    const distance = Math.max(verticalDistance, horizontalDistance, size.z) * 1.18;

    this.camera.near = Math.max(0.01, distance / 100);
    this.camera.far = Math.max(100, distance * 100);
    this.camera.position.set(center.x, center.y + size.y * 0.02, center.z + distance);
    this.camera.lookAt(center.x, center.y + size.y * 0.02, center.z);
    this.camera.updateProjectionMatrix();
  }

  clear() {
    this.loadToken += 1;
    this.clearFaceExpression(true);
    this.closeMouth(true);
    this._resetIdleRig();
    if (!this.currentVrm) {
      this.idleRig = null;
      return;
    }
    this.scene.remove(this.currentVrm.scene);
    VRMUtils.deepDispose(this.currentVrm.scene);
    this.currentVrm = null;
    this.idleRig = null;
    this.faceBindings = {};
    this.mouthBindings = {};
  }

  resize() {
    const width = Math.max(1, this.host.clientWidth);
    const height = Math.max(1, this.host.clientHeight);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
    if (this.currentVrm) this.frame(this.currentVrm.scene);
  }

  dispose() {
    this.clear();
    this.resizeObserver.disconnect();
    this.renderer.setAnimationLoop(null);
    this.renderer.dispose();
  }
}
