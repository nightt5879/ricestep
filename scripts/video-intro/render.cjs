const path=require('path'),fs=require('fs');
const {bundle}=require('@remotion/bundler');
const {renderMedia,renderStill,selectComposition}=require('@remotion/renderer');
const chrome='C:/Program Files/Google/Chrome/Application/chrome.exe';
const out=path.resolve(__dirname,'../output');
(async()=>{
fs.mkdirSync(path.join(out,'qa'),{recursive:true});
const url=await bundle({entryPoint:path.join(__dirname,'src.tsx'),publicDir:path.join(__dirname,'public')});
const composition=await selectComposition({serveUrl:url,id:'FeiyuIntro',browserExecutable:chrome});
const timeline=require('./timeline.json');
const frames=timeline.shots.flatMap(s=>[s.from+Math.min(30,s.duration-1),s.from+Math.floor(s.duration*.6),s.from+s.duration-1]);
if(!process.argv.includes('--video-only'))for(const frame of frames){await renderStill({serveUrl:url,composition,frame,output:path.join(out,'qa','f'+frame+'.png'),browserExecutable:chrome});console.log('Still '+frame);}
if(process.argv.includes('--stills-only'))return;
let last=-1;
await renderMedia({serveUrl:url,composition,codec:'h264',crf:18,pixelFormat:'yuv420p',audioCodec:'aac',audioBitrate:'192k',outputLocation:path.join(out,'feiyu-intro-1080x1920.mp4'),browserExecutable:chrome,concurrency:4,onProgress:p=>{const n=Math.floor(p.progress*10);if(n!==last){last=n;console.log('Render '+n*10+'%');}}});
console.log('Video complete');})();
