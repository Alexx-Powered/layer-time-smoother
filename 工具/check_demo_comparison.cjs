// Exercise the production G-code reader, nozzle clock and reversible displacement
// snapshot on a small plate whose geometry is unchanged by the feed-rate edit.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../demo/index.html'),'utf8').replace(/\r\n/g,'\n');
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
// A legend must keep the same units while a different point in time is shown.
vm.runInContext(section('function originalColorRange(', 'function frame(){')+
  'const legendNodes={};function $(id){return legendNodes[id]??={}}',ctx);
const petg={hot:255,air:25,tg:79.4,alpha:78,tau:6.5,c:35,beta:75,bed:65,
  reheat:18,support:40,weldTime:4};
const range=ctx.shrinkColorRange(before,petg);
assert(range.min>=0&&range.max>range.min);
assert.strictEqual(ctx.shrinkColorRange(before,petg),range);
assert.equal(before.layers[0][0].displacement,undefined);
const zeroRange=ctx.shrinkColorRange(before,{...petg,alpha:0});
assert.equal(zeroRange.min,0);assert(zeroRange.max>=.5);
assert.deepEqual(ctx.shrinkColorRange(before,petg),range);
for(const model of [before,after]){
  assert(model.wallPairs.slice(0,2).every(layer=>layer.every(side=>side.length===0)));
  assert(model.wallPairs[2][0].length>0,'First plotted step compares layers three and two');
}
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
const localSnapshot=ctx.snapshotOffsets([{localShrink:Float32Array.of(2,4)}],
  {fromLocal:[Float32Array.of(0,2)],mix:.5},'localShrink','fromLocal');
assert.deepEqual([...localSnapshot[0]],[1,3]);
console.log('Per-layer clocks, nozzle coordinates and interrupted geometry transitions verified.');

// A dense layer used to switch to pixel-wide lines. Build the production bead
// geometry and verify that a corner, a width/height change and a travel gap survive.
vm.runInContext(section('var THREE=',';(()=>{\nconst sides=')+
  section('const sides=','const palette=')+
  section('function beadGeometry(','function makeViewport(')+
  section('  const stock=new Map();','  const bedCanvas=')+
  section('  function thermalMaterial(','  function refreshThermalAttribute(')+
  section('  function writeOffsets(','  function schemeActive(')+
  section('  function dispose(m){','  function schedule()')+
  'let model,comparison,flatLayers,lastBedView;var meshes=[],scene=new THREE.Scene();',ctx);
const denseXY=new Float32Array(2*90001);
for(let i=0;i<90001;i++)denseXY[2*i]=i/1000;
const paths=[
  {xy:denseXY,times:new Float32Array(90001),width:.42,h:.2,nodeBase:0},
  {xy:[20,2,21,2,21,3],times:[0,1,2],width:.5,h:0,nodeBase:90001,filament:1}
];
ctx.renderer={getContext:()=>({DYNAMIC_DRAW:35048})};
ctx.setModel({layers:[paths],z:[12],thickness:[.3],filamentColors:['#cc0044','#438bec'],sourceCount:90002,segments:90002},true);
const mesh=ctx.meshes[0],g=mesh.geometry,n=g.instanceCount;
assert(mesh.isMesh&&mesh.material.isMeshStandardMaterial&&mesh.position.z===12);
assert(g.isInstancedBufferGeometry&&n===90002);
assert.equal(g.getAttribute('spoolRGB').count,n);
assert.notEqual(g.getAttribute('spoolRGB').getX(0),g.getAttribute('spoolRGB').getX(n-1));
assert.equal(g.getAttribute('position').count,16);
assert.equal(g.index.count,48);
assert.deepEqual([...g.getAttribute('roadEnds').array.slice(-8)],[20,2,21,2,21,2,21,3]);
const perps=g.getAttribute('roadPerps').array;
assert.deepEqual([...perps.slice(-6,-4)],[...perps.slice(-4,-2)]);
assert(Math.abs(g.getAttribute('beadSize').getX(0)-.42)<1e-6);
assert(Math.abs(g.getAttribute('beadSize').getY(0)-.2)<1e-6);
assert(Math.abs(g.getAttribute('beadSize').getY(n-1)-.3)<1e-6);
// During playback the draw count may be smaller than the layer's full buffer.
g.instanceCount=3;
const offsets=new Float32Array(2*90004);offsets.set([1,2,3,4,5,6],2*90001);
ctx.writeOffsets({geometry:g},paths,'wallDisplacement',offsets);
assert.deepEqual([...g.getAttribute('wallDisplacement').array.slice(-8)],[1,2,3,4,3,4,5,6]);
const shrink=new Float32Array(90004);shrink.set([2,4,3],90001);
ctx.writeOffsets({geometry:g},paths,'localShrink',shrink,1);
assert.deepEqual([...g.getAttribute('localShrink').array.slice(-4)],[2,4,4,3]);
console.log('Dense bead dimensions, corner joins, travel gaps and thermal endpoints verified.');

