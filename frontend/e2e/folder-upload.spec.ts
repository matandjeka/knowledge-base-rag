import {test,expect,type Page} from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const user={id:"test-user",email:"client@example.com",workspace_id:"client-workspace"};
type Posted={id:string;kind:string;pathname:string;filename:string;text_columns?:string[];metadata_columns?:string[]};

/**
 * Mocks the sign-in, Vercel Blob client-upload handshake, and the jobs API. Job creation is
 * followed by the client polling GET /jobs/{id} to a terminal state (see awaitJobCompletion) —
 * every job here completes immediately except one whose kind matches `opts.failKind`, which
 * reports "failed" so the cancel-to-free-the-workspace-slot path can be exercised.
 */
async function mockBackend(page:Page,opts:{rateLimitFirstJob?:boolean;failKind?:string}={}) {
  const posted:Posted[]=[];const cancelled:string[]=[];const failedIds=new Set<string>();let limited=false;
  await page.route("**/api/backend/**",async route=>{
    const request=route.request();const pathname=new URL(request.url()).pathname;
    if(pathname.endsWith("auth/refresh"))return route.fulfill({json:{access_token:"test-token",user}});
    if(pathname.endsWith("sources"))return route.fulfill({json:[]});
    if(pathname.endsWith("uploads/preview"))return route.fulfill({json:{row_count:2,sample_rows:[],columns:[{name:"id",inferred_type:"integer"},{name:"notes",inferred_type:"text"}]}});
    const cancelMatch=pathname.match(/\/jobs\/([^/]+)\/cancel$/);
    if(cancelMatch&&request.method()==="POST"){cancelled.push(cancelMatch[1]);return route.fulfill({json:{ok:true}});}
    const jobIdMatch=pathname.match(/\/jobs\/([^/]+)$/);
    if(jobIdMatch&&request.method()==="GET") {
      const id=jobIdMatch[1];const status=failedIds.has(id)?"failed":"complete";
      return route.fulfill({json:{id,source_id:"s",status,stage:status,step:1,processed:1,total:1,error:status==="failed"?"Could not parse this file.":undefined}});
    }
    if(pathname.endsWith("jobs")&&request.method()==="POST") {
      if(opts.rateLimitFirstJob&&!limited){limited=true;return route.fulfill({status:429,headers:{"Retry-After":"1"},json:{detail:"Too many requests. Please slow down."}});}
      const body=request.postDataJSON() as Posted;posted.push(body);
      if(opts.failKind&&body.kind===opts.failKind)failedIds.add(body.id);
      return route.fulfill({status:202,json:{id:body.id,source_id:"s",status:"queued",stage:"queued",step:0,processed:0,total:0,dispatched:true}});
    }
    if(pathname.endsWith("jobs"))return route.fulfill({json:[]});
    return route.fulfill({status:404,json:{detail:"Not found"}});
  });
  await page.route("**/api/upload",route=>route.fulfill({json:{type:"blob.generate-client-token",clientToken:`vercel_blob_client_teststore_${Buffer.from("token").toString("base64")}`}}));
  await page.route(/vercel\.com\/api\/blob/,route=>{
    const name=new URL(route.request().url()).searchParams.get("pathname")||"";
    return route.fulfill({headers:{"access-control-allow-origin":"*"},json:{url:`https://blob.test/${name}`,downloadUrl:`https://blob.test/${name}`,pathname:name,contentType:"application/octet-stream",contentDisposition:"attachment",etag:"etag"}});
  });
  return {posted,cancelled};
}

function makeFolder(dir:string) {
  fs.mkdirSync(path.join(dir,"nested"),{recursive:true});
  fs.writeFileSync(path.join(dir,"policy.pdf"),"%PDF-1.4 test");
  fs.writeFileSync(path.join(dir,"handbook.docx"),"docx");
  fs.writeFileSync(path.join(dir,"nested","faq.csv"),"id,notes\n1,hello\n2,world\n");
  fs.writeFileSync(path.join(dir,"logo.png"),"png");
  return dir;
}

async function openAddSource(page:Page) {
  await page.goto("/");
  await page.getByRole("button",{name:"Sources",exact:true}).click();
  await page.getByRole("button",{name:"Add source",exact:true}).click();
}

