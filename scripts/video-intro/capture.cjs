const fs=require('fs'),path=require('path'),cp=require('child_process');
const root=path.resolve(__dirname,'../../..');
const out=path.join(__dirname,'public/ui'); fs.mkdirSync(out,{recursive:true});
const cache=path.join(__dirname,'fixtures');fs.mkdirSync(cache,{recursive:true});
const prefix='RICESTEP_CONFIG={tts_enabled=false,sound_enabled=false,storage_enabled=false,clock_enabled=false}\n';
const base=prefix+['game','character','voice','main'].map(x=>fs.readFileSync(path.join(root,'app',x+'.lua'),'utf8')).join('\n');
const harness=`
local start0,tick0=on_start,on_tick
local factory0=create_game
function create_game(opts) opts.random=function() return 3 end; return factory0(opts) end
local function tick(t) if t>0 then tick0(t) end end
local function single() on_button_click("function"); tick(350) end
local function double() on_button_click("function"); on_button_click("function") end
local function kitchen() single();single();double() end
local function perfect() kitchen();double();tick(1000);on_button_click("function");assert(ricestep_debug().state.rice==5) end
local function home() tick(600);single();double();assert(ricestep_debug().page=="home") end
local function feed() perfect();home();single();single();single();double();assert(ricestep_debug().state.rice==4) end
local function burn() feed();tick(4000);kitchen();double();tick(5000);tick(5000);assert(ricestep_debug().state.cooking_quality=="burnt");assert(ricestep_debug().state.rice==4) end
function on_tick() end
function on_start() start0();tick(40); ACTION end
`;
const jobs=[];const add=(name,action)=>jobs.push({name,action});
add('home','tick(300)');add('kitchen','kitchen()');
for(let n=0;n<=20;n++)add('cook-'+n,`kitchen();double();tick(${n*50})`);
for(const t of [0,800,1600,2600,4000])add('perfect-'+t,`perfect();tick(${t})`);
for(const t of [0,800,1600,2600,4000])add('feed-'+t,`feed();tick(${t})`);
for(const t of [1000,3000,5000,7000,9000])add('late-'+t,`feed();tick(4000);kitchen();double();tick(${Math.min(t,5000)});tick(${Math.max(t-5000,0)})`);
for(const t of [0,1000,2600,4000])add('burnt-'+t,`burn();tick(${t})`);
add('after-burnt-home','burn();home()');add('memory','burn();home();double();assert(ricestep_debug().line=="上次饭晒黑了，这回盯紧")');
async function capture(job){const lua=path.join(cache,job.name+'.lua'),png=path.join(out,job.name+'.png');
if(fs.existsSync(png))fs.unlinkSync(png);
fs.writeFileSync(lua,base+'\n'+harness.replace('ACTION',job.action));
cp.execFileSync(process.execPath,[path.join(root,'scripts/minify-lua.cjs'),lua],{windowsHide:true});
await new Promise((ok,no)=>cp.execFile(path.join(root,'.tools/LingClaw-SDK-0.1.0-windows-x64/lingclaw-sdk.exe'),['--headless',lua,png],{windowsHide:true,timeout:30000},e=>e?no(e):ok()));
if(!fs.existsSync(png))throw Error('Missing '+job.name);console.log('Captured '+job.name);
}
(async()=>{let idx=0;await Promise.all(Array.from({length:4},async()=>{while(idx<jobs.length)await capture(jobs[idx++]);}));
fs.writeFileSync(path.join(__dirname,'capture-manifest.json'),JSON.stringify({source:'Unmodified production Lua, official SDK v0.1.0 API4 headless; isolated simulated inventory; no device',jobs},null,2));
fs.mkdirSync(path.join(__dirname,'public/voice'),{recursive:true});for(const name of ['hello','perfect','feed','burnt','remember_burnt','focus_start'])fs.copyFileSync(path.join(root,'assets/audio/v1',name+'.mp3'),path.join(__dirname,'public/voice',name+'.mp3'));
const lua=fs.readFileSync(path.join(root,'app/character.lua'),'utf8');const high=lua.split('local character_high=')[1].split('local character_low=')[0];
const pal=['#FFF5E5','#17233e','#355798','#73b0de','#c4e0f2','#ffead6','#f9fbff','#d68f98','#e3ba60'];
let n=0;for(const m of high.matchAll(/\{(\d+),(\d+),\[=\[([\s\S]*?)\]=\]\}/g)){let shapes='';let s=m[3];for(let i=0;i<s.length;i+=5){let [x,y,w,h,c]=[...s.slice(i,i+5)].map(v=>v.charCodeAt(0)-33);shapes+=`<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${pal[c]}"/>`;}
fs.writeFileSync(path.join(__dirname,'public',`pose-${++n}.svg`),`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${m[1]} ${m[2]}" shape-rendering="crispEdges">${shapes}</svg>`);}
console.log('All captures completed, six existing poses decoded.');})();
