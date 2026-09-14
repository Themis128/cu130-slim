import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';
const EMAIL=process.env.TIKTOK_DEV_EMAIL, PASSWORD=process.env.TIKTOK_DEV_PASSWORD;
const OUT=process.env.OUT_DIR||'/out';
const APP_ID='7630494700880906241';
fs.mkdirSync(OUT,{recursive:true});
const write=(n,d)=>fs.writeFileSync(path.join(OUT,n),JSON.stringify(d,null,2));
const shot=async(p,n)=>p.screenshot({path:path.join(OUT,n),fullPage:true}).catch(()=>{});
async function dismiss(page){for(let i=0;i<4;i++){await page.evaluate(()=>[...document.querySelectorAll('button')].find(b=>/allow all/i.test(b.textContent||''))?.click()).catch(()=>{});await page.waitForTimeout(500);}}
const browser=await chromium.launch({headless:true,args:['--disable-blink-features=AutomationControlled']});
const page=await (await browser.newContext({viewport:{width:1440,height:1100}})).newPage();
await page.goto('https://developers.tiktok.com/login/',{waitUntil:'domcontentloaded',timeout:120000});
await page.waitForTimeout(1200); await dismiss(page);
await page.getByPlaceholder('Email').fill(EMAIL);
await page.getByPlaceholder('Password').fill(PASSWORD);
const btn=page.getByRole('button',{name:/^Log in$/i});
for(let i=0;i<30;i++){if(!(await btn.isDisabled().catch(()=>true)))break;await page.waitForTimeout(200);}
await btn.click({force:true}); await page.waitForTimeout(12000); await dismiss(page);
await page.goto(`https://developers.tiktok.com/app/${APP_ID}`,{waitUntil:'domcontentloaded',timeout:120000});
await page.waitForTimeout(4500); await dismiss(page);
await shot(page,'so-01.png');
const before=await page.locator('body').innerText();
const formError=/This form has \d+ error/i.test(before);
const hasSubmit=/Submit for review/i.test(before);
let submitted=false;
if(!formError && hasSubmit){
  await page.getByRole('button',{name:/Submit for review/i}).first().click({force:true}).catch(async()=>{
    await page.evaluate(()=>[...document.querySelectorAll('button')].find(e=>/Submit for review/i.test(e.textContent||''))?.click());
  });
  await page.waitForTimeout(2500);
  // click any confirm in dialog
  for (const name of [/Confirm/i, /Submit/i, /Yes/i, /Continue/i, /OK/i]) {
    const b=page.getByRole('button',{name}).first();
    if(await b.isVisible().catch(()=>false)){
      const t=(await b.innerText().catch(()=>'')).toLowerCase();
      if(/cancel|close|back/.test(t)) continue;
      await b.click({force:true});
      await page.waitForTimeout(2000);
    }
  }
  submitted=true;
}
await page.waitForTimeout(5000);
await shot(page,'so-02.png');
const after=await page.locator('body').innerText();
const out={
  ok:true, formError, hasSubmit, submitted,
  notApproved:/Not approved/i.test(after),
  underReview:/Under review/i.test(after)&&!/to Rejected/i.test(after),
  hasReturnToDraft:/Return to Draft/i.test(after),
  stillHasSubmit:/Submit for review/i.test(after),
  url:page.url(),
  topBanner:(after.match(/Changes to your app[^\n]{0,80}|Production\n[^\n]{0,40}/)||[])[0]||null,
};
write('submit-only.json',out);
console.log(JSON.stringify(out,null,2));
await browser.close();
