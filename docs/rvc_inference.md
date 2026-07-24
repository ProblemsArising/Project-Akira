# Internal RVC inference

Issue #38 adds an application-owned RVC boundary that accepts the
`SynthesizedAudio` buffer produced by `audio.tts` and returns another
`SynthesizedAudio` buffer. Issue #39 connects that final buffer to direct
blocking playback through Akira's selected output device or the system default.

## Optional runtime

RVC has a large machine-learning dependency tree, so it is kept separate from
Akira's base requirements:

`infer-rvc-python==1.2.0` pulls in `fairseq==0.12.2`. Fairseq requires an
old OmegaConf release whose package metadata is rejected by pip 24.1 and
newer. Use pip 24.0 inside Akira's virtual environment for this optional
runtime:

```powershell
python -m pip install pip==24.0
python -m pip --version
python -m pip install -r requirements-rvc.txt
```

`requirements-rvc.txt` pins NumPy 1.26.4 because the wrapper pins
`faiss-cpu==1.7.3`, whose Windows wheel uses the NumPy 1.x binary ABI. NumPy 2
causes `_ARRAY_API not found` or `numpy.core.multiarray failed to import` while
importing RVC.

The wrapper's normal dependency resolution may install CPU-only PyTorch. On an
NVIDIA system, install Akira's matching CUDA 12.8 wheel set after the base RVC
requirements:

```powershell
python -m pip install --force-reinstall -r requirements-rvc-cuda.txt
```

Verify the runtime before loading a real voice model:

```powershell
python -c "import infer_rvc_python, torch; print('RVC import: OK'); print('Torch:', torch.__version__); print('CUDA:', torch.cuda.is_available(), torch.version.cuda)"
```

Do not add `pip==24.0` to `requirements-rvc.txt`: pip should be downgraded as
a separate bootstrap step before resolving the legacy dependency tree.

The pinned wrapper requires Python 3.10 and may also require FFmpeg and the
Microsoft C++ build tools on Windows. Keeping it optional means normal Akira
installs and unit tests do not import PyTorch or load voice models unless voice
conversion is enabled.

## Converting and playing TTS directly

`TextToSpeech` accepts any object with a `convert(SynthesizedAudio)` method.
Passing an `RVCConverter` makes `speak()` synthesize the source voice, convert
it in memory, and play the converted buffer directly without VB-CABLE or an
external voice changer:

```python
from audio.rvc import RVCConverter, RVCModelConfig
from audio.tts import TextToSpeech

config = RVCModelConfig(
    model_path="data/voice_models/akira/akira.pth",
    index_path="data/voice_models/akira/akira.index",
)

with RVCConverter(config) as converter:
    speaker = TextToSpeech(
        output_device=None,  # system default; an explicit device index also works
        audio_converter=converter,
    )
    speaker.speak("Testing Akira's internal voice conversion.")
```

For diagnostics or future audio consumers, `speaker.render(text)` returns the
final converted `SynthesizedAudio` without playing it, and
`speaker.play_audio(buffer)` plays an existing synthesized or converted buffer.
Playback adapts channel count and sample rate to the chosen endpoint and blocks
until the utterance finishes.

The `.pth` model is required. The `.index` file is optional. A converter keeps
one configured model preloaded across calls; call `close()` when switching
models or shutting down so the backend can release CPU/GPU resources.

`hubert_path` and `rmvpe_path` can be passed to `RVCConverter` for a fully local
offline setup when those support model files are managed by Akira.
