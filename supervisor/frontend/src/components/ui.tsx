import React, {useState, useEffect} from "react";
import {createPortal} from "react-dom";
import {Layers, X, AlertCircle, ChevronRight} from "lucide-react";
import type {Row} from "../api";

const FIELD_HELP:Record<string,string>={
 "name":"A human-readable name shown only inside Edgeplane.",
 "display name":"An operator-friendly vhost name; it does not have to be a DNS hostname.",
 "hostname":"The SSH-reachable hostname or IP address of this agent node, without a URL scheme.",
 "management url":"The agent API base URL used by Supervisor after provisioning, for example https://203.0.113.10:9443. For a local Docker agent use https://host.docker.internal:9443.",
 "public ipv4":"The public IPv4 address serving CDN traffic at this POP. Do not enter localhost or an SSH-only private address.",
 "public ipv6":"The public IPv6 address serving CDN traffic at this POP. Leave blank if IPv6 is not routed to this node.",
 "city":"POP city used for operations, monitoring labels and geographic identification, for example Frankfurt.",
 "country":"POP country name or ISO country code used in node labels and operational views.",
 "provider":"Network, datacenter or cloud provider operating this POP.",
 "ssh username":"Linux account Supervisor uses only to bootstrap and maintain the agent. Root or a sudo-capable account is accepted.",
 "ssh port":"SSH TCP port on the agent node; normally 22.",
 "ssh password":"Password for the SSH account. It is encrypted at rest and may be blank when a private key is supplied.",
 "powerdns api base url":"PowerDNS Authoritative HTTP API address, for example http://powerdns:8081. This is not the public DNS resolver address.",
 "server id":"PowerDNS server identifier used in API paths; the usual value is localhost.",
 "managed cdn zone":"Authoritative zone owned by Edgeplane, such as edge.example.net. Customer CNAMEs point into this zone.",
 "dns ttl (seconds)":"How long recursive resolvers may cache generated POP A/AAAA answers. Lower values react faster but increase DNS load.",
 "api key":"Secret configured as api-key in PowerDNS. Leaving it blank preserves an already stored key.",
 "username":"Account or API-gateway username. Its exact purpose depends on the form where it appears.",
 "password":"Secret for this account. A blank value normally keeps the stored password when editing.",
 "ttl seconds":"Lifetime of this DNS RRset in resolver caches.",
 "name · @, relative, or fqdn":"Use @ for the zone apex, a label such as abc123, or a complete name inside the managed zone.",
 "hostname / ip":"Origin server DNS name or IP address that receives cache misses from the CDN.",
 "port":"TCP port exposed by the origin, commonly 80 for HTTP or 443 for HTTPS.",
 "host header":"HTTP Host header sent to the origin. Leave blank to use the customer request host.",
 "sni":"Required when an HTTPS origin is entered as an IP address. Enter the DNS hostname printed on the origin certificate (for example origin.example.com); this controls certificate verification and TLS SNI.",
 "maximum request body (mb)":"Largest accepted client request body for this vhost; requests above it are rejected.",
 "path":"URL path used by this access rule, beginning with /.",
 "redirect path":"URL path to redirect. Use / with prefix matching to redirect the entire vhost.",
 "origin route path":"URL path that should use one chosen origin. Prefix matching includes every path beginning with these characters.",
 "redirect target url":"Fixed http:// or https:// destination. The redirect does not forward the original path or query string.",
 "trusted cidrs":"Only these proxy networks may supply the configured client-IP header. Never trust the entire Internet.",
 "header":"For Real IP, the trusted client-IP header, normally X-Forwarded-For. Header values from untrusted senders are ignored.",
 "recursive":"Walk a trusted proxy chain to select the last non-trusted client IP.",
 "forward to origin":"Send the resolved client IP as X-Real-IP and X-Forwarded-For to the origin. Leave off if the origin must not receive it.",
 "request":"JSON object of fixed HTTP headers sent to the origin, for example {\"X-CDN-Source\":\"edge\"}. These override matching forwarded headers.",
 "response":"JSON object of HTTP headers sent to clients, for example {\"X-Content-Type-Options\":\"nosniff\"}.",
 "debug":"Send X-Served-By and X-Request-ID response headers for diagnostics. Turn off if POP identity must not be disclosed.",
 "country codes · comma separated, e.g. ir, de":"Two-letter ISO country codes evaluated using the installed MaxMind database.",
 "geonames city ids · comma separated":"Numeric GeoNames IDs from the MaxMind City database; use the IP lookup tool to find them.",
 "allowed ips / cidrs · one per line":"Client addresses or networks allowed by this path rule, such as 192.0.2.10 or 192.0.2.0/24.",
 "initial password · 12+ characters":"Temporary login password. The user must replace it after the first sign-in.",
 "reset password · leave empty to keep current":"Set a temporary replacement password and revoke existing sessions, or leave blank for no change.",
 "time zone":"IANA time-zone name used for dates in this account, for example Asia/Tehran or Europe/Berlin.",
};

