#requires -Version 7.2
<#
.SYNOPSIS
Create the isolated Windows CPython 3.11 / PyTorch CUDA 12.8 pose research environment.
.DESCRIPTION
Only writes this repository's .cache. Requires an existing 64-bit Python 3.11,
20 GiB free disk, and (for smoke) a compatible NVIDIA driver/GPU. No global pip,
CUDA toolkit, driver, model download, training or device access is performed.
Locked wheels total 3.30 GB, under the 8 GB download cap; unpacking uses additional disk.
Includes the Requests HTTP stack required by prepare_pose_dataset.py, with each
dependency pinned to an official PyPI wheel URL, SHA256 and byte count.
Raw pip reports and official package URL/hash provenance are retained in .cache.
.EXAMPLE
pwsh -File scripts/pose/setup-training.ps1 -Python311 C:\Python311\python.exe
.EXAMPLE
pwsh -File scripts/pose/setup-training.ps1 -Python311 C:\Python311\python.exe -Offline -SkipSmoke
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Python311,
    [switch]$Offline,
    [switch]$SkipSmoke
)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'This lock is for Windows CPython 3.11 x64 wheels.' }
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$cache = Join-Path $repo '.cache'
$environment = Join-Path $cache 'pose-train-venv'
$python = Join-Path $environment 'Scripts/python.exe'
$basePython = (Get-Command $Python311 -CommandType Application -ErrorAction Stop).Source
& $basePython -I -B -c 'import sys,struct; assert sys.version_info[:2]==(3,11) and struct.calcsize("P")==8, "Python3.11 x64 required"'
if ($LASTEXITCODE -ne 0) { throw 'Supply an existing 64-bit Python 3.11 interpreter.' }
if ((Get-Item -LiteralPath $repo).PSDrive.Free -lt 20GB) { throw 'At least 20 GiB free disk is required before setup.' }
$null = New-Item -ItemType Directory -Path $cache -Force
foreach ($target in @($cache, $environment)) {
    if (Test-Path -LiteralPath $target) {
        if ((Get-Item -LiteralPath $target).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing redirected cache path: $target" }
    }
}
if (-not (Test-Path -LiteralPath $python)) {
    if (Test-Path -LiteralPath $environment) { throw 'Incomplete environment exists; preserve it for inspection.' }
    & $basePython -I -B -m venv $environment
    if ($LASTEXITCODE -ne 0) { throw 'Isolated venv creation failed.' }
}
$setupSource = @'
from pathlib import Path
import concurrent.futures,hashlib,importlib.metadata,json,os,re,shutil,subprocess,sys,threading,time,urllib.request,urllib.parse
root=Path(__file__).resolve().parents[1]
lock=json.loads((root/'pose-training.lock.json').read_text())
assert os.name=='nt' and sys.version_info[:2]==(3,11) and sys.maxsize>2**32
assert lock['schemaVersion']==1 and lock['platform']=='win_amd64' and lock['python']=='3.11'
assert lock['environment']=='.cache/pose-train-venv' and lock['wheelDirectory']=='.cache/pose-training-wheels'
assert Path(sys.prefix).resolve()==(root/lock['environment']).resolve() and sys.prefix!=sys.base_prefix
assert lock['gpuSmoke']=={'batch':8,'allocatorLimitBytes':536870912,'processBudgetBytes':2147483648}
offline='--offline' in sys.argv
wheels=root/lock['wheelDirectory'];wheels.mkdir(exist_ok=True)
for target in (root/'.cache',root/lock['environment'],wheels):
 assert not target.is_symlink() and target.resolve().is_relative_to(root.resolve()),'Refusing a redirected cache path'
assert len({p['name'].lower().replace('_','-') for p in lock['packages']})==len(lock['packages'])
assert sum(p['bytes'] for p in lock['packages'])==lock['totalWheelBytes']<=8_000_000_000
assert shutil.disk_usage(root).free>=20*1024**3
allowed={'download.pytorch.org','download-r2.pytorch.org','files.pythonhosted.org'}
for p in lock['packages']:
 assert urllib.parse.urlparse(p['url']).scheme=='https' and urllib.parse.urlparse(p['url']).hostname in allowed
 assert Path(p['filename']).name==p['filename'] and '/' not in p['filename'] and '\\' not in p['filename']
 assert re.fullmatch(r'[A-Za-z0-9_.-]+',p['name']) and re.fullmatch(r'[A-Za-z0-9.+_-]+',p['version'])
 assert re.fullmatch(r'[0-9a-f]{64}',p['sha256']) and isinstance(p['bytes'],int) and p['bytes']>0
 if not offline:
  with urllib.request.urlopen(urllib.request.Request(p['url'],method='HEAD'),timeout=30) as r:
   assert urllib.parse.urlparse(r.url).scheme=='https' and urllib.parse.urlparse(r.url).hostname in allowed
   assert int(r.headers['Content-Length'])==p['bytes'],p['name']
print('Locked wheel payload',lock['totalWheelBytes'],'bytes (<8GB); offline:',offline,flush=True)
def digest(path):
 with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
for p in lock['packages']:
 final=wheels/p['filename']
 if final.exists():
  assert final.stat().st_size==p['bytes'] and digest(final)==p['sha256']
  print('Reused',p['filename'],flush=True);continue
 if offline:raise RuntimeError('Offline wheel missing: '+p['filename'])
 partial=final.with_suffix('.whl.partial');state=final.with_suffix('.whl.state.json')
 assert sum(x.stat().st_size for x in wheels.iterdir() if x.is_file())+ (0 if partial.exists() else p['bytes']) <=8_000_000_000
 def save_state():
  temporary=state.with_suffix('.json.tmp')
  temporary.write_text(json.dumps({'sha256':p['sha256'],'chunk':chunk,'completed':sorted(completed)}))
  os.replace(temporary,state)
 chunk=32*1024**2;n=(p['bytes']+chunk-1)//chunk
 completed=[]
 if state.exists():
  record=json.loads(state.read_text());assert record['sha256']==p['sha256'] and record['chunk']==chunk
  completed=record['completed'];assert partial.exists() and partial.stat().st_size==p['bytes']
  assert len(completed)==len(set(completed)) and all(isinstance(i,int) and 0<=i<n for i in completed)
 else:
  assert not partial.exists(),'Untracked partial; preserve and investigate rather than overwrite'
  with partial.open('wb') as f:f.truncate(p['bytes'])
  save_state()
 guard=threading.Lock();started=time.monotonic()
 def get(index):
  start=index*chunk;end=min(p['bytes'],start+chunk)-1
  req=urllib.request.Request(p['url'],headers={'Range':f'bytes={start}-{end}'})
  with urllib.request.urlopen(req,timeout=90) as response:
   assert urllib.parse.urlparse(response.url).scheme=='https' and urllib.parse.urlparse(response.url).hostname in allowed
   if response.status!=206 or response.headers.get('Content-Range')!=f'bytes {start}-{end}/{p["bytes"]}':
    raise ValueError('Server did not honor exact bounded range')
   with partial.open('r+b') as f:
    f.seek(start);remaining=end-start+1
    while remaining:
     block=response.read(min(1024**2,remaining))
     if not block:raise ValueError('Truncated range response')
     f.write(block);remaining-=len(block)
    if response.read(1):raise ValueError('Oversized range response')
    f.flush()
  with guard:
   completed.append(index)
   save_state()
   if len(completed)%8==0 or len(completed)==n:
    print(p['name'],f'{len(completed)}/{n} chunks',f'{time.monotonic()-started:.0f}s',flush=True)
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
  list(pool.map(get,[i for i in range(n) if i not in completed]))
 assert partial.stat().st_size==p['bytes'] and digest(partial)==p['sha256'],'Wheel SHA mismatch'
 os.replace(partial,final)
 print('Verified',p['name'],p['version'],p['bytes'],'bytes SHA256',p['sha256'],flush=True)
print('All locked wheels downloaded and hash-verified.',flush=True)

requirements=root/'.cache/pose-training-requirements.txt'
requirements.write_text('\n'.join(p['name']+'=='+p['version']+' --hash=sha256:'+p['sha256'] for p in lock['packages'])+'\n')
missing=[]
for p in lock['packages']:
 try:version=importlib.metadata.version(p['name'])
 except importlib.metadata.PackageNotFoundError:missing.append(p['name']);continue
 if version!=p['version']:raise RuntimeError('Existing isolated package differs from lock; preserved: '+p['name'])
env=os.environ.copy();temporary=root/'.cache/pose-training-temp';temporary.mkdir(exist_ok=True)
env['TMP']=env['TEMP']=str(temporary)
if missing:
 report=root/'.cache/pose-training-install-report.json'
 if report.exists():report=root/('.cache/pose-training-install-report-'+str(time.time_ns())+'.json')
 with (root/'.cache/pose-training-install.log').open('w') as log:
  subprocess.run([sys.executable,'-m','pip','--isolated','install','--no-index','--find-links',str(wheels),
   '--require-hashes','--only-binary=:all:','--disable-pip-version-check','--no-cache-dir',
   '--report',str(report),'-r',str(requirements)],env=env,check=True,stdout=log,stderr=subprocess.STDOUT)
 print('Raw pip install report:',report,flush=True)
subprocess.run([sys.executable,'-m','pip','--isolated','check'],env=env,check=True)
versions={p['name']:importlib.metadata.version(p['name']) for p in lock['packages']}
assert all(versions[p['name']]==p['version'] for p in lock['packages'])
import requests
assert requests.__version__==versions['requests']
environment={'passed':True,'python':sys.version,'isolated':sys.prefix!=sys.base_prefix,
 'lockSha256':digest(root/'pose-training.lock.json'),'versions':versions,
 'wheelPayloadBytes':lock['totalWheelBytes'],'sources':lock['packages'],
 'pipReports':'pose-training-install-report*.json (raw local-wheel URLs; official HTTPS provenance above)',
 'boardUsed':False,'gpuSmokeRunSeparately':True}
(root/'.cache/pose-training-environment.json').write_text(json.dumps(environment,indent=2)+'\n')
print('Isolated pinned environment verified:',sys.prefix,flush=True)

'@
$setupPath = Join-Path $cache 'pose-training-setup-helper.py'
[IO.File]::WriteAllText($setupPath, $setupSource, [Text.UTF8Encoding]::new($false))
$arguments = @('-I', '-B', $setupPath)
if ($Offline) { $arguments += '--offline' }
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Locked environment setup failed; logs/partials were preserved.' }
if (-not $SkipSmoke) {
    $smokeSource = @'
"""One small synthetic forward/backward only; no optimizer or training data."""
from pathlib import Path
import importlib.metadata,json,sys,time
import torch
root=Path(__file__).resolve().parents[1]
assert sys.prefix!=sys.base_prefix and Path(sys.prefix).resolve()==(root/'.cache/pose-train-venv').resolve()
assert torch.__version__=='2.7.1+cu128' and torch.version.cuda=='12.8'
assert torch.cuda.is_available(),'CUDA unavailable; no driver/environment changes attempted'
torch.set_num_threads(2)
device=0
properties=torch.cuda.get_device_properties(device)
cap=512*1024**2
torch.cuda.set_per_process_memory_fraction(cap/properties.total_memory,device)
free_before,total=torch.cuda.mem_get_info(device)
assert free_before>=2304*1024**2,'Other GPU users leave insufficient headroom; smoke skipped'
torch.manual_seed(2701)
torch.cuda.manual_seed_all(2701)
torch.backends.cudnn.benchmark=False
torch.backends.cudnn.deterministic=True
torch.backends.cudnn.allow_tf32=False
torch.backends.cuda.matmul.allow_tf32=False
torch.cuda.reset_peak_memory_stats(device)
started=time.monotonic()
model=torch.nn.Sequential(torch.nn.Conv2d(3,8,3,stride=2,padding=1),torch.nn.ReLU(),
    torch.nn.Conv2d(8,8,3,padding=1,groups=8),torch.nn.ReLU(),torch.nn.Conv2d(8,12,1)).to(device)
data=torch.randn(8,3,96,96,device=device,requires_grad=True)
output=model(data)
assert tuple(output.shape)==(8,12,48,48)
loss=output.square().mean()
loss.backward()
torch.cuda.synchronize(device)
assert torch.isfinite(loss).item() and torch.isfinite(output).all().item()
assert data.grad is not None and torch.isfinite(data.grad).all().item()
assert all(p.grad is not None and torch.isfinite(p.grad).all().item() and p.grad.abs().sum().item()>0 for p in model.parameters())
peak_allocated=torch.cuda.max_memory_allocated(device);peak_reserved=torch.cuda.max_memory_reserved(device)
assert peak_reserved<=cap<2*1024**3
free_after,_=torch.cuda.mem_get_info(device)
report={'passed':True,'synthetic_smoke_only':True,'optimizer_steps':0,'hardware_board_used':False,
 'python':sys.version,'torch':torch.__version__,'cuda_runtime':torch.version.cuda,'cudnn':torch.backends.cudnn.version(),
 'gpu':properties.name,'compute_capability':list(torch.cuda.get_device_capability(device)),
 'compiled_cuda_architectures':torch.cuda.get_arch_list(),'total_vram_bytes':total,
 'batch':8,'input':[8,3,96,96],'output':list(output.shape),'loss':loss.item(),
 'allocator_limit_bytes':cap,'requested_process_budget_bytes':2*1024**3,
 'peak_tensor_allocated_bytes':peak_allocated,'peak_allocator_reserved_bytes':peak_reserved,
 'device_free_before_bytes':free_before,'device_free_after_bytes':free_after,
 'seconds':time.monotonic()-started,'versions':{name:importlib.metadata.version(name) for name in ('numpy','Pillow','torch')}}
(root/'.cache/pose-training-smoke.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))

'@
    $smokePath = Join-Path $cache 'pose-training-smoke.py'
    [IO.File]::WriteAllText($smokePath, $smokeSource, [Text.UTF8Encoding]::new($false))
    & $python -I -B $smokePath
    if ($LASTEXITCODE -ne 0) { throw 'Tiny GPU forward/backward smoke failed; no training was started.' }
}
Write-Output "Ready: $python"
