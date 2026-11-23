// Run the same parser, contacts and solver used by the demo on its bundled cube.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const demo=path.join(__dirname,'../demo');
const html=fs.readFileSync(path.join(demo,'index.html'),'utf8');
const script=id=>html.match(new RegExp('<script[^>]*id="'+id+'"[^>]*>([\\s\\S]*?)</script>'))[1];
const prepare=html.slice(html.indexOf('function prepare(d){'),html.indexOf('const definitions='));
const api=new Function(script('thermalCore')+'\n'+script('reader').split('self.onmessage=')[0]+'\n'+prepare+
  '\nreturn {parseGcode,prepare,presets,mappedSample,prepareLayerPreview,layerHeatHistory,solveRoadMechanics,mechanicsTime,bandReading,probeNormals}')();
const identity=t=>t, material=api.presets.petg;

function plate(code,seconds,heights){
  return api.prepare(api.parseGcode(code,'cooling test',null,{},0,{heights,seconds}));
}
function profile(model){
  const heat=api.layerHeatHistory(model,identity,material);
  return model.layers.map((roads,i)=>{
    for(const p of roads){
      p.mapped=new Float32Array(p.times.length*5);
      for(let j=0;j<p.times.length;j++)api.mappedSample(p,j,identity,material,p.mapped,5*j);
    }
    const net=api.prepareLayerPreview(roads,model.islands[i],heat[i],material,identity);
    api.solveRoadMechanics(roads,net,Infinity,material,true);
    return model.probeSets.map((set,side)=>{
      const {path,j,f,xy}=set[i],v=roads[path].displacement,k=2*j,[nx,ny]=api.probeNormals[side];
      return {xy,um:1000*(nx*((1-f)*v[k-2]+f*v[k])+ny*((1-f)*v[k-1]+f*v[k+1]))};
    });
  });
}
function sameWallSteps(rows){
  const steps=[];
  // Compare wall coordinates within 10 microns. Exclude the bed/startup and the end boundary; the latter is checked separately.
  for(let i=3;i<rows.length-1;i++)for(let side=0;side<4;side++){
    const a=rows[i-1][side],b=rows[i][side];
    if(Math.hypot(b.xy[0]-a.xy[0],b.xy[1]-a.xy[1])<.01)steps.push(Math.abs(b.um-a.um));
  }
  return steps;
}

function checkBridge(){
  // Two separated walls, a bridge in the third slice, then two walls again.
  const moves=['G90','M83'],heights=[.2,.4,.6,.8];
  for(const [i,z] of heights.entries()){
    moves.push('; CHANGE_LAYER','; Z_HEIGHT: '+z,'G1 Z'+z+' F600','; FEATURE: Outer wall');
    for(const x of [-10,6]){
      moves.push('G0 X'+x+' Y0 F2400','G1 X'+(x+4)+' E.2 F600','G1 Y4 E.2',
        'G1 X'+x+' E.2','G1 Y0 E.2');
    }
    if(i===2)moves.push('G0 X-9 Y0 F1800','G1 X9 E.9 F600');
  }
  const m=plate(moves.join('\n'),heights.map(()=>18),heights);
  const centers=m.islands.map(layer=>layer.map(g=>g.x));
  for(const index of [0,1,3])assert(Math.max(...centers[index])-Math.min(...centers[index])>10);
  assert(Math.max(...centers[2])-Math.min(...centers[2])<.01);
  profile(m);
  for(const [i,road] of m.layers[3].entries()){
    const g=m.islands[3][i];
    let left=Infinity,right=-Infinity;
    for(let j=0;j<road.times.length;j++){
      const dx=road.xy[2*j]-g.x;
      if(dx<-1)left=Math.min(left,road.displacement[2*j]);
      if(dx>1)right=Math.max(right,road.displacement[2*j]);
      assert(road.localShrink[j]<.012,'Colour must exclude the rigid displacement of the bridge');
    }
    assert(right>left,'Opposite pillar walls must approach one another even when the base translates');
    assert(road.contact.some((v,k)=>k%4===1&&v>.9),'The pillar still touches the layer below');
  }
}

function checkDeckPost(){
  function build(bow){
    const moves=['G90','M83'],z=[.2,.4,.6,.8];
    function wall(x,y,w,h){
      moves.push('G0 X'+x+' Y'+y+' F3000','G1 X'+(x+w)+' E1 F1200','G1 Y'+(y+h)+' E1',
        'G1 X'+x+' E1','G1 Y'+y+' E1');
    }
    for(const [i,height] of z.entries()){
      moves.push('; CHANGE_LAYER','; Z_HEIGHT: '+height,'G1 Z'+height+' F600','; FEATURE: Outer wall');
      wall(0,0,3,3);wall(bow,0,8,6);
      if(i===0)moves.push('G0 X1 Y0','G1 X'+(bow+1)+' E1 F600');
    }
    const m=plate(moves.join('\n'),z.map(()=>18),z);
    profile(m);
    const post=m.layers.at(-1)[0];
    const offsets=Array.from(post.localShrink,v=>v*1000);
    assert(Math.max(...offsets.map(Math.abs))<10,'A 3 mm post cannot acquire a hull-sized displacement');
    assert(Math.abs(post.displacement[0])>post.localShrink[0],'Geometry carries the attachment translation');
    return offsets;
  }
  const near=build(20),far=build(60);
  assert.equal(near.length,far.length);
  assert(near.every((u,i)=>Math.abs(u-far[i])<.1),'Moving the bow must not drag a separate deck post');
}

