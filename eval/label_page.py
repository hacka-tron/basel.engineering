# ruff: noqa: E501
"""Build one self-contained offline labelling page for the judge calibration pool.

Reads ``eval/runs/calibration-pool.jsonl`` and ``eval/calibration.yaml`` and writes
``eval/runs/label.html`` (gitignored: it holds private source text). Inline CSS/JS
only, no network. The candidate kind is deliberately not shown (it would bias the
labeller). The page exports ``calibration-labels.json`` for ``eval.import_labels``.

All text reaches the page as JSON inside a ``<script type="application/json">`` block
(with ``<``, ``>``, ``&`` and U+2028/9 escaped) and is rendered with ``textContent``
only, so answers and sources cannot inject markup.
"""

import argparse
import json
from pathlib import Path

from eval.calibration_candidates import CALIBRATION_PATH, POOL_PATH, read_yaml

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "runs" / "label.html"


def json_for_script(data) -> str:
    """JSON safe to embed in a <script> element."""
    text = json.dumps(data, ensure_ascii=False)
    for char, esc in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):
        text = text.replace(char, esc)
    return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def build_items(pool: list[dict], yaml_items: list[dict]) -> list[dict]:
    """Pool items in yaml order, with split and any existing labels (no kind)."""
    by_id = {entry["id"]: entry for entry in yaml_items}
    out = []
    for item in pool:
        entry = by_id.get(item["id"])
        if entry is None:
            continue
        out.append(
            {
                "id": item["id"],
                "title": item["case_id"],  # the id starts with the kind: never display it
                "split": str(entry.get("split", "")).upper(),
                "question": item["question"],
                "answer": item["answer"],
                "sources": [
                    {"n": s["n"], "path": s["source_path"], "text": s["text"].strip()}
                    for s in item["sources"]
                ],
                "faithful": entry.get("faithful"),
                "relevant": entry.get("relevant"),
                "reason": entry.get("reason") or "",
            }
        )
    return out


