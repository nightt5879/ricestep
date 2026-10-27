// Compile the generated raster art into the primitive format supported by Mini3.
// This is an asset encoder; the original image and its generation prompt are kept.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { PNG } = require('pngjs');
const { encode } = require('./rectangle-encoder.cjs');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'assets/generated/whale-girl-sheet-v1.png'));
const image = PNG.sync.read(source);
const palette = [0x17233e, 0x355798, 0x73b0de, 0xc4e0f2, 0xffead6, 0xf9fbff, 0xd68f98, 0xe3ba60];
const regions = [[0,654],[654,1287],[1287,1881]];
function quantize(frame, width, height) {
  let left=frame[1], right=frame[0], top=image.height, bottom=0;
  for(let y=0;y<image.height;y++) for(let x=frame[0];x<frame[1];x++) {
    if(image.data[(y*image.width+x)*4+3]>200) {
      left=Math.min(left,x);right=Math.max(right,x);top=Math.min(top,y);bottom=Math.max(bottom,y);
    }
  }
  const pixels = [];
  for(let y=0;y<height;y++) for(let x=0;x<width;x++) {
    const sx=Math.min(right,Math.floor(left+(x+.5)*(right-left+1)/width));
    const sy=Math.min(bottom,Math.floor(top+(y+.5)*(bottom-top+1)/height));
    const i=(sy*image.width+sx)*4;
    if(image.data[i+3]<180) {pixels.push(-1);continue;}
    let best=0, distance=Infinity;
    palette.forEach((p,n)=>{
      const dr=image.data[i]-(p>>16),dg=image.data[i+1]-((p>>8)&255),db=image.data[i+2]-(p&255);
      const d=dr*dr+dg*dg+db*db;
      if(d<distance){distance=d;best=n;}
    });
    pixels.push(best);
  }
  const rectangles=encode(pixels,width,height,palette.length).map(([x,y,w,h,c])=>[x,y,w,h,c+1]);
  return {width,height,rectangles};
}
function compile(budget, sizes) {
  return regions.map(region=>{
    for(const [w,h] of sizes) {
      const frame=quantize(region,w,h);
      if(frame.rectangles.length<=budget)return frame;
    }
    throw Error('Character exceeds primitive budget');
  });
}
const high = compile(232, [[32,40],[28,36],[26,34],[24,32],[22,30],[20,28]]);
const low = compile(102, [[20,28],[18,26],[16,24],[14,22],[12,20]]);
// Five printable bytes per rectangle avoid hundreds of Lua table allocations at boot.
const table=frames=>'{'+frames.map(f=>'{'+f.width+','+f.height+',[=['+f.rectangles.flat().map(n=>String.fromCharCode(n+33)).join('')+']=]}').join(',')+'}';
const lua = `-- Generated from imagegen asset. Do not hand-edit; run npm run assets.\n-- Source SHA256: ${crypto.createHash('sha256').update(source).digest('hex')}\nlocal character_palette={${palette.map(p=>'0x'+p.toString(16)).join(',')}}\ncharacter_palette[0]=0xFFF5E5\nlocal character_high=${table(high)}\nlocal character_low=${table(low)}\nfunction draw_character(pose,x,y,scale)\n local frames=app.api_version>=5 and character_high or character_low\n local frame=frames[pose] or frames[1]\n for i=1,#frame[3],5 do\n  local rx,ry,rw,rh,c=string.byte(frame[3],i,i+4)\n  rx,ry,rw,rh,c=rx-33,ry-33,rw-33,rh-33,c-33\n  local x1=x+math.floor(rx*32*scale/frame[1])\n  local y1=y+math.floor(ry*40*scale/frame[2])\n  local x2=x+math.floor((rx+rw)*32*scale/frame[1])\n  local y2=y+math.floor((ry+rh)*40*scale/frame[2])\n  screen.rect(x1,y1,x2-x1,y2-y1,character_palette[c])\n end\nend\n`;
fs.writeFileSync(path.join(root,'app/character.lua'),lua);
const report={sourceSha256:crypto.createHash('sha256').update(source).digest('hex'), high:high.map(f=>({grid:[f.width,f.height],shapes:f.rectangles.length})),low:low.map(f=>({grid:[f.width,f.height],shapes:f.rectangles.length}))};
fs.writeFileSync(path.join(root,'assets/generated/character-build.json'),JSON.stringify(report,null,2)+'\n');
// A software-rendered preview of the encoded primitives, not the source artwork.
const preview=new PNG({width:720,height:240});
for(let i=0;i<preview.data.length;i+=4){preview.data[i]=255;preview.data[i+1]=246;preview.data[i+2]=227;preview.data[i+3]=255;}
high.forEach((f,n)=>f.rectangles.forEach(([x,y,w,h,c])=>{
  const x1=n*240+72+Math.floor(x*96/f.width),x2=n*240+72+Math.floor((x+w)*96/f.width);
  const y1=50+Math.floor(y*120/f.height),y2=50+Math.floor((y+h)*120/f.height),color=c===0?0xFFF5E5:palette[c-1];
  for(let yy=y1;yy<y2;yy++)for(let xx=x1;xx<x2;xx++){const i=(yy*720+xx)*4;preview.data[i]=color>>16;preview.data[i+1]=(color>>8)&255;preview.data[i+2]=color&255;}
}));
fs.mkdirSync(path.join(root,'.cache'),{recursive:true});
fs.writeFileSync(path.join(root,'.cache/character-preview.png'),PNG.sync.write(preview));
console.log(JSON.stringify(report,null,2));
