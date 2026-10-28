"""Local multi-table browser workbench for MiniDB."""
from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from database_engine import DatabaseServer

ROOT = Path(__file__).parent
CATALOG_ROOT = ROOT / "minidb_web_catalog"
SERVER = DatabaseServer(CATALOG_ROOT, pool_size=8, policy="lru")


def _database(name: str = "default"):
    return SERVER.engine(name)


def _database_names() -> list[str]:
    return SERVER.names()


def _create_database(name: str) -> None:
    SERVER.create_database(name)

HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MiniDB Workbench</title>
<style>
:root{color-scheme:dark;--bg:#120910;--side:#1d0c15;--panel:#28111c;--line:#583342;--text:#fff2f5;--muted:#c7aab3;--accent:#c13f61;--accent2:#f08ca4;--green:#9be0b4;--red:#ff9aaa}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,Segoe UI,sans-serif}.app{min-height:100vh;display:grid;grid-template-columns:230px 1fr}.side{background:var(--side);border-right:1px solid var(--line);padding:20px 14px}.brand{font-weight:800;font-size:19px;padding:5px 8px 22px}.brand small{display:block;color:var(--muted);font-size:10px;letter-spacing:.14em}.label{margin:18px 8px 6px;color:#a77785;font-size:10px;letter-spacing:.14em;font-weight:750}.nav{display:block;width:100%;margin:3px 0;padding:10px;border:0;border-radius:6px;background:none;color:#e0c8ce;text-align:left;cursor:pointer}.nav:hover,.nav.active{background:#552033;color:#fff}.conn{position:fixed;bottom:18px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding:14px 8px;width:195px}.conn b{color:var(--green)}main{min-width:0}.top{height:62px;display:flex;align-items:center;justify-content:space-between;gap:16px;padding:0 26px;background:#210d17;border-bottom:1px solid var(--line)}.topright{display:flex;gap:10px;align-items:center}.select,.button{border:1px solid var(--line);border-radius:6px;background:#351421;color:var(--text);padding:8px 10px}.button{cursor:pointer}.button:hover{background:#502034}.primary{background:var(--accent);border-color:var(--accent);font-weight:700}.content{max-width:1450px;margin:auto;padding:24px 26px}.view{display:none}.view.active{display:block}.head{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:18px}.head h1{font-size:23px;margin:0}.head p{margin:4px 0 0;color:var(--muted)}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}.metric,.card{background:var(--panel);border:1px solid var(--line);border-radius:9px;overflow:hidden}.metric{padding:13px}.metric span{color:var(--muted);font-size:11px}.metric strong{display:block;font-size:21px;margin-top:5px}.green{color:var(--green)}.pink{color:#ffadc0}.grid,.explorer,.monitor{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:15px}.card h2{font-size:13px;margin:0;padding:12px 14px;border-bottom:1px solid var(--line)}.tag{float:right;color:#ffd2dd;background:#50182b;border-radius:4px;padding:2px 6px;font:11px ui-monospace,monospace}.editor{padding:13px;background:#160910}textarea{width:100%;height:170px;resize:vertical;background:#10070c;border:1px solid #694052;border-radius:6px;color:#ffedf1;padding:12px;font:14px/1.55 ui-monospace,monospace}.actions{padding:10px 13px;display:flex;justify-content:space-between;align-items:center;color:var(--muted)}.body{padding:14px;min-height:140px;overflow:auto}.empty{color:var(--muted);text-align:center;padding:45px 8px}.table{width:100%;border-collapse:collapse}.table th,.table td{text-align:left;padding:9px;border-bottom:1px solid #53303d}.table th{color:#e0c2cb}.quick{padding:8px}.quick button{display:block;width:100%;padding:9px;border:0;border-radius:5px;background:none;color:#ead5da;text-align:left;font:12px ui-monospace,monospace;cursor:pointer}.quick button:hover{background:#50182b}.info{padding:13px 14px;color:var(--muted);font-size:12px;line-height:1.65}.schema{padding:10px 14px}.schema div{display:grid;grid-template-columns:1fr 100px 85px;padding:9px 0;border-bottom:1px solid var(--line)}.history .item{padding:11px 14px;border-bottom:1px solid var(--line);font:12px ui-monospace,monospace;cursor:pointer}.tree,.frames{display:flex;flex-wrap:wrap;gap:7px;padding:14px}.key,.frame{border:1px solid #864154;border-radius:4px;padding:6px 8px;font:12px ui-monospace,monospace;color:#ffd3dc}.key{background:#50182b}.frame.used{background:#50182b}.error{color:var(--red);background:#4a1827;padding:12px;border-radius:6px}.toast{color:var(--green)}@media(max-width:900px){.app{grid-template-columns:1fr}.side{display:none}.grid,.explorer,.monitor{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,1fr)}.content{padding:17px}.top{padding:0 17px}}@media(max-width:500px){.metrics{grid-template-columns:1fr}.top{height:auto;min-height:62px;flex-wrap:wrap;padding:10px 15px}}
</style></head>
<body><div class="app">
<aside class="side"><div class="brand">MiniDB<small>RELATIONAL ENGINE WORKBENCH</small></div><div class="label">WORKSPACE</div>
<button class="nav active" data-view="query">▣ &nbsp; Query editor</button><button class="nav" data-view="explorer">▤ &nbsp; Table explorer</button><button class="nav" data-view="history">◷ &nbsp; Query history</button><div class="label">ENGINE</div><button class="nav" data-view="monitor">◉ &nbsp; Engine monitor</button><div class="conn"><b>● Connected</b><br>Local MiniDB<br><span id="side-policy">LRU buffer pool</span></div></aside>
<main><header class="top"><div>Local MiniDB / <b id="crumb-database">default</b> / <b id="crumb-table">users</b></div><div class="topright"><label for="database-picker">Database</label><select id="database-picker" class="select"></select><button class="button" id="create-database">+ New database</button><label for="table-picker">Table</label><select id="table-picker" class="select"></select><button class="button" id="refresh-all">Refresh</button></div></header>
<div class="content">
<section class="view active" id="query-view"><div class="head"><div><h1>Query editor</h1><p>Run SQL against any table in the catalog.</p></div><button class="button" id="seed">Load sample data</button></div>
<div class="metrics"><div class="metric"><span>Engine</span><strong class="green">Online</strong></div><div class="metric"><span>Eviction policy</span><strong class="pink" id="policy">LRU</strong></div><div class="metric"><span>Cache hits</span><strong id="hits">0</strong></div><div class="metric"><span>Hit rate</span><strong id="rate">0.00%</strong></div></div>
<div class="grid"><div><section class="card"><h2>SQL editor <span class="tag" id="editor-table">users</span></h2><div class="editor"><textarea id="sql" spellcheck="false">SELECT * FROM users;</textarea></div><div class="actions"><span id="status">Ready</span><span id="transaction-state" class="tag">Autocommit</span><button class="button primary" id="run">▶ Run query</button></div></section><section class="card" style="margin-top:15px"><h2>Results <span class="tag" id="count">0 rows</span></h2><div class="body" id="result"><div class="empty">Run a query to view results.</div></div></section></div>
<aside><section class="card"><h2>Examples</h2><div class="quick" id="examples"></div></section><section class="card" style="margin-top:15px"><h2>Execution path</h2><div class="info">Primary-key equality and range filters use the B+ tree. Other predicates scan table pages through the buffer pool.</div></section></aside></div></section>
<section class="view" id="explorer-view"><div class="head"><div><h1>Table explorer</h1><p>Browse rows and schema for the selected table.</p></div><button class="button primary" id="refresh-rows">Refresh rows</button></div><div class="explorer"><section class="card"><h2 id="explorer-title">users</h2><div class="body" id="rows"></div></section><section class="card"><h2>Table definition</h2><div class="schema" id="schema"></div></section></div></section>
<section class="view" id="history-view"><div class="head"><div><h1>Query history</h1><p>Executed in this browser session.</p></div></div><section class="card history" id="history"><div class="empty">No queries executed yet.</div></section></section>
<section class="view" id="monitor-view"><div class="head"><div><h1>Engine monitor</h1><p>Live index and buffer state for the selected table.</p></div><button class="button" id="monitor-refresh">Refresh monitor</button></div><div class="metrics"><div class="metric"><span>Allocated pages</span><strong id="pages">0</strong></div><div class="metric"><span>Page size</span><strong id="size">4 KiB</strong></div><div class="metric"><span>Buffer frames</span><strong id="pool">0</strong></div><div class="metric"><span>Cache misses</span><strong id="misses">0</strong></div></div><div class="monitor"><div><section class="card"><h2>B+ tree index <span class="tag" id="index">primary key</span></h2><div class="tree" id="tree"></div></section><section class="card" style="margin-top:15px"><h2>Buffer pool</h2><div class="frames" id="frames"></div><div class="info">Dirty pages: <b id="dirty">0</b></div></section></div><section class="card"><h2>Selected table</h2><div class="info" id="table-info"></div></section></div></section>
</div></main></div>
<script>
const $=s=>document.querySelector(s), state={databases:[],database:"default",tables:[],table:"",history:[]};
function esc(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
async function request(url,options){if(url.startsWith("/")&&!url.startsWith("/databases")&&!url.startsWith("/session")){const sep=url.includes("?")?"&":"?";url+=sep+"db="+encodeURIComponent(state.database)}const response=await fetch(url,options);let data={};try{data=await response.json()}catch{}if(!response.ok){const error=Error(data.error||`Request failed (${response.status})`);error.payload=data;throw error}return data}
function draw(rows,target,count){rows=Array.isArray(rows)?rows:[rows];$(count).textContent=`${rows.length} row${rows.length===1?"":"s"}`;if(!rows.length){$(target).innerHTML='<div class="empty">No rows.</div>';return}const cols=Object.keys(rows[0]);$(target).innerHTML='<table class="table"><thead><tr>'+cols.map(c=>`<th>${esc(c)}</th>`).join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+cols.map(c=>`<td>${esc(r[c])}</td>`).join('')+'</tr>').join('')+'</tbody></table>'}
function setTransactionState(active){const el=$('#transaction-state');if(!el)return;el.textContent=active?'● Transaction active':'Autocommit';el.style.background=active?'#6d4814':'#243d30';el.style.color=active?'#ffe0a3':'#b7f0c5'}
function resultMarkup(result){if(Array.isArray(result)){if(!result.length)return '<div class="info">Completed — no rows returned.</div>';const cols=Object.keys(result[0]||{});if(!cols.length)return `<div class="info">${esc(JSON.stringify(result))}</div>`;return '<table class="table"><thead><tr>'+cols.map(c=>`<th>${esc(c)}</th>`).join('')+'</tr></thead><tbody>'+result.map(row=>'<tr>'+cols.map(c=>`<td>${esc(row[c])}</td>`).join('')+'</tr>').join('')+'</tbody></table>'}if(result&&typeof result==='object'&&result.message)return `<div class="toast info">✓ ${esc(result.message)}</div>`;if(result&&typeof result==='object'&&Number.isInteger(result.rows_affected))return `<div class="toast info">✓ ${result.rows_affected} row${result.rows_affected===1?'':'s'} affected</div>`;return `<pre class="info">${esc(JSON.stringify(result,null,2))}</pre>`}
function displayExecution(result){const statements=result&&Array.isArray(result.statements)?result.statements:[{statement:'Result',result}];let rowCount=0,hasRows=false;for(const item of statements){if(Array.isArray(item.result)){hasRows=true;rowCount+=item.result.length}}$('#result').innerHTML=statements.map((item,i)=>`<section style="margin-bottom:12px;border-bottom:1px solid var(--line);padding-bottom:10px"><div style="display:flex;justify-content:space-between;gap:10px;margin-bottom:7px"><code>${esc(item.statement)}</code><span class="toast">Completed</span></div>${resultMarkup(item.result)}</section>`).join('');$('#count').textContent=hasRows?`${rowCount} rows`:`${statements.length} statement${statements.length===1?'':'s'}`}
function addHistory(sql,ok){state.history.unshift({sql,ok});$('#history').innerHTML=state.history.map((item,i)=>`<div class="item" data-history="${i}">${esc(item.sql)}<br><small>${item.ok?'completed':'failed'}</small></div>`).join('');document.querySelectorAll('[data-history]').forEach(el=>el.onclick=()=>{$('#sql').value=state.history[Number(el.dataset.history)].sql;showView('query')})}
function updateTableLabels(){const name=state.table||'(no tables)';$('#crumb-database').textContent=state.database;$('#crumb-table').textContent=name;$('#editor-table').textContent=name;$('#explorer-title').textContent=name;document.title=`MiniDB · ${name}`;renderExamples()}
function renderExamples(){const name=state.table;if(!name){$('#examples').innerHTML='<div class="info">Create a table using CREATE TABLE to begin.</div>';return}const schema=state.schema||[];const pk=schema.find(c=>c.primary_key);const pval=pk&&['INT','INTEGER'].includes(pk.type.toUpperCase())?'1':"'sample'";const samples=[`SELECT * FROM ${name};`,`SELECT COUNT(*) AS total FROM ${name};`,`DESCRIBE ${name};`,`SHOW TABLES;`];if(pk)samples.splice(1,0,`SELECT * FROM ${name} WHERE ${pk.column} = ${pval};`);$('#examples').innerHTML=samples.map((q,i)=>`<button data-example="${i}">${esc(q)}</button>`).join('');document.querySelectorAll('[data-example]').forEach(el=>el.onclick=()=>{$('#sql').value=samples[Number(el.dataset.example)];runQuery()})}
async function refreshDatabases(preferred){const data=await request('/databases');state.databases=data.databases;const picker=$('#database-picker');picker.innerHTML=state.databases.map(d=>`<option value="${esc(d)}">${esc(d)}</option>`).join('');state.database=state.databases.includes(preferred)?preferred:(state.databases.includes(state.database)?state.database:'default');picker.value=state.database}
async function refreshTables(preferred){const data=await request('/tables');state.tables=data.tables;const picker=$('#table-picker');picker.innerHTML=state.tables.map(t=>`<option value="${esc(t)}">${esc(t)}</option>`).join('');state.table=state.tables.includes(preferred)?preferred:(state.tables.includes(state.table)?state.table:(state.tables[0]||''));picker.value=state.table;picker.disabled=!state.table;state.schema=state.table?(await request(`/describe?table=${encodeURIComponent(state.table)}`)).schema:[];updateTableLabels()}
async function refreshDashboard(){if(!state.table){$('#table-info').textContent='No table selected.';return}const d=await request(`/status?table=${encodeURIComponent(state.table)}`);const m=d.metrics;$('#policy').textContent=m.policy.toUpperCase();$('#side-policy').textContent=m.policy.toUpperCase()+' buffer pool';$('#hits').textContent=m.hits;$('#rate').textContent=m.hit_rate_percent.toFixed(2)+'%';$('#misses').textContent=m.misses;$('#pages').textContent=d.page_count;$('#size').textContent=`${d.page_size/1024} KiB`;$('#pool').textContent=`${d.frame_pages.filter(x=>x!==null).length} / ${d.pool_size}`;$('#dirty').textContent=d.dirty_pages;$('#index').textContent=d.indexed_column;$('#frames').innerHTML=d.frame_pages.map(x=>`<span class="frame ${x!==null?'used':''}">${x===null?'·':x}</span>`).join('');$('#tree').innerHTML=d.index_levels.map(level=>'<div class="tree">'+level.map(keys=>`<span class="key">[${keys.join(', ')||'empty'}]</span>`).join('')+'</div>').join('');$('#table-info').innerHTML=`<b>${esc(d.table)}</b><br>Primary key: ${esc(d.primary_key)}<br>Columns: ${d.schema.map(c=>esc(c.column)+' ('+esc(c.type)+')').join(', ')}`}
async function refreshRows(){if(!state.table)return;const [rowsData,schemaData]=await Promise.all([request(`/rows?table=${encodeURIComponent(state.table)}`),request(`/describe?table=${encodeURIComponent(state.table)}`)]);draw(rowsData.rows,'#rows','#count');$('#schema').innerHTML='<div><b>Column</b><b>Type</b><b>Key</b></div>'+schemaData.schema.map(c=>`<div><span>${esc(c.column)}</span><span>${esc(c.type)}</span><span>${c.primary_key?'PRIMARY':'—'}</span></div>`).join('');state.schema=schemaData.schema;renderExamples();await refreshDashboard()}
async function runQuery(query=$('#sql').value){const status=$('#status');status.textContent='Running…';status.className='';try{const data=await request('/execute',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sql:query,database:state.database})});displayExecution(data.result);setTransactionState(data.transaction_active);if(data.result&&data.result.database){state.database=data.result.database;await refreshDatabases(state.database)}else if(/^\s*CREATE\s+DATABASE\b/i.test(query)){await refreshDatabases(state.database)}addHistory(query,true);status.textContent='Completed';status.className='toast';const previous=state.table;await refreshTables(previous);if(state.table)await refreshDashboard()}catch(error){if(error.payload&&'transaction_active'in error.payload)setTransactionState(error.payload.transaction_active);$('#result').innerHTML=`<div class="error">${esc(error.message)}</div>`;addHistory(query,false);status.textContent='Failed';status.className='error'}}
function showView(name){document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.id===name+'-view'));document.querySelectorAll('.nav').forEach(b=>b.classList.toggle('active',b.dataset.view===name));if(name==='explorer')refreshRows();if(name==='monitor')refreshDashboard()}
$('#run').onclick=()=>runQuery();$('#database-picker').onchange=async e=>{state.database=e.target.value;state.table='';state.history=[];$('#history').innerHTML='<div class="empty">No queries executed yet.</div>';await refreshTables('');if(state.table){$('#sql').value=`SELECT * FROM ${state.table};`;await refreshDashboard()}else{$('#sql').value='CREATE TABLE items (id INT PRIMARY KEY, name TEXT);';$('#status').textContent=`Database '${state.database}' is empty. Create a table to begin.`;$('#status').className=''}};$('#create-database').onclick=async()=>{const name=prompt('Choose a database name (letters, numbers, and underscores):');if(name===null)return;try{await request('/databases',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});await refreshDatabases(name);await refreshTables('');$('#sql').value='CREATE TABLE items (id INT PRIMARY KEY, name TEXT);';$('#status').textContent=`Database '${name}' created. Run the CREATE TABLE statement to add your first table.`;$('#status').className='toast'}catch(error){$('#status').textContent=error.message;$('#status').className='error'}};$('#table-picker').onchange=async e=>{const old=state.table;state.table=e.target.value;state.schema=(await request(`/describe?table=${encodeURIComponent(state.table)}`)).schema;const pattern=new RegExp(`\\bFROM\\s+${old}\\b`,'i');if(old&&pattern.test($('#sql').value))$('#sql').value=$('#sql').value.replace(pattern,`FROM ${state.table}`);updateTableLabels();await refreshDashboard()};$('#refresh-all').onclick=async()=>{await refreshTables(state.table);await refreshDashboard()};$('#refresh-rows').onclick=refreshRows;$('#monitor-refresh').onclick=refreshDashboard;document.querySelectorAll('.nav').forEach(b=>b.onclick=()=>showView(b.dataset.view));$('#seed').onclick=async()=>{try{if(!state.tables.includes('users'))await request('/execute',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sql:'CREATE TABLE users (id INT PRIMARY KEY, name TEXT, role TEXT)'})});const desc=await request('/describe?table=users');const names=desc.schema.map(c=>c.column);if(!['id','name','role'].every(n=>names.includes(n)))throw Error('The users table must have id, name, and role columns to load the sample rows.');for(const row of [[1,'Ada','admin'],[2,'Grace','engineer'],[3,'Linus','engineer']]){const found=await request('/execute',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sql:`SELECT id FROM users WHERE id = ${row[0]}`})});if(!found.result.length)await request('/execute',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sql:`INSERT INTO users (id, name, role) VALUES (${row[0]}, '${row[1]}', '${row[2]}')`})})}await refreshTables('users');await refreshRows();showView('explorer')}catch(error){$('#status').textContent=error.message;$('#status').className='error'}};
(async()=>{try{const session=await request('/session');state.database=session.database||'default';setTransactionState(session.transaction_active);await refreshDatabases(state.database);await refreshTables('users');if(state.table){$('#sql').value=`SELECT * FROM ${state.table};`;await refreshDashboard()}else{$('#sql').value='CREATE TABLE users (id INT PRIMARY KEY, name TEXT, role TEXT);';$('#status').textContent='Create your first table.'}}catch(error){$('#status').textContent=error.message;$('#status').className='error'}})();
</script></body></html>'''


def _table_names(database: str = "default") -> list[str]:
    return [item["table"] for item in SERVER.engine(database).execute("SHOW TABLES")]


def _default_table(database: str = "default") -> str | None:
    tables = _table_names(database)
    return "users" if "users" in tables else (tables[0] if tables else None)


def send_json(handler: BaseHTTPRequestHandler, status: HTTPStatus, body: dict) -> None:
    data = json.dumps(body, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        params = parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                data = HTML.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if parsed.path == "/databases":
                send_json(self, HTTPStatus.OK, {"databases": _database_names()})
                return
            if parsed.path == "/session":
                send_json(self, HTTPStatus.OK, {"database": SERVER.current_database, "transaction_active": SERVER.transaction_active})
                return
            db = params.get("db", ["default"])[0]
            engine = _database(db)
            if parsed.path == "/tables":
                send_json(self, HTTPStatus.OK, {"tables": _table_names(db)})
                return
            table = params.get("table", [_default_table(db)])[0]
            if parsed.path in {"/describe", "/rows", "/status"}:
                if not table:
                    send_json(self, HTTPStatus.NOT_FOUND, {"error": "No table exists yet"})
                    return
                if parsed.path == "/describe":
                    send_json(self, HTTPStatus.OK, {"table": table, "schema": engine.execute(f"DESCRIBE {table}")})
                elif parsed.path == "/rows":
                    send_json(self, HTTPStatus.OK, {"table": table, "rows": engine.execute(f"SELECT * FROM {table}")})
                else:
                    dashboard = engine.dashboard(table)
                    send_json(self, HTTPStatus.OK, dashboard)
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except (KeyError, ValueError, TypeError) as error:
            send_json(self, HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        try:
            if parsed.path == "/databases":
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > 10_000:
                    raise ValueError("Request body must be between 1 byte and 10 KB")
                payload = json.loads(self.rfile.read(size))
                name = payload.get("name")
                result = SERVER.create_database(name)
                send_json(self, HTTPStatus.CREATED, {**result, "database": name})
                return
            if parsed.path != "/execute":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 1_000_000:
                raise ValueError("Request body must be between 1 byte and 1 MB")
            payload = json.loads(self.rfile.read(size))
            sql = payload.get("sql")
            if not isinstance(sql, str) or not sql.strip():
                raise ValueError("A non-empty SQL string is required")
            result = SERVER.execute(sql, database=payload.get("database", "default"))
            send_json(self, HTTPStatus.OK, {"result": result, "database": SERVER.current_database, "transaction_active": SERVER.transaction_active})
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as error:
            send_json(self, HTTPStatus.BAD_REQUEST, {"error": str(error), "transaction_active": SERVER.transaction_active})

    def log_message(self, *_args) -> None:
        pass


def main() -> None:
    if "users" not in _table_names():
        SERVER.execute("CREATE TABLE users (id INT PRIMARY KEY, name TEXT, role TEXT)", database="default")
    port = int(os.environ.get("MINIDB_PORT", "8000"))
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"MiniDB console: http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SERVER.close()
        server.server_close()


if __name__ == "__main__":
    main()
