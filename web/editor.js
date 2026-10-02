import {EditorState, StateField, Compartment} from '@codemirror/state';
import {EditorView, Decoration, WidgetType, keymap, placeholder, drawSelection} from '@codemirror/view';
import {history, historyField, historyKeymap, defaultKeymap, indentWithTab} from '@codemirror/commands';
import {markdown} from '@codemirror/lang-markdown';

let bridge = null, remote = false, sourceMode = false, revision=0;
const mode = new Compartment();
const menu = document.getElementById('blocks');

class Marker extends WidgetType {
  constructor(text, cls) { super(); this.text=text; this.cls=cls; }
  eq(other) { return this.text===other.text && this.cls===other.cls; }
  toDOM() { const el=document.createElement('span'); el.className=this.cls; el.textContent=this.text; return el; }
  ignoreEvent() { return false; }
}

class ImagePreview extends WidgetType {
  constructor(path, caption) { super(); this.path=path; this.caption=caption; }
  eq(other) { return this.path===other.path && this.caption===other.caption; }
  toDOM() {
    const figure=document.createElement('figure'), img=document.createElement('img'), caption=document.createElement('figcaption');
    figure.className='attached-image';
    img.src='file:///'+this.path.split('/').map((part,index)=>index===0?part:encodeURIComponent(part)).join('/');
    img.alt=this.caption;
    caption.textContent=this.caption;
    figure.append(img,caption);
    return figure;
  }
  ignoreEvent() { return false; }
}

