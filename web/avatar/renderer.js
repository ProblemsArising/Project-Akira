import * as THREE from "./vendor/three/three.module.min.js";
import { GLTFLoader } from "./vendor/three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "./vendor/three-vrm/three-vrm.module.min.js";

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
      if (this.currentVrm) this.currentVrm.update(delta);
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
    this.scene.add(vrm.scene);
    vrm.scene.updateMatrixWorld(true);
    this.frame(vrm.scene);
    return vrm;
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
    if (!this.currentVrm) return;
    this.scene.remove(this.currentVrm.scene);
    VRMUtils.deepDispose(this.currentVrm.scene);
    this.currentVrm = null;
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
