# -*- coding: utf-8 -*-
"""建立並啟用三個 n8n workflows（透過 public API）"""
import json
import os
import urllib.request
import uuid

BASE = "https://YOUR-LABEL.japaneast.cloudapp.azure.com"
API_KEY = open(os.path.expandvars(r"%TEMP%\n8n_key.txt"), encoding="ascii").read().strip()

CRED_AOAI = {"id": "YOUR_N8N_CRED_ID_AZURE_OPENAI", "name": "Azure OpenAI account"}
CRED_QDRANT = {"id": "YOUR_N8N_CRED_ID_QDRANT", "name": "Qdrant local"}
CRED_HDR = {"id": "YOUR_N8N_CRED_ID_HTTP_HEADER", "name": "AOAI api-key header"}
# LINE 憑證由環境變數提供（值記錄於 credentials.md）
LINE_ID = os.environ["LINE_CHANNEL_ID"]
LINE_SECRET = os.environ["LINE_CHANNEL_SECRET"]
EMBED_URL = ("https://YOUR-AOAI-RESOURCE.openai.azure.com/openai/deployments/"
             "text-embedding-3-small/embeddings?api-version=2024-02-01")

# ---------- 共用 JS ----------

JS_PARSE_CONTACTS = r'''
const XLSX = require('xlsx');
const crypto = require('crypto');
const binKeys = Object.keys($input.first().binary || {});
if (!binKeys.length) throw new Error('沒有收到上傳檔案');
const buf = await this.helpers.getBinaryDataBuffer(0, binKeys[0]);
const wb = XLSX.read(buf, {type: 'buffer'});
let best = null;
for (const n of wb.SheetNames) {
  const m = n.trim().match(/^通訊錄(\d{8})$/);
  if (m && (!best || m[1] > best.date)) best = {name: n, date: m[1]};
}
if (!best) throw new Error('找不到 通訊錄YYYYMMDD 工作表');
const rows = XLSX.utils.sheet_to_json(wb.Sheets[best.name], {header: 1, defval: ''});
const S = v => String(v == null ? '' : v).replace(/\s+/g, ' ').trim();
const uuid = t => { const h = crypto.createHash('md5').update(t, 'utf8').digest('hex');
  return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20,32)}`; };
const chunks = [];
let vendorRow = rows.length;
for (let i = 0; i < rows.length; i++) if (S(rows[i][0]) === '廠商') { vendorRow = i; break; }
// 上半部：資通處員工（左右兩組欄位）
const dept = {0: '處本部', 1: '處本部'};
for (let i = 0; i < vendorRow; i++) {
  for (const [g, [cN, cP, cE]] of [[0, [0, 2, 3]], [1, [4, 6, 7]]]) {
    const name = S(rows[i][cN]), phone = S(rows[i][cP]), ext = S(rows[i][cE]);
    if (!name || name === '姓名') continue;
    if (/科\s*(\(\d+人\))?$/.test(name) || /科\(\d+人\)/.test(name)) { dept[g] = name.replace(/\(\d+人\)/, '').trim(); continue; }
    if (!phone && !ext) continue;
    chunks.push({ text: `資通處 ${dept[g]} ${name}，連絡電話 ${phone || '（無）'}${ext ? '，分機 ' + ext : ''}`,
      meta: {source: 'contacts', kind: 'staff', dept: dept[g], name, sheet: best.name} });
  }
}
// 下半部：廠商（公司名稱含負責系統，向下沿用）
const comp = {0: '', 1: ''};
for (let i = vendorRow + 1; i < rows.length; i++) {
  for (const [g, [cC, cN, cP, cE]] of [[0, [0, 1, 2, 3]], [1, [4, 5, 6, 7]]]) {
    const rawC = String(rows[i][cC] == null ? '' : rows[i][cC]).trim();
    const name = S(rows[i][cN]), phone = S(rows[i][cP]), ext = S(rows[i][cE]);
    if (rawC && rawC !== '公司') comp[g] = rawC.replace(/\s+/g, '');
    if (!name || name === '姓名' || !phone) continue;
    const m = comp[g].match(/^([^(（]+)[(（](.+)[)）]$/);
    const vendor = m ? m[1] : comp[g];
    const sys = m ? m[2] : '';
    chunks.push({ text: `廠商 ${vendor}${sys ? '（負責系統：' + sys + '）' : ''} 聯絡人 ${name}，電話 ${phone}${ext ? '，分機 ' + ext : ''}`,
      meta: {source: 'contacts', kind: 'vendor', vendor, system: sys, name, sheet: best.name} });
  }
}
if (!chunks.length) throw new Error('解析結果為 0 筆');
return chunks.map(c => ({json: {id: uuid('contacts|' + c.text), text: c.text, metadata: c.meta}}));
'''

