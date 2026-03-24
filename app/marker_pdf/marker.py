"""marker-pdf 使用示例（支持 LLM）

可复用脚本：给定输入 PDF 路径，使用 marker-pdf 将文档转换为 markdown 并保存到输出目录。
示例默认使用 OpenAI 作为 LLM（需设置环境变量 `OPENAI_API_KEY`）。

用法:
    python -m app.examples.marker_example /path/to/input.pdf /path/to/outdir

注意：在运行前请先安装依赖：
    pip install -r requirements.txt
如果要在本机运行 marker 模型，需满足 `marker-pdf` 的 PyTorch 等依赖。
"""

import sys
import os
from pathlib import Path
from marker.config.parser import ConfigParser
from marker.models import create_model_dict

os.environ["SURYA_MODEL_DIR"] = str(Path(__file__).parent.parent.parent / "models")


def parse(input_path: str, output_dir: str, use_llm: bool = False):
    """
    解析 PDF 为 markdown，强制使用本地 models 目录，支持 LLM。
    :param input_path: 输入 PDF 路径
    :param output_dir: 输出 markdown 目录
    :param use_llm: 是否启用 LLM
    """
    import torch
    from marker.config.parser import ConfigParser
    from marker.models import create_model_dict
    from marker.output import text_from_rendered
    from marker.converters.pdf import PdfConverter

    # 设备选择
    def get_available_device():
        try:
            if torch.cuda.is_available():
                test_tensor = torch.tensor([1.0], device="cuda")
                test_tensor = test_tensor + 1
                print("当前使用设备: GPU")
                return "cuda"
            else:
                print("当前使用设备: CPU (CUDA不可用)")
                return "cpu"
        except Exception as e:
            print(f"GPU初始化失败，回退到CPU: {str(e)}")
            return "cpu"

    # 强制模型路径
    model_dir = str(Path(__file__).parent.parent.parent / "models")
    os.environ["SURYA_MODEL_DIR"] = model_dir
    os.environ["MARKER_CACHE_DIR"] = model_dir

    device = get_available_device()
    models = create_model_dict(device=device)

    cfg = {
        "output_format": "markdown",
        "use_llm": use_llm,
        "model_dir": model_dir,
        "fpath": input_path,
    }

    config_parser = ConfigParser(cfg)
    converter_cls = config_parser.get_converter_cls()
    converter = converter_cls(
        config=config_parser.generate_config_dict(),
        artifact_dict=models,
        processor_list=config_parser.get_processors(),
        renderer=config_parser.get_renderer(),
        llm_service=config_parser.get_llm_service(),
    )
    rendered = converter(input_path)
    # 提取 markdown 内容
    try:
        text = rendered.markdown
    except AttributeError:
        # 兼容不同输出类型
        text, _, _ = text_from_rendered(rendered)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_md = out_dir / (Path(input_path).stem + ".md")
    out_md.write_text(text, encoding="utf-8")
    print(f"Saved markdown to {out_md}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m app.marker_pdf.marker_example input.pdf output_dir")
        sys.exit(1)
    input_path = sys.argv[1]
    output_dir = sys.argv[2]
    parse(input_path, output_dir)

# def get_available_device():
#     """获取可用的设备，GPU不可用时回退到CPU"""
#     import torch
#     try:
#         if torch.cuda.is_available():
#             # 尝试创建一个小张量来测试GPU是否真的可用
#             test_tensor = torch.tensor([1.0], device="cuda")
#             test_tensor = test_tensor + 1
#             print("当前使用设备: GPU")
#             return "cuda"
#         else:
#             print("当前使用设备: CPU (CUDA不可用)")
#             return "cpu"
#     except Exception as e:
#         print(f"GPU初始化失败，回退到CPU: {str(e)}")
#         return "cpu"

# def parse(file_path):
#     device = get_available_device()
    
#     # 使用marker-pdf API解析PDF并转换为Markdown
#     models = create_model_dict(device=device)
#     config_parser = ConfigParser({
#         "fpath": file_path,
#         "output_format": "markdown",
#         "model_dir": os.environ["MARKER_CACHE_DIR"]
#     })
    
#     converter_cls = config_parser.get_converter_cls()
#     converter = converter_cls(
#         config=config_parser.generate_config_dict(),
#         artifact_dict=models,
#         processor_list=config_parser.get_processors(),
#         renderer=config_parser.get_renderer(),
#         llm_service=config_parser.get_llm_service(),
#     )
    
#     rendered = converter(file_path)
#     # 提取markdown内容字符串，而不是直接使用MarkdownOutput对象
#     content = rendered.markdown