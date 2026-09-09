import React, {useState, useEffect} from "react";
import {Layers, X, AlertCircle, ChevronRight} from "lucide-react";
import type {Row} from "../api";
export function Badge({value}:{value:unknown}) { const text=String(value ?? 'PENDING'); return <span className={'badge '+text.toLowerCase()}><i/>{text}</span>; }
export function Empty({title='No records yet',text='Create your first resource to begin managing your CDN.'}) {return <div className="empty"><Layers size={32}/><h3>{title}</h3><p>{text}</p></div>}
export function ErrorBox({error}:{error:unknown}) {return error ? <div className="error" role="alert"><AlertCircle size={17}/>{String(error instanceof Error ? error.message:error)}</div>:null}
export function Modal({title,children,close}:{title:string,children:React.ReactNode,close:()=>void}) {return <div className="overlay" onClick={close}><section className="modal" role="dialog" aria-modal="true" aria-label={title} onClick={e=>e.stopPropagation()}><header><h2>{title}</h2><button aria-label="Close" onClick={close}><X size={20}/></button></header>{children}</section></div>}
export function Field({label,value,onChange,type='text',required=false}:{label:string,value:any,onChange:(v:string)=>void,type?:string,required?:boolean}) {return <label>{label}<input required={required} type={type} value={value??''} onChange={e=>onChange(e.target.value)}/></label>}
export function Table({rows,columns,onClick}:{rows:Row[],columns:string[],onClick?:(row:Row)=>void}) {if(!rows.length)return <Empty/>;return <div className="tablewrap"><table><thead><tr>{columns.map(c=><th key={c}>{c.replaceAll('_',' ')}</th>)}{onClick&&<th/>}</tr></thead><tbody>{rows.map((row,i)=><tr key={row.id||i} onClick={()=>onClick?.(row)} tabIndex={onClick?0:undefined} onKeyDown={e=>{if(e.key==='Enter')onClick?.(row)}}>{columns.map(c=><td key={c}>{c==='status'||c==='role'?<Badge value={row[c]}/>:c.includes('_at')||c==='last_seen'?(row[c]?new Date(row[c]).toLocaleString():'—'):Array.isArray(row[c])?row[c].map((v:any)=>typeof v==='object'?JSON.stringify(v):v).join(', '):typeof row[c]==='boolean'?<Badge value={row[c]?'ENABLED':'DISABLED'}/>:typeof row[c]==='object'?<code>{JSON.stringify(row[c])}</code>:String(row[c]??'—')}</td>)}{onClick&&<td><ChevronRight size={16}/></td>}</tr>)}</tbody></table></div>}

export function ListField({label,values,onChange,numeric=false,multiline=false}:{label:string,values:(string|number)[],onChange:(values:any[])=>void,numeric?:boolean,multiline?:boolean}) {
 const separator=multiline?'\n':', '; const canonical=values.join(separator); const [draft,setDraft]=useState(canonical);
 useEffect(()=>setDraft(canonical),[canonical]);
 const commit=()=>onChange(draft.split(multiline?'\n':',').map(v=>v.trim()).filter(Boolean).map(v=>numeric?Number(v):v));
 return <label>{label}{multiline?<textarea value={draft} onChange={e=>setDraft(e.target.value)} onBlur={commit}/>:<input value={draft} onChange={e=>setDraft(e.target.value)} onBlur={commit}/>}</label>;
}