JS_PARSE_EVENTS = r'''
const XLSX = require('xlsx');
const crypto = require('crypto');
const binKeys = Object.keys($input.first().binary || {});
if (!binKeys.length) throw new Error('沒有收到上傳檔案');
const buf = await this.helpers.getBinaryDataBuffer(0, binKeys[0]);
const wb = XLSX.read(buf, {type: 'buffer'});
const S = v => String(v == null ? '' : v).replace(/\s+/g, ' ').trim();
const uuid = t => { const h = crypto.createHash('md5').update(t, 'utf8').digest('hex');
  return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20,32)}`; };
const out = [];
for (const sn of wb.SheetNames) {
  if (!/^\d{6}\s*$/.test(sn)) continue;
  const month = sn.trim();
  const rows = XLSX.utils.sheet_to_json(wb.Sheets[sn], {header: 1, defval: '', raw: false});
  let h = -1;
  for (let i = 0; i < Math.min(rows.length, 12); i++) {
    const set = rows[i].map(S);
    if (set.includes('值班人員') && set.some(c => c.startsWith('事件類別'))) { h = i; break; }
  }
  if (h < 0) continue;
  const hdr = rows[h].map(S);
  const col = sub => hdr.findIndex(c => c.includes(sub));
  const ci = { shift: col('值班時間'), person: col('值班人員'), cat: col('事件類別'), desc: col('事件描述'),
    proc: col('處理過程'), level: col('異常級別'), start: col('事件發生時間'), end: col('事件處理完成'), result: col('處理結果') };
  for (let i = h + 1; i < rows.length; i++) {
    const r = rows[i];
    const date = S(r[0]);
    const get = k => ci[k] >= 0 ? S(r[ci[k]]) : '';
    const cat = get('cat'), desc = get('desc');
    if (!desc || date.startsWith('範例')) continue;
    if (!(cat.includes('異常通報') || cat.includes('資安事件'))) continue;
    const text = `[${month}] ${date} ${get('shift')} 值班人員:${get('person')}｜類別:${cat}` +
      (get('level') ? `｜異常級別:${get('level')}` : '') +
      `｜事件:${desc}｜處理過程:${get('proc') || '（未填）'}｜結果:${get('result') || '（未填）'}` +
      (get('start') ? `｜發生:${get('start')}` : '') + (get('end') ? `｜完成:${get('end')}` : '');
    out.push({json: { id: uuid(`events|${month}|${date}|${desc}|${get('start')}`), text,
      metadata: {source: 'events', month, date, category: cat, level: get('level'), person: get('person')} }});
  }
}
if (!out.length) throw new Error('解析結果為 0 筆異常事件');
return out;
'''

JS_BATCH_CONTACTS = r'''
const items = $('解析通訊錄').all().map(i => i.json);
const out = []; const B = 64;
for (let i = 0; i < items.length; i += B) {
  const s = items.slice(i, i + B);
  out.push({json: {ids: s.map(x => x.id), texts: s.map(x => x.text), metas: s.map(x => x.metadata)}});
}
return out;
'''

JS_FILTER_NEW_EVENTS = r'''
const existing = new Set((($json.result) || []).map(p => String(p.id)));
const all = $('解析工作日誌').all();
const fresh = all.filter(i => !existing.has(String(i.json.id)));
const out = []; const B = 64;
for (let i = 0; i < fresh.length; i += B) {
  const s = fresh.slice(i, i + B);
  out.push({json: {ids: s.map(x => x.json.id), texts: s.map(x => x.json.text), metas: s.map(x => x.json.metadata)}});
}
if (!out.length) return [{json: {ids: [], texts: [], metas: [], message: '無新增事件，全部已存在'}}];
return out;
'''

def js_build_points(batch_node):
    return f'''
const batches = $('{batch_node}').all();
return $input.all().map((item, bi) => {{
  const b = batches[bi].json;
  const points = item.json.data.map((d, i) => ({{ id: b.ids[i], vector: d.embedding,
    payload: {{ content: b.texts[i], metadata: b.metas[i] }} }}));
  return {{ json: {{ points }} }};
}});
'''