function checkEqualLayerClock(){
  const timeline=html.slice(html.indexOf('function computeTimeline(){'),html.indexOf('function refreshSamples(){'));
  // Deliberately choose different within-layer visit fractions.
  const data={layers:[[],[],[]],starts:[0,6,29],duration:[6,23,7],visit:[1,26,30],total:36};
  const clocks=new Function('data','scenario','delayLayer',
    'let previewPlan,mean,endPrint,endTime,previewDurations,previewLayerStarts,settled=[];'+
    'const refreshSamples=()=>{},upper=(list,t)=>list.filter(x=>x<=t).length;'+
    timeline+';computeTimeline();return {simTime,originalTime};');
  for(const [scenario,expected] of [['equal',[12,12,12]],['delay',[12,24,12]],['raw',data.duration]]){
    const {simTime,originalTime}=clocks(data,scenario,1);
    for(const [i,start] of data.starts.entries())assert.equal(simTime(start+data.duration[i])-simTime(start),expected[i]);
    for(const t of [-1,0,5.5,6,14,28.9,29,35,36,40])assert(Math.abs(originalTime(simTime(t))-t)<1e-8);
  }
}

// Compare the last real layer against the same slice followed by a literal copy
// of its decoded G-code roads. The extra reference slice is test-only.
function checkLastLayer(code,heights,seconds,retime=false){
  const decode=()=>api.parseGcode(code,'terminal test',null,{},0,{heights,seconds});
  const physical=decode(),explicit=decode(),last=heights.length-1;
  const rise=last?heights[last]-heights[last-1]:heights[0];
  explicit.layers.push(explicit.layers[last].map(p=>({...p})));
  explicit.z.push(heights[last]+rise);explicit.duration.push(seconds[last]);
  const real=api.prepare(physical),reference=api.prepare(explicit);
  assert.deepEqual(real.z,heights);
  assert.deepEqual(real.duration,seconds);
  assert.equal(real.total,seconds.reduce((a,b)=>a+b,0));
  assert.equal(real.counts[last],reference.counts[last]);
  const start=real.starts[last],end=start+seconds[last];
  const map=retime?t=>t<=start?t:t<=end?start+(t-start)*.55:start+(end-start)*.55+(t-end):identity;
  // On an explicit next layer, the counterfactual clock repeats the retimed pass.
  const nextMap=t=>t<=end?map(t):map(end)+map(start+(t-end))-map(start);
  function solve(m,i,map){
    const heat=api.layerHeatHistory(m,map,material);
    const paths=m.layers[i];
    for(const road of paths){
      road.mapped=new Float32Array(5*road.times.length);
      for(let j=0;j<road.times.length;j++)api.mappedSample(road,j,map,material,road.mapped,j*5);
    }
    return api.prepareLayerPreview(paths,m.islands[i],heat[i],material,map);
  }
  const net=solve(real,last,map),expected=solve(reference,last,nextMap);
  assert(real.layers[last].every(p=>p.contact.every((v,k)=>k%4!==3||v===0)));
  for(const [i,road] of real.layers[last].entries()){
    const ref=reference.layers[last][i];
    for(let j=0;j<road.times.length;j++){
      const k=5*j;
      assert(Math.abs(road.mapped[k+1]-ref.mapped[k+1])<.002 ||
        road.mapped[k+1]===ref.mapped[k+1],'Local contact time must match the first covering road');
      assert(Math.abs(road.mapped[k+3]-ref.mapped[k+3])<1e-5);
    }
  }
  assert.equal(net.curve.final,expected.curve.final);
  const lastBirth=real.layers[last].reduce((t,p)=>Math.max(t,p.mapped[5*(p.times.length-1)]),0);
  assert.equal(net.playbackEnd,lastBirth+12*Math.max(material.tau,material.weldTime));
  assert.equal(api.mechanicsTime(net,lastBirth),lastBirth);
  assert.equal(api.mechanicsTime(net,net.playbackEnd),Infinity);
  const mid=(lastBirth+net.playbackEnd)/2;
  assert(Math.abs(api.mechanicsTime(net,mid)-(lastBirth+net.settled)/2)<1e-6);
  for(const t of [map(end)-.1,map(end)+2,Infinity]){
    api.solveRoadMechanics(real.layers[last],net,t,material);
    api.solveRoadMechanics(reference.layers[last],expected,t,material);
    for(let k=0;k<net.displacement.length;k++)assert(Math.abs(net.displacement[k]-expected.displacement[k])<1e-6);
  }
  if(last<2)return;
  const lower=solve(real,last-1,map);
  api.solveRoadMechanics(real.layers[last-1],lower,Infinity,material);
  return Math.abs(api.bandReading(real.wallPairs[last][0],lower.displacement,net.displacement).step);
}

