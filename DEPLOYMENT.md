# 建置流程（從零到上線）

> 本文記錄整套系統的完整建置步驟與踩坑記錄。所有 `<佔位符>` 請替換為自己的值。

## 0. 前置作業

| 項目 | 動作 |
|---|---|
| Azure CLI | `winget install Microsoft.AzureCLI` → `az login --use-device-code` |
| LINE | [LINE Developers Console](https://developers.line.biz/) 建立 Provider + Messaging API Channel，記下 **Channel ID / Channel Secret**（不需要 long-lived token，見步驟 5） |

## 1. Azure 資源

```bash
# 資源群組
az group create -n rg-op-bot -l japaneast

# Azure OpenAI（模型可用性以 eastus 最齊）
az cognitiveservices account create -n <AOAI資源名> -g rg-op-bot -l eastus --kind OpenAI --sku S0 --custom-domain <AOAI資源名>
az cognitiveservices account deployment create -n <AOAI資源名> -g rg-op-bot \
  --deployment-name gpt-4o-mini --model-name gpt-4o-mini --model-version 2024-07-18 \
  --model-format OpenAI --sku-name GlobalStandard --sku-capacity 200      # ⚠ 至少 100，見踩坑#7
az cognitiveservices account deployment create -n <AOAI資源名> -g rg-op-bot \
  --deployment-name text-embedding-3-small --model-name text-embedding-3-small \
  --model-version 1 --model-format OpenAI --sku-name Standard --sku-capacity 50

# VM（先確認 SKU 可用性，見踩坑#2）
az vm list-skus -l japaneast --size Standard_B --all -o table
az vm create -g rg-op-bot -n vm-op-bot \
  --image Canonical:0001-com-ubuntu-server-jammy:22_04-lts-arm64:latest \
  --size Standard_B2pts_v2 --zone 2 --admin-username azureuser \
  --ssh-key-values ~/.ssh/id_rsa.pub \
  --public-ip-address-dns-name <你的label> \
  --custom-data cloud-init.yml

# NSG：僅開 443/80，**不開 22**（維運全走 az vm run-command，零管理端口暴露）
az network nsg rule create -g rg-op-bot --nsg-name vm-op-botNSG -n allow-web \
  --priority 1010 --protocol Tcp --access Allow --direction Inbound --destination-port-ranges 80 443
az network nsg rule delete -g rg-op-bot --nsg-name vm-op-botNSG -n default-allow-ssh
```

## 2. 容器層（cloud-init 開機自動完成）

[cloud-init.yml](cloud-init.yml) 會自動：建 2GB swap（1GB RAM 機型必須）→ 裝 Docker → 啟動三容器：

- **Caddy**（443/80，唯一對外）：自動簽 Let's Encrypt 憑證，反代 n8n —— LINE Webhook 強制 HTTPS，Azure VM 免費 DNS `<label>.<region>.cloudapp.azure.com` 正好可簽憑證
- **n8n**（僅容器網路）：環境變數需含 `WEBHOOK_URL`/`N8N_HOST`/`N8N_PROTOCOL=https`、`NODE_FUNCTION_ALLOW_EXTERNAL=xlsx`、`NODE_FUNCTION_ALLOW_BUILTIN=crypto`（Code 節點解析 Excel 與算雜湊用）
- **Qdrant**（僅 127.0.0.1:6333）：向量資料庫

驗證：`az vm run-command invoke ... --scripts "docker ps"`，瀏覽器開 `https://<FQDN>` 看到 n8n。

## 3. n8n 初始化（全程 API，免點 UI）

```text
1. 開 https://<FQDN> 完成 owner 註冊（⚠ 誰先開誰是 owner，部署完盡快做）
   或 POST /rest/owner/setup {email, firstName, lastName, password}
2. POST /rest/login {emailOrLdapLoginId, password} → 取得 cookie
3. POST /rest/api-keys {label, expiresAt:null, scopes:[...]} → API key（後續走 /api/v1）
4. 建三個 credentials（走內部 /rest/credentials，公開 API 對 data schema 驗證過嚴，見踩坑#5）：
   - azureOpenAiApi：{apiKey, resourceName, apiVersion: 2024-08-01-preview}
   - qdrantApi：{apiKey: "", qdrantUrl: "http://qdrant:6333"}（容器網路名稱）
   - httpHeaderAuth：{name: "api-key", value: <AOAI key>}（給 HTTP 節點呼叫 embeddings 用）
```

## 4. 建立 Workflows

```bash
python tools/build_workflows.py      # A1 通訊錄全量重建、A2 工作日誌增量、B LINE Bot
python tools/update_workflow_b.py    # B 現行版（見下方說明）
```

- **A1**：表單上傳 xlsx → Code 解析（取檔內最新 `通訊錄YYYYMMDD` 工作表，雙欄排版+科別+廠商區）→ 重建 collection → 批次 embedding → 寫入 Qdrant
- **A2**：表單上傳 → 解析各月份工作表（表頭位置/欄位順序逐月浮動，以表頭文字定位）→ 過濾異常通報/資安事件 → 內容雜湊產生**確定性 point ID** → 先查既有 ID、只 embedding 新增（增量冪等）
- **B**：Webhook → 先回 200（LINE 5 秒限制）→ 撈整份通訊錄注入 system prompt（**小表全文注入**，見踩坑#8）→ AI Agent（gpt-4o-mini + events 向量檢索工具 topK=8）→ 簽發 stateless token → Reply
- ⚠ formTrigger 的 `path` 參數實際不生效，表單註冊在 `/form/<webhookId>`（踩坑#6）；表單上傳欄位名固定 `field-0`

## 5. LINE 接線

```text
1. 簽發 stateless token（效期15分，免存 long-lived token）：
   POST https://api.line.me/oauth2/v3/token  grant_type=client_credentials&client_id=<ID>&client_secret=<SECRET>
2. 設定 Webhook：PUT https://api.line.me/v2/bot/channel/webhook/endpoint {"endpoint":"https://<FQDN>/webhook/line-bot"}
3. 驗證：POST https://api.line.me/v2/bot/channel/webhook/test → success=true
4. LINE Official Account Manager → 回應設定：Webhook 開啟、自動回應訊息關閉（否則罐頭訊息搶答）
5. Messaging API 分頁開「Allow bot to join group chats」，手機邀 Bot 入群
```

## 6. 知識庫建立／更新

開啟 `上傳知識庫.html` → 左右區塊分別選通訊錄／工作日誌 xlsx 送出 → 30~60 秒完成。
檔案直接進 n8n 解析，不落地雲端儲存體；工作日誌每月整份重傳即可（只會處理新事件）。

## 7. 踩坑全記錄

| # | 症狀 | 原因 | 解法 |
|---|---|---|---|
| 1 | `az vm create --generate-ssh-keys` 報 `Incorrect padding` | CLI bug：無現成金鑰時驗證邏輯對路徑字串做 base64 解碼 | 先 `ssh-keygen` 再用 `--ssh-key-values` |
| 2 | B1s 等 x86 B 系列 `SkuNotAvailable` | 訂閱對區域的容量限制 | `az vm list-skus` 查可用 SKU，改 ARM `B2pts_v2`（更便宜，容器全有 arm64 映像） |
| 3 | NSG 限自家 IP 後連不上 | CGNAT 出口 IP 不固定且依目的地換池 | 乾脆不開 22，維運走 `az vm run-command` |
| 4 | LINE Webhook 驗證失敗 | LINE 強制 HTTPS + 有效憑證 | Caddy + VM 免費 DNS 自動簽 Let's Encrypt |
| 5 | 公開 API 建 credential 一直 400 | `/api/v1/credentials` 對 data 做嚴格 schema 驗證 | 改走內部 `/rest/credentials`（cookie 認證） |
| 6 | `/form/<path>` 404 | formTrigger 實際以 webhookId 註冊 | 查 DB 或用 `/form/<webhookId>` |
| 7 | Bot 偶爾不回覆，log 出現 429 | Agent 多輪呼叫 × 全文 prompt 超過 10K TPM 配額 | deployment capacity 調到 200（TPM 是速率上限，不另計費） |
| 8 | 通報對象答錯人 | 短文本（人名+電話）做向量檢索區分度極低，正確答案排不進 top-10 | **小而結構化的表直接全文注入 prompt**，向量檢索留給大量長文本（歷史事件） |
| 9 | 某些問法只回歷史不回通報人 | prompt 只「建議」步驟，LLM 自由發揮 | 強制兩段式輸出格式（缺一不可、查無資料須明寫） |

## 8. 成本

| 項目 | 月費（USD） |
|---|---|
| VM B2pts_v2（ARM 2vCPU/1GB） | ~6 |
| gpt-4o-mini（百次查詢，含全文 prompt） | ~0.3 |
| text-embedding-3-small | ~0.01 |
| LINE Messaging API（Reply） | 0 |
| **合計** | **~$6.3/月** |