JS_PARSE_LINE = r'''
const b = $input.first().json.body || {};
const ev = (b.events || [])[0] || {};
return [{json: { type: ev.type || '', text: (ev.message && ev.message.text) || '', replyToken: ev.replyToken || '' }}];
'''

SYSTEM_MSG = ("你是資訊機房 OP 通報助理。使用工具查詢通訊錄與歷史異常事件記錄後回答：\n"
              "1. 這個問題應該通報給誰（單位或廠商、姓名、電話、分機）\n"
              "2. 歷史上類似事件是如何處理的（摘要處理過程與結果）\n"
              "請用繁體中文回答，條列式、簡潔清楚，適合在 LINE 訊息中閱讀。若查無相關資料，請誠實說明。")


def http_node(name, pos, params, cred=None, execute_once=False, on_error=None):
    n = {"name": name, "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
         "position": pos, "parameters": params}
    if cred:
        n["credentials"] = cred
    if execute_once:
        n["executeOnce"] = True
    if on_error:
        n["onError"] = on_error
    return n


def code_node(name, pos, js):
    return {"name": name, "type": "n8n-nodes-base.code", "typeVersion": 2,
            "position": pos, "parameters": {"jsCode": js}}


def embed_node(name, pos):
    return http_node(name, pos, {
        "method": "POST", "url": EMBED_URL,
        "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({input: $json.texts}) }}", "options": {}
    }, cred={"httpHeaderAuth": CRED_HDR})


def chain(*names):
    return {names[i]: {"main": [[{"node": names[i+1], "type": "main", "index": 0}]]}
            for i in range(len(names) - 1)}


def form_trigger(name, path, title, desc):
    return {"name": name, "type": "n8n-nodes-base.formTrigger", "typeVersion": 2.2,
            "position": [0, 0], "webhookId": str(uuid.uuid4()),
            "parameters": {"path": path, "formTitle": title, "formDescription": desc,
                           "formFields": {"values": [{"fieldLabel": "file", "fieldType": "file",
                                                      "acceptFileTypes": ".xlsx", "requiredField": True,
                                                      "multipleFiles": False}]},
                           "options": {}}}

# ---------- Workflow A1：通訊錄 ----------

WF_A1 = {
    "name": "A1 知識庫-通訊錄全量重建",
    "settings": {"executionOrder": "v1"},
    "nodes": [
        form_trigger("上傳表單", "kb-contacts", "通訊錄知識庫更新", "上傳 資通處通訊錄(範例).xlsx，將全量重建 contacts 向量庫"),
        code_node("解析通訊錄", [220, 0], JS_PARSE_CONTACTS),
        http_node("刪除集合", [440, 0], {"method": "DELETE", "url": "http://qdrant:6333/collections/contacts", "options": {}},
                  execute_once=True, on_error="continueRegularOutput"),
        http_node("建立集合", [660, 0], {"method": "PUT", "url": "http://qdrant:6333/collections/contacts",
                  "sendBody": True, "specifyBody": "json",
                  "jsonBody": '{"vectors":{"size":1536,"distance":"Cosine"}}', "options": {}}, execute_once=True),
        code_node("分批", [880, 0], JS_BATCH_CONTACTS),
        embed_node("產生向量", [1100, 0]),
        code_node("組裝點", [1320, 0], js_build_points("分批")),
        http_node("寫入Qdrant", [1540, 0], {"method": "PUT", "url": "http://qdrant:6333/collections/contacts/points?wait=true",
                  "sendBody": True, "specifyBody": "json",
                  "jsonBody": "={{ JSON.stringify({points: $json.points}) }}", "options": {}}),
    ],
    "connections": chain("上傳表單", "解析通訊錄", "刪除集合", "建立集合", "分批", "產生向量", "組裝點", "寫入Qdrant"),
}

# ---------- Workflow A2：工作日誌 ----------

JS_CHECK_IDS_BODY = ("={{ JSON.stringify({ids: $('解析工作日誌').all().map(i => i.json.id), "
                     "with_payload: false, with_vector: false}) }}")

