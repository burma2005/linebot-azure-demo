# -*- coding: utf-8 -*-
"""改版 Workflow B：通訊錄全文進 system prompt（捨棄向量檢索），歷史事件保留 RAG topK=8"""
import json
import os
import urllib.request

BASE = "https://YOUR-LABEL.japaneast.cloudapp.azure.com"
API_KEY = open(os.path.expandvars(r"%TEMP%\n8n_key.txt"), encoding="ascii").read().strip()
WF_ID = "YOUR_WORKFLOW_B_ID"

CRED_AOAI = {"id": "YOUR_N8N_CRED_ID_AZURE_OPENAI", "name": "Azure OpenAI account"}
CRED_QDRANT = {"id": "YOUR_N8N_CRED_ID_QDRANT", "name": "Qdrant local"}
LINE_ID = os.environ["LINE_CHANNEL_ID"]
LINE_SECRET = os.environ["LINE_CHANNEL_SECRET"]

JS_PARSE_LINE = r'''
const b = $input.first().json.body || {};
const ev = (b.events || [])[0] || {};
return [{json: { type: ev.type || '', text: (ev.message && ev.message.text) || '', replyToken: ev.replyToken || '' }}];
'''

JS_JOIN_CONTACTS = r'''
const pts = ($input.first().json.result || {}).points || [];
const lines = pts.map(p => (p.payload || {}).content || '').filter(Boolean);
return [{json: {contactsText: lines.join('\n'), count: lines.length}}];
'''

SYSTEM_MSG = ("=你是資訊機房 OP 通報助理。\n"
              "以下是完整通訊錄（資通處員工與廠商，廠商括號內為其負責系統）：\n"
              "===通訊錄開始===\n"
              "{{ $('組通訊錄').first().json.contactsText }}\n"
              "===通訊錄結束===\n\n"
              "【強制規則】無論使用者怎麼問（通報、查詢、描述異常、問過去案例都一樣），"
              "你的回覆「必須」依序包含以下兩個段落，缺一不可：\n\n"
              "【📞 通報對象】\n"
              "從通訊錄挑出與問題最相關的聯絡人，依「負責系統」與問題的關聯判斷"
              "（例如官網問題找『雲嶺電信（負責系統：官網）』、看板問題找『星辰科技（負責系統：多媒體看板）』）。"
              "列出單位/廠商、姓名、電話、分機；同系統多位聯絡人都要列。"
              "若歷史事件記錄中提到實際聯絡過的廠商或人員，優先列出該聯絡人。"
              "找不到明確對應時列出最接近的選項並註明不確定。\n\n"
              "【📋 歷史類似事件】\n"
              "必須先呼叫 history_lookup 工具（用簡短關鍵字查詢，例如『官網』『門禁』『空調』），"
              "摘要 1~3 筆最相關事件的處理過程與結果。\n\n"
              "任一段落查無資料時，該段落要寫「查無相關資料」，不可整段省略。"
              "全程使用繁體中文、條列簡潔、適合 LINE 閱讀。")

REPLY_BODY = ("={{ JSON.stringify({replyToken: $('解析事件').first().json.replyToken, "
              "messages: [{type: 'text', text: String($('AI Agent').first().json.output || '抱歉，目前查無相關資料').slice(0, 4900)}]}) }}")


def if_msg():
    return {"name": "是文字訊息?", "type": "n8n-nodes-base.if", "typeVersion": 2.2, "position": [400, 0],
            "parameters": {"conditions": {"options": {"caseSensitive": True, "leftValue": "",
                                                      "typeValidation": "loose", "version": 2},
                           "combinator": "and",
                           "conditions": [
                               {"leftValue": "={{ $json.type }}", "rightValue": "message",
                                "operator": {"type": "string", "operation": "equals"}},
                               {"leftValue": "={{ $json.text }}", "rightValue": "",
                                "operator": {"type": "string", "operation": "notEmpty", "singleValue": True}},
                               {"leftValue": "={{ $json.replyToken }}", "rightValue": "",
                                "operator": {"type": "string", "operation": "notEmpty", "singleValue": True}}]},
                           "options": {}}}


def api(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 headers={"X-N8N-API-KEY": API_KEY, "Content-Type": "application/json"})
    data = json.dumps(body).encode("utf-8") if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} {method} {path}: {e.read().decode('utf-8')[:600]}")
        return None


cur = api("GET", f"/api/v1/workflows/{WF_ID}")
webhook_id = next(n.get("webhookId") for n in cur["nodes"] if n["type"] == "n8n-nodes-base.webhook")

