#作用：从知识库检索职业晋升相关知识
from typing import Literal
from langchain_core.tools import tool

@tool(parse_docstring=True)
def search_knowledge(query:str,kb_type: Literal["项目管理", "技术管理"])->str:
    """检索职业发展、转岗、晋升相关的知识库。用户询问这类问题时使用。

    Args:
        query: 查询的语句。
        kb_type:知识库类型，如：项目管理、技术管理等
    """
    if kb_type=="项目管理":
        return "项目管理需要进行PMP、工信部软件项目经理认证，同时已经承当相关工作一年以上。"
    elif kb_type =="技术管理":
        return "技术管理需要通过公司内部的技术路线考核、团队中承担技术负责人角色。"    
    else:
        return "未命中任何知识库，你来根据用户的问题自行回答。"