function checkPlateToolChanges(){
  const config=['; filament_nozzle_map = 0,0','; machine_load_filament_time = 26',
    '; machine_unload_filament_time = 31','; machine_max_speed_e = 30',';VT0','G90','M83'];
  function wall(object,x){
    return ['; start printing object, unique label id: '+object,'; FEATURE: Outer wall',
      'G0 X'+x+' F600','G1 X'+(x+5)+' E1 F300','; stop printing object'];
  }
  const swap=['; CP TOOLCHANGE START','M620 S1A','T1',';VG1 E4 F240','; VFLUSH_START',
    ';VG1 E8 F240','SYNC T10','; VFLUSH_END','M622 J1','SYNC T90','T0',';VG1 E50 F60',
    'M623','M621 S1A','; CP TOOLCHANGE END'];
  const heights=[.2,.4,.6],seconds=[15,110,20];
  const code=[...config,...heights.flatMap((z,i)=>['; CHANGE_LAYER','; Z_HEIGHT: '+z,'G0 Z'+z+' F600',
    ...(i<2?wall(1,0):[]),...(i===1?swap:[]),...wall(2,12)])].join('\n');
  const read=(id,input=code)=>api.prepare(api.parseGcode(input,'two parts',id,{},0,{heights,seconds}));
  const a=read('1'),b=read('2');
  assert.deepEqual(a.changes,{count:1,load:26,unload:31,swap:0,prime:1,purge:2,sync:10,incomplete:false});
  assert.deepEqual(b.changes,a.changes);
  assert(a.layerTiming&&b.layerTiming);
  assert.deepEqual(b.duration,seconds);
  assert.equal(a.total,b.total);
  assert.equal(a.lastPeriod,110,'A shorter part must not inherit the rest of the plate as its last cooling period');
  for(const model of [a,b]){
    // Calibration stretches movement, but the stops must remain 31, 26, 1, 2 and 10 seconds.
    const waits=[];
    for(let k=0;k<model.motion.length;k+=9)if(model.motion[k+8]===2)waits.push(model.motion[k+1]-model.motion[k]);
    assert.deepEqual(waits.map(t=>Math.round(1000*t)/1000),[31,26,1,2,10]);
  }
  const ha=api.layerHeatHistory(a,identity,material),hb=api.layerHeatHistory(b,identity,material);
  assert(hb[0].span-ha[0].span>70,'Only the second object crosses the swap before its next wall');
  assert.equal(api.layerHeatHistory(b,identity,material,false)[0].span,seconds[0]);
  assert(read('2',code.replace('; machine_load_filament_time = 26','')).changes.incomplete);
}

async function run(){
  checkBridge();checkDeckPost();checkEqualLayerClock();checkPlateToolChanges();
  require(path.join(demo,'smooth-example.js'));require(path.join(demo,'voron-gcode.js'));
  const code=Buffer.from(await voronGcodeBytes()).toString('utf8');
  const {before,heights}=layerSmoothingExample.result;
  const rawTop=checkLastLayer(code,heights,before);
  const smoothed=require('node:zlib').gunzipSync(Buffer.from(layerSmoothingExample.gzip,'base64')).toString('utf8');
  const smoothTop=checkLastLayer(smoothed,heights,layerSmoothingExample.result.after);
  const pass=(z)=>['; CHANGE_LAYER','; Z_HEIGHT: '+z,'G0 Z'+z+' F600','; FEATURE: Outer wall','G0 X0 Y0 F3000',
    'G1 X8 E1 F600','G1 Y4 E1','G1 X0 E1','G1 Y0 E1','G0 X0 Y0 F3000','G1 X8 E1 F600'].join('\n');
  for(const heights of [[.25],[.2,.45,.6]]){
    const gcode=['G90','M83',...heights.map(pass)].join('\n');
    checkLastLayer(gcode,heights,heights.map((_,i)=>7+4*i),true);
  }
  console.log('Cube top matches an explicit repeat: '+rawTop.toFixed(3)+' / '+smoothTop.toFixed(3)+' µm before/after smoothing.');
  const equal=sameWallSteps(profile(plate(code,heights.map(()=>18),heights)));
  const raw=sameWallSteps(profile(plate(code,before,heights)));
  assert(equal.length>450&&raw.length===equal.length);
  assert(Math.max(...equal)<.8,'Constant layer time should also flatten the split sidewalls');
  assert(Math.max(...raw)>4,'Real layer time changes must still affect the wall');
  console.log('Verified independent bodies, a later bridge and '+equal.length+' same-position pairs on the cube.');
}
run().catch(e=>{console.error(e);process.exitCode=1});
