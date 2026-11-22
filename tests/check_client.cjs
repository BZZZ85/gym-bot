const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('webapp/static/app.js','utf8');
const html = fs.readFileSync('webapp/static/index.html','utf8');
const ids = new Set([...html.matchAll(/id="([^"]+)"/g)].map(match=>match[1]));
for (const match of source.matchAll(/\$\("#([^"\s]+)"\)/g)) assert.ok(ids.has(match[1]), 'Missing HTML id: '+match[1]);
function environment(search='') {
 const nodes=new Map();
 const node=(selector)=>{if(!nodes.has(selector))nodes.set(selector,{hidden:true,textContent:'',value:'',dataset:{},classList:{add(){},toggle(){}},setAttribute(){},removeAttribute(){},addEventListener(){},append(){},replaceChildren(){},style:{}});return nodes.get(selector)};
 let now=Date.UTC(2026,10,22,10); let intervalId=0;
 class Clock extends Date {constructor(...args){super(...(args.length?args:[now]));}static now(){return now;}}
 const storage=new Map(), fetchCalls=[];
 const context=vm.createContext({console,Date:Clock,Intl,Map,Set,Math,JSON,Number,String,URL,URLSearchParams,Uint8Array,Error,AbortController,
 crypto:require('node:crypto').webcrypto,
 location:{search,origin:'http://localhost'},window:{Telegram:undefined,scrollTo(){}},
 document:{querySelector:node,querySelectorAll:()=>[],createElement:()=>node('created-'+Math.random()),createElementNS:()=>node('created-'+Math.random()),addEventListener(){},hidden:false},
 localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},
 setTimeout:()=>1,clearTimeout(){},setInterval:()=>++intervalId,clearInterval(){},
 fetch:async(...args)=>{fetchCalls.push(args);return {ok:false,status:401,json:async()=>({detail:'Повторите вход'})}}});
 vm.runInContext(source,context);
 return {context,nodes,fetchCalls,setNow(value){now=value},run:code=>vm.runInContext(code,context)};
}
(async()=>{
 const e=environment();
 assert.equal(e.nodes.get('#welcome').hidden,false);
 assert.equal(e.fetchCalls.length,0,'Landing must not fetch private data');
 assert.equal(e.run('filteredExercises([{exercise:"Жим лёжа"},{exercise:"Присед"}]," ЛЕЖА ").length'),1);
 assert.equal(e.run('filteredExercises([{exercise:"Присед"}],"жим").length'),0);
 assert.equal(e.run('dayKey(new Date("2026-11-21T22:30:00Z"))'),'2026-11-22');
 let summary=await e.run('demoApi("/api/summary",{})');
 assert.equal(summary.total_sessions,5);assert.equal(summary.week_sessions,2);assert.equal(summary.today,null);
 const options={body:JSON.stringify({exercise:'Подтягивания / узкий хват',entries:[{weight:0,reps:12}],request_id:'same-request'})};
 e.context.testOptions=options;
 assert.equal((await e.run('demoApi("/api/records",testOptions)')).added,1);
 assert.equal((await e.run('demoApi("/api/records",testOptions)')).duplicate,true);
 summary=await e.run('demoApi("/api/summary",{})');
 assert.equal(summary.total_sessions,6);assert.equal(summary.today.sets,1);assert.equal(summary.today.volume,0);
 const history=await e.run('demoApi("/api/history",{})');
 assert.equal(history[0].entries[0].exercise,'Подтягивания / узкий хват');
 const progress=await e.run('demoApi("/api/exercise/history?exercise="+encodeURIComponent("Подтягивания / узкий хват"),{})');
 assert.equal(progress.max_weight,0);assert.equal(progress.sessions.length,1);
 e.run('startTimer()');e.setNow(Date.UTC(2026,10,22,10,0,35));e.run('tickTimer()');
 assert.equal(e.nodes.get('#timer-display').textContent,'00:55');
 assert.equal(e.nodes.get('#rest-dock').hidden,false);
 assert.equal(e.nodes.get('#rest-remaining').textContent,'00:55');
 e.run('stopTimer()');e.setNow(Date.UTC(2026,10,22,10,1,0));e.run('startTimer(true)');
 assert.equal(e.nodes.get('#timer-display').textContent,'00:55');
 e.setNow(Date.UTC(2026,10,22,10,2,0));e.run('tickTimer()');
 assert.equal(e.nodes.get('#timer-display').textContent,'00:00');
 assert.equal(e.run('timer.interval'),null);
 assert.equal(e.nodes.get('#rest-dock').hidden,true);
 await assert.rejects(e.run('api("/api/summary")'),error=>error.status===401&&error.message==='Повторите вход');
 assert.equal(e.fetchCalls.at(-1)[1].headers['X-Telegram-Init-Data'],'');
 assert.ok(e.run('uuid()').match(/^[0-9a-f-]{36}$/));
 console.log('PASS: HTML references, landing, Moscow dates, demo save/history/progress, duplicate protection, bodyweight, timer pause/resume/background, API errors, UUID.');
})().catch(error=>{console.error(error);process.exitCode=1});
