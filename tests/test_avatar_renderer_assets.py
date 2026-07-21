from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AVATAR_ROOT = ROOT / "web" / "avatar"


class AvatarRendererAssetTests(unittest.TestCase):
    def test_avatar_page_loads_local_vrm_renderer_modules(self) -> None:
        html = (AVATAR_ROOT / "index.html").read_text(encoding="utf-8")

        self.assertNotIn('type="importmap"', html)
        self.assertIn('/static/avatar/bootstrap.js', html)
        self.assertIn('type="module" src="/static/avatar/app.js"', html)
        self.assertLess(
            html.index('/static/avatar/bootstrap.js'),
            html.index('/static/avatar/app.js'),
        )
        self.assertIn('id="rendererHost"', html)
        self.assertIn('id="modelInput"', html)
        self.assertIn('id="removeModelButton"', html)

    def test_renderer_uses_official_three_vrm_loader_pattern(self) -> None:
        renderer = (AVATAR_ROOT / "renderer.js").read_text(encoding="utf-8")
        app = (AVATAR_ROOT / "app.js").read_text(encoding="utf-8")

        self.assertIn("GLTFLoader", renderer)
        self.assertIn("VRMLoaderPlugin", renderer)
        self.assertIn("gltf.userData.vrm", renderer)
        self.assertIn("VRMUtils.rotateVRM0", renderer)
        self.assertIn("/api/avatar/model", app)
        self.assertIn("avatar.model.changed", app)

    def test_renderer_module_graph_uses_direct_local_imports(self) -> None:
        renderer = (AVATAR_ROOT / "renderer.js").read_text(encoding="utf-8")
        self.assertIn('./vendor/three/three.module.min.js', renderer)
        self.assertIn('./vendor/three/addons/loaders/GLTFLoader.js', renderer)
        self.assertIn('./vendor/three-vrm/three-vrm.module.min.js', renderer)

        bare_three = re.compile(r'from\s*["\']three["\']')
        vendor_files = (
            AVATAR_ROOT / "vendor" / "three" / "addons" / "loaders" / "GLTFLoader.js",
            AVATAR_ROOT / "vendor" / "three" / "addons" / "utils" / "BufferGeometryUtils.js",
            AVATAR_ROOT / "vendor" / "three-vrm" / "three-vrm.module.min.js",
        )
        for path in vendor_files:
            with self.subTest(path=path):
                self.assertIsNone(
                    bare_three.search(path.read_text(encoding="utf-8"))
                )

    def test_avatar_module_startup_watchdog_is_present(self) -> None:
        bootstrap = (AVATAR_ROOT / "bootstrap.js").read_text(encoding="utf-8")
        app = (AVATAR_ROOT / "app.js").read_text(encoding="utf-8")

        self.assertIn("__akiraAvatarAppStarted", bootstrap)
        self.assertIn("Renderer unavailable", bootstrap)
        self.assertIn("__akiraAvatarAppStarted = true", app)

    def test_vendored_renderer_dependencies_and_licenses_are_present(self) -> None:
        expected = (
            AVATAR_ROOT / "vendor" / "three" / "three.module.min.js",
            AVATAR_ROOT / "vendor" / "three" / "three.core.min.js",
            AVATAR_ROOT / "vendor" / "three" / "addons" / "loaders" / "GLTFLoader.js",
            AVATAR_ROOT / "vendor" / "three" / "addons" / "utils" / "BufferGeometryUtils.js",
            AVATAR_ROOT / "vendor" / "three" / "LICENSE.txt",
            AVATAR_ROOT / "vendor" / "three-vrm" / "three-vrm.module.min.js",
            AVATAR_ROOT / "vendor" / "three-vrm" / "LICENSE.txt",
        )
        for path in expected:
            with self.subTest(path=path):
                self.assertTrue(path.is_file())
                self.assertGreater(path.stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()
