// Normalize generated video voice without changing pitch, speed, or app assets.
const fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'../..');
const source=path.resolve(process.argv[2]||''),out=path.resolve(process.argv[3]||'');
if(process.argv.length!==4||source===out)throw Error('Usage: node prepare-voice.cjs GENERATED_DIR OUTPUT_DIR');
const dependencies=path.join(root,'work/filming/video/node_modules');
const ffmpeg=require(path.join(dependencies,'ffmpeg-static'));
const ffprobe=require(path.join(dependencies,'ffprobe-static')).path;
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
const generation=JSON.parse(fs.readFileSync(path.join(source,'generation.json'),'utf8'));
if(generation.status!=='success'||generation.style!=='cute'||generation.clips.length!==6)throw Error('Incomplete cute voice generation');
fs.mkdirSync(out,{recursive:true});
const manifest={version:1,provider:'Local FeiyuVoice-CosyVoice3',voice:'cute F6',
 modelRepository:'Esmerubphire/FeiyuVoice-CosyVoice3',checkpointSha256:generation.checkpoint_sha256,
 referenceSha256:generation.reference_sha256,seed:generation.inference.seed,
 processing:'FFmpeg loudnorm I=-18 TP=-2 LRA=9; no pitch or speed change',clips:[]};
for(const clip of generation.clips){
 if(!/^[a-z_]+$/.test(clip.id)||clip.file!==clip.id+'.wav')throw Error('Unexpected clip name');
 const input=path.join(source,clip.file),file=clip.id+'.mp3',output=path.join(out,file);
 if(sha(fs.readFileSync(input))!==clip.sha256)throw Error('Generated WAV checksum mismatch: '+clip.id);
 cp.execFileSync(ffmpeg,['-hide_banner','-v','error','-y','-i',input,'-af','loudnorm=I=-18:TP=-2:LRA=9',
   '-ar','48000','-ac','1','-codec:a','libmp3lame','-b:a','192k',output],{windowsHide:true});
 const probe=JSON.parse(cp.execFileSync(ffprobe,['-v','error','-show_format','-show_streams','-of','json',output],{encoding:'utf8',windowsHide:true}));
 const bytes=fs.readFileSync(output),seconds=Number(probe.format.duration);
 if(!(seconds>0&&seconds<15))throw Error('Unexpected clip length: '+clip.id);
 manifest.clips.push({id:clip.id,file,text:clip.text,duration_ms:Math.ceil(seconds*1000),bytes:bytes.length,
   sha256:sha(bytes),sample_rate:48000,channels:1,sourceWavSha256:clip.sha256});
 console.log(clip.id+': '+seconds.toFixed(3)+'s');
}
fs.writeFileSync(path.join(out,'manifest.json'),JSON.stringify(manifest,null,2)+'\n');
