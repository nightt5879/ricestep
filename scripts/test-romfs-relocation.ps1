#requires -Version 7.0
[CmdletBinding()]
param([string]$Compiler = 'gcc')
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$sdk = Join-Path $repo '.cache/native-mini3-sdk'
$out = Join-Path $repo '.cache/native-tests/romfs'
$null = New-Item -ItemType Directory -Path $out -Force
& node (Join-Path $PSScriptRoot 'prepare-pose-resources.cjs') --verify (Join-Path $repo 'dist/native/pose-resources')
if ($LASTEXITCODE -ne 0) { throw 'Verified actual resource images required.' }
foreach ($name in @('romfs.c', 'romfs.h')) {
    $lines = & git -C $sdk show "c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35:src/middleware/romfs/$name"
    if ($LASTEXITCODE -ne 0) { throw 'Pinned SDK ROMFS source unavailable.' }
    [IO.File]::WriteAllText((Join-Path $out $name), ($lines -join "`n") + "`n")
}
@'
#include <stdlib.h>
#include <string.h>
#define exram_malloc(alignment, bytes) malloc(bytes)
#define exram_free(p) free(p)
#ifdef _WIN32
/* POSIX tokenizer shim for old MinGW CRTs; original ROMFS is unchanged. */
static char *host_strtok_r(char *s, const char *delim, char **save) {
    if (!s) s = *save;
    s += strspn(s, delim);
    if (!*s) { *save = s; return NULL; }
    char *end = s + strcspn(s, delim);
    if (*end) *end++ = 0;
    *save = end; return s;
}
#define strtok_r host_strtok_r
#endif
'@ | Set-Content -LiteralPath (Join-Path $out 'sysheap.h') -Encoding utf8
@'
#define LISA_LOGE(...) ((void)0)
#define LISA_LOGI(...) ((void)0)
'@ | Set-Content -LiteralPath (Join-Path $out 'lisa_log.h') -Encoding utf8
$binary = Join-Path $out 'romfs-relocation.exe'
& $Compiler '-std=gnu11' '-Werror=implicit-function-declaration' '-O2' "-I$out" (Join-Path $out 'romfs.c') (Join-Path $repo 'tests/native/romfs_relocation_test.c') '-o' $binary
if ($LASTEXITCODE -ne 0) { throw 'ROMFS host compile failed.' }
& $binary (Join-Path $repo '.cache/stock-recovery-mini3/respak.bin') (Join-Path $repo '.cache/stock-recovery-mini3/emoji.bin') (Join-Path $repo 'dist/native/pose-resources/resources.bin')
if ($LASTEXITCODE -ne 0) { throw 'ROMFS relocated resources test failed.' }