test("a mixed folder registers each PDF, DOCX and CSV as its own job and skips other files",async({page},testInfo)=>{
  const {posted}=await mockBackend(page);
  await openAddSource(page);
  await page.locator("input[webkitdirectory]").setInputFiles(makeFolder(testInfo.outputPath("kb")));
  await expect(page.getByText("3 of 3 files processed")).toBeVisible();
  await expect(page.getByText("1 file that is not PDF, DOCX, or CSV was skipped.")).toBeVisible();
  expect(posted.map(j=>[j.kind,j.filename]).sort()).toEqual([["csv","faq.csv"],["docx","handbook.docx"],["pdf","policy.pdf"]]);
  for(const job of posted) {
    expect(job.pathname).toMatch(new RegExp(`^workspaces/client-workspace/uploads/${job.id}/[0-9a-f-]{36}\\.${job.kind}$`));
  }
  const csv=posted.find(j=>j.kind==="csv")!;
  expect(csv.text_columns).toEqual(["notes"]);
  expect(csv.metadata_columns).toEqual(["id"]);
  await expect(page.getByRole("button",{name:"Close form",exact:true})).toBeEnabled();
});

test("re-selecting the folder does not add files twice",async({page},testInfo)=>{
  const {posted}=await mockBackend(page);
  await openAddSource(page);
  const folder=makeFolder(testInfo.outputPath("kb"));
  await page.locator("input[webkitdirectory]").setInputFiles(folder);
  await expect(page.getByText("3 of 3 files processed")).toBeVisible();
  await page.locator("input[webkitdirectory]").setInputFiles(folder);
  await expect(page.getByText("Every supported file in that folder was already added.")).toBeVisible();
  expect(posted).toHaveLength(3);
});

test("a rate-limited job is retried after the server's Retry-After",async({page},testInfo)=>{
  const {posted}=await mockBackend(page,{rateLimitFirstJob:true});
  await openAddSource(page);
  await page.locator("input[webkitdirectory]").setInputFiles(makeFolder(testInfo.outputPath("kb")));
  await expect(page.getByText("3 of 3 files processed")).toBeVisible({timeout:15_000});
  expect(posted).toHaveLength(3);
  await expect(page.locator(".batch-list .status.failed")).toHaveCount(0);
});

test("a file whose indexing job fails is cancelled so later files in the folder are not blocked",async({page},testInfo)=>{
  const {posted,cancelled}=await mockBackend(page,{failKind:"docx"});
  await openAddSource(page);
  await page.locator("input[webkitdirectory]").setInputFiles(makeFolder(testInfo.outputPath("kb")));
  await expect(page.getByText("3 of 3 files processed")).toBeVisible();
  expect(posted).toHaveLength(3); // one job per file was still created — the failure did not stop the batch
  const failedJob=posted.find(j=>j.kind==="docx")!;
  expect(cancelled).toEqual([failedJob.id]); // freed the workspace's one-job slot so the pdf/csv jobs could be created
  await expect(page.getByText("Could not parse this file.")).toBeVisible();
  await expect(page.getByText("1 of 3 files failed.")).toBeVisible();
  await expect(page.locator(".batch-list li", {hasText:"policy.pdf"}).locator(".status.ready")).toBeVisible();
});

test("the form cannot be closed while a folder is uploading",async({page},testInfo)=>{
  await mockBackend(page);
  let release=()=>{};const gate=new Promise<void>(resolve=>{release=resolve;});
  await page.route("**/api/upload",async route=>{await gate;await route.fulfill({json:{type:"blob.generate-client-token",clientToken:`vercel_blob_client_teststore_${Buffer.from("token").toString("base64")}`}});});
  await openAddSource(page);
  await page.locator("input[webkitdirectory]").setInputFiles(makeFolder(testInfo.outputPath("kb")));
  await expect(page.getByRole("button",{name:"Close form",exact:true})).toBeDisabled();
  release();
  await expect(page.getByText("3 of 3 files processed")).toBeVisible();
  await expect(page.getByRole("button",{name:"Close form",exact:true})).toBeEnabled();
});
