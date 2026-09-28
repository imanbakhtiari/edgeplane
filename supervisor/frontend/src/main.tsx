import React, { useState, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, NavLink, Route, Routes, useSearchParams } from 'react-router-dom';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { Activity, Network, Server, Globe, Shield, Gauge, ListChecks, FileText, Settings, Layers, Sun, Moon, ChevronsLeft, ChevronsRight, Users as UsersIcon, SlidersHorizontal, BarChart3, LockKeyhole, Search, Plus, RefreshCw, ArrowUpRight, X, ChevronRight, LogOut, CheckCircle2, AlertCircle, Terminal } from 'lucide-react';
import { api, getCSRF, setCSRF, type Row } from './api';
import { Terminal as XTerminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import './style.css';
import './layout.css';
import './operations.css';
import Preferences from './pages/Preferences';
import Users from './pages/Users';
import Monitoring from './pages/Monitoring';
import Traffic from './pages/Traffic';
import VhostSecurity from './pages/VhostSecurity';
import DNSSettings from './pages/DNSSettings';
import TerminalDock, {openTerminal} from './components/TerminalDock';
import OperatorHelp from './components/OperatorHelp';
import NodeMetrics from './components/NodeMetrics';
import {Badge,Empty,ErrorBox,Modal,Field,Table,ConfirmDialog,LabelText} from './components/ui';
const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
const initialTheme=localStorage.getItem('edgeplane-theme')||'system';
document.documentElement.dataset.theme=initialTheme==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):initialTheme;
const nav = [['Dashboard','/',Activity],['Monitoring','/monitoring',Activity],['Routing health','/routing-health',Network],['Traffic','/traffic',BarChart3],['Customers','/customers',UsersIcon],['Topology','/topology',Network],['Nodes','/agents',Server],['Vhosts','/vhosts',Globe],['Cache policies','/cache-policies',Layers],['Rate limits','/rate-limit-policies',Gauge],['Real IP','/real-ip-policies',Shield],['Headers','/header-policies',FileText],['Certificates','/certificates',LockKeyhole],['DNS','/dns',Network],['Records','/dns/records',FileText],['IP addresses','/dns/routing',Network],['Subnets & BGP','/dns/bgp',Network],['Connection','/dns/connection',Settings],['Jobs','/jobs',ListChecks],['Logs','/logs',Terminal],['Audit log','/audit',FileText],['Users','/users',UsersIcon],['Preferences','/preferences',SlidersHorizontal],['Settings','/settings',Settings]] as const;
const navSection:Record<string,string>={'/':'dashboard','/agents':'nodes','/dns/records':'dns','/dns/routing':'dns','/dns/bgp':'dns','/dns/connection':'dns'};
const titles = Object.fromEntries(nav.map(([label,path])=>[path,label]));
function Login({onLogin}:{onLogin:(v:Row)=>void}) {const [name,setName]=useState('admin'),[password,setPassword]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);return <div className="login">
<form onSubmit={async e=>{e.preventDefault();setBusy(true);try{const me=await api('/auth/login','POST',{username:name,password});setCSRF(me.csrf);onLogin(me)}catch(e){setError(String(e))}finally{setBusy(false)}}}>
<div className="brand">
<Layers/> Edgeplane</div>
<h1>Your network.<br/>One control plane.</h1>
<p>Sign in to manage your CDN infrastructure.</p>
<Field label="Username" value={name} onChange={setName} required/>
<Field label="Password" value={password} onChange={setPassword} type="password" required/>
<ErrorBox error={error}/>
<button className="primary" disabled={busy}>{busy?'Signing in…':'Sign in'}<ArrowUpRight size={17}/>
</button>
<small>Supervisor · Infrastructure management</small>
</form>
</div>}
function Password({done}:{done:()=>void}) {const [old,setOld]=useState(''),[next,setNext]=useState(''),[error,setError]=useState('');return <div className="login">
<form onSubmit={async e=>{e.preventDefault();try{await api('/auth/password','POST',{old_password:old,new_password:next});done()}catch(e){setError(String(e))}}}>
<h2>Secure your administrator account</h2>
<p>Replace the bootstrap password before managing infrastructure.</p>
<Field label="Current password" value={old} onChange={setOld} type="password"/>
<Field label="New password · minimum 12 characters" value={next} onChange={setNext} type="password"/>
<ErrorBox error={error}/>
<button className="primary">Change password</button>
</form>
</div>}
function Dashboard({topology=false}:{topology?:boolean}) {const nodes=useQuery<Row[]>({queryKey:['agents'],queryFn:()=>api('/agents'),refetchInterval:10000}),hosts=useQuery<Row[]>({queryKey:['vhosts'],queryFn:()=>api('/vhosts')}),jobs=useQuery<Row[]>({queryKey:['jobs'],queryFn:()=>api('/jobs'),refetchInterval:3000});const ns=nodes.data||[];if(nodes.isLoading)return <div className="skeleton"/>;return <>
<div className="pagehead">
<div className="eyebrow">NETWORK OVERVIEW</div>
<h1>{topology?'Your edge network':'Infrastructure at a glance'}</h1>
<p>Desired state, node health, and deployments across your network.</p>
</div>
<div className="stats">{[['Nodes',ns.length],['Healthy',ns.filter(n=>n.status==='READY').length],['Active vhosts',hosts.data?.filter(v=>v.enabled).length||0],['Out of sync',ns.filter(n=>n.desired_revision!==n.applied_revision).length]].map(([label,value])=>
<div className="stat" key={label}>
<span>{label}</span>
<strong>{value}</strong>
<small>Live control plane state</small>
</div>)}</div>
<div className="sectionhead">
<h2>Edge topology</h2>
<span className="subtle">DNS / unicast</span>
</div>
<div className="topology">
<div className="control">
<Layers size={22}/>
<div>
<b>SUPERVISOR</b>
<small>PostgreSQL desired state</small>
</div>
<Badge value="CONNECTED"/>
</div>
<div className="connector"/>
<div className="nodegrid">{ns.map(n=>
<NavLink className="nodecard" key={n.id} to={'/agents?id='+n.id}>
<div className="sectionhead">
<Server size={21}/>
<Badge value={n.demo?'DEMO':n.maintenance?'MAINTENANCE':n.status}/>
</div>
<h3>{n.name}</h3>
<p>{n.city||'Location not set'} · {n.provider||'Provider not set'}</p>
<code>{n.public_ipv4||n.hostname}</code>
<div className="nodefoot">
<span>NGINX → Varnish</span>
<b>rev {n.applied_revision}</b>
</div>
</NavLink>)}</div>{!ns.length&&<Empty title="Your network starts here" text="Add a node to provision an edge and synchronize all active vhosts."/>}<div className="origin-label">↓ Customer origin pools</div>
</div>
<div className="sectionhead">
<h2>Recent operations</h2>
<NavLink to="/jobs">View all <ArrowUpRight size={14}/>
</NavLink>
</div>
<Table rows={(jobs.data||[]).slice(0,6)} columns={['kind','status','created_at']}/>
<ErrorBox error={nodes.error||jobs.error}/>
</>}
const blankHost:Row={name:'',domains:[],origins:[{host:'',port:80,scheme:'http',tls_verify:false}],enabled:true,options:{},deploy:true};
function SshCredentialFields({cred,setCred}:{cred:Row,setCred:(next:Row)=>void}) {
 const mode=cred.auth_mode||'password';
 return <>
  <div className="formgrid">
<Field label="SSH username" value={cred.username} onChange={v=>setCred({...cred,username:v})}/>
<Field label="SSH port" type="number" value={cred.port} onChange={v=>setCred({...cred,port:Number(v)})}/>
</div>
  <label>SSH authentication method<select value={mode} onChange={e=>setCred({...cred,auth_mode:e.target.value})}>
<option value="password">Password only</option>
<option value="private_key">SSH key (dedicated key by default)</option>
<option value="key_and_password">SSH key + SSH password</option>
<option value="supervisor_key">Dedicated Supervisor key (legacy)</option>
</select>
</label>
  {['password','key_and_password'].includes(mode)&&<Field label="SSH login password" type="password" value={cred.password} onChange={v=>setCred({...cred,password:v})} help="Linux SSH login password, separate from the sudo password. A publickey-only server rejects this method."/>}
  {['private_key','key_and_password'].includes(mode)&&<label>SSH private key (optional; overrides dedicated key)<textarea value={cred.private_key} onChange={e=>setCred({...cred,private_key:e.target.value})} placeholder="Leave blank to use supervisor/ssh_keys/edgeplane_ed25519; or paste the private key authorized on this server"/>
</label>}
  {mode==='supervisor_key'&&<p>The node must authorize the dedicated Edgeplane public key. The Supervisor container must be able to read its mounted private key.</p>}
  {cred.username!=='root'&&<>
<label className="check">
<input type="checkbox" checked={cred.sudo_password_required} onChange={e=>setCred({...cred,sudo_password_required:e.target.checked,sudo_password:e.target.checked?cred.sudo_password:''})}/>Sudo password required</label>{cred.sudo_password_required&&<Field label="Sudo password" help="Used only for sudo -S after SSH authentication; it does not log in to SSH." value={cred.sudo_password} onChange={v=>setCred({...cred,sudo_password:v})} type="password"/>}</>}
  <p>Choose the method allowed by the remote sshd. Root needs no sudo; other users need passwordless sudo or the separate sudo password.</p>
 </>;
}
function sshTestMessage(result:Row){return result.success?`SSH connected with ${String(result.auth_mode||'configured').replaceAll('_',' ')} authentication; sudo: ${result.sudo}.`:`${result.stage}: ${result.message}`}
function VhostForm({initial,close}:{initial?:Row,close:()=>void}) {const [data,setData]=useState<Row>(initial?{...initial,deploy:true}:{...blankHost}),[tab,setTab]=useState('General'),[error,setError]=useState(''),[busy,setBusy]=useState(false),[certCreate,setCertCreate]=useState(false);const policies=useQuery({queryKey:['policies'],queryFn:async()=>Object.fromEntries(await Promise.all(['cache-policies','rate-limit-policies','real-ip-policies','header-policies','certificates'].map(async p=>[p,await api(p.startsWith('/')?p:'/'+p)])))});const set=(k:string,v:any)=>setData({...data,[k]:v});const setCertificate=(id:string|null)=>setData(current=>({...current,certificate_id:id,options:id?{...current.options,tls_mode:'terminate'}:{...current.options,tls_mode:'auto',redirect_https:false}}));const policyTab:Row={'Cache':['cache-policies','cache_policy_id'],'Rate Limiting':['rate-limit-policies','rate_policy_id'],'Real IP':['real-ip-policies','real_ip_policy_id'],'Headers':['header-policies','header_policy_id'],'TLS':['certificates','certificate_id']};async function save(){setBusy(true);try{const payload=Object.fromEntries(['name','domains','origins','enabled','options','cache_policy_id','rate_policy_id','real_ip_policy_id','header_policy_id','certificate_id','customer_id','deploy'].filter(k=>data[k]!==undefined).map(k=>[k,data[k]]));await api('/vhosts'+(initial?'/'+initial.id:''),initial?'PUT':'POST',payload);void client.invalidateQueries();close()}catch(e){setError(String(e))}finally{setBusy(false)}}return <><Modal title={initial?'Edit vhost':'Create vhost'} close={close}>
<div className="tabs">{['General','Customer','Origin','Cache','Rate Limiting','Real IP','Headers','TLS','Geography','Access rules','Traffic','Logging','Advanced','Review'].map(t=>
<button className={t===tab?'selected':''} key={t} onClick={()=>setTab(t)}>{t}</button>)}</div>
<div className="formbody">{['Customer','Geography','Access rules','Traffic'].includes(tab)&&<VhostSecurity data={data} set={set} tab={tab}/>}{tab==='General'&&<>
<Field label="Display name" required value={data.name} onChange={v=>set('name',v)}/>
<label>Domains · one per line<textarea value={data.domains.join('\n')} onChange={e=>set('domains',e.target.value.split('\n'))}/>
</label>
<label className="check">
<input type="checkbox" checked={data.enabled} onChange={e=>set('enabled',e.target.checked)}/> Vhost enabled</label>
</>}{tab==='Origin'&&<>{data.origins.map((origin:Row,i:number)=>
<div className="originform" key={i}>
<h3>Origin {i+1}</h3>
<Field label="Hostname / IP" value={origin.host} onChange={v=>set('origins',data.origins.map((o:Row,j:number)=>j===i?{...o,host:v}:o))}/>
<div className="formgrid">
<label>Scheme<select value={origin.scheme} onChange={e=>set('origins',data.origins.map((o:Row,j:number)=>j===i?{...o,scheme:e.target.value}:o))}>
<option>http</option>
<option>https</option>
</select>
</label>
<Field label="Port" type="number" value={origin.port} onChange={v=>set('origins',data.origins.map((o:Row,j:number)=>j===i?{...o,port:Number(v)}:o))}/>
</div>{origin.scheme==='https'&&/^\d{1,3}(\.\d{1,3}){3}$/.test(origin.host)&&origin.tls_verify&&!origin.sni&&<p className="warning">Verification is enabled for an IP origin. Set SNI to the certificate DNS name or verification will fail.</p>}{['host_header','sni'].map(k=>
<Field key={k} label={k.replace('_',' ')} value={origin[k]} onChange={v=>set('origins',data.origins.map((o:Row,j:number)=>j===i?{...o,[k]:v||null}:o))}/>)}
{origin.scheme==='https'&&<><label className="check"><input type="checkbox" checked={origin.tls_verify===true} onChange={e=>set('origins',data.origins.map((o:Row,j:number)=>j===i?{...o,tls_verify:e.target.checked}:o))}/> Verify the origin TLS certificate</label><p>Off forwards encrypted origin traffic without validating its certificate. Enable only with a trusted certificate and matching SNI.</p></>}<label className="check">
<input type="checkbox" checked={origin.backup||false} onChange={e=>set('origins',data.origins.map((o:Row,j:number)=>j===i?{...o,backup:e.target.checked}:o))}/> Backup origin</label>{i>0&&<button onClick={()=>set('origins',data.origins.filter((_:any,j:number)=>j!==i))}>Remove origin</button>}</div>)}<button onClick={()=>set('origins',[...data.origins,{host:'',port:80,scheme:data.origins[0].scheme,backup:true,tls_verify:false}])}>
<Plus size={16}/>Add origin</button>
</>}{policyTab[tab]&&<>
<p>{tab==='TLS'?'Port 80 proxies and caches HTTP or HTTPS origins. On port 443, choose POP TLS termination or encrypted passthrough to an HTTPS origin.':`Choose a reusable ${tab.toLowerCase()} policy. Defaults are resolved from PostgreSQL on each revision.`}</p>
{tab==='TLS'&&<><label>HTTPS mode<select value={data.options.tls_mode||'auto'} onChange={e=>setData(current=>({...current,certificate_id:['http_only','passthrough'].includes(e.target.value)?null:current.certificate_id,options:{...current.options,tls_mode:e.target.value,redirect_https:false}}))}><option value="auto">Automatic — POP certificate, otherwise HTTPS origin passthrough</option><option value="terminate">Terminate TLS at POP — certificate required</option><option value="passthrough">TLS passthrough — HTTPS origin required</option><option value="http_only">HTTP only — explicitly disable HTTPS for this domain</option></select></label><div className="originform"><strong>Browser → POP → origin</strong><p>With a POP certificate: HTTPS is decrypted here, cached, and forwarded to your HTTP or HTTPS origin.</p><p>Without a POP certificate: automatic mode forwards encrypted HTTPS to an HTTPS origin. The browser verifies the origin certificate for the requested domain. HTTPS caching, HTTP rules, origin Host/SNI overrides, and HTTP analytics are not available on this passthrough path; the origin sees the POP address.</p><p>An HTTP-only origin cannot handle browser HTTPS without a POP certificate. Plain HTTP will always be labelled “Not secure” by browsers.</p></div></>}
<label>{tab}<select value={data[policyTab[tab][1]]||''} onChange={e=>tab==='TLS'?setCertificate(e.target.value||null):set(policyTab[tab][1],e.target.value||null)}>
<option value="">{tab==='TLS'?'No POP certificate — use selected HTTPS mode':'Global default'}</option>{policies.data?.[policyTab[tab][0]]?.map((p:Row)=>
<option key={p.id} value={p.id}>{p.name}</option>)}</select>
</label>
{tab==='TLS'&&<label className="check"><input type="checkbox" disabled={!data.certificate_id} checked={data.options.redirect_https===true} onChange={e=>set('options',{...data.options,redirect_https:e.target.checked})}/> Redirect HTTP clients to HTTPS after edge TLS is active</label>}
{tab==='TLS'&&!data.certificate_id&&<div className="warning">{data.origins.every((o:Row)=>o.scheme==='https')&&data.options.tls_mode!=='http_only'?'HTTPS will pass through to the origin. Every origin must present a valid certificate for each public domain.':'To serve browser HTTPS with an HTTP origin, select or create a POP certificate.'} <button type="button" onClick={()=>setCertCreate(true)}>Create manual or Certbot certificate</button></div>}
</>}{tab==='Logging'&&<label className="check">
<input type="checkbox" checked={data.options.logging!==false} onChange={e=>set('options',{...data.options,logging:e.target.checked})}/>Enable separate JSON access logs</label>}{tab==='Advanced'&&<>
<Field label="Maximum request body (MB)" type="number" value={data.options.max_body_mb||32} onChange={v=>set('options',{...data.options,max_body_mb:Number(v)})}/>
<label className="check">
<input type="checkbox" checked={data.options.websocket!==false} onChange={e=>set('options',{...data.options,websocket:e.target.checked})}/>Enable WebSocket bypass</label>
</>}{tab==='Review'&&<>
<h3>Desired state summary</h3>{!data.certificate_id&&<p className="warning">{data.origins.every((o:Row)=>o.scheme==='https')&&data.options.tls_mode!=='http_only'?'HTTPS passthrough: origin certificate is presented directly to browsers; only HTTP traffic uses the POP cache.':'No HTTPS certificate or HTTPS origin: browser HTTPS is unavailable. Configure POP TLS termination to enable it.'}</p>}<pre>{JSON.stringify(data,null,2)}</pre>
<p>Saving creates an immutable revision. Every active node receives the full desired configuration.</p>
</>}<ErrorBox error={error}/>
</div>
<footer>
<span>Saving immediately reconciles this vhost to every active POP.</span>
<button className="primary" onClick={save} disabled={busy}>{busy?'Saving…':'Save & deploy'}</button>
</footer>
</Modal>{certCreate&&<JsonForm resource="certificates" close={()=>{setCertCreate(false);policies.refetch()}}/>}</>}
function NodeForm({close}:{close:()=>void}) {
 const [data,setData]=useState<Row>({name:'',hostname:'',management_url:'',city:'',country:'',provider:''});
 const [cred,setCred]=useState<Row>({username:'root',port:22,auth_mode:'private_key',password:'',private_key:'',sudo_password_required:false,sudo_password:'',firewall:false,management_cidrs:[]});
 const [node,setNode]=useState<Row|null>(null),[fingerprint,setFingerprint]=useState<Row|null>(null),[test,setTest]=useState<Row|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const activities=useQuery<Row[]>({queryKey:['agent-activities',node?.id],queryFn:()=>api(`/agents/${node!.id}/activities`),enabled:!!node,refetchInterval:3000});
 async function inspect(){
  setBusy(true);setError('');setTest(null);
  try{const n=await api('/agents','POST',data);await api(`/agents/${n.id}/credentials`,'PUT',cred);setNode(n);setFingerprint(await api(`/agents/${n.id}/fingerprint`))}catch(e){setError(String(e))}finally{setBusy(false)}
 }
 async function testConnection(){
  setBusy(true);setError('');
  try{setTest(await api(`/agents/${node!.id}/test-ssh`,'POST',{host_key:fingerprint!.host_key}))}catch(e){setError(String(e))}finally{setBusy(false)}
 }
 async function retryFingerprint(){setBusy(true);setError('');try{setFingerprint(await api(`/agents/${node!.id}/fingerprint`))}catch(e){setError(String(e))}finally{setBusy(false)}}
 async function provision(){
  setBusy(true);setError('');
  try{await api(`/agents/${node!.id}/approve-host-key`,'POST',{host_key:fingerprint!.host_key});await api(`/agents/${node!.id}/provision`,'POST');await client.invalidateQueries();close()}catch(e){setError(String(e))}finally{setBusy(false)}
 }
 return <Modal title="Add CDN node" close={close}>
<div className="formbody">
<div className="notice">1. Enter node and SSH details → 2. Verify host fingerprint → 3. Test SSH and sudo → 4. Provision</div>{!node?<>
<div className="formgrid">{['name','hostname','management_url','public_ipv4','city','country','provider'].map(k=>
<Field key={k} label={k.replaceAll('_',' ')} value={data[k]} onChange={v=>setData({...data,[k]:v})}/>)}</div>
<h3>SSH bootstrap credentials</h3>
<SshCredentialFields cred={cred} setCred={setCred}/>
</>:<>
<h3>Verify this host fingerprint</h3>
<p>Compare this fingerprint with a trusted console before approval.</p>
<code className="fingerprint">{fingerprint?.fingerprint||'Host key retrieval failed'}</code>
<div className="actions">
<button disabled={busy} onClick={retryFingerprint}>
<RefreshCw size={15}/>Retry SSH discovery</button>
<button disabled={busy||!fingerprint} onClick={testConnection}>
<RefreshCw size={15}/>Test SSH & sudo</button>
</div>{test&&<div className={test.success?'notice':'error'}>{sshTestMessage(test)}</div>}<h3>Persistent activity log</h3>
<Table rows={activities.data||[]} columns={['stage','status','message','created_at']}/>
</>}<ErrorBox error={error}/>
</div>
<footer>
<button onClick={close}>Cancel</button>{!node?<button className="primary" disabled={busy} onClick={inspect}>{busy?'Connecting…':'Save & inspect host'}</button>:<button className="primary" disabled={busy||!fingerprint} onClick={provision}>{busy?'Working…':'Approve identity & provision'}</button>}</footer>
</Modal>
}
function JsonForm({resource,initial,close}:{resource:string,initial?:Row,close:()=>void}) {const [name,setName]=useState(initial?.name||''),[text,setText]=useState(JSON.stringify(initial?.config||{},null,2)),[error,setError]=useState(''),[cert,setCert]=useState(''),[key,setKey]=useState(''),[certMode,setCertMode]=useState('manual'),[challenge,setChallenge]=useState('http-01'),[domains,setDomains]=useState(''),[email,setEmail]=useState(''),[autoRenew,setAutoRenew]=useState(true);return <Modal title={titles['/'+resource]||resource} close={close}>
<form onSubmit={async e=>{e.preventDefault();try{const certificateBody=certMode==='manual'?{name,source:'manual',certificate:cert,private_key:key,domains:[]}:{name,source:'certbot',challenge,domains:domains.split('\n').map(v=>v.trim()).filter(Boolean),email,auto_renew:autoRenew,renew_before_days:30};await api('/'+resource+(initial?'/'+initial.id:''),initial?'PUT':'POST',resource==='certificates'?certificateBody:{name,config:JSON.parse(text),is_default:initial?.is_default||false});await client.invalidateQueries();close()}catch(e){setError(String(e))}}}>
<div className="formbody">
<Field label="Name" required value={name} onChange={setName}/>{resource==='certificates'?<>
<label>
<LabelText help="Manual accepts an existing PEM pair. Certbot can validate through every active POP with HTTP-01 or through PowerDNS with DNS-01. Keys are encrypted in PostgreSQL.">Certificate management</LabelText>
<select value={certMode} onChange={e=>setCertMode(e.target.value)}>
<option value="manual">Manual PEM upload</option>
<option value="certbot">Managed Certbot certificate</option>
</select>
</label>{certMode==='manual'?<>
<label>
<LabelText help="Full certificate chain in PEM format. Its SAN names must cover every vhost that selects it.">Certificate PEM</LabelText>
<textarea required value={cert} onChange={e=>setCert(e.target.value)}/>
</label>
<label>
<LabelText help="Unencrypted PEM private key matching the certificate. It is encrypted before storage and never returned by the API.">Private key PEM</LabelText>
<textarea required value={key} onChange={e=>setKey(e.target.value)}/>
</label>
</>:<>
<label>
<LabelText help="One hostname per line. PowerDNS must be authoritative for every name. Wildcards use DNS-01 automatically.">Domains</LabelText>
<textarea required value={domains} onChange={e=>setDomains(e.target.value)} placeholder={'cdn.example.com\n*.example.com'}/>
</label>
<label>Validation method<select value={challenge} onChange={e=>setChallenge(e.target.value)}>
<option value="http-01">HTTP-01 through active POPs</option>
<option value="dns-01">DNS-01 through PowerDNS</option>
</select></label>
<Field label="ACME account email" required value={email} onChange={setEmail} help="Email used by Let's Encrypt for expiry and account notices."/>
<label className="check">
<input type="checkbox" checked={autoRenew} onChange={e=>setAutoRenew(e.target.checked)}/>
<LabelText help="Reissue 30 days before expiration and deploy the renewed certificate as a new desired-state revision.">Automatic renewal</LabelText>
</label>
<p>{challenge==='http-01'?'Every requested domain must resolve to an active POP on port 80. The challenge is installed on all POPs before validation.':'PowerDNS must be authoritative and its native API-key authentication must pass the DNS connection test. DNS-01 is required for wildcard names.'}</p>
</>}</>:<>
<PolicyFields resource={resource} text={text} onChange={setText}/>
<p>Set one policy as the global default. A vhost can inherit that global policy or explicitly select another policy through the UI or API policy ID.</p>
</>}<ErrorBox error={error}/>
</div>
<footer>
<button type="button" onClick={close}>Cancel</button>
<button className="primary">{resource==='certificates'&&certMode==='certbot'?'Issue certificate':'Save policy'}</button>
</footer>
</form>
</Modal>}
function JobDetail({row,close}:{row:Row,close:()=>void}) {const events=useQuery<Row[]>({queryKey:['events',row.id],queryFn:()=>api(`/jobs/${row.id}/events`),refetchInterval:2000}),job=useQuery<Row>({queryKey:['job',row.id],queryFn:()=>api(`/jobs/${row.id}`),refetchInterval:2000});const [error,setError]=useState('');return <Modal title={row.kind+' · '+row.id.slice(0,8)} close={close}>
<div className="formbody">
<Badge value={job.data?.status}/>
<h3>Per-node results</h3>
<Table rows={job.data?.targets||[]} columns={['agent_id','status','result']}/>
<h3>Durable progress events</h3>
<div className="timeline">{events.data?.map(event=>
<div key={event.id}>{event.level==='ERROR'?<AlertCircle size={18}/>:<CheckCircle2 size={18}/>}<div>
<small>{new Date(event.created_at).toLocaleTimeString()}</small>
<pre>{event.message}</pre>
</div>
</div>)}</div>
<ErrorBox error={error}/>
</div>
<footer>
<button onClick={async()=>{try{await api(`/jobs/${row.id}/retry`,'POST');await client.invalidateQueries();close()}catch(e){setError(String(e))}}}>Retry failed targets</button>
</footer>
</Modal>}
function NodeEdit({node,close}:{node:Row,close:()=>void}) {
 const [data,setData]=useState<Row>({...node,labels:node.labels||{}}),[cred,setCred]=useState<Row>({username:'root',port:22,auth_mode:'private_key',password:'',private_key:'',sudo_password_required:false,sudo_password:'',firewall:false,management_cidrs:[]}),[replaceCred,setReplaceCred]=useState(false),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const summary=useQuery<Row>({queryKey:['agent-credential-summary',node.id],queryFn:()=>api(`/agents/${node.id}/credentials/summary`)});
 useEffect(()=>{if(summary.data?.configured)setCred(current=>({...current,username:summary.data!.username,port:summary.data!.port,auth_mode:summary.data!.auth_mode,sudo_password_required:summary.data!.sudo_password_required}))},[summary.data]);
 const save=async()=>{setBusy(true);setError('');try{const payload=Object.fromEntries(['name','hostname','management_url','public_ipv4','public_ipv6','city','country','provider','notes','labels'].map(k=>[k,['public_ipv4','public_ipv6'].includes(k)?(data[k]||null):(data[k]??(k==='labels'?{}:''))]));await api(`/agents/${node.id}`,'PUT',payload);if(replaceCred){await api(`/agents/${node.id}/credentials`,'PUT',cred)}await client.invalidateQueries();close()}catch(e){setError(String(e))}finally{setBusy(false)}};
 return <Modal title={`Edit ${node.name}`} close={close}>
<div className="formbody">
<div className="notice">Change node addressing here. SSH secrets are never displayed. Changing the SSH hostname requires fingerprint approval again.</div>
<div className="formgrid">{['name','hostname','management_url','public_ipv4','public_ipv6','city','country','provider'].map(k=>
<Field key={k} label={k.replaceAll('_',' ')} value={data[k]} onChange={v=>setData({...data,[k]:v})}/>)}</div>
<label className="check">
<input type="checkbox" checked={replaceCred} onChange={e=>setReplaceCred(e.target.checked)}/>Replace SSH user, port, or authentication settings</label>{replaceCred&&<>
<SshCredentialFields cred={cred} setCred={setCred}/>
</>}<ErrorBox error={error}/>
</div>
<footer>
<button onClick={close}>Cancel</button>
<button className="primary" disabled={busy} onClick={save}>{busy?'Saving…':'Save node settings'}</button>
</footer>
</Modal>
}
function ServiceDetail({node,service,close}:{node:Row,service:string,close:()=>void}) {
 const [confirm,setConfirm]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[result,setResult]=useState('');
 const logs=useQuery<Row>({queryKey:['node-service-logs',node.id,service],queryFn:()=>api(`/agents/${node.id}/services/${encodeURIComponent(service)}/logs`),retry:false});
 const restart=async()=>{setBusy(true);setError('');try{const response=await api<Row>(`/agents/${node.id}/services/${encodeURIComponent(service)}/restart`,'POST');setResult(response.success?(service==='nginx'?'NGINX validated and gracefully reloaded.':`${service} restarted.`):`${service} command failed: ${response.output||'Check logs.'}`);await logs.refetch();await client.invalidateQueries();setConfirm(false)}catch(e){setError(String(e));setConfirm(false)}finally{setBusy(false)}};
 return <>
<Modal title={`${node.name} · ${service}`} close={close}>
<div className="formbody">
<p>{service==='nginx'?'NGINX uses validation followed by nginx -s reload; no stop/start is sent.':'Restarting this service may briefly interrupt its function. Agent management can also be restarted over approved SSH.'}</p>
<div className="actions">
<button onClick={()=>logs.refetch()} disabled={logs.isFetching}>Refresh logs</button>
<button className="primary" disabled={busy} onClick={()=>setConfirm(true)}>{service==='nginx'?'Validate & reload NGINX':'Restart service'}</button>
</div>{result&&<div className="notice">{result}</div>}<ErrorBox error={error||logs.error}/>
<h3>Recent systemd journal · last 80 lines</h3>
<pre className="service-logs">{logs.isLoading?'Loading logs…':logs.data?.output||'No log entries returned.'}</pre>
</div>
</Modal>{confirm&&<ConfirmDialog title={service==='nginx'?'Graceful NGINX reload':`Restart ${service}`} message={service==='nginx'?`Validate NGINX and send nginx -s reload on ${node.name}?`:`Restart ${service} on ${node.name}? This may interrupt its current work.`} confirmLabel={service==='nginx'?'Validate & reload':'Restart service'} busy={busy} danger={service!=='nginx'} onCancel={()=>setConfirm(false)} onConfirm={restart}/>}</>;
}
function AppliedVhostDetail({node,vhost,close}:{node:Row,vhost:Row,close:()=>void}) {
 const [copied,setCopied]=useState(false),[copyError,setCopyError]=useState('');
 return <Modal title={`${vhost.name} · ${node.name}`} close={close}>
<div className="formbody">
<div className="node-summary">
<div>
<small>Vhost state</small>
<strong><Badge value={vhost.status}/></strong>
</div>
<div>
<small>Managed file</small>
<strong>{vhost.filename||'Not present on node'}</strong>
</div>
<div>
<small>Domains</small>
<strong>{vhost.domains?.join(', ')||'Legacy configuration'}</strong>
</div>
</div>
<h3>Desired versus current</h3>
<div className="hash-compare"><div><small>Desired state hash</small><code>{vhost.desired_hash||'Not desired'}</code></div><div><small>Current node hash</small><code>{vhost.applied_hash||'Not reported'}</code></div></div>
<p>This is the read-only file currently active on this POP. Edit the central vhost to validate and distribute its change to every active node.</p>
<div className="actions">
<a href={`/vhosts?id=${encodeURIComponent(vhost.id)}`}>
<button className="primary">Edit vhost for all nodes</button>
</a>
</div>
{vhost.content&&<><div className="sectionhead"><h3>Applied NGINX configuration</h3><button onClick={async()=>{try{await navigator.clipboard.writeText(vhost.content);setCopied(true);setCopyError('')}catch{setCopyError('Clipboard access denied. Select and copy the configuration below.')}}}>{copied?'Copied':'Copy configuration'}</button></div><ErrorBox error={copyError}/><pre className="config-viewer">{vhost.content}</pre></>}
</div>
</Modal>;
}
function VhostDeployment({vhost}:{vhost:Row}) {const state=useQuery<Row[]>({queryKey:['vhost-deployment',vhost.id],queryFn:()=>api(`/vhosts/${vhost.id}/deployment`),refetchInterval:10000});return <><div className="sectionhead"><h3>Desired and current state on all nodes</h3><button onClick={()=>state.refetch()} disabled={state.isFetching}><RefreshCw size={14}/>Refresh</button></div><p>Each node reports a hash of the vhost it actually applied. SYNCED means its current hash exactly matches the supervisor’s desired hash.</p>{state.isLoading?<div className="skeleton"/>:<Table rows={state.data||[]} columns={['node_name','city','status','last_seen']}/>}<ErrorBox error={state.error}/></>}
function VhostYamlEditor({vhost,close}:{vhost:Row,close:()=>void}) {const source=useQuery<Row>({queryKey:['vhost-yaml',vhost.id],queryFn:()=>api(`/vhosts/${vhost.id}/yaml`)}),[text,setText]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);useEffect(()=>{if(source.data?.yaml)setText(source.data.yaml)},[source.data]);const save=async()=>{setBusy(true);setError('');try{await api(`/vhosts/${vhost.id}/yaml`,'PUT',{yaml:text,deploy:true});await client.invalidateQueries();close()}catch(e){setError(String(e))}finally{setBusy(false)}};return <Modal title={`Edit YAML · ${vhost.name}`} close={close}><div className="formbody"><p>Schema-validated vhost YAML. Saving creates the desired state and automatically synchronizes every active node. Raw NGINX/VCL directives and certificate secrets are not accepted.</p><textarea className="yaml-editor" value={text} onChange={e=>setText(e.target.value)} spellCheck={false} aria-label="Vhost YAML"/><ErrorBox error={error||source.error}/></div><footer><button onClick={close}>Cancel</button><button className="primary" onClick={save} disabled={busy||source.isLoading}>{busy?'Validating & syncing…':'Save YAML & sync all nodes'}</button></footer></Modal>}
function SshTerminal({node,close}:{node:Row,close:()=>void}) {const host=React.useRef<HTMLDivElement>(null),term=React.useRef<XTerminal|null>(null),[error,setError]=useState('');useEffect(()=>{if(!host.current)return;const terminal=new XTerminal({cursorBlink:true,convertEol:true,fontSize:13,fontFamily:'ui-monospace, SFMono-Regular, Menlo, monospace',theme:{background:'#101419',foreground:'#dce4eb',cursor:'#7ad49b'}}),addon=new FitAddon();terminal.loadAddon(addon);terminal.open(host.current);addon.fit();term.current=terminal;const scheme=location.protocol==='https:'?'wss':'ws',ws=new WebSocket(`${scheme}://${location.host}/api/v1/ws/ssh/${node.id}?csrf=${encodeURIComponent(getCSRF())}`);ws.onopen=()=>{addon.fit();ws.send(JSON.stringify({type:'resize',cols:terminal.cols,rows:terminal.rows}))};ws.onmessage=e=>{try{const message=JSON.parse(e.data);if(message.type==='output')terminal.write(message.data);else if(message.type==='error'){setError(message.message);terminal.writeln(`\r\n\x1b[31m${message.message}\x1b[0m`)}}catch{terminal.write(e.data)}};ws.onclose=e=>terminal.writeln(`\r\n[SSH session closed${e.reason?`: ${e.reason}`:''}]`);const input=terminal.onData(data=>ws.readyState===WebSocket.OPEN&&ws.send(JSON.stringify({type:'input',data})));const resize=new ResizeObserver(()=>{addon.fit();if(ws.readyState===WebSocket.OPEN)ws.send(JSON.stringify({type:'resize',cols:terminal.cols,rows:terminal.rows}))});resize.observe(host.current);return()=>{resize.disconnect();input.dispose();ws.close();terminal.dispose()}},[node.id]);const paste=async()=>{try{const value=await navigator.clipboard.readText();term.current?.paste(value)}catch{setError('Clipboard permission was denied. Use Ctrl+Shift+V inside the terminal instead.')}};return <div className="terminal-overlay"><section className="terminal-window"><header><div><strong>SSH · {node.name}</strong><small>{node.hostname}</small></div><div className="actions"><button onClick={paste}>Paste clipboard</button><button onClick={close}><X size={16}/></button></div></header><div ref={host} className="terminal-host"/><ErrorBox error={error}/><footer><small>Connected as the configured SSH user. This session is authenticated and audited; sudo is not entered automatically.</small></footer></section></div>}
function AgentDetail({row,close}:{row:Row,close:()=>void}) {
 const [vhostSearch,setVhostSearch]=useState(''),[vhostPage,setVhostPage]=useState(0);
 const detail=useQuery<Row>({queryKey:['agent',row.id],queryFn:()=>api(`/agents/${row.id}`),refetchInterval:3000}),node={...row,...(detail.data||{})};
 const activities=useQuery<Row[]>({queryKey:['agent-activities',row.id],queryFn:()=>api(`/agents/${row.id}/activities`),refetchInterval:3000});
 const appliedVhosts=useQuery<Row[]>({queryKey:['agent-vhosts',row.id],queryFn:()=>api(`/agents/${row.id}/vhosts`),retry:false,refetchInterval:10000});
 const [pending,setPending]=useState<string|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[result,setResult]=useState(''),[sshTest,setSshTest]=useState<Row|null>(null),[fingerprint,setFingerprint]=useState<Row|null>(null),[editing,setEditing]=useState(false),[terminalOpen,setTerminalOpen]=useState(false),[selectedService,setSelectedService]=useState<string|null>(null),[selectedVhost,setSelectedVhost]=useState<Row|null>(null);
 const run=async(kind:string)=>{setBusy(true);setError('');setResult('');try{const deleting=kind==='delete',response=await api(deleting?`/agents/${row.id}`:`/agents/${row.id}/${kind}`,deleting?'DELETE':'POST',kind==='maintenance'?{enabled:!node.maintenance}:undefined);if(deleting){await client.invalidateQueries();close();return}setResult(response.id?`${kind.toUpperCase()} queued as job ${response.id}. Progress will update below.`:`Node ${kind} completed.`);await client.invalidateQueries();await activities.refetch();setPending(null)}catch(e){setError(String(e));setPending(null)}finally{setBusy(false)}};
 const testSsh=async()=>{setBusy(true);setError('');setSshTest(null);try{const response=await api(`/agents/${row.id}/test-approved-ssh`,'POST');setSshTest(response);await activities.refetch();await client.invalidateQueries()}catch(e){setError(String(e))}finally{setBusy(false)}};
 const discoverSsh=async()=>{setBusy(true);setError('');setFingerprint(null);try{setFingerprint(await api(`/agents/${row.id}/fingerprint`));await activities.refetch()}catch(e){setError(String(e))}finally{setBusy(false)}};
 const testDiscovered=async()=>{setBusy(true);setError('');try{setSshTest(await api(`/agents/${row.id}/test-ssh`,'POST',{host_key:fingerprint!.host_key}));await activities.refetch()}catch(e){setError(String(e))}finally{setBusy(false)}};
 const approveSsh=async()=>{setBusy(true);setError('');try{await api(`/agents/${row.id}/approve-host-key`,'POST',{host_key:fingerprint!.host_key});setFingerprint({...fingerprint,approved:true});setResult('SSH identity approved. Test SSH & sudo, then reprovision the node.');await activities.refetch()}catch(e){setError(String(e))}finally{setBusy(false)}};
 const messages:Row={reload:`Validate and reload NGINX on ${row.name}?`,reprovision:`Upgrade only the management Agent on ${row.name}? NGINX and Varnish configuration and processes will not be changed or signalled.`,disable:`Disable ${row.name} and remove it from DNS eligibility?`,activate:`Activate ${row.name} and resume health checks?`,delete:`Permanently delete ${row.name}, its credentials, health history, traffic data, and job targets?`};
 const confirmTitles:Row={reload:'Reload edge service',reprovision:'Upgrade management Agent',disable:'Disable edge node',activate:'Activate edge node',delete:'Delete edge node permanently'};
 const confirmLabels:Row={reload:'Validate & reload',reprovision:'Upgrade Agent only',disable:'Disable node',activate:'Activate node',delete:'Permanently delete'};
 const observedServices=Object.entries(node.observed?.services||{}) as [string,any][];
 const componentVersions=Object.entries(node.observed?.component_versions||{}) as [string,string][];
 const services=observedServices.length?observedServices:['nginx','varnish','cdn-agent','prometheus','prometheus-node-exporter','prometheus-nginx-exporter','prometheus-varnish-exporter'].map(name=>[name,{active:undefined,observed:'Live status unavailable; click for SSH logs and controls.'}] as [string,any]);
 const agentOnline=['READY','DEGRADED'].includes(node.status)||Boolean(node.last_seen&&Date.now()-new Date(node.last_seen).getTime()<10*60*1000);
 if(editing)return <NodeEdit node={node} close={()=>setEditing(false)}/>;
 return <>
<Modal title={node.name} close={close} wide>
<div className="formbody">
<div className="sectionhead">
<div>
<Badge value={node.status}/>
<span className="node-state-text">{agentOnline?'Agent API connected':'Agent API unavailable — configuration actions cannot reach this POP'}</span>
</div>
<small>Node ID · {node.id}</small>
</div>
<div className="node-summary">
<div>
<small>POP</small>
<strong>{node.city||'Not set'} · {node.country||'Not set'}</strong>
</div>
<div>
<small>Management API</small>
<strong>{node.management_url}</strong>
</div>
<div>
<small>Vhost desired/current state</small>
<strong>{node.vhosts_synced||0} / {node.vhosts_desired||0} synchronized</strong>
</div>
</div>
<p>Service cards below show component runtime state. Each vhost is compared separately using its desired and currently applied hashes.</p>
<NodeMetrics observed={node.observed||{}}/>
<div className="actions node-actions">
<button onClick={()=>setEditing(true)}>Edit node &amp; SSH</button>
<button disabled={busy} onClick={discoverSsh}>Discover SSH identity</button>
<button disabled={busy} onClick={testSsh}>Test approved SSH &amp; sudo</button>
<button disabled={busy} onClick={()=>openTerminal(node)}><Terminal size={14}/>Open SSH terminal</button>
<button disabled={busy||!agentOnline} title={!agentOnline?'Provision the Agent or restore its management API first':''} onClick={()=>run('sync-vhosts')}>Sync vhosts</button>
<button disabled={busy} onClick={()=>run('reprovision-services')} title="Idempotently install missing components and repair changed service configuration; unchanged services are not restarted">Reprovision all services</button>
<button disabled={busy||!agentOnline} onClick={()=>run('validate')}>Validate configuration</button>
<button disabled={busy||!agentOnline} onClick={()=>setPending('reload')}>Reload NGINX</button>
<button disabled={busy} className="primary" onClick={()=>setPending('reprovision')}>Upgrade Agent only</button>
<button disabled={busy||!node.observed?.previous_agent_version} onClick={()=>run('rollback-agent')} title="Restore the previous Agent executable; restarts management only">Rollback Agent{node.observed?.previous_agent_version?` to ${node.observed.previous_agent_version}`:''}</button>
<button disabled={busy} onClick={()=>run('maintenance')}>{node.maintenance?'Exit':'Enter'} maintenance</button>{node.active?<button disabled={busy} onClick={()=>setPending('disable')}>Disable node</button>:<button className="primary" disabled={busy} onClick={()=>setPending('activate')}>Activate node</button>}<button className="danger" disabled={busy} onClick={()=>setPending('delete')}>Delete node</button>
</div>{fingerprint&&<div className="identity-card">
<div>
<strong>Discovered SSH fingerprint</strong>
<code>{fingerprint.fingerprint}</code>
<small>{fingerprint.approved?'This identity is approved.':'Compare this fingerprint with the server console before approval.'}</small>
</div>
<div className="actions">
<button disabled={busy} onClick={testDiscovered}>Test this identity</button>{!fingerprint.approved&&<button className="primary" disabled={busy} onClick={approveSsh}>Approve identity</button>}</div>
</div>}{result&&<div className="notice">{result}</div>}{sshTest&&<div className={sshTest.success?'notice':'error'}>{sshTestMessage(sshTest)}</div>}<ErrorBox error={error}/>
<div className="sectionhead">
<h3>Remote services</h3>
<small>{node.last_seen?`Last report ${new Date(node.last_seen).toLocaleString()}`:'No successful health report yet'}</small>
</div>{services.length?<div className="service-grid">{services.map(([name,state])=>
<button className="service-card" key={name} type="button" onClick={()=>setSelectedService(name)} aria-label={`View ${name} logs and controls`}>
<span className="service-card-head"><strong>{name}</strong><Badge value={state.active===undefined?'UNKNOWN':state.active?'RUNNING':'DOWN'}/></span>
<small className="service-version">{(name==='cdn-agent'?node.observed?.agent_version:componentVersions.find(([component])=>component===name||component.replaceAll('_','-')===name.replace(/^prometheus-/,''))?.[1])||'Version unavailable'}</small>
<small className="service-observed">{state.observed||'No status detail'}</small>
</button>)}</div>:<div className="empty compact">
<Server size={24}/>
<h3>No remote service status</h3>
<p>The Supervisor cannot query the Agent API. Test SSH, correct the node settings if needed, then reprovision.</p>
</div>}
<div className="sectionhead">
<h3>Applied vhosts</h3>
<small>Desired versus current files on this node</small>
</div>
<input aria-label="Search applied vhosts" placeholder="Search name or domain…" value={vhostSearch} onChange={e=>{setVhostSearch(e.target.value);setVhostPage(0)}}/>
{appliedVhosts.isLoading?<div className="skeleton"/>:appliedVhosts.data?.length?<><div className="vhost-file-list">{appliedVhosts.data.filter(v=>`${v.name} ${(v.domains||[]).join(' ')}`.toLowerCase().includes(vhostSearch.toLowerCase())).slice(vhostPage*5,vhostPage*5+5).map(vhost=><button type="button" key={vhost.id} className="vhost-file" onClick={()=>setSelectedVhost(vhost)}><div><strong>{vhost.name}</strong><small>{vhost.domains?.join(', ')||vhost.filename||'Missing from node'}</small></div><Badge value={vhost.status}/></button>)}</div><div className="pagination"><button disabled={!vhostPage} onClick={()=>setVhostPage(vhostPage-1)}>Previous</button><span>Page {vhostPage+1} · 5 per page</span><button disabled={(vhostPage+1)*5>=appliedVhosts.data.filter(v=>`${v.name} ${(v.domains||[]).join(' ')}`.toLowerCase().includes(vhostSearch.toLowerCase())).length} onClick={()=>setVhostPage(vhostPage+1)}>Next</button></div></>:<div className="empty compact"><FileText size={24}/><h3>No vhost state reported</h3><p>Upgrade this node’s Agent, then synchronize vhosts.</p></div>}
<ErrorBox error={appliedVhosts.error}/>
<h3>Provisioning and node activity</h3>
<p>Newest events first. Actions are queued to the worker; this view refreshes every three seconds.</p>
<div className="activity-list">{(activities.data||[]).map((item:Row)=>
<div key={item.id} className="activity-row">
<Badge value={item.status}/>
<div>
<strong>{item.message}</strong>
<small>{item.stage} · {new Date(item.created_at).toLocaleString()}</small>
</div>
</div>)}</div>
</div>
</Modal>{terminalOpen&&<SshTerminal node={node} close={()=>setTerminalOpen(false)}/>} {selectedService&&<ServiceDetail node={node} service={selectedService} close={()=>setSelectedService(null)}/>}{selectedVhost&&<AppliedVhostDetail node={node} vhost={selectedVhost} close={()=>setSelectedVhost(null)}/>}{pending&&<ConfirmDialog title={confirmTitles[pending]} message={messages[pending]} confirmLabel={confirmLabels[pending]} danger={['reprovision','disable','delete'].includes(pending)} busy={busy} onCancel={()=>setPending(null)} onConfirm={()=>run(pending==='reprovision'?'reprovision-auto-approve':pending)}/>}</>;
}
function Resource({resource}:{resource:string}) {
 const [q,setQ]=useState(''),[offset,setOffset]=useState(0),[create,setCreate]=useState(false),[selected,setSelected]=useState<Row|null>(null),[error,setError]=useState(''),[deleteVhost,setDeleteVhost]=useState<Row|null>(null),[yamlVhost,setYamlVhost]=useState<Row|null>(null);
 const deferredQ=React.useDeferredValue(q);
 const query=useQuery<Row[]>({queryKey:[resource,offset,deferredQ],queryFn:()=>api('/'+resource+'?offset='+offset+'&q='+encodeURIComponent(deferredQ)),refetchInterval:5000});
 const [params]=useSearchParams();
 useEffect(()=>{const id=params.get('id');if(id&&query.data){const row=query.data.find(r=>r.id===id);if(row)setSelected(row)}},[params,query.data]);
 const rows=(query.data||[]).filter(r=>JSON.stringify(r).toLowerCase().includes(q.toLowerCase()));
 const columns:Record<string,string[]>={agents:['name','city','public_ipv4','active','status','last_seen','services_healthy','services_desired','service_state','vhosts_synced','vhosts_desired','vhost_state'],vhosts:['name','domains','enabled','cdn_hostname'],jobs:['kind','status','created_at'],audit:['action','resource','source_ip','created_at'],dns:['name','type','values','status'],certificates:['name','source','status','domains','auto_renew','expires_at']};
 const canCreate=['agents','vhosts','certificates','cache-policies','rate-limit-policies','real-ip-policies','header-policies'].includes(resource);
 async function action(path:string,body?:any){try{await api(path,'POST',body);await client.invalidateQueries();setSelected(null)}catch(e){setError(String(e))}}
 return <>
<div className="pagehead">
<div className="eyebrow">CONTROL PLANE</div>
<div className="sectionhead">
<h1>{titles['/'+resource]}</h1>
<div className="actions">{resource==='agents'&&<><button onClick={()=>action('/sync')}>
<RefreshCw size={16}/>Sync vhosts</button><button onClick={()=>action('/sync-services')}>
<RefreshCw size={16}/>Sync services</button></>}{resource==='dns'&&<button onClick={()=>action('/dns/reconcile')}>
<RefreshCw size={16}/>Reconcile DNS</button>}{canCreate&&<button className="primary" onClick={()=>setCreate(true)}>
<Plus size={17}/>{resource==='agents'?'Add CDN node':resource==='vhosts'?'Create vhost':'Create'}</button>}</div>
</div>
<p>{resource==='agents'?'Manage edge capacity, health and configuration drift.':resource==='vhosts'?'Publish customer domains with reusable traffic policies.':'Manage and inspect your network configuration.'}</p>
</div>
<div className="toolbar">
<Search size={17}/>
<input placeholder="Search these records…" value={q} onChange={e=>setQ(e.target.value)}/>
<span>{rows.length} records</span>
</div>
<ErrorBox error={error||query.error}/>{query.isLoading?<div className="skeleton"/>:<Table rows={rows} columns={columns[resource]||['name','is_default','config']} onClick={setSelected}/>}<div className="pagination">
<button disabled={!offset} onClick={()=>setOffset(Math.max(0,offset-100))}>Previous</button>
<span>Page {offset/100+1}</span>
<button disabled={(query.data?.length||0)<100} onClick={()=>setOffset(offset+100)}>Next</button>
</div>
 {create&&!selected&&(resource==='vhosts'?<VhostForm close={()=>setCreate(false)}/>:resource==='agents'?<NodeForm close={()=>setCreate(false)}/>:<JsonForm resource={resource} close={()=>setCreate(false)}/>)}
 {selected&&(resource==='jobs'?<JobDetail row={selected} close={()=>setSelected(null)}/>:resource==='vhosts'?<Modal title={selected.name} close={()=>setSelected(null)}>
<div className="formbody">
<h3>Customer DNS onboarding</h3>
<code>{selected.domains.join(', ')} → {selected.cdn_hostname}</code>
<p>Use a CNAME for subdomains. Apex domains require A/AAAA or provider ALIAS/ANAME.</p>
<VhostDeployment vhost={selected}/>
<div className="actions">
<button onClick={()=>setCreate(true)}>Edit vhost</button>
<button onClick={()=>setYamlVhost(selected)}><FileText size={15}/>Edit YAML</button>
<button onClick={()=>action(`/vhosts/${selected.id}/purge`,{paths:[]})}>Purge vhost cache</button>
<button className="danger" onClick={()=>setDeleteVhost(selected)}>Delete</button>
</div>
</div>{create&&<VhostForm initial={selected} close={()=>{setCreate(false);setSelected(null)}}/>}</Modal>:resource==='agents'?<AgentDetail row={selected} close={()=>setSelected(null)}/>:resource==='certificates'?<Modal title={selected.name} close={()=>setSelected(null)}>
<div className="formbody">
<Badge value={selected.status}/>
<p>{selected.source==='certbot'?`Managed by Certbot using ${selected.challenge||'dns-01'}. The encrypted key and full chain are deployed to every vhost and POP selecting this certificate.`:'Manually uploaded PEM certificate. Replace it by creating a new certificate and selecting it on the vhost.'}</p>
<Table rows={[selected]} columns={['source','challenge','domains','auto_renew','expires_at']}/>
<ErrorBox error={error}/>
</div>{selected.source==='certbot'&&<footer>
<button onClick={()=>action(`/certificates/${selected.id}/renew`)}>Renew now & deploy</button>
</footer>}</Modal>:resource.includes('policies')?<JsonForm resource={resource} initial={selected} close={()=>setSelected(null)}/>:<Modal title="Record details" close={()=>setSelected(null)}>
<pre className="formbody">{JSON.stringify(selected,null,2)}</pre>
</Modal>)}
 {deleteVhost&&<ConfirmDialog title="Delete vhost" message={`Delete ${deleteVhost.name} from desired state and every active edge?`} confirmLabel="Delete vhost" danger onCancel={()=>setDeleteVhost(null)} onConfirm={async()=>{try{await api(`/vhosts/${deleteVhost.id}`,'DELETE');setDeleteVhost(null);setSelected(null);await client.invalidateQueries()}catch(e){setError(String(e))}}}/>}
 {yamlVhost&&<VhostYamlEditor vhost={yamlVhost} close={()=>setYamlVhost(null)}/>}</>;
}
function Logs(){const [node,setNode]=useState(''),[vhost,setVhost]=useState(''),[search,setSearch]=useState(''),[live,setLive]=useState(true);const nodes=useQuery<Row[]>({queryKey:['agents'],queryFn:()=>api('/agents')}),hosts=useQuery<Row[]>({queryKey:['vhosts'],queryFn:()=>api('/vhosts')});const logs=useQuery({queryKey:['logs',node,vhost,search],queryFn:()=>api(`/vhosts/${vhost}/logs?agent_id=${node}&limit=100&search=${encodeURIComponent(search)}`),enabled:!!node&&!!vhost,refetchInterval:live?3000:false});return <>
<div className="pagehead">
<div className="eyebrow">OBSERVABILITY</div>
<h1>Request logs</h1>
<p>Bounded per-vhost JSON logs, retrieved securely from the selected edge.</p>
</div>
<div className="filters">
<select value={node} onChange={e=>setNode(e.target.value)}>
<option value="">Select node</option>{nodes.data?.map(n=>
<option value={n.id} key={n.id}>{n.name} · {n.city}</option>)}</select>
<select value={vhost} onChange={e=>setVhost(e.target.value)}>
<option value="">Select vhost</option>{hosts.data?.map(n=>
<option value={n.id} key={n.id}>{n.name}</option>)}</select>
<input placeholder="Path contains…" value={search} onChange={e=>setSearch(e.target.value)}/>
<label className="check">
<input type="checkbox" checked={live} onChange={e=>setLive(e.target.checked)}/>Live · 3s</label>
</div>
<ErrorBox error={logs.error}/>
<Table rows={logs.data?.items||[]} columns={['timestamp','method','uri','status','cache_status','real_client_ip','request_id']}/>
</>}
function SettingsPage(){const config=useQuery<Row>({queryKey:['settings'],queryFn:()=>api('/settings')}),revisions=useQuery<Row[]>({queryKey:['revisions'],queryFn:()=>api('/config-revisions')});const [error,setError]=useState(''),[saved,setSaved]=useState(false),[rollback,setRollback]=useState<Row|null>(null),[form,setForm]=useState<Row>({country_enabled:true,city_enabled:true,monitoring_mode:'full'});useEffect(()=>{if(config.data)setForm({country_enabled:config.data.maxmind?.country_enabled??true,city_enabled:config.data.maxmind?.city_enabled??true,monitoring_mode:config.data.monitoring?.mode||'full'})},[config.data]);const save=async()=>{try{await api('/settings','PUT',form);setSaved(true);setError('');await client.invalidateQueries({queryKey:['settings']})}catch(e){setError(String(e))}};return <>
<div className="pagehead">
<div className="eyebrow">CONTROL PLANE</div>
<h1>System settings</h1>
<p>Runtime controls are stored in PostgreSQL and survive container recreation. Environment values are bootstrap defaults.</p>
</div>
<ErrorBox error={error||config.error}/>
<div className="settings-grid">
<section className="panel">
<h2>Geographic data</h2>
<p>Control which MaxMind databases are sent to newly provisioned or reprovisioned POPs.</p>
<label className="check">
<input type="checkbox" checked={form.country_enabled} onChange={e=>setForm({...form,country_enabled:e.target.checked})}/>
<LabelText help="Enables country-level access rules and aggregate traffic geography. Requires GeoLite2-Country.mmdb.">Enable country detection</LabelText>
</label>
<label className="check">
<input type="checkbox" checked={form.city_enabled} onChange={e=>setForm({...form,city_enabled:e.target.checked})}/>
<LabelText help="Enables GeoNames city-ID matching. This is more precise but uses the larger GeoLite2-City.mmdb.">Enable city detection</LabelText>
</label>
<div className="setting-status">
<Badge value={config.data?.maxmind?.country_available?'COUNTRY READY':'COUNTRY MISSING'}/>
<Badge value={config.data?.maxmind?.city_available?'CITY READY':'CITY MISSING'}/>
</div>
<small>Changing this controls future provisioning. Reprovision nodes to add newly enabled databases.</small>
</section>
<section className="panel">
<h2>Monitoring load</h2>
<p>Health checks always remain enabled. Choose how much traffic telemetry the Supervisor collects from POPs.</p>
<label>
<LabelText help="Full stores hourly aggregate history. Metrics only keeps bounded counters without hourly PostgreSQL writes. Off stops traffic collection but keeps node health checks and agent Prometheus endpoints.">Collection mode</LabelText>
<select value={form.monitoring_mode} onChange={e=>setForm({...form,monitoring_mode:e.target.value})}>
<option value="full">Full · metrics and hourly history</option>
<option value="metrics_only">Metrics only · lower database load</option>
<option value="off">Off · health checks only</option>
</select>
</label>
<small>Use Metrics only during high load. No individual request URLs or client IPs are stored by this collector.</small>
</section>
</div>
<div className="actions settings-actions">
<button className="primary" onClick={save}>{saved?'Settings saved':'Save runtime settings'}</button>
<NavLink to="/dns">Configure and test PowerDNS <ArrowUpRight size={15}/>
</NavLink>
</div>
<section className="panel system-summary">
<h2>Bootstrap status</h2>
<div className="summary-grid">
<div>
<small>Environment</small>
<strong>{config.data?.environment}</strong>
</div>
<div>
<small>PowerDNS</small>
<strong>{config.data?.dns_configured?'Configured in database':'Not configured'}</strong>
</div>
<div>
<small>CDN zone</small>
<strong>{config.data?.cdn_zone}</strong>
</div>
<div>
<small>BGP</small>
<strong>{config.data?.bgp_enabled?'Enabled':'Disabled'}</strong>
</div>
</div>
</section>
<h2>Configuration revisions</h2>
<Table rows={revisions.data||[]} columns={['id','hash','created_at']} onClick={setRollback}/>{rollback&&<ConfirmDialog title="Restore configuration revision" message={`Restore revision ${rollback.id} as a new revision and deploy it to all active nodes?`} confirmLabel="Restore & deploy" onCancel={()=>setRollback(null)} onConfirm={async()=>{try{await api(`/config-revisions/${rollback.id}/rollback`,'POST');setRollback(null);await client.invalidateQueries()}catch(e){setError(String(e))}}}/>}</>}
function App(){
 const [me,setMe]=useState<Row|null>(null),[checked,setChecked]=useState(false);
 const [sidebarCollapsed,setSidebarCollapsed]=useState(false);
 useEffect(()=>setSidebarCollapsed(false),[me?.id]);
 useQuery({queryKey:['me'],queryFn:async()=>{try{const user=await api('/auth/me');setCSRF(user.csrf);setMe(user);return user}finally{setChecked(true)}},retry:false});
 const monitor=useQuery<Row>({queryKey:['monitoring'],queryFn:()=>api('/monitoring'),enabled:!!me&&!me.must_change_password,refetchInterval:15000});
 const prefs=me?.preferences||{};
 useEffect(()=>{if(!me||me.must_change_password)return;const scheme=location.protocol==='https:'?'wss':'ws',socket=new WebSocket(`${scheme}://${location.host}/api/v1/ws/status`);socket.onmessage=()=>{client.invalidateQueries({queryKey:['agents']});client.invalidateQueries({queryKey:['monitoring']});client.invalidateQueries({queryKey:['routing-health']});client.invalidateQueries({queryKey:['jobs']});client.invalidateQueries({queryKey:['agent-activities']});client.invalidateQueries({queryKey:['vhost-deployment']});client.invalidateQueries({queryKey:['agent-vhosts']})};return()=>socket.close()},[me?.id,me?.must_change_password]);
 useEffect(()=>{const media=matchMedia('(prefers-color-scheme: dark)');const selected=prefs.theme||localStorage.getItem('edgeplane-theme')||'system';const apply=()=>{document.documentElement.dataset.theme=selected==='system'?(media.matches?'dark':'light'):selected};apply();media.addEventListener('change',apply);return()=>media.removeEventListener('change',apply)},[prefs.theme]);
 const savePrefs=async(next:Row)=>{try{if(next.theme)localStorage.setItem('edgeplane-theme',next.theme);const value=await api('/auth/preferences','PUT',{theme:'system',sidebar_collapsed:false,compact_tables:false,timezone:'UTC',...prefs,...next});setMe(current=>current?{...current,preferences:value}:current)}catch(e){console.error('Preference update failed',e)}};
 const logout=()=>{client.clear();setMe(null)};
 if(!checked)return <div className="login">Connecting to Supervisor…</div>;
 if(!me)return <Login onLogin={setMe}/>;
 if(me.must_change_password)return <Password done={logout}/>;
 const allowed=(path:string)=>(me.section_permissions||[]).includes(navSection[path]||path.slice(1));
 return <div className={'shell '+(sidebarCollapsed?'collapsed ':'')+(prefs.compact_tables?'compact':'')}>
<aside>
<div className="sidebar-brand">
<NavLink to="/" className="brand">
<Layers size={25}/>
<span className="brand-name">Edgeplane</span>
</NavLink>
<button className="collapse-toggle" aria-label={sidebarCollapsed?'Expand sidebar':'Collapse sidebar'} onClick={()=>setSidebarCollapsed(!sidebarCollapsed)}>{sidebarCollapsed?<ChevronsRight size={17}/>:<ChevronsLeft size={17}/>}</button>
</div>
<div className="workspace">
<span className="workspace-icon">P</span>
<div>Provider workspace<small>CDN operations</small>
</div>
</div>
<nav>{nav.filter(([,path])=>allowed(path)&&!['/dns/records','/dns/routing','/dns/bgp','/dns/connection','/routing-health','/traffic','/topology','/cache-policies','/rate-limit-policies','/real-ip-policies','/header-policies'].includes(path)).map(([label,path,Icon])=>{
 const link=<NavLink title={label} to={path} end={path==='/'}><Icon size={18}/><span className="nav-label">{label}</span>{path==='/agents'&&!!monitor.data?.out_of_sync&&<span className="nav-count">{monitor.data.out_of_sync}</span>}</NavLink>;
 const children=path==='/monitoring'?['/routing-health','/traffic','/topology']:path==='/vhosts'?['/cache-policies','/rate-limit-policies','/real-ip-policies','/header-policies']:path==='/dns'?['/dns/records','/dns/routing','/dns/bgp','/dns/connection']:[];
 return children.length?<details className="nav-group" key={`${me.id}:${path}`} open><summary><Icon size={18}/><span className="nav-label">{label}</span></summary>{path!=='/dns'&&link}{nav.filter(([,p])=>children.includes(p)&&allowed(p)).map(([l,p,I])=><NavLink key={p} to={p}><I size={16}/><span className="nav-label">{l==='Traffic'?'Traffic & customer monitoring':l}</span></NavLink>)}</details>:<React.Fragment key={path}>{link}</React.Fragment>
})}</nav>
<div className="sidebar-tools">
<button aria-label="Toggle light and dark theme" onClick={()=>savePrefs({theme:document.documentElement.dataset.theme==='dark'?'light':'dark'})}>{document.documentElement.dataset.theme==='dark'?<Sun size={17}/>:<Moon size={17}/>}<span>Appearance</span>
</button>
</div>
<div className="account">
<div className="avatar">{me.username[0].toUpperCase()}</div>
<div className="account-info">{me.username}<small>{me.role}</small>
</div>
<button aria-label="Sign out" onClick={async()=>{await api('/auth/logout','POST');logout()}}>
<LogOut size={17}/>
</button>
</div>
</aside>
<div className="main">
<header className="topbar">
<span>Provider workspace <ChevronRight size={13}/> CDN operations</span>
<span className="connection">
<i/> {monitor.isError?'Monitoring unavailable':'Control plane connected'}<OperatorHelp/></span>
</header>
<main>
<Routes>{nav.filter(([,path])=>allowed(path)).map(([,path])=>
<Route key={path} path={path} element={path==='/'?<Dashboard/>:path==='/topology'?<Dashboard topology/>:path==='/monitoring'?<Monitoring/>:path==='/traffic'?<Traffic/>:path==='/customers'?<Traffic customersOnly/>:path==='/preferences'?<Preferences me={me} onSaved={preferences=>setMe({...me,preferences})} onLogout={logout}/>:path==='/users'?<Users me={me}/>:path.startsWith('/dns')?<DNSSettings key={path} section={path==='/dns/connection'?'connection':path==='/dns/routing'?'routing':path==='/dns/bgp'?'bgp':'records'}/>:path==='/logs'?<Logs/>:path==='/settings'?<SettingsPage/>:<Resource key={path} resource={path.slice(1)}/>}/>)}</Routes>
</main>
<div className="bottom">Edgeplane · CDN operations<span>Agent drift checked every 5 minutes</span>
</div>
</div>
<TerminalDock/>
</div>
}
createRoot(document.getElementById('root')!).render(<React.StrictMode>
<QueryClientProvider client={client}>
<BrowserRouter>
<App/>
</BrowserRouter>
</QueryClientProvider>
</React.StrictMode>);