function fieldHelp(label:string){return FIELD_HELP[label.trim().toLowerCase()]||FIELD_HELP[label.split('·')[0].trim().toLowerCase()]}
export function Help({text}:{text:string}){
 const [position,setPosition]=useState<{left:number,top:number,above:boolean}|null>(null);
 const show=(element:HTMLElement)=>{
  const rect=element.getBoundingClientRect(),width=Math.min(360,window.innerWidth-24),above=rect.bottom+120>window.innerHeight;
  setPosition({left:Math.max(12,Math.min(rect.left,window.innerWidth-width-12)),top:above?rect.top-8:rect.bottom+8,above});
 };
 return <><span className="help-tip" tabIndex={0} role="img" aria-label={text} onMouseEnter={e=>show(e.currentTarget)} onMouseLeave={()=>setPosition(null)} onFocus={e=>show(e.currentTarget)} onBlur={()=>setPosition(null)}>!</span>{position&&createPortal(<span className={'help-popover '+(position.above?'above':'')} role="tooltip" style={{left:position.left,top:position.top}}>{text}</span>,document.body)}</>;
}
export function LabelText({children,help}:{children:React.ReactNode,help?:string}){return <span className="label-text">{children}{help&&<Help text={help}/>}</span>}
export function Badge({value}:{value:unknown}) { const text=String(value ?? 'PENDING'); return <span className={'badge '+text.toLowerCase().replaceAll(' ','-')}><i/>{text}</span>; }
export function Empty({title='No records yet',text='Create your first resource to begin managing your CDN.'}) {return <div className="empty"><Layers size={32}/><h3>{title}</h3><p>{text}</p></div>}
export function ErrorBox({error}:{error:unknown}) {return error ? <div className="error" role="alert"><AlertCircle size={17}/>{String(error instanceof Error ? error.message:error)}</div>:null}
export function Modal({title,children,close,wide=false}:{title:string,children:React.ReactNode,close:()=>void,wide?:boolean}) {return <div className="overlay" onClick={close}><section className={'modal '+(wide?'modal-wide':'')} role="dialog" aria-modal="true" aria-label={title} onClick={e=>e.stopPropagation()}><header><h2>{title}</h2><button aria-label="Close" onClick={close}><X size={20}/></button></header>{children}</section></div>}
export function ConfirmDialog({title,message,confirmLabel='Confirm',danger=false,busy=false,onConfirm,onCancel}:{title:string,message:string,confirmLabel?:string,danger?:boolean,busy?:boolean,onConfirm:()=>void,onCancel:()=>void}) {
 return <Modal title={title} close={onCancel}><div className="confirm-body"><AlertCircle size={25}/><div><h3>{message}</h3><p>Review this operation before continuing. Progress and failures will be recorded in the activity log.</p></div></div><footer><button disabled={busy} onClick={onCancel}>Cancel</button><button disabled={busy} className={danger?'danger':'primary'} onClick={onConfirm}>{busy?'Working…':confirmLabel}</button></footer></Modal>;
}
export function Field({label,value,onChange,type='text',required=false,help}:{label:string,value:any,onChange:(v:string)=>void,type?:string,required?:boolean,help?:string}) {const tip=help||fieldHelp(label);return <label><LabelText help={tip}>{label}</LabelText><input required={required} type={type} value={value??''} onChange={e=>onChange(e.target.value)}/></label>}
export function Table({rows,columns,onClick}:{rows:Row[],columns:string[],onClick?:(row:Row)=>void}) {
 if(!rows.length)return <Empty/>;
 const visible=columns.includes('is_default')&&rows.some(row=>'enabled' in row)?['name','enabled','is_default','config']:columns;
 return <div className="tablewrap"><table><thead><tr>{visible.map(c=><th key={c}>{c.replaceAll('_',' ')}</th>)}{onClick&&<th/>}</tr></thead><tbody>{rows.map((row,i)=><tr key={row.id||i} onClick={()=>onClick?.(row)} tabIndex={onClick?0:undefined} onKeyDown={e=>{if(e.key==='Enter')onClick?.(row)}}>{visible.map(c=><td key={c}>{['status','role','service_state','vhost_state'].includes(c)?<Badge value={row[c]}/>:c.includes('_at')||c==='last_seen'?(row[c]?new Date(row[c]).toLocaleString():'—'):Array.isArray(row[c])?row[c].map((v:any)=>typeof v==='object'?JSON.stringify(v):v).join(', '):typeof row[c]==='boolean'?<Badge value={row[c]?'ENABLED':'DISABLED'}/>:typeof row[c]==='object'?<code>{JSON.stringify(row[c])}</code>:String(row[c]??'—')}</td>)}{onClick&&<td><ChevronRight size={16}/></td>}</tr>)}</tbody></table></div>
}

export function ListField({label,values,onChange,numeric=false,multiline=false,help}:{label:string,values:(string|number)[],onChange:(values:any[])=>void,numeric?:boolean,multiline?:boolean,help?:string}) {
 const separator=multiline?'\n':', '; const canonical=values.join(separator); const [draft,setDraft]=useState(canonical);
 useEffect(()=>setDraft(canonical),[canonical]);
 const commit=()=>onChange(draft.split(multiline?'\n':',').map(v=>v.trim()).filter(Boolean).map(v=>numeric?Number(v):v));
 return <label><LabelText help={help||fieldHelp(label)}>{label}</LabelText>{multiline?<textarea value={draft} onChange={e=>setDraft(e.target.value)} onBlur={commit}/>:<input value={draft} onChange={e=>setDraft(e.target.value)} onBlur={commit}/>}</label>;
}
