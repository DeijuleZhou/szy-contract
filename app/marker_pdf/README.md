# marker-pdf 示例（位于 app/examples）

这个目录包含一个可复用的示例脚本 `marker_example.py`，演示如何使用 `marker-pdf` 把 PDF 转为 Markdown，并在需要时调用 LLM 提升质量。

快速开始

1. 安装依赖（推荐虚拟环境）：

```bash
pip install -r requirements.txt
```

2. 设置 LLM API Key（若使用 LLM）：

```bash
# 以 OpenAI 为例
export OPENAI_API_KEY="sk-..."
# Windows PowerShell:
$env:OPENAI_API_KEY='sk-...'
```

3. 运行示例：

```bash
python -m app.examples.marker_example examples/sample.pdf out/
```

输出：脚本会在 `out/` 下生成 `sample.md`。

说明

- `marker_example.py` 使用 `ConfigParser` 以配置方式构建转换器，并通过 `PdfConverter` 执行转换。
- 若要改用其他 LLM 服务（Gemini、Vertex、Ollama 等），参考 `marker-pdf` 的文档修改配置键。

注意事项

- `marker-pdf` 需要 PyTorch 等较重的依赖，若在没有 GPU 的机器上运行，请确保有足够的系统资源。
- 若只需要纯文本提取，可将 `use_llm` 设为 `False`，这可以避免额外的 API 请求和费用。