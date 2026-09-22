import unittest

try:
    import av
except ImportError:
    raise unittest.SkipTest("PyAV ('av') is not installed")

import numpy as np
import json
import logging
from speech_pipeline import KhmerSTTService, KhmerTranslationService
from complaint_auditor import OllamaComplaintAuditor

logging.basicConfig(level=logging.INFO)

import sys
from pathlib import Path

def run_sound_test():
    in_audio = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent.parent / "IMG_1053.MOV")
    if not Path(in_audio).is_file():
        print(f"Audio file not found: {in_audio}")
        return
    print(f"Loading media from: {in_audio}")
    container = av.open(in_audio)
    resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
    pcm_chunks = [rframe.to_ndarray()[0] for frame in container.decode(audio=0) for rframe in resampler.resample(frame)]
    audio_pcm = np.concatenate(pcm_chunks)

    print(f"\n[1] Decoded Audio Length: {len(audio_pcm)} samples ({len(audio_pcm)/16000:.2f}s)")

    print("\n[2] Transcribing with fine-tuned Khmer model...")
    stt = KhmerSTTService()
    km_text = stt.transcribe(audio_pcm)
    print(f"--> Khmer Transcript: \"{km_text}\"")

    print("\n[3] Translating to English...")
    trans = KhmerTranslationService()
    en_text = trans.translate(km_text)
    print(f"--> English Translation: \"{en_text}\"")

    print("\n[4] Auditing Complaint with LLM...")
    auditor = OllamaComplaintAuditor()
    analysis = auditor.analyze(km_text, en_text)

    print("\n================ FINAL COMPLAINT VERDICT ================")
    print(json.dumps(analysis.as_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    run_sound_test()
