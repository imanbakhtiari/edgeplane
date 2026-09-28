import {useEffect,useRef,useState} from 'react';
import {Terminal} from '@xterm/xterm';
import {FitAddon} from '@xterm/addon-fit';
import {Maximize2,Minimize2,Minus,X} from 'lucide-react';
import {getCSRF,type Row} from '../api';
import {ErrorBox} from './ui';

export function openTerminal(node:Row){window.dispatchEvent(new CustomEvent('edgeplane:ssh',{detail:node}))}

function Session({node,visible,minimize,close}:{node:Row,visible:boolean,minimize:()=>void,close:()=>void}){
 const host=useRef<HTMLDivElement>(null),term=useRef<Terminal|null>(null),fit=useRef<FitAddon|null>(null);
 const [fullscreen,setFullscreen]=useState(false),[error,setError]=useState(''),[state,setState]=useState('Connecting');
 useEffect(()=>{
  const terminal=new Terminal({cursorBlink:true,fontSize:13,scrollback:10000,theme:{background:'#101419',foreground:'#dce4eb'}}),addon=new FitAddon();
  terminal.loadAddon(addon);terminal.open(host.current!);term.current=terminal;fit.current=addon;
  const ws=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/api/v1/ws/ssh/${node.id}?csrf=${encodeURIComponent(getCSRF())}`);
  const resize=()=>{if(!host.current?.clientWidth||!host.current?.clientHeight)return;addon.fit();if(ws.readyState===WebSocket.OPEN)ws.send(JSON.stringify({type:'resize',cols:terminal.cols,rows:terminal.rows}))};
  ws.onopen=()=>{setState('Connected');resize()};
  ws.onmessage=e=>{try{const message=JSON.parse(e.data);if(message.type==='output')terminal.write(message.data);else if(message.type==='error')setError(message.message)}catch{terminal.write(e.data)}};
  ws.onclose=()=>{setState('Disconnected');terminal.writeln('\r\n[Session disconnected]')};
  ws.onerror=()=>setError('SSH connection failed. Check the node connection and your session.');
  const input=terminal.onData(data=>{if(ws.readyState===WebSocket.OPEN)ws.send(JSON.stringify({type:'input',data}))});
  const observer=new ResizeObserver(resize);observer.observe(host.current!);resize();
  return()=>{observer.disconnect();input.dispose();ws.close();terminal.dispose();term.current=null};
 },[node.id]);
 useEffect(()=>{if(visible){const frame=requestAnimationFrame(()=>{fit.current?.fit();term.current?.focus()});return()=>cancelAnimationFrame(frame)}},[visible,fullscreen]);
 return <div className="terminal-overlay" style={{display:visible?'flex':'none'}}><section role="dialog" aria-label={`SSH ${node.name}`} className={'terminal-window '+(fullscreen?'terminal-fullscreen':'')}>
  <header><div><strong>SSH · {node.name}</strong><small>{node.hostname} · {state}</small></div><div className="actions">
   <button onClick={async()=>{try{term.current?.paste(await navigator.clipboard.readText());setError('')}catch{setError('Clipboard access denied. Paste with Ctrl+Shift+V in the terminal.')}}}>Paste clipboard</button>
   <button aria-label="Minimize session" onClick={minimize}><Minus size={16}/></button>
   <button aria-label={fullscreen?'Restore window':'Full screen'} onClick={()=>setFullscreen(!fullscreen)}>{fullscreen?<Minimize2 size={16}/>:<Maximize2 size={16}/>}</button>
   <button aria-label="Close SSH session" onClick={close}><X size={16}/></button>
  </div></header><div ref={host} className="terminal-host"/><ErrorBox error={error}/><footer><small>Configured SSH user · Minimize keeps this session connected. Closing the tab or signing out ends it.</small></footer>
 </section></div>
}

export default function TerminalDock(){
 const [sessions,setSessions]=useState<Row[]>([]),[active,setActive]=useState<string|null>(null);
 useEffect(()=>{const open=(event:Event)=>{const node=(event as CustomEvent<Row>).detail;setSessions(rows=>rows.some(row=>row.id===node.id)?rows:[...rows,node]);setActive(node.id)};window.addEventListener('edgeplane:ssh',open);return()=>window.removeEventListener('edgeplane:ssh',open)},[]);
 const close=(id:string)=>{setSessions(rows=>rows.filter(row=>row.id!==id));setActive(current=>current===id?null:current)};
 return <>{sessions.map(node=><Session key={node.id} node={node} visible={active===node.id} minimize={()=>setActive(null)} close={()=>close(node.id)}/>)}{!!sessions.length&&<div className="terminal-dock" aria-label="Open SSH sessions">{sessions.map(node=><button key={node.id} className={active===node.id?'primary':''} onClick={()=>setActive(active===node.id?null:node.id)}>SSH · {node.name}</button>)}</div>}</>
}
