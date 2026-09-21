
建议使用该稳定入口，而不是直接填写 Python 模块路径；这样以后调整内部包结构时，启动命令无需变化。生产环境去掉 `--reload`。
uv run knowledge-rag serve --host 127.0.0.1 --port 8080 --reload
