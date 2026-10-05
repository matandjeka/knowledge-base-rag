"use client";

import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { BookOpen, MessageSquare, Database, FlaskConical, ChartNoAxesCombined, Settings, ArrowUp, Plus, LogOut, Search, ChevronRight, ShieldCheck, FileText, Globe, Table2, LoaderCircle, Menu, RefreshCw } from "lucide-react";
import { upload } from "@vercel/blob/client";
import { ApiError, api, post, restore, setSession, accessToken, type User, type Session } from "@/lib/api";
import { CONTENT_TYPES, MAX_CSV_COLUMNS, MAX_FOLDER_FILES, TOKEN_REFRESH_MS, awaitJobCompletion, csvColumnChoice, fingerprint, planFolder, precheck, relativePath, withRateLimitRetry, type FileKind } from "@/lib/folder-upload";
import type { Source, Job, Answer, Preview } from "@/lib/contracts";

type Page = "Chat" | "Sources" | "Retrieval Lab" | "Evaluation" | "Settings";
const navigation = [{name: "Chat", icon: MessageSquare}, {name: "Sources", icon: Database}, {name: "Retrieval Lab", icon: FlaskConical}, {name: "Evaluation", icon: ChartNoAxesCombined}, {name: "Settings", icon: Settings}] as const;
const modes = ["auto", "fusion", "vector", "sentence_window", "lexical", "graph", "sql"];
function message(error: unknown) { return error instanceof Error ? error.message : "Something went wrong. Please try again."; }
function Busy() { return <LoaderCircle className="spin" size={18} aria-label="Loading"/>; }
function Notice({children}: {children: ReactNode}) { return <div className="notice" role="status">{children}</div>; }
function SourceIcon({type}: {type: string}) { const Icon = ["pdf", "docx"].includes(type) ? FileText : type === "website" ? Globe : type === "csv" ? Table2 : Database; return <Icon size={19}/>; }
function ConfidenceBadge({confidence}: {confidence?: number}) { if (confidence == null) return null; const tier = confidence >= 0.75 ? "high" : confidence >= 0.5 ? "medium" : "low"; return <span className={`confidence-badge ${tier}`}>{Math.round(confidence*100)}% match</span>; }

export default function Workspace() {
  const [user, setUser] = useState<User>();
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState<Page>("Chat");
  const [menu, setMenu] = useState(false);
  useEffect(() => {restore().then(s => setUser(s.user)).catch(() => {}).finally(() => setLoading(false));}, []);
  async function logout() { try {await post("auth/logout", {});} finally {setSession(); setUser(undefined);} }
  if (loading) return <main className="loading"><Busy/> Opening your workspace…</main>;
  if (!user) return <Auth onLogin={setUser}/>;
  return <div className="app-shell">
    <aside className={`sidebar ${menu ? "open" : ""}`}>
      <a className="brand" href="/" aria-label="Knowledge Fusion home"><span className="brand-mark"><BookOpen size={21}/></span><span>Knowledge<span className="brand-light">Fusion</span></span></a>
      <div className="workspace-label"><span className="avatar">{user.email[0].toUpperCase()}</span><div><strong>My workspace</strong><small>Private knowledge base</small></div><ShieldCheck size={16}/></div>
      <span className="nav-label">WORKSPACE</span>
      <nav>{navigation.map(({name, icon: Icon}) => <button key={name} className={page === name ? "nav-item active" : "nav-item"} onClick={() => {setPage(name);setMenu(false);}} aria-current={page === name ? "page" : undefined}><Icon size={18}/>{name}{page === name && <span className="nav-dot"/>}</button>)}</nav>
      <div className="sidebar-bottom"><div className="private-note"><ShieldCheck size={18}/><span>Your knowledge.<br/><strong>Your private space.</strong></span></div><button className="account" onClick={logout}><span className="avatar">{user.email[0].toUpperCase()}</span><span>{user.email}<small>Sign out</small></span><LogOut size={16}/></button></div>
    </aside>
    <div className="main-shell"><header className="topbar"><button className="icon-button mobile-menu" onClick={() => setMenu(!menu)} aria-label="Toggle navigation"><Menu/></button><span className="breadcrumb">Workspace <ChevronRight size={14}/> <strong>{page}</strong></span><span className="private-badge"><ShieldCheck size={14}/> Private workspace</span></header>
      <main className="content">{page === "Chat" || page === "Retrieval Lab" ? <Chat key={page} user={user} lab={page === "Retrieval Lab"}/> : page === "Sources" ? <Sources user={user}/> : page === "Evaluation" ? <Evaluation/> : <Preferences user={user}/>}</main>
    </div>
  </div>;
}

