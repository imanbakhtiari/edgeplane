import {useEffect,useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {Plus,RefreshCw,Trash2} from 'lucide-react';
import {api,type Row} from '../api';
import {ConfirmDialog,ErrorBox,Field,Help,Modal,Table} from '../components/ui';

const lines=(value:string)=>value.split('\n').map(v=>v.trim()).filter(Boolean);

export default function DNSSettings(){
 const client=useQueryClient();
 const settings=useQuery<Row>({queryKey:['dns-settings'],queryFn:()=>api('/settings/dns')});
 const desired=useQuery<Row[]>({queryKey:['dns'],queryFn:()=>api('/dns')});
 const records=useQuery<Row[]>({queryKey:['dns-records'],queryFn:()=>api('/dns/records'),enabled:!!settings.data?.api_url});
 const pops=useQuery<Row[]>({queryKey:['dns-pops'],queryFn:()=>api('/dns/pop-sites?limit=500')});
 const [form,setForm]=useState<Row>({api_url:'',auth_mode:'api_key',api_key:'',username:'',password:'',server_id:'localhost',zone:'edge.example.net',ttl:60,anycast_ipv4:[],anycast_ipv6:[]});
 const [record,setRecord]=useState<Row|null>(null),[error,setError]=useState(''),[result,setResult]=useState<Row|null>(null);
 useEffect(()=>{if(settings.data)setForm(old=>({...old,...settings.data,api_key:'',password:''}))},[settings.data]);
 const set=(key:string,value:any)=>setForm({...form,[key]:value});
 async function save(){
  const keys=['api_url','auth_mode','api_key','username','password','server_id','zone','ttl','anycast_ipv4','anycast_ipv6'];
  await api('/settings/dns','PUT',Object.fromEntries(keys.map(key=>[key,form[key]])));
  await client.invalidateQueries({queryKey:['dns-settings']});
 }
 async function reload(){await Promise.all([client.invalidateQueries({queryKey:['dns-records']}),client.invalidateQueries({queryKey:['dns']})])}
 return <>
  <div className="pagehead"><div className="eyebrow">AUTHORITATIVE DNS</div><h1>PowerDNS management</h1><p>Manage Edgeplane's CDN zone and publish POP service addresses. Customer-owned zones are not modified.</p></div>
  <ErrorBox error={error||settings.error||records.error}/>
  <section className="panel">
   <div className="formgrid"><Field label="PowerDNS API base URL" value={form.api_url} onChange={v=>set('api_url',v)}/><Field label="Server ID" value={form.server_id} onChange={v=>set('server_id',v)}/><Field label="Managed CDN zone" value={form.zone} onChange={v=>set('zone',v)}/><Field label="DNS TTL (seconds)" type="number" value={form.ttl} onChange={v=>set('ttl',Number(v))}/></div>
   <label><span className="label-text">Authentication<Help text="Use API key for the native PowerDNS Authoritative API. Choose username and password only when an authenticating reverse proxy protects that API."/></span><select value={form.auth_mode} onChange={e=>set('auth_mode',e.target.value)}><option value="api_key">API key · native PowerDNS</option><option value="basic">Username and password · API gateway</option></select></label>
   {form.auth_mode==='api_key'?<Field label={settings.data?.has_api_key?'API key · blank keeps stored key':'API key'} type="password" value={form.api_key} onChange={v=>set('api_key',v)}/>:<div className="formgrid"><Field label="Username" value={form.username} onChange={v=>set('username',v)}/><Field label={settings.data?.has_password?'Password · blank keeps stored password':'Password'} type="password" value={form.password} onChange={v=>set('password',v)}/></div>}
   <div className="formgrid"><label><span className="label-text">Shared anycast IPv4 · one per line<Help text="Shared CDN service IPv4 addresses announced by BGP from multiple POPs. These are not node SSH or management addresses."/></span><textarea value={(form.anycast_ipv4||[]).join('\n')} onChange={e=>set('anycast_ipv4',lines(e.target.value))}/></label><label><span className="label-text">Shared anycast IPv6 · one per line<Help text="Shared CDN service IPv6 addresses announced from multiple POPs. Leave empty until the IPv6 prefix is routed and configured on every intended POP."/></span><textarea value={(form.anycast_ipv6||[]).join('\n')} onChange={e=>set('anycast_ipv6',lines(e.target.value))}/></label></div>
   <small>Configured anycast addresses are published for every vhost. If empty, eligible healthy POP unicast addresses are published.</small>
   <div className="actions"><button className="primary" onClick={async()=>{try{await save();setResult({success:true,message:'DNS settings saved'});setError('')}catch(e){setError(String(e))}}}>Save</button><button onClick={async()=>{try{await save();setResult(await api('/dns/test-connection','POST'));setError('')}catch(e){setError(String(e))}}}>Save & test</button><button onClick={async()=>{try{const job=await api('/dns/reconcile','POST');setResult({success:true,message:'Reconciliation queued: '+job.id})}catch(e){setError(String(e))}}}><RefreshCw size={15}/>Reconcile</button></div>
   {result&&<div className={result.success?'notice':'error'}>{result.message}</div>}
  </section>
  <div className="sectionhead"><div><h2>Live records in {form.zone}</h2><small>Read directly from PowerDNS</small></div><div className="actions"><button onClick={reload}><RefreshCw size={15}/>Refresh</button><button className="primary" onClick={()=>setRecord({name:'',type:'A',ttl:form.ttl,values:[]})}><Plus size={15}/>Add record</button></div></div>
  <Table rows={records.data||[]} columns={['name','type','ttl','values']} onClick={setRecord}/>
  <div className="sectionhead"><div><h2>POP publication inventory</h2><small>First 500 real nodes; eligibility and maintenance control unicast publication.</small></div></div>
  <Table rows={pops.data||[]} columns={['name','city','country','public_ipv4','public_ipv6','status','dns_eligible','maintenance']}/>
  <div className="sectionhead"><div><h2>Edgeplane-managed records</h2><small>Desired values and last reconciliation status</small></div></div>
  <Table rows={desired.data||[]} columns={['name','type','values','status']}/>
  {record&&<RecordForm initial={record} zone={form.zone} close={()=>setRecord(null)} saved={async()=>{setRecord(null);await reload()}}/>}
 </>;
}

function RecordForm({initial,zone,close,saved}:{initial:Row,zone:string,close:()=>void,saved:()=>void}){
 const [data,setData]=useState<Row>({...initial,values:initial.values||[]}),[error,setError]=useState(''),[deleteConfirm,setDeleteConfirm]=useState(false);
 const existing=!!initial.name;
 return <><Modal title={existing?'Edit DNS record':'Add DNS record'} close={close}><div className="formbody"><p>Only records inside <b>{zone}</b> are accepted. Saving replaces the complete RRset.</p><div className="formgrid"><Field label="Name · @, relative, or FQDN" value={data.name} onChange={v=>setData({...data,name:v})}/><label><span className="label-text">Type<Help text="A/AAAA store IPs, CNAME points one hostname to another, TXT stores text, and MX/NS/SRV/CAA use standard DNS record syntax."/></span><select disabled={existing} value={data.type} onChange={e=>setData({...data,type:e.target.value})}>{['A','AAAA','CNAME','TXT','CAA','MX','NS','SRV'].map(t=><option key={t}>{t}</option>)}</select></label><Field label="TTL seconds" type="number" value={data.ttl} onChange={v=>setData({...data,ttl:Number(v)})}/></div><label><span className="label-text">Values · one per line<Help text="Enter the complete RRset: one IP, hostname, or record value per line. Saving replaces all existing values for this name and type."/></span><textarea value={data.values.join('\n')} onChange={e=>setData({...data,values:lines(e.target.value)})}/></label><ErrorBox error={error}/></div><footer>{existing?<button className="danger" onClick={()=>setDeleteConfirm(true)}><Trash2 size={15}/>Delete RRset</button>:<button onClick={close}>Cancel</button>}<button className="primary" onClick={async()=>{try{await api('/dns/records','PUT',data);saved()}catch(e){setError(String(e))}}}>Save RRset</button></footer></Modal>{deleteConfirm&&<ConfirmDialog title="Delete DNS record" message={`Delete the complete ${data.name} ${data.type} RRset from PowerDNS?`} confirmLabel="Delete RRset" danger onCancel={()=>setDeleteConfirm(false)} onConfirm={async()=>{try{await api(`/dns/records?name=${encodeURIComponent(data.name)}&type=${data.type}`,'DELETE');saved()}catch(e){setError(String(e));setDeleteConfirm(false)}}}/>}</>;
}
