// Inspect the rendered movie, including actual audio placement and cut frames.
const fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process'),crypto=require('node:crypto');
const project=path.resolve(__dirname,'../..'),stage=path.join(project,'work/filming/video');
const ffmpeg=require(path.join(stage,'node_modules/ffmpeg-static'));
const ffprobe=require(path.join(stage,'node_modules/ffprobe-static')).path;
const movie=path.join(project,'work/filming/output/feiyu-intro-1080x1920.mp4');
const out=path.join(project,'work/filming/output/qa/feiyu-final');
fs.mkdirSync(out,{recursive:true});
const run=(exe,args)=>cp.execFileSync(exe,args,{windowsHide:true,maxBuffer:16*1024*1024});
const timeline=JSON.parse(fs.readFileSync(path.join(stage,'timeline.json')));
const manifest=JSON.parse(fs.readFileSync(path.join(stage,'public/voice/manifest.json')));
const probe=JSON.parse(run(ffprobe,['-v','error','-show_format','-show_streams','-of','json',movie]));
const video=probe.streams.find(s=>s.codec_type==='video'),sound=probe.streams.find(s=>s.codec_type==='audio');
if(video.width!==1080||video.height!==1920||video.r_frame_rate!=='30/1'||Number(video.nb_frames)!==timeline.totalFrames||!sound)throw Error('Unexpected final video format');
const decode=cp.spawnSync(ffmpeg,['-v','error','-i',movie,'-f','null','-'],{windowsHide:true,encoding:'utf8'});
if(decode.status!==0||decode.stderr.trim())throw Error('Final video has decode errors: '+decode.stderr);
const volume=cp.spawnSync(ffmpeg,['-hide_banner','-i',movie,'-vn','-af','volumedetect','-f','null','-'],{windowsHide:true,encoding:'utf8'});
const maxVolume=Number(volume.stderr.match(/max_volume: ([-.\d]+) dB/)[1]);
if(!(maxVolume<-.5&&maxVolume>-20))throw Error('Unexpected final audio peak: '+maxVolume);
const pcm=file=>{const b=run(ffmpeg,['-v','error','-i',file,'-vn','-ac','1','-ar','6000','-f','f32le','pipe:1']);return new Float32Array(b.buffer,b.byteOffset,b.byteLength/4);};
const full=pcm(movie),audio=[];
for(const v of timeline.voice){
 const clip=manifest.clips.find(c=>c.id===v.clip),file=path.join(stage,'public/voice',clip.file);
 if(crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')!==clip.sha256)throw Error('Staged voice differs from manifest: '+v.clip);
 const frame=timeline.shots[v.shot].from+v.offset,source=pcm(file);
 if(v.offset+clip.duration_ms/1000*30>timeline.shots[v.shot].duration)throw Error('Voice cut at scene boundary: '+v.clip);
 const trim=900,len=Math.min(source.length-trim,12000),expected=frame/30*6000;
 let best=-2,bestLag=0,sumA2=0;
 for(let i=0;i<len;i++)sumA2+=source[trim+i]**2;
 for(let lag=-900;lag<=900;lag++){
  let dot=0,sumB2=0,base=Math.round(expected)+trim+lag;
  if(base<0||base+len>full.length)continue;
  for(let i=0;i<len;i++){const a=source[trim+i],b=full[base+i];dot+=a*b;sumB2+=b*b;}
  const corr=dot/Math.sqrt(sumA2*sumB2);
  if(corr>best){best=corr;bestLag=lag;}
 }
 if(best<.98||Math.abs(bestLag/200)>3)throw Error('Final audio mismatch: '+v.clip+' '+best+' '+bestLag);
 audio.push({clip:v.clip,expectedFromFrame:frame,durationMs:clip.duration_ms,offsetMilliseconds:bestLag/6,offsetFrames:bestLag/200,correlation:best});
}
const frameSet=new Set([0,22,32,37,42,timeline.totalFrames-1]);
for(const s of timeline.shots){for(const n of [s.from,s.from+30,s.from+Math.floor(s.duration*.6),s.from+s.duration-1])frameSet.add(n);if(s.from>0)frameSet.add(s.from-1);}
for(const v of timeline.voice)frameSet.add(timeline.shots[v.shot].from+v.offset);
const frames=[...frameSet].sort((a,b)=>a-b),select=frames.map(n=>'eq(n\\,'+n+')').join('+');
run(ffmpeg,['-v','error','-y','-i',movie,'-vf','select='+select,'-fps_mode','vfr',path.join(out,'frame-%03d.png')]);
frames.forEach((n,i)=>fs.renameSync(path.join(out,'frame-'+String(i+1).padStart(3,'0')+'.png'),path.join(out,'f'+n+'.png')));
const sheetFrames=timeline.shots.flatMap(s=>[s.from+30,s.from+Math.floor(s.duration*.6),s.from+s.duration-1]);
run(ffmpeg,['-v','error','-y','-i',movie,'-vf','select='+sheetFrames.map(n=>'eq(n\\,'+n+')').join('+')+',scale=270:480,tile=7x3','-frames:v','1',path.join(out,'contact.png')]);
const report={sha256:crypto.createHash('sha256').update(fs.readFileSync(movie)).digest('hex'),bytes:fs.statSync(movie).size,
 probe,decode:{status:decode.status,stderr:decode.stderr},maxVolumeDbfs:maxVolume,audio,frames,timeline};
fs.writeFileSync(path.join(out,'technical.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify({bytes:report.bytes,sha256:report.sha256,frames:timeline.totalFrames,seconds:timeline.totalFrames/30,maxVolumeDbfs:maxVolume,audio,extractedFrames:frames.length},null,2));
