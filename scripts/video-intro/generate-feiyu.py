"""Generate the six video lines using the verified optional Feiyu TTS runtime.

Run in the separately installed WSL environment. No training or app asset writes.
The upstream model and runtime checks run before a single shared model is loaded.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument('--server', required=True, type=Path)
parser.add_argument('--package', required=True, type=Path)
parser.add_argument('--profile', required=True, type=Path)
parser.add_argument('--output', required=True, type=Path)
args = parser.parse_args()
args.output = args.output.resolve()
args.package = args.package.resolve()
source_manifest = Path(__file__).resolve().parents[2] / 'assets/audio/v1/manifest.json'
lines = {c['id']: c['text'] for c in json.loads(source_manifest.read_text())['clips']}
clip_ids = ['feed', 'hello', 'perfect', 'burnt', 'remember_burnt', 'focus_start']
sys.path.insert(0, str(args.server.resolve()))
from feiyu_runtime.common import (
    apply_backend_env, checked_path, cpu_onnx_sessions, device_type, load,
    prepare, save, sha, verify_audio_library, verify_package, verify_repo,
)

print('Verifying model package and pinned runtime source...', flush=True)
manifest = verify_package(args.package)
profile = load(args.profile)
repo = verify_repo(profile)
if manifest['schema'] != 'feiyu-voice-local-release-v1' or profile['backend'] != 'cuda':
    raise ValueError('Expected the verified Feiyu package and CUDA runtime')
if args.output.is_relative_to(args.package) or args.output == repo or repo.is_relative_to(args.output):
    raise ValueError('Output must be outside model and runtime source directories')
args.output.mkdir(parents=True, exist_ok=False)
os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                  PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='4')
apply_backend_env(profile)
os.chdir(repo)
sys.path[:0] = [str(repo), str(repo / 'third_party/Matcha-TTS')]
print('Loading inference modules...', flush=True)
import numpy as np
import soundfile as sf
import torch
import onnxruntime as ort
from cosyvoice.cli.cosyvoice import AutoModel
from cosyvoice.utils.common import set_all_random_seed

info = prepare(torch, profile)
verify_audio_library(profile)
sessions = []
started = time.perf_counter()
print('Loading CosyVoice3 and the cute F6 voice...', flush=True)
with cpu_onnx_sessions(ort, sessions):
    voice = AutoModel(model_dir=str(checked_path(args.package, manifest['base_model_dir'])),
                      fp16=False, load_trt=False, load_vllm=False)
selected = manifest['styles']['cute']
checkpoint = checked_path(args.package, selected['llm_checkpoint'])
reference = checked_path(args.package, selected['reference_audio'])
if sha(checkpoint) != selected['llm_sha256'] or sha(reference) != selected['reference_sha256']:
    raise ValueError('Cute voice assets differ from the release manifest')
state = torch.load(checkpoint, map_location='cpu', weights_only=True)
voice.model.llm.load_state_dict(state, strict=True)
del state
for name in ('llm', 'flow', 'hift'):
    if next(getattr(voice.model, name).parameters()).device.type != device_type(info['backend']):
        raise RuntimeError('Unexpected network device: ' + name)
voice.model.llm.eval()
params = manifest['inference']
result = dict(style='cute', checkpoint_sha256=sha(checkpoint),
              reference_sha256=sha(reference), manifest_sha256=sha(args.package / 'manifest.json'),
              inference=params, runtime=info, onnx_sessions=sessions, clips=[])
save(args.output / 'generation.json', result)
for clip_id in clip_ids:
    print('Generating ' + clip_id + ': ' + lines[clip_id], flush=True)
    set_all_random_seed(params['seed'])
    with torch.inference_mode():
        chunks = [x['tts_speech'].detach().cpu().numpy()[0] for x in voice.inference_zero_shot(
            lines[clip_id], params['prompt_prefix'] + selected['reference_text'], str(reference),
            stream=False, speed=params['speed'], text_frontend=True)]
    if not chunks:
        raise ValueError('TTS returned no audio for ' + clip_id)
    audio = np.concatenate(chunks)
    if len(audio) <= 2400 or not np.isfinite(audio).all() or abs(audio).max() <= 1e-4:
        raise ValueError('Invalid TTS output for ' + clip_id)
    native = args.output / (clip_id + '.float.wav')
    playback = args.output / (clip_id + '.wav')
    sf.write(native, audio, voice.sample_rate, subtype='FLOAT')
    gain = min(1., .98 / float(abs(audio).max()))
    sf.write(playback, audio * gain, voice.sample_rate, subtype='PCM_16')
    result['clips'].append(dict(id=clip_id, text=lines[clip_id], file=playback.name,
        sha256=sha(playback), native_sha256=sha(native), sample_rate=voice.sample_rate,
        seconds=len(audio) / voice.sample_rate, playback_gain=gain))
    save(args.output / 'generation.json', result)
    print('READY ' + str(playback) + ' (' + str(round(len(audio)/voice.sample_rate, 2)) + 's)', flush=True)
result.update(status='success', elapsed_seconds=time.perf_counter()-started,
              actual_text_frontend=voice.frontend.text_frontend, runtime_rvc=False)
save(args.output / 'generation.json', result)
print('All six video voice clips generated.', flush=True)
