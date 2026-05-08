from pathlib import Path
import argparse
import json

from playwright.sync_api import sync_playwright


DEFAULT_SAVE_PATH = Path(__file__).with_name("douyin_cookies.json")
DEFAULT_STATE_PATH = Path(__file__).with_name("douyin_state.json")
DEFAULT_USER_DATA_DIR = Path(__file__).with_name("douyin_profile")


def get_and_save_cookies(
    save_path=DEFAULT_SAVE_PATH,
    state_path=DEFAULT_STATE_PATH,
    user_data_dir=DEFAULT_USER_DATA_DIR,
):
    save_path = Path(save_path)
    state_path = Path(state_path)
    user_data_dir = Path(user_data_dir)

    with sync_playwright() as p:
        print("正在启动 Playwright 浏览器...")

        context = p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=False,
        )
        page = context.new_page()

        try:
            page.goto("https://www.douyin.com")
            print(f"当前浏览器 UA: {page.evaluate('navigator.userAgent')}")
            print(f"当前浏览器 platform: {page.evaluate('navigator.platform')}")

            print("\n" + "=" * 50)
            print("浏览器已打开。请在弹出的窗口中手动登录 Douyin。")
            print("请在完全登录成功（能看到自己的账号状态）后，再继续。")
            print("=" * 50 + "\n")

            input(">>> 登录完成后，请在此处按下 Enter 键以保存 Cookie...")

            cookies = context.cookies()
            save_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.parent.mkdir(parents=True, exist_ok=True)

            with save_path.open("w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=4)

            context.storage_state(path=str(state_path))

            print(f"\n成功！Cookie 已提取并保存至: {save_path}")
            print(f"完整浏览器状态已保存至: {state_path}")
            print(f"持久化浏览器目录: {user_data_dir}")

        except Exception as e:
            print(f"\n运行过程中出现错误: {e}")

        finally:
            print("正在关闭浏览器...")
            context.close()


def parse_args():
    parser = argparse.ArgumentParser(description="登录 Douyin 并保存 Playwright Cookie JSON。")
    parser.add_argument(
        "-o",
        "--output",
        default=str(DEFAULT_SAVE_PATH),
        help="Cookie 保存路径。多账号时建议用不同文件名，例如 douyin_cookies_main.json。",
    )
    parser.add_argument(
        "--state-output",
        default=str(DEFAULT_STATE_PATH),
        help="完整 Playwright storage_state 保存路径。推荐 channel/debug 优先使用它。",
    )
    parser.add_argument(
        "--user-data-dir",
        default=str(DEFAULT_USER_DATA_DIR),
        help="持久化 Chromium 用户目录。Douyin 写操作建议复用它。",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    get_and_save_cookies(args.output, args.state_output, args.user_data_dir)
