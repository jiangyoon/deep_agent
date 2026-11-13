# core/thinking_mode.py
# 作用：把 config.py 里的 THINKING_MODE 设置，翻译成"发给模型服务的额外请求参数"。
# 单独放一个文件，是为了让"思考模式怎么翻译"这套规则集中在一处，llm_model.py 直接调用它。

# 三种模式（THINKING_MODE 只能取这三个值之一，写错会在 get_extra_body 里直接报错）
#   default：不干预，用模型自己的默认行为（Qwen3.8 默认开启思考，强度最高）
#   low    ：保留思考，但把强度调到最低档，思考更短、更快
#   off    ：完全关闭思考，直接给回答

def get_extra_body(mode):
    """根据模式名，返回要额外发给服务端的参数（字典）。

    返回空字典，表示不额外发送任何东西。
    注意 off 模式：enable_thinking 必须放在 chat_template_kwargs 里面，
    直接放在最外层会被服务端悄悄忽略，而且不会报错。
    """
    if mode == "default":
        return {}

    if mode == "low":
        return {"reasoning_effort": "low"}

    if mode == "off":
        return {"chat_template_kwargs": {"enable_thinking": False}}

    # 写错模式名时，立刻报错，避免"以为关了其实没关"
    raise ValueError(
        "THINKING_MODE 只能是 default、low、off 之一，现在是: " + str(mode)
    )
