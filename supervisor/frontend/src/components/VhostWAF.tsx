import {type Row} from '../api';

export default function VhostWAF({data,set}:{data:Row,set:(key:string,value:any)=>void}) {
  const waf={mode:'off',profile:'standard',crs_version:'',paranoia_level:1,inbound_threshold:5,excluded_rule_ids:[],...data.options?.waf};
  const update=(key:string,value:any)=>set('options',{...data.options,waf:{...waf,[key]:value}});
  return <>
    <h3>Coraza WAF · desired policy</h3>
    <div className="warning">Requires a separately installed, version-matched Coraza NGINX connector, pinned CRS v4 and a POP certificate. Saving is not proof of protection: check successful deployment on every serving node. Automatic connector installation and security-event dashboards are not available yet.</div>
    {!data.certificate_id&&<p className="warning">Select a certificate in TLS before enabling WAF. Encrypted passthrough cannot inspect HTTP requests.</p>}
    <label>Mode<select value={waf.mode} onChange={e=>update('mode',e.target.value)}>
      <option value="off">Off</option><option value="detection" disabled={!data.certificate_id}>Detection only</option><option value="blocking" disabled={!data.certificate_id}>Blocking</option>
    </select></label>
    <label>Installed CRS version<input placeholder="4.x.y — exact installed version" value={waf.crs_version} onChange={e=>update('crs_version',e.target.value)}/></label>
    <label>Profile<select value={waf.profile} onChange={e=>update('profile',e.target.value)}>
      <option value="low">Low — paranoia 1, threshold 10</option><option value="standard">Standard — paranoia 1, threshold 5</option><option value="high">High — paranoia 2, threshold 5</option><option value="custom">Custom</option>
    </select></label>
    {waf.profile==='custom'&&<><label>Paranoia level<input type="number" min={1} max={4} value={waf.paranoia_level} onChange={e=>update('paranoia_level',Number(e.target.value))}/></label><label>Inbound anomaly threshold<input type="number" min={1} max={100} value={waf.inbound_threshold} onChange={e=>update('inbound_threshold',Number(e.target.value))}/></label></>}
    <label>Excluded CRS rule IDs · comma separated<input value={waf.excluded_rule_ids.join(',')} onChange={e=>update('excluded_rule_ids',e.target.value?e.target.value.split(',').map(Number):[])}/></label>
    <p>Exclusions weaken inspection. Start in detection mode and review false positives before blocking. Policies are rendered on both HTTP and POP-terminated HTTPS listeners; existing cached responses still pass through the NGINX request path.</p>
  </>;
}
