const {chromium}=require('playwright');(async()=>{const b=await chromium.launch(),p=await b.newPage({viewport:{width:1100,height:1400},deviceScaleFactor:1.5});
await p.goto('file://'+process.argv[2]);await p.evaluate(()=>document.fonts.ready);await p.waitForTimeout(600);
const bad=await p.$$eval('.cd *',es=>{const r=document.querySelector('.cd').getBoundingClientRect();return es.filter(e=>{const q=e.getBoundingClientRect();return q.width&&(q.right>r.right-59&&!e.closest('.bd,.ft,.frame,.grain')&&e.className!='frame'&&e.className!='ft'&&e.className!='grain'||q.bottom>r.bottom)}).map(e=>e.className||e.tagName)});console.log('bad',bad);
await (await p.$('.cd')).screenshot({path:process.argv[3],type:'jpeg',quality:90});await b.close()})();
