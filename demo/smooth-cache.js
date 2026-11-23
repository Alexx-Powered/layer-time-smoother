/* The exported bytes and measured layer times belong to one plate and engine revision. */
window.createPlateSmoothingCache = (() => {
  let database, engineHash;
  const digest = async bytes => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),
    value=>value.toString(16).padStart(2,'0')).join('');
  function open(){
    return database ||= new Promise(resolve=>{
      if(!window.indexedDB){resolve(null);return}
      const request=indexedDB.open('layer-time-smoother',1);
      request.onupgradeneeded=()=>request.result.createObjectStore('results');
      request.onsuccess=()=>resolve(request.result);
      request.onerror=request.onblocked=()=>resolve(null);
    }).catch(()=>null);
  }
  return function(source){
    let address;
    async function locate(){
      if(!globalThis.crypto?.subtle)return null;
      return address ||= (async()=>{
        const db=await open();if(!db)return null;
        engineHash ||= digest(new TextEncoder().encode(LayerSmoothingWorker.cacheSignature()));
        const plate=source.builtin ? layerSmoothingExample.sha256 : await digest(await (await source.file()).arrayBuffer());
        return {db,prefix:[await engineHash,plate]};
      })().catch(()=>null);
    }
    async function access(mode,key,value){
      const location=await locate();if(!location)return;
      return new Promise((resolve,reject)=>{
        const tx=location.db.transaction('results',mode),store=tx.objectStore('results');
        const id=[...location.prefix,key],r=mode==='readonly'?store.get(id):store.put(value,id);
        tx.oncomplete=()=>resolve(mode==='readonly'?r.result:true);
        tx.onabort=tx.onerror=()=>reject(tx.error);
      }).catch(()=>undefined);
    }
    return {read:key=>access('readonly',key),write:(key,value)=>access('readwrite',key,value)};
  };
})();