// A layer ridge away from the centreline, a nominal slope and a detached wall.
// The same packed coordinates go to the worker, the rendered pin and both traces.
function exterior(xy,base=0,height=.2){
  return {xy,times:Array(xy.length/2).fill(0),nodeBase:base,rank:2,h:height,width:.42};
}
const outer=[0,0,10,0,20,0,30,0,40,0];
const lowWalls=[exterior([0,4,40,4]),exterior(outer,2)];
const highWalls=[exterior([0,4.1,40,4.1],0,.1),exterior(outer.map((v,i)=>i%2?v+.1:v),2,.1)];
const paired=ctx.pairedWalls(ctx.wallSection(lowWalls),ctx.wallSection(highWalls),.1);
const shared=Float32Array.from({length:14},(_,i)=>i%2?.017:.023),ridge=shared.slice();
assert.equal(ctx.bandReading(paired[0],shared,shared).step,0,'Uniform translation cancels on a sloped nominal surface');
ridge[13]-=.04;ridge[11]-=.04;
const peak=ctx.bandReading(paired[0],shared,ridge);
assert(Math.abs(peak.step-40)<.001);
assert.equal(peak.hit.path,1);assert(peak.hit.j>=3);
assert.equal(ctx.bandReading(paired[1],shared,ridge).step,0,'Rear view samples its own wall');
assert.equal(ctx.bandReading(paired[0],shared,ridge,Infinity,1),null,'Unprinted front wall');
assert.equal(ctx.bandReading(paired[0],shared,ridge,Infinity,3).step,0,'Unprinted end of the front wall');
assert(Math.abs(ctx.bandReading(paired[0],shared,ridge,Infinity,Infinity,shared,shared,.25).step-10)<.001);
const floating=ctx.pairedWalls(ctx.wallSection(lowWalls),ctx.wallSection(highWalls),.3);
assert.equal(ctx.bandReading(floating[0],shared,ridge),null,'Vertical air gap');
const distant=ctx.pairedWalls(ctx.wallSection([exterior(outer)]),
  ctx.wallSection([exterior(outer.map((v,i)=>i%2?v+5:v))]),.2);
assert.equal(ctx.bandReading(distant[0],shared,ridge),null,'Different surfaces across an opening');
// Exercise the real canvas labels for uneven Z intervals and for a single layer.
const labels=[],pen={measureText:t=>({width:String(t).length*6}),fillText:(t,x,y)=>labels.push([t,x,y])};
const canvas={getContext:()=>new Proxy(pen,{get:(o,k)=>o[k]||(()=>{})})};
const chartCtx=vm.createContext({data:{z:[.18,.29,.62,.98,1.27]},selected:2,clamp:ctx.clamp||((v,a,b)=>Math.max(a,Math.min(v,b))),
  upper:(z,v)=>z.filter(h=>h<=v).length,$:id=>id==='profile'?canvas:{clientWidth:200,clientHeight:420},
  devicePixelRatio:1,canSimulate:()=>true,chartAxis:{}});
vm.runInContext(section('function selectedNear(z)','function showControls()')+
  section('function drawProfile(','function pause()'),chartCtx);
function checkTick(height,layer){
  const a=labels.find(([label])=>label===height),b=labels.find(([label,x])=>label===layer&&x>150);
  assert(a&&b&&a[2]===b[2]);
}
chartCtx.drawProfile([NaN,1,2,1,0],[1,1,1,1,1],2,null);
checkTick('0.62','3');checkTick('1.27','5');checkTick('0.18','1');
labels.length=0;chartCtx.data.z=[.28];chartCtx.selected=0;
chartCtx.drawProfile([NaN],[1],0,null);checkTick('0.28','1');
console.log('Visible layer steps, transitions, air gaps, playback and dual height/layer scales verified.');
