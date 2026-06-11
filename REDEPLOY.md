# 重建指南：在其他雲 / 其他 Azure 租戶重建本系統

> 用途：未來要在新環境重建時，把本檔 + 整個 repo 餵給 AI Agent（如 Claude Code），照下方範本下指令。

## 1. 給 Agent 的 Prompt 範本（直接複製修改 <> 內容）

```text
請讀取這個 repo，依照 DEPLOYMENT.md 在 <目標：Azure 新租戶 / AWS / GCP> 重建整套 LINE Bot 通報系統。

【授權】所有雲端操作由你透過 CLI 執行；需要登入時用 device code 流程，我會在瀏覽器完成。

【我的環境差異】
- 訂閱/帳號類型：<例：新個人帳號，有免費層資格>
- 偏好區域：<例：japaneast，不可用就近替代>
- 預算上限：<例：$10/月>

【決策授權】遇到資源受限（VM SKU 鎖區、模型不可用、配額不足）直接改用等價替代，
事後告知即可；但「額外費用 >$5/月」或「對外開放新端口」要先問我。

【機密】LINE Channel ID/Secret：<貼值>

【驗收標準（缺一不可）】
1. https://<新FQDN> 可開出 n8n 並完成 owner 設定（隨機帳密，記錄至 credentials.md）
2. 上傳通訊錄與工作日誌 xlsx（位置：<本機路徑>），Qdrant contacts/events 筆數正確
3. LINE webhook 驗證回 200；模擬訊息能產生「📞通報對象 + 📋歷史類似事件」兩段式回答
4. 重傳同一份工作日誌，events 筆數不變（驗證增量去重）
5. 更新 credentials.md，並記錄與 DEPLOYMENT.md 的差異
```

## 2. 必附的上下文（重要性排序）

| # | 檔案 | 作用 |
|---|---|---|
| 1 | `DEPLOYMENT.md` | 主劇本：逐步指令 + 9 條踩坑記錄（避免重踩） |
| 2 | `tools/build_workflows.py`、`tools/update_workflow_b.py` | 工作流即程式碼：n8n 全部邏輯、解析程式、系統提示詞 |
| 3 | `cloud-init.yml` | 容器層一鍵部署（Caddy/n8n/Qdrant） |
| 5 | 原始資料檔 | 通訊錄 xlsx、工作日誌 xlsx 的本機路徑 |

## 3. 跨雲對應表（換雲時需重新對應，其餘照搬）

| 元件 | Azure（現行） | AWS | GCP |
|---|---|---|---|
| LLM + Embedding | Azure OpenAI（gpt-4o-mini + text-embedding-3-small） | Bedrock（Claude Haiku + Titan Embed） | Vertex AI（Gemini Flash + text-embedding） |
| n8n 模型節點 | `lmChatAzureOpenAi` / `embeddingsAzureOpenAi` | `lmChatAwsBedrock` / `embeddingsAwsBedrock` | `lmChatGoogleVertex` / `embeddingsGoogleVertex` |
| n8n credential 類型 | `azureOpenAiApi` | `aws`（可用 IAM Role 免 Key） | `googleApi` |
| VM | Standard_B2pts_v2（ARM, ~$6/月） | EC2 t4g.small（ARM） | e2-small |
| 免費 DNS + 自動 HTTPS | `<label>.<region>.cloudapp.azure.com` + Caddy | ❌ 需 DuckDNS / 自有網域 / ALB+ACM | ❌ 需 DuckDNS / 自有網域 |
| 免 SSH 維運通道 | `az vm run-command` | SSM Run Command | `gcloud compute ssh` |
| 部署自動化 | `--custom-data` cloud-init | EC2 UserData（同格式） | startup-script |

注意事項：
- **向量維度**：換 embedding 模型時，Qdrant collection 維度（現為 1536）與 A1/A2 建集合的 jsonBody 要同步改
- **n8n 節點替換**：`update_workflow_b.py` 內的節點 type 與 credentials 區塊要對應換雲調整
- **LINE 端完全不變**：webhook 設定、stateless token、回覆格式與雲無關

## 4. 不變的核心（與雲無關，直接照搬）

Docker 三容器架構（Caddy 443 反代 / n8n / Qdrant localhost）、四條工作流邏輯、
Excel 解析程式（Code 節點）、增量去重機制（確定性 point ID）、
通訊錄全文注入 + 歷史事件向量檢索的混合策略、強制兩段式輸出提示詞、
LINE 接線流程（HTTPS 強制、5 秒內回 200、reply token 限時）。

## 5. 同 Azure 換租戶（最簡情境）

1. `az login --use-device-code` 登入新租戶帳號
2. 改資源名稱（全域唯一者：AOAI 資源名、DNS label）
3. DEPLOYMENT.md 原樣執行；注意新訂閱的 VM SKU 可用性與 AOAI 配額可能不同（踩坑 #2、#7）
4. LINE channel 可沿用（只要把 webhook endpoint 改指到新 FQDN）——舊環境會同時失效，屬切換而非並行
