import {useEffect,useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {api,type Row} from '../api';
import {ErrorBox,Field} from './ui';

export default function VarnishSettings({nodeId}:{nodeId:string}){
 const client=useQueryClient();
 const [open,setOpen]=useState(false),[form,setForm]=useState<Row>({storage:'file',size_mb:1024,log_retention_days:7}),[ack,setAck]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
 const query=useQuery<Row>({queryKey:['varnish-settings',nodeId],queryFn:()=>api(`/agents/${nodeId}/varnish`),enabled:open});
 useEffect(()=>{if(query.data)setForm(query.data.desired||query.data.reported||query.data.defaults)},[query.data]);
 return <section className="panel"><button onClick={()=>setOpen(!open)} aria-expanded={open}>Varnish storage &amp; log retention {open?'▾':'▸'}</button>{open&&<>
 <p>Cache object expiry is controlled by each vhost’s Cache policy. File retention below only rotates managed NGINX logs; it never deletes Varnish storage.</p>
 <h4>Last reported settings</h4><pre>{query.data?.reported?JSON.stringify(query.data.reported,null,2):'Not reported yet. Upgrade the Agent and apply settings to obtain a provisioning acknowledgement.'}</pre>
 <small>Report time: {query.data?.reported_at?new Date(query.data.reported_at).toLocaleString():'Unknown'}</small>
 <h4>Desired settings</h4><label>Storage<select value={form.storage} onChange={e=>setForm({...form,storage:e.target.value})}><option value="file">File-backed cache (not persistent across restart)</option><option value="malloc">RAM (malloc)</option></select></label>
 <Field label="Cache capacity (MiB)" type="number" value={form.size_mb} onChange={v=>setForm({...form,size_mb:Number(v)})}/>
 <Field label="Rotated-log retention (days)" type="number" value={form.log_retention_days} onChange={v=>setForm({...form,log_retention_days:Number(v)})}/>
 <label><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)}/>I acknowledge storage changes restart Varnish, empty cache and can briefly interrupt requests.</label>
 <ErrorBox error={error||query.error}/>{message&&<p role="status">{message}</p>}
 <button disabled={!ack||busy||query.isLoading||!!query.error} onClick={async()=>{setBusy(true);setError('');try{const result=await api(`/agents/${nodeId}/varnish`,'PUT',{...form,acknowledge_restart:true});setMessage(`Queued job ${result.job_id}. Check Jobs for completion; queued does not mean applied.`);setAck(false);void client.invalidateQueries({queryKey:['jobs']})}catch(e){setError(String(e))}finally{setBusy(false)}}}>{busy?'Saving…':'Save & provision Varnish'}</button>
 </>}</section>;
}
