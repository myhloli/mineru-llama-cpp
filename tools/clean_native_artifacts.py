"""移除 editable 安装留下的扩展，避免混入另一种 Python ABI。"""
from pathlib import Path


def main() -> None:
    """仅清理源码包中的原生扩展，不触碰构建和候选产物目录。"""
    package = Path(__file__).resolve().parents[1] / "src/mineru_llama_cpp"
    for pattern in ("_mineru_llama_cpp*.so", "_mineru_llama_cpp*.pyd"):
        for path in package.glob(pattern):
            path.unlink()


if __name__ == "__main__":
    main()