function PolicyFields({resource,text,onChange}:{resource:string,text:string,onChange:(value:string)=>void}) {
 const defaults:Row={'cache-policies':{enabled:true,default_ttl:120,static_ttl:3600,max_ttl:86400,grace:300,keep:60,ignore_origin_ttl:false,query_string:true,bypass_paths:[],bypass_headers:[],ignored_query_parameters:[]},'rate-limit-policies':{enabled:true,rate:200,burst:400,nodelay:true,dry_run:false,status:429,key:'ip',header:'X-API-Key',exemptions:[],paths:[]},'real-ip-policies':{trusted_cidrs:[],header:'X-Forwarded-For',recursive:true,forward_to_origin:false},'header-policies':{debug:true,request:{},response:{'X-Content-Type-Options':'nosniff'}}};
 let config:Row;try{config={...defaults[resource],...JSON.parse(text)}}catch{return <textarea value={text} onChange={e=>onChange(e.target.value)}/>}
 const update=(k:string,v:any)=>onChange(JSON.stringify({...config,[k]:v},null,2));
 const help:Record<string,Record<string,string>>={
  'rate-limit-policies':{
   enabled:'Apply this limit to vhosts using the policy. Turn off to disable enforcement.',
   rate:'Sustained requests per second per rate key. 200 means 200 requests every second per client IP or API key; it does not mean 200 requests per five minutes.',
   burst:'Temporary excess capacity above the steady rate. With rate 200 and burst 400 + nodelay, up to 400 queued excess requests may pass immediately; later excess requests receive 429 until capacity refills at 200/second. There is no fixed blocking period.',
   nodelay:'Allow requests within the burst immediately. Off spaces excess requests over time.',
   dry_run:'Measure limit violations without rejecting requests. Use while tuning a policy.',
   status:'HTTP status returned when a request exceeds the limit; normally 429.',
   key:'Choose whether limits apply per resolved client IP or per value of a request header.',
   header:'Header used only when Rate key is HTTP header. An absent header falls back to client IP.',
   exemptions:'One trusted client IP or CIDR per line that is exempt from this rate limit.',
   paths:'JSON array of path-specific limits, for example [{"path":"/api/","rate":10,"burst":20}]. These use path prefixes.'
  },
  'real-ip-policies':{
   trusted_cidrs:'Only these proxy IPs or CIDRs may supply the Real IP header. Never trust the entire Internet.',
   header:'Trusted client-IP header, normally X-Forwarded-For. Untrusted senders cannot override the socket IP.',
   recursive:'Walk trusted proxy hops to find the last non-trusted client address.',
   forward_to_origin:'Send the resolved client address to the origin in X-Real-IP and X-Forwarded-For.'
  },
  'header-policies':{
   debug:'Add X-Served-By and X-Request-ID response headers for troubleshooting.',
   request:'JSON object of fixed headers sent to the origin, for example {"X-Edge-Site":"fra1"}. Supported automatic forwarded headers include Host, X-Forwarded-Proto, X-Request-ID, Accept, Authorization, Cookie, Content-Type, Range, conditional cache headers, CORS headers, User-Agent, Referer and Origin. Internal X-CDN-* input is blocked.',
   response:'JSON object of fixed headers sent to clients, for example {"X-Content-Type-Options":"nosniff"}, {"Strict-Transport-Security":"max-age=31536000; includeSubDomains"}, or CORS headers. Header names and safe text values are API-validated.'
  }
 };
 const tip=(key:string)=>help[resource]?.[key];
 const summary=resource==='rate-limit-policies'?'Rate limits use a token bucket measured in requests per second. They do not ban a client for five minutes. Capacity refills continuously; rejected requests receive the selected HTTP status.':resource==='real-ip-policies'?'This policy is global when marked Default. Each vhost may inherit it or select a different policy. Only trust headers from known load balancers/proxies.':resource==='header-policies'?'This policy is global when marked Default. Each vhost may override it. Request headers go to the origin; response headers go to the visitor. Values are fixed safe strings, not raw NGINX.':'';
 return <>
<div className="notice">{summary}</div>
<div className="formgrid">{Object.entries(config).map(([key,value])=>{
  const label=key.replaceAll('_',' '), explanation=tip(key);
  if(typeof value==='boolean')return <label className="check" key={key}>
<input type="checkbox" checked={value} onChange={e=>update(key,e.target.checked)}/>
<LabelText help={explanation}>{label}</LabelText>
</label>;
  if(typeof value==='number')return <Field key={key} label={label} help={explanation} type="number" value={value} onChange={v=>update(key,Number(v))}/>;
  if(typeof value==='string')return key==='key'?<label key={key}>
<LabelText help={explanation}>Rate key</LabelText>
<select value={value} onChange={e=>update(key,e.target.value)}>
<option value="ip">Real client IP</option>
<option value="header">API / HTTP header</option>
</select>
</label>:<Field key={key} label={label} help={explanation} value={value} onChange={v=>update(key,v)}/>;
  return <label key={key}>
<LabelText help={explanation}>{label}</LabelText>{Array.isArray(value)&&!value.some(v=>typeof v==='object')&&key!=='paths'?<textarea value={value.join('\n')} onChange={e=>update(key,e.target.value.split('\n').map(v=>v.trim()).filter(Boolean).map(v=>key==='statuses'?Number(v):v))}/>:<textarea defaultValue={JSON.stringify(value,null,2)} onBlur={e=>{try{update(key,JSON.parse(e.target.value));e.target.setCustomValidity('')}catch{e.target.setCustomValidity('Enter valid JSON')}}}/>}</label>;
 })}</div>
</>
}