def render(items: list[dict]) -> str:
    return PAGE.replace("__DATA__", json_for_script(items))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pool", type=Path, default=POOL_PATH)
    parser.add_argument("--yaml", type=Path, default=CALIBRATION_PATH)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args(argv)
    pool = [json.loads(line) for line in args.pool.read_text(encoding="utf-8").splitlines() if line]
    items = build_items(pool, read_yaml(args.yaml)["items"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(items), encoding="utf-8")
    print(f"wrote {args.out} ({len(items)} items, {args.out.stat().st_size // 1024} KB)")
    return 0


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Judge calibration labelling</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1c2330;--mute:#5f6b7a;--line:#d9dee5;--pass:#1a7f4b;--fail:#c0392b;--hl:#ffe08a;--acc:#2f5bd3}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--card);border-bottom:1px solid var(--line);padding:8px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
header .prog{font-weight:600}
button{font:inherit;border:1px solid var(--line);background:var(--card);border-radius:6px;padding:5px 12px;cursor:pointer}
button:hover{border-color:var(--acc)}
button.on-pass{background:var(--pass);color:#fff;border-color:var(--pass)}
button.on-fail{background:var(--fail);color:#fff;border-color:var(--fail)}
main{max-width:860px;margin:0 auto;padding:16px}
details.help{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 14px;margin-bottom:16px}
details.help summary{cursor:pointer;font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px 20px}
.hd{display:flex;gap:10px;align-items:center;color:var(--mute);font-size:14px}
.hd code{font-size:14px}
.badge{font-size:12px;font-weight:700;border-radius:4px;padding:1px 7px;background:#e8ecf5;color:var(--acc)}
.badge.TEST{background:#f3e6d3;color:#8a5a12}
.lbl{margin:16px 0 4px;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute)}
.q{font-size:17px;font-weight:600}
.ans{font-size:18px;line-height:1.6;white-space:pre-wrap;background:#fbfcfe;border-left:4px solid var(--acc);padding:10px 14px;border-radius:4px}
mark{background:var(--hl);padding:0 2px;border-radius:3px}
.src{border:1px solid var(--line);border-radius:6px;margin:6px 0;background:#fafbfc}
.src summary{cursor:pointer;padding:6px 10px;font-size:14px}
.src pre{margin:0;padding:8px 12px 12px;white-space:pre-wrap;font:13.5px/1.5 ui-monospace,Menlo,monospace;border-top:1px solid var(--line)}
.row{display:flex;gap:10px;align-items:center;margin:10px 0;flex-wrap:wrap}
.row .name{width:90px;font-weight:600}
.row kbd{font-size:12px;color:var(--mute)}
input.reason{width:100%;font:inherit;padding:7px 10px;border:1px solid var(--line);border-radius:6px}
.nav{display:flex;justify-content:space-between;margin-top:14px}
kbd{border:1px solid var(--line);border-radius:3px;padding:0 4px;background:#fff}
.note{color:var(--mute);font-size:13px}
</style></head><body>
<header>
<span class="prog" id="prog"></span>
<button id="prev">&larr; k</button><button id="next">j &rarr;</button>
<button id="export">Export labels</button>
<span class="note" id="saved"></span>
</header>
<main>
<details class="help"><summary>Rules and shortcuts</summary>
<p><b>Faithful: pass</b> = every claim in the answer is supported by the sources shown. <b>Fail</b> = a number, name or status the sources do not support, or a planned feature stated as working now. A refusal passes. Highlighted words in the answer are numbers or capitalised names that appear in no source: a hint to check, not a verdict.</p>
<p><b>Relevant: pass</b> = it addresses the question asked, even briefly. <b>Fail</b> = off topic, a different question, or a refusal.</p>
<p>Give a one-line reason, especially for every fail. Aim for at least 15 fails per judge overall. Judge faithfulness only against the sources shown (open them as needed).</p>
<p>Keys (not while typing in the reason box): <kbd>f</kbd> faithful pass, <kbd>F</kbd> faithful fail, <kbd>r</kbd> relevant pass, <kbd>R</kbd> relevant fail, <kbd>j</kbd> next, <kbd>k</kbd> previous, <kbd>e</kbd> focus reason, <kbd>Esc</kbd> leave reason. Pressing the same label key again clears it. Labels autosave in this browser; Export downloads <code>calibration-labels.json</code>.</p>
</details>
<div class="card" id="card"></div>
</main>
<script type="application/json" id="data">__DATA__</script>
<script>
(function(){
var ITEMS=JSON.parse(document.getElementById('data').textContent);
var KEY='calibration-labels-v1', POS='calibration-pos-v1';
var labels={}, idx=0;
try{labels=JSON.parse(localStorage.getItem(KEY)||'{}')||{};}catch(e){labels={};}
try{idx=Math.min(Math.max(parseInt(localStorage.getItem(POS)||'0',10)||0,0),ITEMS.length-1);}catch(e){idx=0;}
ITEMS.forEach(function(it){
  var l=labels[it.id]||(labels[it.id]={});
  if(l.faithful===undefined)l.faithful=it.faithful||null;
  if(l.relevant===undefined)l.relevant=it.relevant||null;
  if(l.reason===undefined)l.reason=it.reason||'';
});
function save(){
  try{localStorage.setItem(KEY,JSON.stringify(labels));localStorage.setItem(POS,String(idx));
    document.getElementById('saved').textContent='saved';}
  catch(e){document.getElementById('saved').textContent='autosave unavailable: export before closing';}
}
function el(tag,cls,text){var e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;}
function tokens(s){return (s.match(/\d[\d,.]*\d|\d|\b[A-Z][A-Za-z0-9_.+#-]*/g)||[]);}
var STOP={The:1,A:1,An:1,This:1,That:1,These:1,Those:1,It:1,Its:1,He:1,His:1,She:1,They:1,In:1,On:1,At:1,For:1,And:1,But:1,Or:1,If:1,As:1,With:1,There:1,Yes:1,No:1,I:1,We:1,You:1,Basel:1,What:1,How:1,Why:1,When:1,Where:1,Who:1,Is:1,Are:1,Does:1,Do:1};
function norm(t){return t.replace(/[.,]+$/,'').toLowerCase();}
function unsupported(item){
  var hay=item.sources.map(function(s){return s.text+' '+s.path;}).join('\n').toLowerCase();
  var out={};
  tokens(item.answer).forEach(function(t){
    var n=norm(t);
    if(!n||STOP[t])return;
    if(hay.indexOf(n)<0&&hay.indexOf(n.replace(/,/g,''))<0)out[n]=1;
  });
  return out;
}
function renderAnswer(box,item){
  var bad=unsupported(item), text=item.answer, re=/\d[\d,.]*\d|\d|\b[A-Z][A-Za-z0-9_.+#-]*/g, last=0, m;
  while((m=re.exec(text))){
    var n=norm(m[0]);
    if(bad[n]&&!STOP[m[0]]){
      box.appendChild(document.createTextNode(text.slice(last,m.index)));
      var mk=el('mark',null,m[0]);mk.title='not found in any source';box.appendChild(mk);
      last=m.index+m[0].length;
    }
  }
  box.appendChild(document.createTextNode(text.slice(last)));
}
function toggle(row,field,item){
  var l=labels[item.id];
  [['pass','pass (f/r)'],['fail','fail (F/R)']].forEach(function(p){
    var b=el('button',l[field]===p[0]?'on-'+p[0]:null,p[1]);
    b.onclick=function(){set(field,p[0]);};
    row.appendChild(b);
  });
}
function render(){
  var item=ITEMS[idx], l=labels[item.id], card=document.getElementById('card');
  card.textContent='';
  var hd=el('div','hd');hd.appendChild(el('span','badge '+item.split,item.split));
  hd.appendChild(el('code',null,item.title));card.appendChild(hd);
  card.appendChild(el('div','lbl','Question'));card.appendChild(el('div','q',item.question));
  card.appendChild(el('div','lbl','Answer'));
  var a=el('div','ans');renderAnswer(a,item);card.appendChild(a);
  card.appendChild(el('div','lbl','Sources ('+item.sources.length+', collapsed)'));
  item.sources.forEach(function(s){
    var d=el('details','src');d.appendChild(el('summary',null,'['+s.n+'] '+s.path));
    d.appendChild(el('pre',null,s.text));card.appendChild(d);
  });
  card.appendChild(el('div','lbl','Your labels'));
  var r1=el('div','row');r1.appendChild(el('span','name','Faithful'));toggle(r1,'faithful',item);card.appendChild(r1);
  var r2=el('div','row');r2.appendChild(el('span','name','Relevant'));toggle(r2,'relevant',item);card.appendChild(r2);
  var inp=el('input','reason');inp.id='reason';inp.placeholder='One-line reason (especially for fails)';inp.value=l.reason||'';
  inp.oninput=function(){l.reason=inp.value;save();};
  inp.onkeydown=function(e){if(e.key==='Escape')inp.blur();};
  card.appendChild(inp);
  var done=ITEMS.filter(function(i){var x=labels[i.id];return x.faithful&&x.relevant;}).length;
  document.getElementById('prog').textContent='Item '+(idx+1)+' / '+ITEMS.length+'  |  labelled '+done+' / '+ITEMS.length;
  window.scrollTo(0,0);
}
function set(field,v){var l=labels[ITEMS[idx].id];l[field]=(l[field]===v)?null:v;save();render();}
function go(d){idx=Math.min(Math.max(idx+d,0),ITEMS.length-1);save();render();}
document.getElementById('next').onclick=function(){go(1);};
document.getElementById('prev').onclick=function(){go(-1);};
document.getElementById('export').onclick=function(){
  var out={};ITEMS.forEach(function(i){var l=labels[i.id];out[i.id]={faithful:l.faithful||null,relevant:l.relevant||null,reason:l.reason||''};});
  var blob=new Blob([JSON.stringify(out,null,2)],{type:'application/json'});
  var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='calibration-labels.json';
  document.body.appendChild(a);a.click();a.remove();
};
document.addEventListener('keydown',function(e){
  var t=e.target&&e.target.tagName;if(t==='INPUT'||t==='TEXTAREA'||e.metaKey||e.ctrlKey||e.altKey)return;
  switch(e.key){
    case 'f':set('faithful','pass');break;case 'F':set('faithful','fail');break;
    case 'r':set('relevant','pass');break;case 'R':set('relevant','fail');break;
    case 'j':go(1);break;case 'k':go(-1);break;
    case 'e':e.preventDefault();document.getElementById('reason').focus();break;
  }
});
if(ITEMS.length)render();else document.getElementById('card').textContent='No items.';
})();
</script></body></html>
"""

if __name__ == "__main__":
    raise SystemExit(main())
