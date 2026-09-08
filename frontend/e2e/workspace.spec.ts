import {test,expect} from "@playwright/test";
const user={id:"test-user",email:"client@example.com",workspace_id:"client-workspace"};
test("registration form remains accessible on desktop and mobile",async({page})=>{
  await page.route("**/api/backend/auth/refresh",r=>r.fulfill({status:401,json:{detail:"Sign in"}}));
  await page.goto("/");
  await expect(page.getByRole("heading",{name:"Welcome back"})).toBeVisible();
  await page.getByRole("button",{name:"Create an account",exact:true}).click();
  await expect(page.getByLabel("Email address")).toBeVisible();
  await expect(page.getByLabel("Password",{exact:true})).toHaveAttribute("minlength","12");
  await page.screenshot({path:"/tmp/phase18-register-desktop.png",fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await expect(page.getByRole("button",{name:"Create account",exact:true})).toBeVisible();
  await page.screenshot({path:"/tmp/phase18-register-mobile.png",fullPage:true});
});
test("all workspace pages and cited answers render",async({page})=>{
  await page.route("**/api/backend/**",async route=>{
    const path=new URL(route.request().url()).pathname;
    if(path.endsWith("auth/refresh"))return route.fulfill({json:{access_token:"test-token",user}});
    if(path.endsWith("sources"))return route.fulfill({json:[{source_id:"source-1",name:"Retention policy",status:"ready",config:{source_type:"pdf",options:{}}}]});
    if(path.endsWith("jobs"))return route.fulfill({json:[]});
    if(path.endsWith("evaluations/reports"))return route.fulfill({json:{reports:[],invalid_report_ids:[]}});
    if(path.endsWith("settings"))return route.fulfill({json:{app_env:"production",vector_store_backend:"pinecone"}});
    if(path.endsWith("query"))return route.fulfill({json:{answer:"Retain records for seven years. [S1]",insufficient_evidence:false,evidence:[],citation_segments:[{text:"Retain records for seven years.",citation_ids:["S1"]}],citations:[{citation_id:"S1",source_id:"source-1",source_title:"Retention policy",excerpt:"Records must be retained for seven years.",locator:"Page 4",retriever:"vector",score:0.91,locator_details:{source_type:"pdf",page_number:4}}]}});
    return route.fulfill({status:404,json:{detail:"Not found"}});
  });
  await page.goto("/");
  await expect(page.getByRole("heading",{name:"Ask your knowledge base"})).toBeVisible();
  await page.screenshot({path:"/tmp/phase18-workspace.png",fullPage:true});
  await page.getByRole("textbox",{name:"Your question"}).fill("How long should I keep records?");
  await page.getByRole("button",{name:"Ask question",exact:true}).click();
  await expect(page.getByText("Retain records for seven years.",{exact:false}).first()).toBeVisible();
  await page.locator(".citations summary").click();
  await expect(page.getByText("Records must be retained for seven years.")).toBeVisible();
  for(const name of ["Sources","Retrieval Lab","Evaluation","Settings"]){
    await page.getByRole("button",{name,exact:true}).click();
    await expect(page.getByRole("heading",{name,exact:true})).toBeVisible();
  }
});
