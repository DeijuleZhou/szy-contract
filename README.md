# Service A PoC

轻量 PoC：FastAPI + 异步 worker + SQLite（文件存储）

快速运行：

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.api:app --reload --host 0.0.0.0 --port 8000
# 在另一个控制台手动触发（或通过 API）
# curl -X POST "http://127.0.0.1:8000/start?limit=50"
```

说明：外部接口在 `app/services_external.py` 中实现真实 HTTP 调用，URL 与参数可通过环境变量配置：

- `MARKET_BASE_URL`：市场接口基础地址（例如 https://market.example.com）
- `MARKET_CONTRACTS_PATH`：获取合同列表的相对路径（默认 /Market/Basic/ManageContract/GetContractPerformenceList）
- `FILE_BASE_URL`：文件服务基础地址（例如 https://files.example.com）
- `FILE_GET_PATH`：获取文件链接的相对路径（默认 /ViewFile/GetFileUrl）
- `UPLOAD_BASE_URL`：解析服务的上传 API 基础地址（例如 https://uploadservice.example.com）
- `UPLOAD_PATH`：上传路径（默认 /file/upload）
- `WORKFLOW_BASE_URL`：解析/工作流服务基础地址（例如 https://parseservice.example.com）
- `WORKFLOW_RUN_PATH`：触发解析的相对路径（默认 /workflows/run）
- `EXTERNAL_API_KEY`：可选，传入解析/文件服务的 Bearer token
- `HTTP_TIMEOUT`：HTTP 请求超时（秒）

示例（Windows PowerShell）：

```powershell
$env:MARKET_BASE_URL = 'https://market.example.com'
$env:FILE_BASE_URL = 'https://files.example.com'
$env:UPLOAD_BASE_URL = 'https://uploadservice.example.com'
$env:WORKFLOW_BASE_URL = 'https://parseservice.example.com'
$env:EXTERNAL_API_KEY = 'your_api_key'
uvicorn app.api:app --reload --host 0.0.0.0 --port 8000
```

如果解析/上传服务需要 `Authorization`，可通过 `EXTERNAL_API_KEY` 环境变量传入。

日志与级别
- `LOG_LEVEL`：日志级别（DEBUG/INFO/WARNING/ERROR），可通过环境变量覆盖（默认 INFO）。
- `LOG_PATH`：日志文件路径，默认 `logs/serviceA.log`。

示例（将日志写入文件并把级别设为 DEBUG）：

```powershell
$env:LOG_LEVEL = 'DEBUG'
$env:LOG_PATH = 'logs/serviceA.log'
uvicorn app.api:app --reload --host 0.0.0.0 --port 8000
```

日志会同时输出到控制台和 `LOG_PATH` 指定的文件（采用滚动文件，默认 10MB/5 份备份）。


## 接口说明
1. 获取合同信息
接口地址 http://{host}:{port}/GetContractPerformenceList
请求方式 POST
入参字段
{
    "queryFormData": {
        "$EQ$ContractType": // [string]筛选大类（设计、施工、总承包、勘察）
        "$EQ$SignYear": // [string/int]筛选年份（2023、2024、2025、2026）
    }
}
必填项: 无，无查询条件则输出全部
返回参数
[
    {
        "code": 200,
        "msg": "查询成功",
        "data": {
            "Name": "武汉市轨道交通8号线二期工程洪山区政府院内苖木移栽回迁工程",
            "Code": "2021152",
            "ContractType": "SJ",
            "ContractRMBAmount": 123000.000000,
            "SignDate": "2021-03-31T16:26:07",
            "ContractAttachment": "3063146_2021152.pdf_6452018", // 附件名称，用于“获取合同附件”步骤的请求
            "Attachment": "",
            "EngineeringOverview": ""
        }
    },
    {}
]
异常说明
{
    "code": 错误码,
    "msg": "错误信息",
    "data": {}
}
2. 获取合同附件
接口地址 http://{host}:{port}/GetFileUrl
请求方式 POST
入参字段
["8037_IC1.pdf_71406","8038_IC2.pdf_72870"]
//(Array[string]，合同附件名称的数组)
返回参数
{
    "Code": "200",
    "Msg": "获取成功"
    "Data": [
        {
            "8037_IC1.pdf_71406": {
                "DownlUrl": "http:",
                "PreviewUrl": "http:"
            }
        },
        {
            "8038_IC2.pdf_72870": {
                "DownlUrl": "http:",
                "PreviewUrl": "http:"
            }
        }
    ]
}
异常说明
{
    "code": 错误码,
    "msg": "错误信息",
    "data": {}
}

3. 解析文件内容（服务B + AI抽取合并）

流程说明：先把文件上传到解析服务（`/file/upload`）得到 `upload_file_id`，再调用 `POST /workflows/run` 触发解析与 AI 抽取。解析服务应返回解析文本以及可选的结构化字段（例如 `entities`、`summary` 或自定义 `outputs`），本服务会把 `upload_file_id`、解析文本与结构化结果存入 `contracts` 表中的 `file_upload_id`、`parse_text`、`ai_result`。

示例：

I. 上传文件，获取 `upload_file_id`

```bash
curl -X POST 'http://{host}:{port}/file/upload' \
    --header 'Authorization: Bearer {api_key}' \
    --form 'files=@"/path/to/file1.pdf"'
```

示例响应（数组或对象）:

```json
[{
    "id": "ddba2e51",
    "name": "file1.pdf",
    "url": "https://.../file1.pdf"
}]
```

II. 调用 workflow 触发解析与 AI 抽取

```bash
curl -X POST 'http://{host}:{port}/workflows/run' \
    --header 'Authorization: Bearer {api_key}' \
    --header 'Content-Type: application/json' \
    --data-raw '{
        "inputs": {
            "my_files": [
                {"type":"pdf","transfer_method":"local_file","url":"","upload_file_id":"ddba2e51"}
            ]
        },
    }'
```

示例 workflow 返回（简化）：

```json
{
    "outputs": {
        "text": "解析后的全文文本...",
        "entities": [{"type":"Party","value":"甲方"}],
        "summary": "合同要点摘要..."
    }
}
```

本服务会把 `outputs.text` 保存到 `parse_text`，把 `outputs` 中的非字符串字段（如 `entities`、`summary`）保存到 `ai_result`，并把上传返回的 `id` 保存到 `file_upload_id`。

注意：如果你的解析服务返回结构与上例不同，请告知，我会把解析逻辑调整为对应字段。
