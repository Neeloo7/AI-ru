"""One-time model downloads. Runtime speech never contacts these services."""
from pathlib import Path
import requests

MODEL_DIR = Path(__file__).resolve().with_name("models")
FILES = {
    "kokoro-v1.0.int8.onnx": 92361271,
    "voices-v1.0.bin": 28214398,
}
BASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"


def download_voice_models():
    MODEL_DIR.mkdir(exist_ok=True)
    for filename, expected_size in FILES.items():
        target = MODEL_DIR / filename
        if target.exists() and target.stat().st_size == expected_size:
            continue
        partial = target.with_suffix(target.suffix + ".part")
        print(f"Downloading {filename} ({expected_size // 1000000} MB)...", flush=True)
        try:
            with requests.get(BASE_URL + filename, stream=True, timeout=(10, 60)) as response:
                response.raise_for_status()
                with partial.open("wb") as output:
                    for chunk in response.iter_content(1024 * 1024):
                        output.write(chunk)
            if partial.stat().st_size != expected_size:
                raise ValueError(f"Incomplete model download: {filename}")
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)


if __name__ == "__main__":
    download_voice_models()
    from faster_whisper.utils import download_model
    print("Preparing local Whisper base model...", flush=True)
    download_model("base", output_dir=str(MODEL_DIR / "whisper-base"))
    print("Local voice models are ready. No cloud speech account is needed.")
