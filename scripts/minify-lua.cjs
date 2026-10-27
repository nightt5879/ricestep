'use strict';
const fs = require('node:fs');
// Only remove comments and collapse whitespace outside strings; never rename code.
function minifyLua(source) {
  let result='', i=0;
  const longAt = index => /^\[(=*)\[/.exec(source.slice(index));
  while(i<source.length) {
    const c=source[i];
    if(c==='"'||c==="'") {
      const start=i++;
      while(i<source.length) {if(source[i]==='\\'){i+=2;continue;}if(source[i++]===c)break;}
      result+=source.slice(start,i);continue;
    }
    if(source.startsWith('--',i)) {
      const block=longAt(i+2);
      if(block) {const close=']'+block[1]+']';const end=source.indexOf(close,i+2+block[0].length);if(end<0)throw Error('Unclosed Lua comment');i=end+close.length;}
      else {const end=source.indexOf('\n',i);i=end<0?source.length:end+1;}
      if(result&&!result.endsWith(' '))result+=' ';
      continue;
    }
    const block=c==='['&&longAt(i);
    if(block) {const close=']'+block[1]+']';const end=source.indexOf(close,i+block[0].length);if(end<0)throw Error('Unclosed Lua long string');result+=source.slice(i,end+close.length);i=end+close.length;continue;}
    if(/\s/.test(c)) {if(result&&!result.endsWith(' '))result+=' ';i++;continue;}
    result+=c;i++;
  }
  return result.trim()+'\n';
}
module.exports={minifyLua};
if(require.main===module) {const file=process.argv[2];if(!file)throw Error('Lua file required');fs.writeFileSync(file,minifyLua(fs.readFileSync(file,'utf8')));}
