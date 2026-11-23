// Exercise the real panel controls while counting requests at the worker boundary.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {setTimeout: delay} = require('node:timers/promises');

const script=fs.readFileSync(path.join(__dirname,'../demo/smooth-ui.js'),'utf8');
const disk=new Map(), calls={load:0,calculate:0,export:0}, shown=[];
class Element {
  constructor(){this.attrs={};this.children=[];this.hidden=false}
  setAttribute(key,value){this.attrs[key]=String(value)}
  removeAttribute(key){delete this.attrs[key]}
  append(node){this.children.push(node)}
  replaceChildren(){this.children=[]}
  querySelector(){return this.children.find(n=>'data-layer' in n.attrs)}
  addEventListener(){}
}
function openPanel(content){
  const elements=new Map();
  function get(id){
    if(!elements.has(id))elements.set(id,new Element());
    return elements.get(id);
  }
  const window={};
  const context={
    window, Blob, setTimeout, clearTimeout, matchMedia:()=>({matches:true}),
    cancelAnimationFrame(){},ResizeObserver:class{observe(){}},
    document:{getElementById:get,createElementNS:()=>new Element(),addEventListener(){}},
    createPlateSmoothingCache(source){
      const key=id=>source.identity+'/'+id;
      return {read:async id=>disk.get(key(id)),write:async(id,v)=>{disk.set(key(id),v);return true}};
    },
    LayerSmoothingWorker:{create:()=>({
      terminate(){},
      postMessage({id,type,strength}){
        calls[type]++;
        const result=type==='export'?{bytes:new Uint8Array([strength??65])}:type==='calculate'?
          {strength,selected:strength??65,changed:true,heights:[0.2,0.4],before:[4,10],after:[5,9],afterTotal:14,beforeTotal:14}:{};
        setTimeout(()=>this.onmessage({data:{id,type:type==='export'?'file':type==='load'?'loaded':'result',...result}}),1);
      }
    })}
  };
  vm.runInNewContext(script,context);
  const panel=window.createSmoothingPanel({clear:()=>shown.push('original'),cancelPreview(){},inspect(){},
    apply:async (blob,result,isCurrent)=>{if(isCurrent())shown.push(result.strength??'auto')}});
  function selectSource(identity){panel.setSource({identity,name:identity,file:async()=>new Blob([identity])})}
  function click(id){assert.equal(!!get(id).disabled,false,id+' must be enabled');get(id).onclick()}
  function choose(value){get('strength').valueAsNumber=value;get('strength').oninput()}
  const checked=()=>get('smoothSwitch').attrs['aria-checked'];
  selectSource(content);
  return {get,click,choose,checked,selectSource};
}
async function until(predicate){
  for(let i=0;i<200;i++){if(predicate())return;await delay(5)}
  assert.fail('Timed out waiting for smoothing controls');
}
(async()=>{
  const p=openPanel('plate A');
  assert.equal(p.get('smoothAction').textContent,'计算平滑层时间');
  assert.equal(calls.calculate,0);
  p.click('smoothSwitch');
  await until(()=>p.checked()==='true');
  for(let i=0;i<3;i++){
    p.click('smoothSwitch');assert.equal(p.checked(),'false');assert.equal(shown.at(-1),'original');
    p.click('smoothSwitch');assert.equal(p.checked(),'true');assert.equal(shown.at(-1),'auto');
  }
  p.choose(55);p.choose(60);p.choose(65.5);await delay(140);
  assert.equal(calls.calculate,1);assert.equal(p.get('smoothExport').disabled,true);
  p.click('calculateStrength');await until(()=>p.checked()==='true');
  assert.equal(shown.at(-1),65.5);
  p.click('smoothAuto');await until(()=>shown.at(-1)==='auto');
  p.choose(65.5);await until(()=>shown.at(-1)===65.5);
  assert.deepEqual(calls,{load:1,calculate:2,export:2});

  const refreshed=openPanel('plate A');
  await until(()=>refreshed.checked()==='false');
  assert.equal(refreshed.get('strength').value,65.5);
  refreshed.click('smoothSwitch');assert.equal(refreshed.checked(),'true');
  assert.equal(refreshed.get('smoothExport').disabled,false);
  refreshed.click('smoothAuto');await until(()=>shown.at(-1)==='auto');
  assert.deepEqual(calls,{load:1,calculate:2,export:2});

  refreshed.selectSource('plate B');await delay(5);
  assert.equal(refreshed.get('smoothAction').textContent,'计算平滑层时间');
  refreshed.click('smoothSwitch');await until(()=>refreshed.checked()==='true');
  assert.deepEqual(calls,{load:2,calculate:3,export:3});
  console.log('Smoothing switches, stored grades and reloads reused the same results. New plate computed once.');
})().catch(error=>{console.error(error);process.exitCode=1});