function Auth({onLogin}: {onLogin: (user: User) => void}) {
  const [register, setRegister] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [action, setAction] = useState<string | null>(null);
  const [linkToken, setLinkToken] = useState<string | null>(null);
  useEffect(() => {const params = new URLSearchParams(location.search);setAction(params.get("action"));setLinkToken(params.get("token")); if (params.has("token")) history.replaceState({}, "", "/");}, []);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget); setBusy(true);setNotice("");
    try {
      if (action && linkToken) {
        await post(`auth/${action === "reset" ? "reset" : "verify"}`, {token: linkToken, ...(action === "reset" ? {password: data.get("password")} : {})});
        setAction(null);setLinkToken(null);setNotice("Your account is ready. Sign in to continue.");
      } else if (register) {
        const result = await post<{message: string}>("auth/register", {email: data.get("email"), password: data.get("password")});
        setNotice(result.message);setRegister(false);
      } else {
        const session = await post<Session>("auth/login", {email: data.get("email"), password: data.get("password")});
        setSession(session);onLogin(session.user);
      }
    } catch (error) {setNotice(message(error));} finally {setBusy(false);}
  }
  return <main className="auth-shell"><section className="auth-story"><div className="brand"><span className="brand-mark"><BookOpen/></span>Knowledge Fusion</div><div><span className="eyebrow">CONNECT. ASK. UNDERSTAND.</span><h1>Your knowledge,<br/>with the evidence<br/>to back it up.</h1><p>Bring documents, websites, and data into one private workspace. Find answers you can trace to their source.</p><div className="story-tags"><span><FileText size={15}/> Documents</span><span><Globe size={15}/> Websites</span><span><Database size={15}/> Data</span></div></div><small>Built around your sources. Grounded in evidence.</small></section>
    <section className="auth-form"><span className="eyebrow">YOUR PRIVATE WORKSPACE</span><h2>{action ? action === "reset" ? "Reset your password" : "Verify your email" : register ? "Create your account" : "Welcome back"}</h2><p className="muted">{register ? "Make room for everything you know." : "Continue exploring your knowledge base."}</p>
      <form onSubmit={submit}>{!action && <label>Email address<input name="email" type="email" autoComplete="email" placeholder="you@company.com" required maxLength={254} defaultValue="admin@demo.com" readOnly/></label>}{action !== "verify" && <label>Password<input name="password" type="password" autoComplete={register || action === "reset" ? "new-password" : "current-password"} minLength={12} maxLength={128} placeholder="At least 12 characters" required defaultValue="admin-demo-1234" readOnly/></label>}
        {notice && <Notice>{notice}</Notice>}<button className="button primary wide" disabled={busy}>{busy ? <Busy/> : null}{action ? "Continue" : register ? "Create account" : "Sign in"}<ChevronRight size={17}/></button>
        {!action && <button className="text-button" type="button" onClick={async event => {const form = event.currentTarget.form;const email = form ? new FormData(form).get("email") : "";setBusy(true);try {const r=await post<{message: string}>("auth/request-link", {email});setNotice(r.message);} catch(e){setNotice(message(e));}finally{setBusy(false);}}}>Forgot password or need a verification link?</button>}
      </form>{!action && <p className="auth-switch">{register ? "Already have an account?" : "New to Knowledge Fusion?"} <button className="text-button" onClick={() => {setRegister(!register);setNotice("");}}>{register ? "Sign in" : "Create an account"}</button></p>}<div className="auth-privacy"><ShieldCheck size={15}/> Your sources are private to your workspace.</div>
    </section></main>;
}

