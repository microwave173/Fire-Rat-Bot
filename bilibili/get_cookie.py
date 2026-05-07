from pathlib import Path
import argparse
import json

from playwright.sync_api import sync_playwright


DEFAULT_SAVE_PATH = Path(__file__).with_name("bilibili_cookies.json")


def get_and_save_cookies(save_path=DEFAULT_SAVE_PATH):
    save_path = Path(save_path)

    with sync_playwright() as p:
        print("正在启动 Playwright 浏览器...")

        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        try:
            page.goto("https://www.bilibili.com")

            print("\n" + "=" * 50)
            print("浏览器已打开。请在弹出的窗口中手动登录 Bilibili。")
            print("请在完全登录成功（能看到自己的头像和历史记录）后，再继续。")
            print("=" * 50 + "\n")

            input(">>> 登录完成后，请在此处按下 Enter 键以保存 Cookie...")

            cookies = context.cookies()
            save_path.parent.mkdir(parents=True, exist_ok=True)

            with save_path.open("w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=4)

            print(f"\n成功！Cookie 已提取并保存至: {save_path}")

        except Exception as e:
            print(f"\n运行过程中出现错误: {e}")

        finally:
            print("正在关闭浏览器...")
            browser.close()


def parse_args():
    parser = argparse.ArgumentParser(description="登录 Bilibili 并保存 Playwright Cookie JSON。")
    parser.add_argument(
        "-o",
        "--output",
        default=str(DEFAULT_SAVE_PATH),
        help="Cookie 保存路径。多账号时建议用不同文件名，例如 bilibili_cookies_main.json。",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    get_and_save_cookies(args.output)