function decorations(state) {
  const marks=[], caret=state.selection.main;
  const active=(from,to)=>caret.from<=to && caret.to>=from;
  const lineClass=(from,cls)=>marks.push(Decoration.line({class:cls}).range(from));
  const hide=(from,to,widget)=> {if(to>from) marks.push(Decoration.replace(widget ? {widget} : {}).range(from,to));};
  let fence=null;
  for(let n=1;n<=state.doc.lines;n++) {
    const line=state.doc.line(n), text=line.text;
    const match=text.match(/^\s*(`{3,}|~{3,})(.*)$/);
    if(fence) {
      if(match && match[1][0]===fence.char && match[1].length>=fence.length && !match[2].trim()) {
        lineClass(line.from,'cm-code-fence');
        if(!active(line.from,line.to)) hide(line.from,line.to,new Marker('\u200b','code-label'));
        fence=null;
      } else lineClass(line.from,'cm-code');
      continue;
    }
    if(match) {
      fence={char:match[1][0],length:match[1].length};
      lineClass(line.from,'cm-code-fence');
      if(!active(line.from,line.to)) hide(line.from,line.to,new Marker(match[2].trim()||'CODE','code-label'));
      continue;
    }
    const image=text.match(/^!\[([^\]]*)\]\(<([A-Za-z]:\/[^>]+)>\)$/);
    if(image) {
      lineClass(line.from,'cm-image-line');
      if(!active(line.from,line.to)) hide(line.from,line.to,new ImagePreview(image[2],image[1]||'Attached image'));
      continue;
    }
    const heading=text.match(/^(#{1,6})\s+/);
    if(heading) {
      lineClass(line.from,`cm-h${Math.min(3,heading[1].length)}`);
      if(!active(line.from,line.to)) hide(line.from,line.from+heading[0].length);
    }
    const quote=text.match(/^>\s?/);
    if(quote) {lineClass(line.from,'cm-quote'); if(!active(line.from,line.to)) hide(line.from,line.from+quote[0].length);}
    const task=text.match(/^(\s*)[-*+]\s+\[([ xX])\]\s+/);
    const bullet=text.match(/^(\s*)(?:[-*+]\s+|(\d+)\.\s+)/);
    if(task && !active(line.from,line.to)) hide(line.from,line.from+task[0].length,new Marker(task[2]===' '?'☐':'☑','task-marker'));
    else if(bullet && !active(line.from,line.to)) hide(line.from,line.from+bullet[0].length,new Marker(bullet[2]?bullet[2]+'.':'•','list-marker'));
    // Style inline syntax while preserving every source character. Hide only
    // delimiters away from the current selection; code is never parsed here.
    const tokens=/(`+)([^`]+?)\1|\*\*(.+?)\*\*|__(.+?)__|~~(.+?)~~|(?<!\*)\*([^*]+?)\*(?!\*)|(?<!_)_([^_]+?)_(?!_)|\[([^\]]+)\]\(([^)]+)\)/g;
    for(const hit of text.matchAll(tokens)) {
      const from=line.from+hit.index,to=from+hit[0].length;
      let left,right,cls;
      if(hit[1]) {left=hit[1].length;right=left;cls='cm-inline-code';}
      else if(hit[3]||hit[4]) {left=right=2;cls='cm-strong';}
      else if(hit[5]) {left=right=2;cls='cm-strike';}
      else if(hit[6]||hit[7]) {left=right=1;cls='cm-emphasis';}
      else {left=1;right=hit[0].length-hit[8].length-1;cls='cm-link';}
      marks.push(Decoration.mark({class:cls}).range(from+left,to-right));
      if(!active(from,to)) {hide(from,from+left);hide(to-right,to);}
    }
  }
  return Decoration.set(marks,true);
}

const liveFormatting=StateField.define({
  create:decorations,
  update(value,tr) {return tr.docChanged||tr.selection?decorations(tr.state):value;},
  provide:field=>EditorView.decorations.from(field)
});

function publish() {
  if(remote || !bridge) return;
  const {anchor,head}=view.state.selection.main;
  bridge.stateChanged(view.state.doc.toString(),anchor,head,revision);
}

function wrap(left,right=left) {
  const range=view.state.selection.main, text=view.state.sliceDoc(range.from,range.to);
  view.dispatch({changes:{from:range.from,to:range.to,insert:left+text+right},selection:{anchor:range.from+left.length,head:range.from+left.length+text.length}});
}

function prefix(prefix) {
  const range=view.state.selection.main, start=view.state.doc.lineAt(range.from), end=view.state.doc.lineAt(range.to);
  const changes=[];
  for(let n=start.number;n<=end.number;n++) {
    const line=view.state.doc.line(n);
    const current=line.text.match(/^(?:#{1,6}\s+|>\s?|[-*+]\s+(?:\[[ xX]\]\s+)?|\d+\.\s+)/)?.[0]||'';
    changes.push({from:line.from,to:line.from+current.length,insert:prefix});
  }
  view.dispatch({changes});
}

function command(name) {
  if(!menu.hidden) {
    const line=view.state.doc.lineAt(view.state.selection.main.head);
    if(line.text==='/' || line.text.startsWith('/')) view.dispatch({changes:{from:line.from,to:line.to,insert:''}});
    menu.hidden=true;
  }
  if(/^h[123]$/.test(name)) prefix('#'.repeat(Number(name[1]))+' ');
  else if(name==='paragraph') prefix('');
  else if(name==='bold') wrap('**');
  else if(name==='italic') wrap('*');
  else if(name==='strike') wrap('~~');
  else if(name==='bullet') prefix('- ');
  else if(name==='ordered') prefix('1. ');
  else if(name==='task') prefix('- [ ] ');
  else if(name==='quote') prefix('> ');
  else if(name==='code') {
    const range=view.state.selection.main, selected=view.state.sliceDoc(range.from,range.to);
    const fence='`'.repeat(Math.max(3,...Array.from(selected.matchAll(/`+/g),m=>m[0].length+1)));
    const pre=range.from && view.state.sliceDoc(range.from-1,range.from)!=='\n'?'\n':'';
    const post=range.to<view.state.doc.length && view.state.sliceDoc(range.to,range.to+1)!=='\n'?'\n':'';
    const before=pre+fence+'text\n';
    view.dispatch({changes:{from:range.from,to:range.to,insert:before+selected+'\n'+fence+post},selection:{anchor:range.from+before.length,head:range.from+before.length+selected.length}});
  }
  else if(name==='link') wrap('[','](https://example.com)');
  view.focus();
}

function editorExtensions() { return [
  history(),drawSelection(),markdown(),EditorView.lineWrapping,
  EditorView.domEventHandlers({paste(event) {
    const hasImage=Array.from(event.clipboardData?.items||[]).some(item=>item.kind==='file' && item.type.startsWith('image/'));
    if(!hasImage) return false;
    event.preventDefault();
    publish();
    bridge?.attachClipboardImage();
    return true;
  }}),
  placeholder('Write markdown… Type / for blocks.'),
  keymap.of([
    {key:'Mod-b',run:()=>{command('bold');return true;}},
    {key:'Mod-i',run:()=>{command('italic');return true;}},
    {key:'Ctrl-Enter',run:()=>{publish();bridge?.copyPrompt();return true;}},
    {key:'Escape',run:()=>{menu.hidden=true;bridge?.cancelCapture();return true;}},
    ...historyKeymap,...defaultKeymap,indentWithTab
  ]),
  mode.of(sourceMode?[]:liveFormatting),
  EditorView.updateListener.of(update=>{
    if(update.docChanged || update.selectionSet) publish();
    const line=update.state.doc.lineAt(update.state.selection.main.head);
    if(!sourceMode && /^\/[a-z]*$/.test(line.text)) {
      const box=view.coordsAtPos(line.to);
      if(box) {menu.style.left=Math.max(8,Math.min(box.left,window.innerWidth-245))+'px';menu.style.top=Math.min(box.bottom+5,window.innerHeight-310)+'px';menu.hidden=false;}
    } else menu.hidden=true;
  })
]; }
const view=new EditorView({parent:document.getElementById('editor'),state:EditorState.create({doc:'',extensions:editorExtensions()})});

document.querySelectorAll('[data-command]').forEach(button=>{
  button.addEventListener('mousedown',event=>event.preventDefault());
  button.addEventListener('click',()=>command(button.dataset.command));
});
document.getElementById('image').addEventListener('mousedown',event=>event.preventDefault());
document.getElementById('image').addEventListener('click',()=>{publish();bridge?.attachImageDialog();});

function setSource(enabled) {
  sourceMode=enabled;
  document.body.classList.toggle('source-mode',enabled);
  const button=document.getElementById('source');
  button.setAttribute('aria-pressed',String(enabled));
  button.textContent=enabled?'¶ Formatted':'</> Source';
  view.dispatch({effects:mode.reconfigure(enabled?[]:liveFormatting)});
  view.focus();
}
document.getElementById('source').addEventListener('mousedown',event=>event.preventDefault());
document.getElementById('source').addEventListener('click',()=>setSource(!sourceMode));

window.scratchpad={
  view,command,setSource,
  text:()=>view.state.doc.toString(),
  saveState:()=>({editor:view.state.toJSON({history:historyField}),sourceMode,revision,scrollTop:view.scrollDOM.scrollTop,scrollLeft:view.scrollDOM.scrollLeft}),
  restoreState(saved) {
    remote=true;
    try {
      sourceMode=saved.sourceMode;
      revision=saved.revision;
      view.setState(EditorState.fromJSON(saved.editor,{extensions:editorExtensions()},{history:historyField}));
      document.body.classList.toggle('source-mode',sourceMode);
      const button=document.getElementById('source');
      button.setAttribute('aria-pressed',String(sourceMode));
      button.textContent=sourceMode?'¶ Formatted':'</> Source';
      requestAnimationFrame(()=>{view.scrollDOM.scrollTop=saved.scrollTop;view.scrollDOM.scrollLeft=saved.scrollLeft;});
    } finally {remote=false;}
  },
  setDocument(text,anchor,head,version=revision) {
    revision=version;
    remote=true;
    try {
      const old=view.state.doc.toString();
      let start=0,end=old.length,newEnd=text.length;
      while(start<end && start<newEnd && old[start]===text[start]) start++;
      while(end>start && newEnd>start && old[end-1]===text[newEnd-1]) {end--;newEnd--;}
      const spec={selection:{anchor:Math.min(anchor,text.length),head:Math.min(head,text.length)}};
      if(old!==text) spec.changes={from:start,to:end,insert:text.slice(start,newEnd)};
      view.dispatch(spec);
    } finally {remote=false;}
  },
  focus:()=>view.focus(),
  shutdown:()=>view.destroy()
};

if(typeof QWebChannel!=='undefined' && window.qt) {
  new QWebChannel(qt.webChannelTransport,channel=>{bridge=channel.objects.bridge;bridge.ready();});
}
