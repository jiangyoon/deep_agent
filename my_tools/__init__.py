# my_tools/__init__.py
# 作用：把所有自定义工具收集成一个列表 ALL_TOOLS。
# 以后新增工具，只需要两步：
#   1. 在本文件夹里新建一个工具文件
#   2. 在下面 import 它，并加进 ALL_TOOLS 列表

from my_tools.time_tool import get_current_time
from my_tools.math_tool import divide
from my_tools.knowledge_tool import search_knowledge

ALL_TOOLS = [
    get_current_time,
    divide,
    search_knowledge
]
