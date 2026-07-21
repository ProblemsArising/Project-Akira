# Embedded avatar renderer dependencies

Project Akira bundles these browser modules so VRM rendering remains fully
local and does not depend on a CDN:

- Three.js 0.180.0
- `@pixiv/three-vrm` 3.5.5

The matching MIT license texts are included beside each package. `GLTFLoader`
and `BufferGeometryUtils` are copied from the Three.js 0.180.0 package.

The vendored ESM files use direct relative imports instead of package-name imports so they work in Project Akira's embedded Chromium view without import-map support.
