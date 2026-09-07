"""Isolated model host for TTS engines with incompatible Python dependencies.

The parent process speaks newline-delimited JSON over stdin/stdout.  Audio is
written to a caller-created temporary WAV path, which avoids sending large
waveforms through the pipe.
"""
import argparse
import json
import sys
import traceback

import numpy as np
import soundfile
import torch


def respond(stream, payload):
    print(json.dumps(payload), file=stream, flush=True)


def load_qwen(audio_prompt_wav, use_gpu):
    if not audio_prompt_wav:
        raise ValueError("Qwen3 TTS requires a selected voice WAV.")
    from qwen_tts import Qwen3TTSModel

    use_cuda = use_gpu and torch.cuda.is_available()
    # Qwen3-TTS FP16 sampling is known to produce NaNs/device-side asserts on
    # Turing (SM 7.5) cards such as the RTX 2060. FP32 is stable but needs more
    # VRAM than this model's generation cache leaves available, so CPU is the
    # only reliable mode on pre-Ampere hardware.
    if use_cuda and torch.cuda.get_device_capability()[0] < 8:
        print("Qwen3 TTS: pre-Ampere GPU detected; using CPU to avoid the known FP16 sampler crash.", file=sys.stderr)
        use_cuda = False
    device = "cuda:0" if use_cuda else "cpu"
    model = Qwen3TTSModel.from_pretrained(
        "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        device_map=device,
        dtype=torch.float16 if device.startswith("cuda") else torch.float32,
        attn_implementation="eager",
    )
    prompt = model.create_voice_clone_prompt(
        ref_audio=audio_prompt_wav,
        x_vector_only_mode=True,
    )

    def generate(text):
        wavs, sample_rate = model.generate_voice_clone(
            text=text, language="English", voice_clone_prompt=prompt
        )
        return np.asarray(wavs[0], dtype=np.float32).flatten(), sample_rate

    return generate, use_cuda


def load_pocket(audio_prompt_wav, use_gpu, pocket_voice="alba"):
    from pocket_tts import TTSModel

    model = TTSModel.load_model()
    # PocketTTS is designed and supported primarily for CPU inference.  Its
    # CUDA path is experimental and, on some consumer cards, can yield corrupt
    # / non-speech audio instead of a useful error.  Keep this engine on CPU
    # even when the application's general GPU checkbox is enabled.
    if use_gpu and torch.cuda.is_available():
        print(
            "PocketTTS: using the supported CPU runtime; its CUDA path is experimental.",
            file=sys.stderr,
        )
    voice_state = model.get_state_for_audio_prompt(audio_prompt_wav or pocket_voice)

    def generate(text):
        wav = model.generate_audio(voice_state, text)
        return wav.detach().cpu().numpy().flatten(), model.sample_rate

    return generate, False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=("qwen3", "pocket"), required=True)
    args = parser.parse_args()

    # Third-party libraries may print progress to stdout. Reserve the original
    # stdout solely for the protocol so those messages cannot corrupt JSON.
    protocol_stdout = sys.stdout
    sys.stdout = sys.stderr
    request = json.loads(sys.stdin.readline())
    try:
        if args.engine == "qwen3":
            generate, uses_gpu = load_qwen(request.get("audio_prompt_wav"), request.get("use_gpu", True))
        else:
            generate, uses_gpu = load_pocket(
                request.get("audio_prompt_wav"),
                request.get("use_gpu", True),
                request.get("pocket_voice", "alba"),
            )
        respond(protocol_stdout, {"ok": True, "uses_gpu": uses_gpu})
    except Exception as exc:
        respond(protocol_stdout, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        traceback.print_exc(file=sys.stderr)
        return 1

    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request["action"] == "shutdown":
                respond(protocol_stdout, {"ok": True})
                return 0
            wav, sample_rate = generate(request["text"])
            soundfile.write(request["output_path"], wav, sample_rate)
            respond(protocol_stdout, {"ok": True, "sample_rate": sample_rate})
        except Exception as exc:
            respond(protocol_stdout, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            traceback.print_exc(file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
