import {useState} from 'react';

const codes='AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW'.split(' ');
const names=new Intl.DisplayNames(['en'],{type:'region'});
const countries=codes.map(code=>({code,name:names.of(code)||code})).sort((a,b)=>a.name.localeCompare(b.name));

export default function CountrySelect({values,onChange}:{values:string[],onChange:(values:string[])=>void}){
 const [query,setQuery]=useState('');
 const selected=new Set(values);
 return <div><label>Countries</label><div className="actions">{values.map(code=><button type="button" key={code} aria-label={`Remove ${names.of(code)}`} onClick={()=>onChange(values.filter(v=>v!==code))}>{names.of(code)} ({code}) ×</button>)}</div><details><summary>Select countries ({values.length} selected)</summary><input aria-label="Search countries by name or ISO code" placeholder="Search France or FR…" value={query} onChange={e=>setQuery(e.target.value)}/><div style={{maxHeight:240,overflowY:'auto'}}>{countries.filter(c=>`${c.name} ${c.code}`.toLowerCase().includes(query.trim().toLowerCase())).map(c=><label className="check" key={c.code}><input type="checkbox" checked={selected.has(c.code)} onChange={e=>onChange(e.target.checked?[...values,c.code]:values.filter(v=>v!==c.code))}/>{c.name} ({c.code})</label>)}</div></details></div>;
}
