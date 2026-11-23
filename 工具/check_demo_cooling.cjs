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
  for(const index of [0,1])assert(Math.max(...centers[index])-Math.min(...centers[index])>10);
  for(const index of [2,3])assert(Math.max(...centers[index])-Math.min(...centers[index])<.01);
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
  checkBridge();checkEqualLayerClock();
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