WF_A2 = {
    "name": "A2 知識庫-工作日誌增量",
    "settings": {"executionOrder": "v1"},
    "nodes": [
        form_trigger("上傳表單", "kb-events", "工作日誌知識庫更新", "上傳 資訊機房月工作日誌(範例).xlsx，只增量寫入新的異常通報/資安事件"),
        code_node("解析工作日誌", [220, 0], JS_PARSE_EVENTS),
        http_node("確保集合", [440, 0], {"method": "PUT", "url": "http://qdrant:6333/collections/events",
                  "sendBody": True, "specifyBody": "json",
                  "jsonBody": '{"vectors":{"size":1536,"distance":"Cosine"}}', "options": {}},
                  execute_once=True, on_error="continueRegularOutput"),
        http_node("查詢既有ID", [660, 0], {"method": "POST", "url": "http://qdrant:6333/collections/events/points",
                  "sendBody": True, "specifyBody": "json", "jsonBody": JS_CHECK_IDS_BODY, "options": {}},
                  execute_once=True),
        code_node("過濾新增並分批", [880, 0], JS_FILTER_NEW_EVENTS),
        {"name": "有新增?", "type": "n8n-nodes-base.if", "typeVersion": 2.2, "position": [1100, 0],
         "parameters": {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
                        "combinator": "and",
                        "conditions": [{"leftValue": "={{ $json.texts.length }}", "rightValue": 0,
                                        "operator": {"type": "number", "operation": "gt"}}]},
                        "options": {}}},
        embed_node("產生向量", [1320, -100]),
        code_node("組裝點", [1540, -100], js_build_points("過濾新增並分批")),
        http_node("寫入Qdrant", [1760, -100], {"method": "PUT", "url": "http://qdrant:6333/collections/events/points?wait=true",
                  "sendBody": True, "specifyBody": "json",
                  "jsonBody": "={{ JSON.stringify({points: $json.points}) }}", "options": {}}),
    ],
    "connections": {
        **chain("上傳表單", "解析工作日誌", "確保集合", "查詢既有ID", "過濾新增並分批", "有新增?"),
        "有新增?": {"main": [[{"node": "產生向量", "type": "main", "index": 0}], []]},
        **chain("產生向量", "組裝點", "寫入Qdrant"),
    },
}

# ---------- Workflow B：LINE Bot ----------

REPLY_BODY = ("={{ JSON.stringify({replyToken: $('解析事件').first().json.replyToken, "
              "messages: [{type: 'text', text: String($('AI Agent').first().json.output || '抱歉，目前查無相關資料').slice(0, 4900)}]}) }}")

