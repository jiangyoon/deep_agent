# display/stream_runner.py
# 作用：用流式方式向 Agent 提一个问题，并把整个过程打印出来。
# 阶段 2 的流式入口和阶段 3 的入口都要用它，所以单独放在这里，
# 避免"一个入口文件去导入另一个入口文件"。

from display.stream_handler import StreamPrinter


def ask_stream(agent, question, config=None):
    """用流式方式向 Agent 提一个问题。

    参数 config：可选。阶段 6 起用它带上会话编号（由 core/session_store.py 的 make_config 生成），
                 不传就是一次独立的提问。
    """
    print("=" * 60)
    print("问题:", question)
    print("=" * 60)

    printer = StreamPrinter()

    # stream_mode 传一个列表，表示同时开启两种模式：
    #   "updates"  每完成一个步骤吐一次
    #   "messages" 模型生成的每个小片段吐一次
    # for 循环每转一圈，就拿到一个新片段，片段是 (模式名, 数据)
    chunks = agent.stream(
        {"messages": [{"role": "user", "content": question}]},
        stream_mode=["updates", "messages"],
        config=config,
    )

    for chunk in chunks:
        mode = chunk[0]
        payload = chunk[1]
        printer.handle(mode, payload)

    printer.finish()
    print("")
