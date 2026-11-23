/* Compare and export exactly the selection displayed in the panel. */
window.createSmoothingPanel = function ({apply, clear, cancelPreview, inspect}) {
  const el = id => document.getElementById(id);
  const panel = el('smoothPanel'), slider = el('strength'), menu = el('exportMenu');
  const svg = el('timeCurve'), status = el('smoothStatus');
  let plate, enabled=false, selection=0, strength=null, current=null, chart=null, layer=0, timer;
  let output=null, displayRevision=0, animation=0, painted=[], opacity=0;
  const keyFor = strength => strength===null?'auto':String(strength);
  const reducedMotion=matchMedia('(prefers-reduced-motion: reduce)');
  function exportReady(available) {
    el('smoothExport').disabled = el('exportMenuButton').disabled = !available;
    if (!available) closeMenu();
  }
  function closeMenu() {
    menu.hidden = true;
    el('exportMenuButton').setAttribute('aria-expanded','false');
  }
  function formatTime(s) {
    const secs = Math.round(Math.abs(s));
    return secs >= 3600 ? Math.floor(secs/3600)+'时 '+Math.floor(secs/60)%60+'分 '+secs%60+'秒' :
      (secs>=60 ? Math.floor(secs/60)+'分 ' : '')+secs%60+'秒';
  }
  function setStatus(text) {status.textContent = text}
  function selectLayer(height) {
    if (!chart?.heights.length) return;
    const heights = chart.heights;
    let lo = 0, hi = heights.length;
    while (lo < hi) {const mid = (lo+hi)>>>1; if (heights[mid]<height) lo=mid+1; else hi=mid}
    const index = Math.min(lo, heights.length-1);
    const next = index>0 && Math.abs(heights[index-1]-height)<Math.abs(heights[index]-height) ? index-1 : index;
    if (next!==layer) {layer=next; markLayer()}
  }
  let chartX = () => 0;
  function markLayer() {
    const cursor = svg.querySelector('line[data-layer]');
    if (!chart || !cursor) return;
    cursor.setAttribute('x1',chartX(layer));cursor.setAttribute('x2',chartX(layer));
    el('layerReadout').textContent='第 '+(layer+1)+' 层　原始 '+chart.before[layer].toFixed(1)+' 秒 · 平滑 '+chart.after[layer].toFixed(1)+' 秒';
  }
  function drawChart(animate=false) {
    cancelAnimationFrame(animation);svg.replaceChildren();
    if (!chart?.before.length) return;
    const width = Math.max(250,svg.clientWidth || 680),height=160,left=43,right=width-14,top=17,bottom=133;
    const count=chart.before.length;
    let max=1;
    for (let i=0;i<count;i++) max=Math.max(max,chart.before[i],chart.after[i]);
    const magnitude=10**Math.floor(Math.log10(max/3)),step=Math.ceil(max/3/magnitude)*magnitude;
    max=Math.ceil(max/step)*step;
    chartX = i => left+(right-left)*i/Math.max(1,count-1);
    const y = s => bottom-(bottom-top)*s/max;
    svg.setAttribute('viewBox','0 0 '+width+' '+height);
    function add(tag,attrs,label) {
      const node=document.createElementNS('http://www.w3.org/2000/svg',tag);
      for(const [key,value] of Object.entries(attrs)) node.setAttribute(key,value);
      if(label!==undefined) node.textContent=label;
      svg.append(node);return node;
    }
    for(let v=0;v<=max+.0001;v+=step){
      add('line',{x1:left,x2:right,y1:y(v),y2:y(v),stroke:'#4b443b'});
      add('text',{x:left-6,y:y(v)+3,'text-anchor':'end'},Number(v.toFixed(1))+'s');
    }
    add('text',{x:left,y:151},'第 1 层');
    if(count>1) add('text',{x:right,y:151,'text-anchor':'end'},'第 '+count+' 层');
    const points=values=>values.map((s,i)=>chartX(i).toFixed(2)+','+y(s).toFixed(2)).join(' ');
    const target=enabled?chart.after:chart.before,alpha=enabled?1:0;
    const line=add('polyline',{fill:'none',stroke:'#ffac53','stroke-width':2});
    add('polyline',{points:points(chart.before),fill:'none',stroke:'#beb7ae','stroke-width':1.4,'stroke-dasharray':'5 4'});
    const dot=count===1?add('circle',{cx:chartX(0),r:2.5,fill:'#ffac53'}):null;
    if(count===1)add('circle',{cx:chartX(0),cy:y(chart.before[0]),r:1.5,fill:'#beb7ae'});
    function paint(values,a){
      painted=values;opacity=a;line.setAttribute('points',points(values));line.setAttribute('opacity',a);
      if(dot){dot.setAttribute('cy',y(values[0]));dot.setAttribute('opacity',a)}
    }
    if(animate===true&&!reducedMotion.matches&&!panel.hidden){
      const from=painted.length===count?painted:chart.before,initial=opacity;
      let start;paint(from,initial);
      function step(now){
        start??=now;
        const t=Math.min(1,(now-start)/440),f=t*t*(3-2*t);
        paint(t===1?target:target.map((v,i)=>from[i]+(v-from[i])*f),initial+(alpha-initial)*f);
        if(t<1)animation=requestAnimationFrame(step);
      }
      animation=requestAnimationFrame(step);
    }else paint(target,alpha);
    add('line',{'data-layer':'',y1:top,y2:bottom,stroke:'#ecbf79','stroke-opacity':.55});
    layer=Math.min(layer,count-1);markLayer();
  }
  svg.addEventListener('click',event=>{
    if(!chart)return;
    const x=event.clientX-svg.getBoundingClientRect().left;
    const i=Math.min(chart.heights.length-1,Math.max(0,Math.round((x-chartX(0))/(chartX(1)-chartX(0))||0)));
    inspect(chart.heights[i]);
  });
  let lastChartWidth=0;
  new ResizeObserver(()=>{
    if(svg.clientWidth!==lastChartWidth){lastChartWidth=svg.clientWidth;drawChart()}
  }).observe(svg);
  function display(entry,animate){
    chart=entry.result;drawChart(animate);
    const delta=chart.afterTotal-chart.beforeTotal;
    el('smoothTotal').textContent=formatTime(chart.afterTotal);
    const sign=delta<-.05?'−':delta>.05?'+':'';
    el('smoothCost').textContent=sign+formatTime(delta)+'（'+sign+(chart.beforeTotal?Math.abs(100*delta/chart.beforeTotal):0).toFixed(1)+'%）';
  }
  function controls(){
    const entry=plate.entries.get(strength),busy=plate.jobs.has(strength);
    const button=el('smoothSwitch');
    if(entry){
      button.setAttribute('role','switch');
      button.setAttribute('aria-checked',String(enabled&&current===entry));
    }else{
      button.removeAttribute('role');button.removeAttribute('aria-checked');
    }
    button.disabled=!entry&&busy;
    el('smoothAction').textContent=entry?'层时间平滑':busy?'正在计算…':'计算平滑层时间';
    el('calculateStrength').hidden=!!entry;
    el('calculateStrength').disabled=busy;
    el('smoothAuto').setAttribute('aria-pressed',String(strength===null));
    const selected=entry?.result.selected;
    el('strengthValue').textContent=strength!==null?String(strength):!entry?'自动':selected==null?'自动 · 保留原速':'自动 · '+selected;
    if(strength===null&&selected!=null)slider.value=selected;
    exportReady(!!entry&&entry===current&&!!output);
  }
  // A single worker owns this plate. Serialize calculation + export so the
  // Python solver's most recent result always matches the bytes being saved.
  function makePlate(source){
    let input,worker,ready,serial=Promise.resolve(),requestId=0,closed=false;
    const requests=new Map();
    const file=()=>input ||= Promise.resolve().then(source.file);
    const store=createPlateSmoothingCache({...source,file});
    const state={...source,file,store,entries:new Map(),jobs:new Map()};
    function retain(value,entry){
      state.entries.set(value,entry);
      for(const [key,older] of state.entries){
        if(state.entries.size<=3)break;
        if(older.saved&&key!==value&&key!==strength&&older!==current)state.entries.delete(key);
      }
    }
    function closeWorker(){
      if(worker)worker.terminate();worker=null;ready=null;
      for(const req of requests.values())req.reject(Error('当前切片已切换。'));
      requests.clear();
    }
    state.close=()=>{closed=true;closeWorker()};
    function request(type,body,transfer=[]){
      if(closed||!worker)return Promise.reject(Error('当前切片已切换。'));
      const id=++requestId;
      return new Promise((resolve,reject)=>{
        requests.set(id,{resolve,reject});
        try{worker.postMessage({type,id,...body},transfer)}catch(e){requests.delete(id);reject(e)}
      });
    }
    async function start(){
      worker=LayerSmoothingWorker.create();
      worker.onmessage=({data:m})=>{
        const req=requests.get(m.id);if(!req)return;
        if(m.type==='status'||m.type==='preview')return;
        requests.delete(m.id);
        if(m.type==='error')req.reject(Error(m.message));else req.resolve(m);
      };
      worker.onerror=()=>closeWorker();
      const bytes=await (await file()).arrayBuffer();
      await request('load',{bytes},[bytes]);
    }
    state.find=async value=>{
      if(state.entries.has(value))return state.entries.get(value);
      const saved=await store.read(keyFor(value));
      if(saved&&!closed&&!state.entries.has(value))retain(value,saved);
      return state.entries.get(value);
    };
    state.solve=value=>{
      if(state.jobs.has(value))return state.jobs.get(value);
      async function work(){
        if(closed)throw Error('当前切片已切换。');
        const existing=await state.find(value);if(existing)return existing;
        let r,blob=null;
        if(source.builtin&&value===null){
          const cached=await LayerSmoothingWorker.getDemoAuto();r=cached.result;
          if(r.changed)blob=new Blob([cached.bytes],{type:'application/octet-stream'});
        }else{
          if(!ready)ready=start().catch(error=>{closeWorker();throw error});
          await ready;
          r=await request('calculate',{strength:value});
          if(r.changed){
            const reply=await request('export',{strength:value});
            blob=new Blob([reply.bytes],{type:'application/octet-stream'});
          }
        }
        const entry={result:r,blob};
        if(!closed){
          entry.saved=!!(await store.write(keyFor(value),{...entry,saved:true}));
          if(!closed)retain(value,entry);
        }
        return entry;
      }
      const job=serial.then(work).finally(()=>state.jobs.delete(value));
      serial=job.catch(()=>{});state.jobs.set(value,job);return job;
    };
    return state;
  }
  async function present(entry,on,revision){
    if(revision!==selection)return;
    const owner=plate;
    const blob=entry.blob||await owner.file();
    if(revision!==selection)return;
    const changed=current!==entry||enabled!==on;
    current=entry;output=blob;enabled=on;
    controls();
    const note=entry.saved?'结果已保存到本机。':'本次打开期间复用此结果。';
    const label=!on?'显示原始走线。'+note:entry.result.changed?'显示平滑走线。'+note:'本档保留原始进给。'+note;
    if(!changed){display(entry,false);setStatus(label);return}
    const transition=++displayRevision;
    setStatus(on?'正在准备平滑走线…':label);
    try{
      if(on)await apply(blob,entry.result,()=>transition===displayRevision&&enabled);
      else clear();
      if(owner!==plate||transition!==displayRevision)return;
      display(entry,true);
      if(selection===revision)setStatus(label);
    }catch(error){
      if(owner!==plate||transition!==displayRevision)return;
      enabled=false;clear();controls();display(entry,true);
      setStatus('平滑结果已就绪，3D 预览读取失败。可打开开关重试。'+error.message);
    }
  }
  async function calculate(){
    if(!plate||plate.jobs.has(strength))return;
    const owner=plate,value=strength,revision=++selection;
    clearTimeout(timer);panel.hidden=false;
    const task=owner.solve(value);
    controls();setStatus(value===null?'正在为当前盘自动选参…':'正在计算平滑度 '+value+'…');
    try{
      const entry=await task;if(revision!==selection)return;
      remember(owner,value);
      await present(entry,true,revision);
    }catch(error){if(revision===selection)setStatus(error.message+'　可再次点击计算。')}
    finally{if(owner===plate)controls()}
  }
  function remember(owner,value){
    if(owner.last===value)return;
    owner.last=value;void owner.store.write('last',{strength:value});
  }
  function choose(value){
    if(value===strength)return;
    strength=value;
    const revision=++selection;clearTimeout(timer);controls();
    const known=plate.entries.get(value);
    if(known){
      remember(plate,value);void present(known,enabled,revision);return;
    }
    const previous=current?.result.strength;
    const label=previous==null?'自动档':'平滑度 '+previous;
    setStatus((current?'时间数据仍为'+label+'。':'')+'点击「计算平滑层时间」应用新参数。');
    async function reuse(){
      const owner=plate;
      try{
        const entry=await (owner.jobs.get(value)||owner.find(value));
        if(revision===selection&&entry){
          remember(owner,value);
          await present(entry,enabled,revision);
        }
      }catch(error){if(revision===selection)setStatus(error.message)}
    }
    timer=setTimeout(reuse,120);
  }
  el('smoothSwitch').onclick=()=>{
    const entry=plate.entries.get(strength);
    if(!entry){void calculate();return}
    clearTimeout(timer);panel.hidden=false;
    const on=current===entry?!enabled:true;
    remember(plate,strength);void present(entry,on,++selection);
  };
  el('calculateStrength').onclick=calculate;
  slider.oninput=()=>choose(slider.valueAsNumber);
  el('smoothAuto').onclick=()=>choose(null);
  function name(){
    const suffix=strength===null?'auto':String(strength);
    return plate.name+'-smooth-'+suffix+'.gcode';
  }
  el('smoothExport').onclick=()=>{
    if(!output)return;
    const a=document.createElement('a'),url=URL.createObjectURL(output);
    a.href=url;a.download=name();a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);
  };
  el('exportMenuButton').onclick=()=>{
    menu.hidden=!menu.hidden;el('exportMenuButton').setAttribute('aria-expanded',String(!menu.hidden));
    if(!menu.hidden)menu.querySelector('button').focus();
  };
  menu.onclick=async e=>{
    const button=e.target.closest('[data-slicer]');if(!button||!output)return;
    closeMenu();el('exportMenuButton').focus();
    await window.SlicerOpen.open(button.dataset.slicer,output,name());
  };
  document.addEventListener('click',e=>{if(!e.target.closest('.exportWrap'))closeMenu()});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!menu.hidden){closeMenu();el('exportMenuButton').focus()}});
  return {setSource(next){
    clearTimeout(timer);cancelPreview();displayRevision++;
    const revision=++selection;
    if(plate&&next.identity===plate.identity&&next.plateName===plate.plateName){
      const entry=plate.entries.get(strength),on=enabled;
      current=null;enabled=false;output=null;controls();
      if(entry)void present(entry,on,revision);
      else if(plate.jobs.has(strength)){
        void plate.jobs.get(strength).then(e=>present(e,true,revision))
          .catch(error=>{if(revision===selection)setStatus(error.message+'　可再次点击计算。')})
          .finally(()=>{if(revision===selection)controls()});
      }
      return;
    }
    plate?.close();plate=makePlate(next);current=null;enabled=false;strength=null;output=null;chart=null;
    slider.value=65;panel.hidden=true;painted=[];opacity=0;drawChart();controls();
    el('layerReadout').textContent='';el('smoothTotal').textContent=el('smoothCost').textContent='—';
    const owner=plate;
    (async()=>{
      const last=await owner.store.read('last');if(!last||revision!==selection)return;
      const entry=await owner.find(last.strength);if(!entry||revision!==selection)return;
      owner.last=strength=last.strength;if(strength!==null)slider.value=strength;
      await present(entry,false,revision);
    })().catch(()=>{});
  },selectLayer};
};
