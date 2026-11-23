// Exercise the production G-code reader, nozzle clock and reversible displacement
// snapshot on a small plate whose geometry is unchanged by the feed-rate edit.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../demo/index.html'),'utf8');
const ctx=vm.createContext({self:{},console,TextDecoder});
function section(first,last){
  const start=html.indexOf(first),end=html.indexOf(last,start+first.length);
  assert(start>=0&&end>start);return html.slice(start,end);
}
for(const id of ['thermalCore','reader']){
  const script=html.match(new RegExp('<script[^>]*id="'+id+'"[^>]*>([\\s\\S]*?)</script>'));
  vm.runInContext(script[1],ctx);
}
vm.runInContext(section('const clamp=','function builtin()')+
  section('function prepare(d){','const presets=')+
  section('function nozzleTrack(d){','function clockText(')+
  section('function correspondingPrintTime(','function swapPreview('),ctx);
const moves=['G90','M83'];
for(const z of [0.2,0.4,0.6]){
  moves.push('; CHANGE_LAYER','; Z_HEIGHT: '+z,'G1 Z'+z+' F600','G1 X1 Y1 F1800',
    '; FEATURE: Outer wall','G1 X9 E1 F600','G1 Y8 E1','G1 X1 E1',
    'G0 X2 Y2 F2400','; FEATURE: Sparse infill','G1 X8 Y5 E1 F900','G0 X5 Y1 F2400');
}
const code=moves.join('\n'),z=[0.2,0.4,0.6];
function model(code,seconds){
  return ctx.prepare(ctx.parseGcode(code,'test',null,{},60,{heights:z,seconds}));
}
const before=model(code,[12,28,20]),after=model(code.replaceAll('E1 F900','E1 F450'),[11,18,25]);
assert.equal(after.layerTiming,true);assert.deepEqual(Array.from(after.duration),[11,18,25]);
assert.equal(ctx.sameRoadGeometry(before,after),true);
const source=ctx.nozzleTrack(before),target=ctx.nozzleTrack(after);
for(let i=0;i<source.order.length;i++){
  const span=ctx.nozzleSpan(source,i),mid=(span.start+span.end)/2;
  const mapped=ctx.correspondingPrintTime(before,after,mid);
  const a=ctx.nozzlePosition(before,span,mid).tip;
  const b=ctx.nozzlePosition(after,ctx.nozzleSpan(target,i),mapped).tip;
  for(let axis=0;axis<3;axis++)assert(Math.abs(a[axis]-b[axis])<0.0001);
}
assert.equal(model(code,[9]).layerTiming,false);
const start=[{displacement:Float32Array.of(1,4)}];
const end=[{displacement:Float32Array.of(5,12)}];
const forward=ctx.snapshotOffsets(start,null),reverse=ctx.snapshotOffsets(end,{from:forward,mix:.25});
assert.deepEqual([...reverse[0]],[2,6]);
assert.deepEqual([...ctx.snapshotOffsets(start,{from:reverse,mix:0})[0]],[2,6]);
assert.deepEqual([...ctx.snapshotOffsets(start,{from:reverse,mix:1})[0]],[1,4]);
assert.deepEqual([...end[0].displacement],[5,12]);
console.log('Per-layer clocks, nozzle coordinates and interrupted geometry transitions verified.');
