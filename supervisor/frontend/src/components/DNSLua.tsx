import {useState} from 'react';
import {api,type Row} from '../api';
import {ErrorBox,Field} from './ui';

export default function DNSLua(){
 const [rules,setRules]=useState<Row[]>([{kind:'country',matches:['IR'],address:'185.79.98.207'},{kind:'continent',matches:['EU'],address:'185.79.97.93'}]),[fallback,setFallback]=useState(''),[result,setResult]=useState<Row|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[copied,setCopied]=useState(false);
 const update=(index:number,key:string,value:any)=>{setRules(rules.map((r,i)=>i===index?{...r,[key]:value}:r));setResult(null)};
 return <section className="panel"><h2>PowerDNS Lua / GeoIP routing</h2><p>Ordered country, continent and client-network rules. First match wins. Preview only: this does not overwrite DNS or origin records. Replace the example addresses before use.</p>
 {rules.map((r,i)=><div className="formgrid" key={i}><label>Rule {i+1}<select value={r.kind} onChange={e=>update(i,'kind',e.target.value)}><option value="country">Country / country group</option><option value="continent">Continent</option><option value="netmask">Network ACL</option></select></label><Field label="Codes or CIDRs · comma separated" value={r.matches.join(',')} onChange={v=>update(i,'matches',v.split(',').map(x=>x.trim()).filter(Boolean))}/><Field label="Answer IPv4" value={r.address} onChange={v=>update(i,'address',v)}/><button onClick={()=>{setRules(rules.filter((_,j)=>i!==j));setResult(null)}}>Remove rule</button></div>)}
 <button onClick={()=>{setRules([...rules,{kind:'country',matches:[],address:''}]);setResult(null)}}>Add rule</button><Field label="Rest of world / unknown · IPv4" value={fallback} onChange={v=>{setFallback(v);setResult(null)}}/>
 <button disabled={busy} onClick={async()=>{setBusy(true);setError('');setCopied(false);try{setResult(await api('/dns/lua-preview','POST',{rules,fallback}))}catch(e){setError(String(e))}finally{setBusy(false)}}}>Generate Lua record</button><ErrorBox error={error}/>
 {result&&<><p>{result.warning}</p><pre>{result.content}</pre><button onClick={async()=>{try{await navigator.clipboard.writeText(result.content);setCopied(true)}catch(e){setError(String(e))}}}>{copied?'Copied':'Copy PowerDNS record content'}</button></>}
 </section>;
}
