"""手动创建 Web 用户：python -m scripts.manage_users create 用户名。"""

import argparse
import getpass

from career_agent.core.user_store import UserStore


def main():
    parser = argparse.ArgumentParser(description="创建职业规划助手 Web 用户")
    parser.add_argument("action", choices=["create"])
    parser.add_argument("username", help="1–64 位字母、数字、下划线或连字符，区分大小写")
    args = parser.parse_args()
    try:
        password = getpass.getpass("密码（8–128 位，输入不回显）：")
        confirmation = getpass.getpass("再次输入密码：")
        if password != confirmation:
            raise ValueError("两次密码不一致")
        user = UserStore().create_user(args.username, password)
    except (ValueError, EOFError, KeyboardInterrupt) as error:
        parser.exit(1, f"创建失败：{error}\n")
    print(f"已创建用户：{user['username']}")


if __name__ == "__main__":
    main()
