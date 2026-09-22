import {useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {Plus} from 'lucide-react';
import {api,type Row} from '../api';
import {Badge,ErrorBox,Field,Modal,Table} from '../components/ui';

const sections=['dashboard','monitoring','routing-health','traffic','customers','topology','nodes','vhosts','cache-policies','rate-limit-policies','real-ip-policies','header-policies','certificates','dns','jobs','logs','audit','users','settings','preferences'];
const defaults:Record<string,string[]>={ADMIN:sections,OPERATOR:sections.filter(s=>!['users','settings','dns'].includes(s)),VIEWER:['dashboard','monitoring','traffic','customers','topology','nodes','vhosts','jobs','logs','preferences']};
const blank={username:'',password:'',role:'VIEWER',active:true,section_permissions:[] as string[]};

export default function Users({me}:{me:Row}){
 const client=useQueryClient(),users=useQuery<Row[]>({queryKey:['users'],queryFn:()=>api('/users')});
 const [selected,setSelected]=useState<Row|null>(null),[create,setCreate]=useState(false),[form,setForm]=useState<Row>(blank),[error,setError]=useState('');
 const effective=form.section_permissions?.length?form.section_permissions:defaults[form.role]||[];
 const toggle=(section:string)=>setForm({...form,section_permissions:effective.includes(section)?effective.filter((s:string)=>s!==section):[...effective,section]});
 async function save(){
  try{
   const body={role:form.role,active:form.active,section_permissions:form.section_permissions||[]};
   if(create)await api('/users','POST',{...body,username:form.username,password:form.password});
   else await api('/users/'+selected!.id,'PUT',body);
   await client.invalidateQueries({queryKey:['users']});setSelected(null);setCreate(false);
  }catch(e){setError(String(e))}
 }
 return <>
  <div className="pagehead"><div className="eyebrow">ACCESS CONTROL</div><div className="sectionhead"><h1>Users, roles & sections</h1><button className="primary" onClick={()=>{setCreate(true);setForm({...blank})}}><Plus size={16}/>Add user</button></div><p>Roles govern actions; section permissions govern which application areas and APIs a user can access.</p></div>
  <div className="rolecards">{[['ADMIN','Full operational and security control.'],['OPERATOR','Can change normal CDN resources, but not administrative resources.'],['VIEWER','Read-only; write APIs remain denied.']].map(([role,description])=><div className="panel" key={role}><Badge value={role}/><p>{description}</p></div>)}</div>
  <ErrorBox error={error||users.error}/><Table rows={users.data||[]} columns={['username','role','section_permissions','active','created_at']} onClick={row=>{setSelected(row);setForm({username:row.username,password:'',role:row.role,active:row.active,section_permissions:row.section_permissions||[]})}}/>
  {(create||selected)&&<Modal title={create?'Create user':`Manage ${selected!.username}`} close={()=>{setCreate(false);setSelected(null)}}><div className="formbody">
   {create&&<Field label="Username" required value={form.username} onChange={username=>setForm({...form,username})}/>}<label>Role<select value={form.role} onChange={e=>setForm({...form,role:e.target.value,section_permissions:[]})}><option>VIEWER</option><option>OPERATOR</option><option>ADMIN</option></select></label>
   {!create&&<label className="check"><input type="checkbox" checked={form.active} onChange={e=>setForm({...form,active:e.target.checked})}/>Account enabled</label>}
   <h3>Accessible sections</h3><p>Defaults are selected from the role. Changing a checkbox creates a custom assignment for this user.</p><div className="permission-grid">{sections.map(section=><label className="check" key={section}><input type="checkbox" checked={effective.includes(section)} onChange={()=>toggle(section)}/>{section.replaceAll('-',' ')}</label>)}</div>
   <button onClick={()=>setForm({...form,section_permissions:[]})}>Reset to role defaults</button>
   <Field label={create?'Initial password · 12+ characters':'Reset password · leave empty to keep current'} value={form.password} type="password" onChange={password=>setForm({...form,password})}/>
   {!create&&form.password&&<button onClick={async()=>{try{await api(`/users/${selected!.id}/reset-password`,'POST',{password:form.password});await client.invalidateQueries();setSelected(null)}catch(e){setError(String(e))}}}>Reset password & revoke sessions</button>}
   <ErrorBox error={error}/></div><footer><button onClick={()=>{setCreate(false);setSelected(null)}}>Cancel</button><button className="primary" onClick={save}>Save user</button></footer></Modal>}
 </>;
}