function Chat({user, lab}: {user: User; lab: boolean}) {
  const [sources, setSources] = useState<Source[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState(lab ? "fusion" : "auto");
  const [topK, setTopK] = useState(5);
  const [rerank, setRerank] = useState(false);
  const [rerankingAvailable, setRerankingAvailable] = useState(false);
  useEffect(() => {let active = true; api<{reranking_available: boolean}>("settings").then(s => {if (active) setRerankingAvailable(s.reranking_available);}).catch(() => {}); return () => {active = false;};}, []);
  const [fusion, setFusion] = useState(["vector", "sentence_window", "lexical"]);
  const [strategy, setStrategy] = useState("rrf");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [history, setHistory] = useState<{question: string; response: Answer; elapsed: number}[]>([]);
  useEffect(() => {api<Source[]>(`sources?workspace_id=${user.workspace_id}`).then(setSources).catch(e => setError(message(e)));}, [user.workspace_id]);
  async function ask(event: FormEvent) {
    event.preventDefault(); if (!question.trim()) return; setBusy(true);setError("");const started=performance.now();
    try {
      const response = await post<Answer>("query", {workspace_id: user.workspace_id, question, source_ids: selected, retrieval_mode: mode, top_k: topK,
        ...(mode === "fusion" ? {fusion_retrievers: fusion, fusion_strategy: strategy, rerank: rerankingAvailable && rerank} : {})});
      setHistory(h => [...h, {question, response, elapsed: (performance.now()-started)/1000}]);setQuestion("");
    } catch(e) {setError(message(e));} finally {setBusy(false);}
  }
  return <div className="chat-layout"><section className="chat-main"><div className="page-heading"><div><span className="eyebrow">{lab ? "EXPLORE YOUR RETRIEVAL" : "ANSWERS WITH EVIDENCE"}</span><h1>{lab ? "Retrieval Lab" : "Ask your knowledge base"}</h1><p>Find the answer. Follow the source.</p></div><button className="button secondary" onClick={() => {setHistory([]);setError("");}}><Plus size={16}/>New conversation</button></div>
    {history.length === 0 ? <div className="chat-empty"><div className="empty-symbol"><BookOpen size={32}/></div><h2>A little curiosity goes a long way.</h2><p>Ask a question across your documents and data.<br/>Every answer comes with evidence you can inspect.</p><div className="suggestions">{["Summarize the key points in my sources", "What do my documents say about retention?", "Compare the policies across my sources"].map(q => <button key={q} onClick={() => setQuestion(q)}><MessageSquare size={17}/>{q}<ChevronRight size={15}/></button>)}</div></div> : <div className="conversation" aria-live="polite">{history.map((item, i) => <article key={i}><div className="question-bubble">{item.question}</div><div className="answer-label"><span className="mini-brand"><BookOpen size={16}/></span>Knowledge Fusion <small>{(((item.response.latency_ms ?? item.elapsed*1000))/1000).toFixed(2)}s</small></div><AnswerView answer={item.response} id={`answer-${i}`}/></article>)}</div>}
    {error && <Notice>{error}</Notice>}<form className="composer" onSubmit={ask}><textarea aria-label="Your question" placeholder="Ask anything about your sources…" value={question} onChange={e=>setQuestion(e.target.value)} maxLength={4000} required rows={2}/><div><span><ShieldCheck size={14}/> Grounded in your selected sources</span><button className="send" disabled={busy || !sources.some(s=>s.status === "ready")} aria-label="Ask question">{busy ? <Busy/> : <ArrowUp size={20}/>}</button></div></form><p className="composer-note">Answers depend on your source material. Review citations for context.</p>
    </section><aside className="source-panel"><div className="panel-title"><h3>Sources</h3><span className="count">{sources.filter(s=>s.status === "ready").length}</span></div><p className="muted small">Choose the knowledge to draw from.</p><button className="text-button" onClick={()=>setSelected([])}>Use all available sources</button><div className="source-options">{sources.filter(s=>s.status === "ready").map(source=><label className="source-option" key={source.source_id}><input type="checkbox" checked={selected.includes(source.source_id)} onChange={e=>setSelected(v=>e.target.checked?[...v,source.source_id]:v.filter(id=>id!==source.source_id))}/><SourceIcon type={source.config.source_type}/><span>{source.name}<small>{source.config.source_type.toUpperCase()}</small></span></label>)}{sources.length===0 && <div className="panel-empty"><Database size={24}/><p>No sources yet</p><small>Open Sources to add your first document or website.</small></div>}</div>
      {lab && <div className="lab-controls"><h3>Retrieval controls</h3><label>Strategy<select value={mode} onChange={e=>setMode(e.target.value)}>{modes.map(m=><option key={m} value={m}>{m.replaceAll("_", " ")}</option>)}</select></label><label>Results<input type="number" min={1} max={20} value={topK} onChange={e=>setTopK(Number(e.target.value))}/></label>{mode==="fusion" && <><label>Fusion method<select value={strategy} onChange={e=>setStrategy(e.target.value)}><option value="rrf">Reciprocal rank fusion</option><option value="weighted_rrf">Weighted fusion</option></select></label>{["vector","sentence_window","lexical","graph"].map(m=><label className="check" key={m}><input type="checkbox" checked={fusion.includes(m)} onChange={e=>setFusion(v=>e.target.checked?[...v,m]:v.filter(x=>x!==m))}/>{m.replaceAll("_", " ")}</label>)}{rerankingAvailable ? <label className="check"><input type="checkbox" checked={rerank} onChange={e=>setRerank(e.target.checked)}/>Re-rank results</label> : <small>Results use fusion ranking.</small>}</>}{mode === "sql" && <small>Select exactly one database source.</small>}</div>}
      <div className="evidence-note"><ShieldCheck size={18}/><strong>Evidence you can inspect</strong><p>Open a citation to see the exact passage behind an answer.</p></div></aside></div>;
}

function AnswerView({answer, id}: {answer: Answer; id: string}) {
  return <div className="answer">{answer.insufficient_evidence && <Notice>There is not enough evidence to fully answer this question.</Notice>}{answer.citation_segments?.length ? answer.citation_segments.map((segment,i)=><p key={i}>{segment.text} {segment.citation_ids.map(c=><a className="citation-pill" key={c} href={`#${id}-${c}`}>{c}</a>)}</p>) : <p className="preserve">{answer.answer}</p>}<div className="citations">{answer.citations.map(c=><details key={c.citation_id} id={`${id}-${c.citation_id}`}><summary><span className="citation-pill">{c.citation_id}</span><strong>{c.source_title || "Source"}</strong><ConfidenceBadge confidence={c.confidence}/><span>{c.locator}</span></summary><blockquote>{c.excerpt}</blockquote><small>{c.retriever} · relevance {c.score.toFixed(3)}</small>{["pdf","docx","csv"].includes(c.locator_details.source_type) && <Download sourceId={c.source_id}/>}{c.locator_details.url && /^https?:\/\//.test(c.locator_details.url) && <a className="source-link" href={c.locator_details.url} target="_blank" rel="noreferrer noopener">Open source ↗</a>}</details>)}</div><details className="trace"><summary>Retrieval trace & evidence</summary><pre>{JSON.stringify({routing: answer.routing_trace, evidence: answer.evidence},null,2)}</pre></details></div>;
}

function Download({sourceId}: {sourceId:string}) {
  const [error,setError]=useState("");
  async function open() {
    const request = () => fetch(`/api/download?source_id=${sourceId}`,{headers:{Authorization:`Bearer ${accessToken()}`}});
    try {
      let r = await request();
      if (r.status === 401) { await restore(); r = await request(); }
      if (!r.ok) throw new Error("Could not authorize this download.");
      const {url} = await r.json();
      window.open(url,"_blank","noopener,noreferrer");
    } catch(e) { setError(message(e)); }
  }
  return <><button className="text-button" onClick={open}>Open original ↗</button>{error&&<small role="alert">{error}</small>}</>;
}

function Sources({user}: {user: User}) {
  const [sources,setSources]=useState<Source[]>([]); const [jobs,setJobs]=useState<Job[]>([]);
  const [error,setError]=useState("");const [adding,setAdding]=useState(false);const [uploading,setUploading]=useState(false);const [filter,setFilter]=useState("");
  const [loadError,setLoadError]=useState("");const [buildingGraph,setBuildingGraph]=useState(false);
  const [inspected,setInspected]=useState<{source: Source; documents: unknown} | null>(null);
  async function load() {try {const [s,j]=await Promise.all([api<Source[]>(`sources?workspace_id=${user.workspace_id}`),api<Job[]>("jobs")]);setSources(s);setJobs(j);setLoadError("");return j;}catch(e){setLoadError(message(e));return undefined;}}
  const hasActiveJob=jobs.some(j=>!["complete","cancelled","failed"].includes(j.status));
  async function buildGraph() {
    const id=crypto.randomUUID();setBuildingGraph(true);setError("");
    try {const result=await post<Job>("jobs",{id,kind:"graph"});if(result.dispatched===false)setError("Graph build registered. Use Resume in ingestion activity to start processing.");await load();}
    catch(e){const current=await load();if(!current?.some(j=>j.id===id))setError(message(e));}
    finally{setBuildingGraph(false);}
  }
  useEffect(()=>{void load();const timer=setInterval(load,5000);return()=>clearInterval(timer);},[user.workspace_id]); // eslint-disable-line react-hooks/exhaustive-deps
  async function jobAction(job: Job, action: string) {setError("");try {await post(`jobs/${job.id}/${action}`,{});await load();}catch(e){setError(message(e));}}
  return <><div className="page-heading"><div><span className="eyebrow">BUILD YOUR KNOWLEDGE BASE</span><h1>Sources</h1><p>Your documents and data, connected in one place.</p></div><button className="button primary" disabled={uploading} onClick={()=>setAdding(!adding)}><Plus size={17}/>{adding?"Close form":"Add source"}</button></div>
    <button className="text-button" disabled={buildingGraph||hasActiveJob||!sources.some(s=>s.status==="ready"&&s.config.source_type!=="database")} onClick={buildGraph}>{buildingGraph?"Starting graph build…":"Build knowledge graph"}</button><div className="stats"><Stat label="Total sources" value={sources.length}/><Stat label="Ready to explore" value={sources.filter(s=>s.status==="ready").length}/><Stat label="Active jobs" value={jobs.filter(j=>!["complete","cancelled","failed"].includes(j.status)).length}/></div>
    {(error||loadError)&&<Notice>{error||loadError}</Notice>}{adding&&<AddSource user={user} onAdded={()=>{setAdding(false);void load();}} onChanged={()=>void load()} onBusyChange={setUploading}/>}
    <div className="card"><div className="table-toolbar"><h3>Your sources</h3><label className="search"><Search size={16}/><input aria-label="Find a source" placeholder="Find a source…" value={filter} onChange={e=>setFilter(e.target.value)}/></label></div><div className="table-scroll"><table><thead><tr><th>Name</th><th>Type</th><th>Status</th><th>Actions</th></tr></thead><tbody>{sources.filter(s=>s.name.toLowerCase().includes(filter.toLowerCase())).map(s=><tr key={s.source_id}><td><span className="source-name"><SourceIcon type={s.config.source_type}/>{s.name}</span></td><td>{s.config.source_type.toUpperCase()}</td><td><span className={`status ${s.status}`}>{s.status}</span></td><td><button className="text-button" onClick={async()=>{try{setInspected({source:s,documents:s.config.source_type==="database"?s.config.options:await api(`sources/${s.source_id}/documents?workspace_id=${user.workspace_id}&limit=100`)});}catch(e){setError(message(e));}}}>Inspect</button><button className="text-button" disabled={s.config.source_type==="database"} onClick={async()=>{try{await post("jobs",{id:crypto.randomUUID(),kind:"reindex",source_id:s.source_id});await load();}catch(e){setError(message(e));}}}>Re-index</button></td></tr>)}</tbody></table></div>{sources.length===0&&<div className="empty-state"><Database size={30}/><h3>Your knowledge starts here</h3><p>Add a PDF, DOCX, website, CSV, or database to start asking questions.</p><button className="button secondary" onClick={()=>setAdding(true)}><Plus size={16}/>Add your first source</button></div>}</div>
    {jobs.length>0&&<section className="card jobs"><h3>Ingestion activity</h3>{jobs.map(j=><div className="job" key={j.id}><span className={`status ${j.status}`}>{j.status}</span><div><strong>{j.stage.replaceAll("_"," ")}</strong><small>{j.error||(j.total>0?`${j.processed} / ${j.total} ${j.stage.startsWith("graph_")?"batches":"passages"}`:"Preparing your source")}</small></div>{!["complete","cancelled"].includes(j.status)&&<><button className="text-button" onClick={()=>jobAction(j,"resume")}><RefreshCw size={14}/>Resume</button><button className="text-button" onClick={()=>jobAction(j,"cancel")}>Cancel</button></>}</div>)}</section>}
    {inspected&&<section className="card inspector"><div className="panel-title"><h3>{inspected.source.name}</h3>{["pdf","docx","csv"].includes(inspected.source.config.source_type)&&<Download sourceId={inspected.source.source_id}/>}<button className="text-button" onClick={()=>setInspected(null)}>Close</button></div><pre>{JSON.stringify(inspected.documents,null,2)}</pre></section>}
  </>;
}
function Stat({label,value}: {label:string;value:number}) {return <div className="stat"><span>{label}</span><strong>{value}</strong></div>;}

type BatchItem = {name:string;status:"queued"|"uploading"|"indexing"|"waiting"|"registered"|"failed";error?:string};
function AddSource({user,onAdded,onChanged,onBusyChange}: {user:User;onAdded:()=>void;onChanged:()=>void;onBusyChange:(busy:boolean)=>void}) {
  const [kind,setKind]=useState("pdf");const [busy,setBusy]=useState(false);const [error,setError]=useState("");
  const [prepared,setPrepared]=useState<{id:string;pathname:string;filename:string;preview?:Preview}>();
  const [textColumns,setTextColumns]=useState<string[]>([]);const [metadataColumns,setMetadataColumns]=useState<string[]>([]);
  const [batch,setBatch]=useState<BatchItem[]>([]);const folderInput=useRef<HTMLInputElement>(null);
  const mounted=useRef(true);const addedFiles=useRef(new Set<string>());const lastRefresh=useRef(0);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  useEffect(()=>{onBusyChange(busy);return()=>onBusyChange(false);},[busy]);
  useEffect(()=>{if(!busy)return;const warn=(e:BeforeUnloadEvent)=>e.preventDefault();window.addEventListener("beforeunload",warn);return()=>window.removeEventListener("beforeunload",warn);},[busy]);
  async function uploadOne(file:File,fileKind:FileKind) {
    const problem=precheck(file);if(problem)throw new Error(problem);
    const id=crypto.randomUUID();const pathname=`workspaces/${user.workspace_id}/uploads/${id}/${crypto.randomUUID()}.${fileKind}`;
    await upload(pathname,file,{access:"private",handleUploadUrl:"/api/upload",headers:{Authorization:`Bearer ${accessToken()}`},contentType:CONTENT_TYPES[fileKind]});
    return {id,pathname};
  }
  const previewCsv=(pathname:string)=>api<Preview>(`uploads/preview?pathname=${encodeURIComponent(pathname)}`);
  async function prepare(file:File) {
    setBusy(true);setError("");setPrepared(undefined);setBatch([]);
    try {await restore();const {id,pathname}=await uploadOne(file,kind as FileKind);
      const preview=kind==="csv"?await previewCsv(pathname):undefined;
      setPrepared({id,pathname,filename:file.name,preview});setTextColumns(preview?.columns.map(c=>c.name)||[]);
    } catch(e){setError(message(e));}finally{setBusy(false);}
  }
  async function uploadFolder(list:FileList|null) {
    if(!list?.length)return;
    const plan=planFolder(Array.from(list),addedFiles.current);const files=plan.selected;
    setPrepared(undefined);setBatch(files.map(({file})=>({name:relativePath(file),status:"queued"})));
    if(!files.length){setError(plan.alreadyAdded?"Every supported file in that folder was already added.":"No PDF, DOCX, or CSV files found in that folder.");return;}
    const plural=(n:number,word:string)=>`${n} ${word}${n===1?"":"s"}`;const is=(n:number)=>n===1?"is":"are";const was=(n:number)=>n===1?"was":"were";
    const notes=[plan.unsupported&&`${plural(plan.unsupported,"file")} that ${is(plan.unsupported)} not PDF, DOCX, or CSV ${was(plan.unsupported)} skipped.`,plan.alreadyAdded&&`${plural(plan.alreadyAdded,"file")} already added ${was(plan.alreadyAdded)} skipped.`,plan.overflow&&`${plural(plan.overflow,"more file")} will not be added in this batch (limit ${MAX_FOLDER_FILES}). Select the same folder again afterwards; files already added are skipped.`].filter(Boolean) as string[];
    setBusy(true);setError(notes.join(" "));
    const update=(i:number,patch:Partial<BatchItem>)=>{if(mounted.current)setBatch(b=>b.map((item,j)=>j===i?{...item,...patch}:item));};
    let registered=0;let failed=0;let undispatched=false;let blocked=false;
    // The workspace allows only one active indexing job at a time, so files are added one at a
    // time, each driven to completion, rather than in parallel (see awaitJobCompletion).
    try {
      await restore();lastRefresh.current=Date.now();
      for(let i=0;i<files.length;i++) {
        const {file,kind:fileKind}=files[i];
        if(blocked){failed++;update(i,{status:"failed",error:"Not attempted: an earlier file left the workspace's indexing slot busy. Resolve it in Ingestion activity, then add this folder again."});continue;}
        const retry=<T,>(run:()=>Promise<T>)=>withRateLimitRetry(run,{onWait:s=>update(i,{status:"waiting",error:`Rate limited; retrying in ${s}s`})});
        try {
          update(i,{status:"uploading",error:undefined});
          if(Date.now()-lastRefresh.current>TOKEN_REFRESH_MS){lastRefresh.current=Date.now();await restore();}
          const {id,pathname}=await uploadOne(file,fileKind);
          let columns={};
          if(fileKind==="csv") {
            const preview=await retry(()=>previewCsv(pathname));
            if(preview.columns.length>MAX_CSV_COLUMNS)throw new Error(`CSV files can have at most ${MAX_CSV_COLUMNS} columns.`);
            const choice=csvColumnChoice(preview.columns);columns={text_columns:choice.text,metadata_columns:choice.metadata};
          }
          const job=await retry(()=>post<Job>("jobs",{id,kind:fileKind,pathname,filename:file.name,...columns}));
          if(job.dispatched===false) {
            // Nothing will advance this job automatically, and it holds the workspace's only slot.
            addedFiles.current.add(fingerprint(file));registered++;undispatched=true;blocked=true;
            update(i,{status:"registered",error:undefined});continue;
          }
          update(i,{status:"indexing",error:undefined});
          const finished=await awaitJobCompletion(job.id,{getJob:jid=>api<Job>(`jobs/${jid}`),cancelJob:jid=>post(`jobs/${jid}/cancel`,{})});
          if(finished.status==="complete") {addedFiles.current.add(fingerprint(file));registered++;update(i,{status:"registered",error:undefined});}
          else {failed++;update(i,{status:"failed",error:finished.error||"Indexing failed."});if(!finished.slotFreed)blocked=true;}
        }catch(e){failed++;update(i,{status:"failed",error:message(e)});if(e instanceof ApiError&&e.status===409)blocked=true;}
      }
    } catch(e){if(mounted.current)setError(message(e));return;}
    finally{if(mounted.current)setBusy(false);}
    if(!mounted.current)return;
    if(undispatched)notes.push("Workflow dispatch is pending for one file; resolve it in Ingestion activity before adding the rest of this folder.");
    else if(blocked)notes.push("Stopped early: a file's indexing job could not be cleared. Resolve it in Ingestion activity, then add the remaining files again.");
    if(registered===files.length&&!notes.length){onAdded();return;}
    if(registered)onChanged();
    setError([failed&&`${failed} of ${files.length} files failed.`,registered&&"Registered files are indexed in the background; follow progress in Ingestion activity.",...notes].filter(Boolean).join(" "));
  }
  async function submit(event:FormEvent<HTMLFormElement>) {
    event.preventDefault();const f=new FormData(event.currentTarget);setBusy(true);setError("");
    try {
      if(kind==="database") {const tables=String(f.get("tables")).split(",").filter(Boolean).map(t=>{const [qualified,tenant]=t.trim().split(":");const parts=qualified.split(".");return {schema_name:parts.length>1?parts[0]:"public",table_name:parts.at(-1),...(tenant?{tenant_column:tenant}:{})};});await post("sources/database",{workspace_id:user.workspace_id,name:f.get("name"),secret_env_var:f.get("secret"),isolation_mode:f.get("isolation"),tables});}
      else {const result=await post<Job>("jobs",kind==="website"?{id:crypto.randomUUID(),kind,url:f.get("url"),crawl_same_domain:f.get("crawl")==="on",page_limit:Number(f.get("page_limit"))}:{id:prepared?.id,kind,pathname:prepared?.pathname,filename:prepared?.filename,text_columns:textColumns,metadata_columns:metadataColumns,row_id_column:f.get("row_id")||undefined});if(result.dispatched===false){setError("Source registered. Workflow dispatch is pending; use Resume in ingestion activity once the service is available.");return;}}
      onAdded();
    }catch(e){setError(message(e));}finally{setBusy(false);}
  }
  return <section className="card add-source"><h3>Add a source</h3><div className="tabs">{["pdf","docx","website","csv","database"].map(k=><button key={k} className={kind===k?"selected":""} onClick={()=>{setKind(k);setPrepared(undefined);setBatch([]);setError("");}} disabled={busy}><SourceIcon type={k}/>{k.toUpperCase()}</button>)}</div><form onSubmit={submit}>
    {["pdf","docx","csv"].includes(kind)?<><label className="dropzone"><FileText size={28}/><strong>{prepared?prepared.filename:`Choose a ${kind.toUpperCase()} file`}</strong><span>Up to 25 MB · stored privately</span><input type="file" accept={`.${kind}`} disabled={busy} onChange={e=>{if(e.target.files?.[0])void prepare(e.target.files[0]);}}/></label><button type="button" className="text-button" disabled={busy} onClick={()=>folderInput.current?.click()}>Or upload a whole folder (PDF, DOCX, and CSV files)</button><input ref={folderInput} type="file" hidden multiple webkitdirectory="" disabled={busy} onChange={e=>{void uploadFolder(e.target.files);e.target.value="";}}/><p className="small">In a folder, CSV text columns are indexed as searchable text and other columns as metadata. Upload a CSV on its own to choose columns.</p>{batch.length>0&&<><p className="small" role="status">{batch.filter(b=>["registered","failed"].includes(b.status)).length} of {batch.length} files processed</p><ul className="batch-list">{batch.map(b=><li key={b.name}><span className="batch-name" title={b.name}>{b.name}</span><span className={`status ${b.status==="registered"?"ready":b.status==="failed"?"failed":["uploading","waiting","indexing"].includes(b.status)?"running":""}`}>{b.status}</span>{b.error&&<small className={`batch-error ${b.status}`}>{b.error}</small>}</li>)}</ul></>}{prepared?.preview&&<><p>{prepared.preview.row_count} rows · choose columns to index</p><div className="columns-choice">{prepared.preview.columns.map(c=><div key={c.name}><strong>{c.name}</strong><small>{c.inferred_type}</small><label className="check"><input type="checkbox" checked={textColumns.includes(c.name)} onChange={e=>setTextColumns(v=>e.target.checked?[...v,c.name]:v.filter(x=>x!==c.name))}/>Searchable text</label><label className="check"><input type="checkbox" checked={metadataColumns.includes(c.name)} onChange={e=>setMetadataColumns(v=>e.target.checked?[...v,c.name]:v.filter(x=>x!==c.name))}/>Metadata</label></div>)}</div><label>Row identifier<select name="row_id"><option value="">Use row number</option>{prepared.preview.columns.map(c=><option key={c.name}>{c.name}</option>)}</select></label><details><summary>Preview rows</summary><pre>{JSON.stringify(prepared.preview.sample_rows,null,2)}</pre></details></>}</>:
    kind==="website"?<><label>Website URL<input name="url" type="url" placeholder="https://example.com/knowledge" required/></label><div className="form-row"><label>Page limit<input name="page_limit" type="number" defaultValue={5} min={1} max={20}/></label><label className="check"><input type="checkbox" name="crawl"/>Follow links on the same domain</label></div></>:
    <><label>Source name<input name="name" required placeholder="Sales database"/></label><label>Provisioned connection reference<input name="secret" required placeholder={`WORKSPACE_${user.workspace_id.replaceAll("-","_").toUpperCase()}_SALES`}/><small>Ask your workspace administrator to configure this connection securely.</small></label><label>Isolation<select name="isolation"><option value="dedicated">Dedicated database</option><option value="shared">Shared tables with tenant columns</option></select></label><label>Allowed tables<input name="tables" required placeholder="public.sales:workspace_id, public.products:workspace_id"/></label></>}
    {error&&<Notice>{error}</Notice>}<button className="button primary" disabled={busy||(["pdf","docx","csv"].includes(kind)&&!prepared)}>{busy?<Busy/>:<Plus size={17}/>}Add to knowledge base</button></form></section>;
}

function Evaluation() {
  const [listing,setListing]=useState<{reports:{report_id:string;configuration_name:string;passed:boolean|null;created_at:string}[];invalid_report_ids:string[]}>();const [report,setReport]=useState<unknown>();const [error,setError]=useState("");
  useEffect(()=>{api<typeof listing>("evaluations/reports").then(setListing).catch(e=>setError(message(e)));},[]);
  return <><div className="page-heading"><div><span className="eyebrow">MEASURE WHAT MATTERS</span><h1>Evaluation</h1><p>Inspect retrieval quality, citations, and deployment gates.</p></div></div>{error&&<Notice>{error}</Notice>}{listing?.invalid_report_ids.length ? <Notice>{listing.invalid_report_ids.length} reports could not be validated.</Notice>:null}<section className="card">{listing?.reports.length?listing.reports.map(r=><button className="report-row" key={r.report_id} onClick={()=>api(`evaluations/reports/${encodeURIComponent(r.report_id)}`).then(setReport).catch(e=>setError(message(e)))}><ChartNoAxesCombined size={20}/><span><strong>{r.configuration_name}</strong><small>{new Date(r.created_at).toLocaleDateString()}</small></span><span className={`status ${r.passed?"ready":"failed"}`}>{r.passed===null?"Not gated":r.passed?"Passed":"Needs attention"}</span><ChevronRight size={17}/></button>):<div className="empty-state"><ChartNoAxesCombined size={32}/><h3>Quality starts with a baseline</h3><p>No evaluation reports have been published to your workspace yet.</p></div>}</section>{report!=null&&<ReportView value={report}/>}</>;
}
function ReportView({value}:{value:unknown}) {const r=value as Record<string,unknown>;return <section className="card inspector"><h3>Benchmark report</h3><div className="metric-grid">{Object.entries((r.aggregate||{}) as Record<string,unknown>).filter(([,v])=>typeof v==="number").map(([k,v])=><div className="stat" key={k}><span>{k.replaceAll("_"," ")}</span><strong>{Number(v).toFixed(3)}</strong></div>)}</div><details open><summary>Cases, slices, and deployment gates</summary><pre>{JSON.stringify(value,null,2)}</pre></details></section>;}
function Preferences({user}:{user:User}) {
  const [settings,setSettings]=useState<Record<string,unknown>>();const [error,setError]=useState("");
  useEffect(()=>{api<Record<string,unknown>>("settings").then(setSettings).catch(e=>setError(message(e)));},[]);
  return <><div className="page-heading"><div><span className="eyebrow">YOUR WORKSPACE, AT A GLANCE</span><h1>Settings</h1><p>Account details and effective application configuration.</p></div></div>{error&&<Notice>{error}</Notice>}<section className="card settings-card"><h3>Account & privacy</h3><dl><dt>Email</dt><dd>{user.email}</dd><dt>Workspace</dt><dd>{user.workspace_id}</dd><dt>Access</dt><dd><ShieldCheck size={15}/> Private to your account</dd></dl></section><section className="card settings-card"><h3>Application configuration</h3><p className="muted">Read-only settings. Credentials are never displayed.</p><dl>{Object.entries(settings||{}).map(([key,value])=><div className="setting-row" key={key}><dt>{key.replaceAll("_"," ")}</dt><dd>{typeof value==="boolean"?value?"Enabled":"Disabled":String(value)}</dd></div>)}</dl></section></>;
}