WF_B = {
    "name": "B LINE Bot 通報查詢",
    "settings": {"executionOrder": "v1"},
    "nodes": [
        {"name": "LINE Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [0, 0],
         "webhookId": str(uuid.uuid4()),
         "parameters": {"httpMethod": "POST", "path": "line-bot", "responseMode": "responseNode", "options": {}}},
        code_node("解析事件", [200, 0], JS_PARSE_LINE),
        {"name": "是文字訊息?", "type": "n8n-nodes-base.if", "typeVersion": 2.2, "position": [400, 0],
         "parameters": {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
                        "combinator": "and",
                        "conditions": [
                            {"leftValue": "={{ $json.type }}", "rightValue": "message",
                             "operator": {"type": "string", "operation": "equals"}},
                            {"leftValue": "={{ $json.text }}", "rightValue": "",
                             "operator": {"type": "string", "operation": "notEmpty", "singleValue": True}},
                            {"leftValue": "={{ $json.replyToken }}", "rightValue": "",
                             "operator": {"type": "string", "operation": "notEmpty", "singleValue": True}}]},
                        "options": {}}},
        {"name": "回應200", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": [620, -100],
         "parameters": {"respondWith": "text", "responseBody": "OK", "options": {}}},
        {"name": "略過回應", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": [620, 150],
         "parameters": {"respondWith": "text", "responseBody": "OK", "options": {}}},
        {"name": "AI Agent", "type": "@n8n/n8n-nodes-langchain.agent", "typeVersion": 1.7, "position": [840, -100],
         "parameters": {"promptType": "define", "text": "={{ $('解析事件').first().json.text }}",
                        "options": {"systemMessage": SYSTEM_MSG}}},
        {"name": "Azure Chat Model", "type": "@n8n/n8n-nodes-langchain.lmChatAzureOpenAi", "typeVersion": 1,
         "position": [700, 120], "parameters": {"model": "gpt-4o-mini", "options": {"temperature": 0.3}},
         "credentials": {"azureOpenAiApi": CRED_AOAI}},
        {"name": "Embeddings Azure", "type": "@n8n/n8n-nodes-langchain.embeddingsAzureOpenAi", "typeVersion": 1,
         "position": [900, 320], "parameters": {"model": "text-embedding-3-small", "options": {}},
         "credentials": {"azureOpenAiApi": CRED_AOAI}},
        {"name": "Qdrant通訊錄", "type": "@n8n/n8n-nodes-langchain.vectorStoreQdrant", "typeVersion": 1.1,
         "position": [880, 120],
         "parameters": {"mode": "retrieve-as-tool", "toolName": "contacts_lookup",
                        "toolDescription": "查詢資通處通訊錄：輸入系統名稱、廠商名稱或人名，回傳負責人/廠商聯絡人的電話與分機。",
                        "qdrantCollection": {"__rl": True, "value": "contacts", "mode": "id"},
                        "topK": 4, "options": {}},
         "credentials": {"qdrantApi": CRED_QDRANT}},
        {"name": "Qdrant歷史事件", "type": "@n8n/n8n-nodes-langchain.vectorStoreQdrant", "typeVersion": 1.1,
         "position": [1060, 120],
         "parameters": {"mode": "retrieve-as-tool", "toolName": "history_lookup",
                        "toolDescription": "查詢歷史異常通報與資安事件記錄：輸入問題或系統描述，回傳過去類似事件的處理過程與結果。",
                        "qdrantCollection": {"__rl": True, "value": "events", "mode": "id"},
                        "topK": 5, "options": {}},
         "credentials": {"qdrantApi": CRED_QDRANT}},
        http_node("取得LINE Token", [1280, -100], {"method": "POST", "url": "https://api.line.me/oauth2/v3/token",
                  "sendBody": True, "contentType": "form-urlencoded",
                  "bodyParameters": {"parameters": [
                      {"name": "grant_type", "value": "client_credentials"},
                      {"name": "client_id", "value": LINE_ID},
                      {"name": "client_secret", "value": LINE_SECRET}]},
                  "options": {}}),
        http_node("回覆LINE", [1500, -100], {"method": "POST", "url": "https://api.line.me/v2/bot/message/reply",
                  "sendHeaders": True,
                  "headerParameters": {"parameters": [
                      {"name": "Authorization", "value": "=Bearer {{ $json.access_token }}"}]},
                  "sendBody": True, "specifyBody": "json", "jsonBody": REPLY_BODY, "options": {}}),
    ],
    "connections": {
        **chain("LINE Webhook", "解析事件", "是文字訊息?"),
        "是文字訊息?": {"main": [[{"node": "回應200", "type": "main", "index": 0}],
                                  [{"node": "略過回應", "type": "main", "index": 0}]]},
        **chain("回應200", "AI Agent", "取得LINE Token", "回覆LINE"),
        "Azure Chat Model": {"ai_languageModel": [[{"node": "AI Agent", "type": "ai_languageModel", "index": 0}]]},
        "Embeddings Azure": {"ai_embedding": [[{"node": "Qdrant通訊錄", "type": "ai_embedding", "index": 0},
                                                {"node": "Qdrant歷史事件", "type": "ai_embedding", "index": 0}]]},
        "Qdrant通訊錄": {"ai_tool": [[{"node": "AI Agent", "type": "ai_tool", "index": 0}]]},
        "Qdrant歷史事件": {"ai_tool": [[{"node": "AI Agent", "type": "ai_tool", "index": 0}]]},
    },
}


def api(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 headers={"X-N8N-API-KEY": API_KEY, "Content-Type": "application/json"})
    data = json.dumps(body).encode("utf-8") if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} {method} {path}: {e.read().decode('utf-8')[:800]}")
        return None


if __name__ == "__main__":
    for wf in (WF_A1, WF_A2, WF_B):
        existing = api("GET", "/api/v1/workflows?limit=100") or {"data": []}
        dup = [w for w in existing["data"] if w["name"] == wf["name"]]
        for d in dup:
            api("DELETE", f"/api/v1/workflows/{d['id']}")
            print(f"刪除舊版 {d['id']}")
        created = api("POST", "/api/v1/workflows", wf)
        if not created:
            print(f"FAILED create: {wf['name']}")
            continue
        wid = created["id"]
        act = api("POST", f"/api/v1/workflows/{wid}/activate", {})
        status = "active" if act and act.get("active") else "NOT ACTIVE"
        print(f"OK {wf['name']} id={wid} {status}")