WF_B = {
    "name": "B LINE Bot 通報查詢",
    "settings": {"executionOrder": "v1"},
    "nodes": [
        {"name": "LINE Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [0, 0],
         "webhookId": webhook_id,
         "parameters": {"httpMethod": "POST", "path": "line-bot", "responseMode": "responseNode", "options": {}}},
        {"name": "解析事件", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [200, 0],
         "parameters": {"jsCode": JS_PARSE_LINE}},
        if_msg(),
        {"name": "回應200", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": [620, -100],
         "parameters": {"respondWith": "text", "responseBody": "OK", "options": {}}},
        {"name": "略過回應", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": [620, 150],
         "parameters": {"respondWith": "text", "responseBody": "OK", "options": {}}},
        {"name": "撈通訊錄", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [840, -100],
         "parameters": {"method": "POST", "url": "http://qdrant:6333/collections/contacts/points/scroll",
                        "sendBody": True, "specifyBody": "json",
                        "jsonBody": '{"limit": 500, "with_payload": true, "with_vector": false}',
                        "options": {}}},
        {"name": "組通訊錄", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [1060, -100],
         "parameters": {"jsCode": JS_JOIN_CONTACTS}},
        {"name": "AI Agent", "type": "@n8n/n8n-nodes-langchain.agent", "typeVersion": 1.7, "position": [1280, -100],
         "parameters": {"promptType": "define", "text": "={{ $('解析事件').first().json.text }}",
                        "options": {"systemMessage": SYSTEM_MSG}}},
        {"name": "Azure Chat Model", "type": "@n8n/n8n-nodes-langchain.lmChatAzureOpenAi", "typeVersion": 1,
         "position": [1180, 120], "parameters": {"model": "gpt-4o-mini", "options": {"temperature": 0.3}},
         "credentials": {"azureOpenAiApi": CRED_AOAI}},
        {"name": "Embeddings Azure", "type": "@n8n/n8n-nodes-langchain.embeddingsAzureOpenAi", "typeVersion": 1,
         "position": [1380, 320], "parameters": {"model": "text-embedding-3-small", "options": {}},
         "credentials": {"azureOpenAiApi": CRED_AOAI}},
        {"name": "Qdrant歷史事件", "type": "@n8n/n8n-nodes-langchain.vectorStoreQdrant", "typeVersion": 1.1,
         "position": [1460, 120],
         "parameters": {"mode": "retrieve-as-tool", "toolName": "history_lookup",
                        "toolDescription": "查詢歷史異常通報與資安事件記錄：輸入簡短關鍵字（如系統名稱），回傳過去類似事件的處理過程與結果。",
                        "qdrantCollection": {"__rl": True, "value": "events", "mode": "id"},
                        "topK": 8, "options": {}},
         "credentials": {"qdrantApi": CRED_QDRANT}},
        {"name": "取得LINE Token", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1700, -100],
         "parameters": {"method": "POST", "url": "https://api.line.me/oauth2/v3/token",
                        "sendBody": True, "contentType": "form-urlencoded",
                        "bodyParameters": {"parameters": [
                            {"name": "grant_type", "value": "client_credentials"},
                            {"name": "client_id", "value": LINE_ID},
                            {"name": "client_secret", "value": LINE_SECRET}]},
                        "options": {}}},
        {"name": "回覆LINE", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1920, -100],
         "parameters": {"method": "POST", "url": "https://api.line.me/v2/bot/message/reply",
                        "sendHeaders": True,
                        "headerParameters": {"parameters": [
                            {"name": "Authorization", "value": "=Bearer {{ $json.access_token }}"}]},
                        "sendBody": True, "specifyBody": "json", "jsonBody": REPLY_BODY, "options": {}}},
    ],
    "connections": {
        "LINE Webhook": {"main": [[{"node": "解析事件", "type": "main", "index": 0}]]},
        "解析事件": {"main": [[{"node": "是文字訊息?", "type": "main", "index": 0}]]},
        "是文字訊息?": {"main": [[{"node": "回應200", "type": "main", "index": 0}],
                                  [{"node": "略過回應", "type": "main", "index": 0}]]},
        "回應200": {"main": [[{"node": "撈通訊錄", "type": "main", "index": 0}]]},
        "撈通訊錄": {"main": [[{"node": "組通訊錄", "type": "main", "index": 0}]]},
        "組通訊錄": {"main": [[{"node": "AI Agent", "type": "main", "index": 0}]]},
        "AI Agent": {"main": [[{"node": "取得LINE Token", "type": "main", "index": 0}]]},
        "取得LINE Token": {"main": [[{"node": "回覆LINE", "type": "main", "index": 0}]]},
        "Azure Chat Model": {"ai_languageModel": [[{"node": "AI Agent", "type": "ai_languageModel", "index": 0}]]},
        "Embeddings Azure": {"ai_embedding": [[{"node": "Qdrant歷史事件", "type": "ai_embedding", "index": 0}]]},
        "Qdrant歷史事件": {"ai_tool": [[{"node": "AI Agent", "type": "ai_tool", "index": 0}]]},
    },
}

updated = api("PUT", f"/api/v1/workflows/{WF_ID}", WF_B)
if updated:
    print(f"updated, active={updated.get('active')}")
    if not updated.get("active"):
        act = api("POST", f"/api/v1/workflows/{WF_ID}/activate", {})
        print(f"activated={bool(act and act.get('active'))}")
