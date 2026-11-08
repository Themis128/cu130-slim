// LinkedIn access-request survey filler — Qualtrics form driver.
// Run via playwright MCP browser_evaluate (or cdp_eval.py) against an open
// linkedincustomerops.qualtrics.com form page. Fill map drives every field type.
// Usage: paste the FILL map for the product, run FILLER as the eval function.

const FILLER = `(fill) => {
  const setT=(el,v)=>{const s=Object.getOwnPropertyDescriptor(el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set;s.call(el,v);el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));};
  const done=[],miss=[];
  // text/textarea by ordinal (fields[] index into visible text inputs)
  const txts=Array.from(document.querySelectorAll('input[type=text],textarea')).filter(e=>e.offsetParent);
  (fill.fields||[]).forEach((v,i)=>{ if(v!=null&&txts[i]){setT(txts[i],v);done.push('f'+i);} });
  // radio groups: {"QID2":0,"QID21":1} -> nth option index
  for(const [q,n] of Object.entries(fill.radios||{})){
    const r=document.querySelectorAll('input[name='+q+']');
    if(r[n]){r[n].click();done.push(q+'='+n);}else miss.push(q);
  }
  // checkboxes: {"QID14":[0,1]} -> option indices to tick
  for(const [q,idx] of Object.entries(fill.checks||{})){
    const r=document.querySelectorAll('input[name='+q+']');
    for(const n of idx){ if(r[n]){r[n].click();done.push(q+'['+n+']');} }
  }
  // selects: {"QID_country":"Greece"} -> option text match
  for(const [q,t] of Object.entries(fill.selects||{})){
    const s=document.querySelector('select[name='+q+'], select[id*="'+q+'"]')||document.querySelector('select');
    if(s){const o=Array.from(s.options).find(o=>o.textContent.trim().toLowerCase()===t.toLowerCase()); if(o){s.value=o.value;s.dispatchEvent(new Event('change',{bubbles:true}));done.push(q+'='+t);}else miss.push(q);}else miss.push(q);
  }
  // advance page if next button exists
  const nb=Array.from(document.querySelectorAll('input[type=submit],button,input[type=button],a')).find(e=>/next|submit/i.test(e.textContent||e.value||''));
  return JSON.stringify({done,miss,next:!!nb});
}`;

// CM Standard Access Request Form (cloudless-gr-member, app 265077368)
const CM_FORM = {
  fields: [
    "Cloudless",                                  // Organization's Legal Name
    null,                                         // Alternate legal name
    "https://cloudless.gr",                       // Organization Website
    "<REGISTERED_STREET>",                        // street address
    "Athens",                                     // City
    "Attica",                                     // State/Province
    "<ZIP>",                                      // Postal code
  ],
  selects: { country: "Greece" },
  radios: {
    QID2: 0,    // another app w/ CM standard? -> Yes (264925843)
    QID21: 1,   // alternate legal name? -> No
    QID23: 0,   // why requesting -> already integrated into products
    QID25: 0,   // primary use case -> Platform: self-service SaaS
  },
  checks: {
    // QID14: capabilities used -> Page management + Page analytics (+Profile mgmt if member surfaces)
    QID14: [0, 1],
  },
};

// Usage in an eval context (playwright MCP browser_evaluate / cdp_eval):
// load this file as the page script, then call:  eval(FILLER)(CM_FORM)
// `module.exports` is intentionally absent — it throws in a browser eval.
