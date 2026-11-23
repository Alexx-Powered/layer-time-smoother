// Run the same parser, contacts and solver used by the demo on its bundled cube.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const demo=path.join(__dirname,'../demo');
const html=fs.readFileSync(path.join(demo,'index.html'),'utf8');
const script=id=>html.match(new RegExp('<script[^>]*id="'+id+'"[^>]*>([\\s\\S]*?)</script>'))[1];
const prepare=html.slice(html.indexOf('function prepare(d){'),html.indexOf('const definitions='));
const api=new Function(script('thermalCore')+'\n'+script('reader').split('self.onmessage=')[0]+'\n'+prepare+
  '\nreturn {parseGcode,prepare,presets,mappedSample,prepareLayerPreview,layerHeatHistory,solveRoadMechanics,probeNormals}')();
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
  // Compare wall coordinates within 10 microns. Exclude bed/startup and free top.
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

async function run(){
  checkBridge();checkDeckPost();checkEqualLayerClock();
  require(path.join(demo,'smooth-example.js'));require(path.join(demo,'voron-gcode.js'));
  const code=Buffer.from(await voronGcodeBytes()).toString('utf8');
  const {before,heights}=layerSmoothingExample.result;
  const equal=sameWallSteps(profile(plate(code,heights.map(()=>18),heights)));
  const raw=sameWallSteps(profile(plate(code,before,heights)));
  assert(equal.length>450&&raw.length===equal.length);
  assert(Math.max(...equal)<.8,'Constant layer time should also flatten the split sidewalls');
  assert(Math.max(...raw)>4,'Real layer time changes must still affect the wall');
  console.log('Verified independent bodies, a later bridge and '+equal.length+' same-position pairs on the cube.');
}
run().catch(e=>{console.error(e);process.exitCode=1});
