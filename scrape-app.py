import re
import time
import sqlite3
import threading
import flet as ft
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

def init_db():
    conn = sqlite3.connect("my_app.db")
    conn.cursor().execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            price TEXT,
            date TIMESTAMP DEFAULT (DATETIME('now', 'localtime'))
        )
    """)
    conn.commit()
    conn.close()

def scrape_yodobashi_perfect(url):
    options = Options()
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36')
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    try:
        driver.get(url)
        time.sleep(5) 

        # --- 1. 商品名の取得 ---
        title = "商品名不明"
        for sel in ["h1", ".productName", "#products_maintitle"]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                if el.text.strip():
                    title = el.text.strip()
                    break
            except: continue

        # --- 2. 価格の取得（全スキャン・フィルタリング方式） ---
        price = "価格不明"
        potential_prices = []

        # ページ内の「円」または「￥」を含む要素をすべて洗い出す（IDやクラスに依存しない）
        # ※ styleタグやscriptタグの中身は除外
        elements = driver.find_elements(By.XPATH, "//*[contains(text(), '円') or contains(text(), '￥')][not(self::script)][not(self::style)]")
        
        for el in elements:
            text = el.text.strip()
            # フィルタリング条件
            if not text: continue
            if "ポイント" in text: continue  # ポイント還元額を除外
            if "希望" in text: continue      # 希望小売価格を除外
            if "還元" in text: continue
            
            # 数字の抽出
            digits = re.sub(r'\D', '', text)
            if digits and int(digits) > 0:
                # 文字色を取得（赤字なら優先度アップ）
                try:
                    color = el.value_of_css_property("color")
                    is_red = "255, 0, 0" in color or "red" in el.get_attribute("class")
                except:
                    is_red = False
                
                # フォントサイズを取得（大きい文字を優先）
                try:
                    font_size = float(re.sub(r'[^\d.]', '', el.value_of_css_property("font-size")))
                except:
                    font_size = 0

                # (優先度スコア, 金額) のタプルで保存
                # 赤字なら+100点、フォントサイズそのまま加算
                score = (100 if is_red else 0) + font_size
                potential_prices.append((score, digits))

        # スコアが一番高いものを採用
        if potential_prices:
            potential_prices.sort(key=lambda x: x[0], reverse=True)
            price = potential_prices[0][1]

        # --- 3. 在庫状況の取得（キーワード広域探索） ---
        stock_text = "在庫不明"
        
        # 在庫に関連しそうなキーワードを含む要素を全検索
        # テキストが短め（50文字以内）のものを狙う
        keywords = ["在庫", "取り寄せ", "入荷", "予定", "残少"]
        xpath_query = " | ".join([f"//*[contains(text(), '{k}')]" for k in keywords])
        
        stock_elements = driver.find_elements(By.XPATH, xpath_query)
        
        best_stock_text = ""
        max_priority = -1

        for el in stock_elements:
            try:
                if not el.is_displayed(): continue
                t = el.text.strip().replace("\n", " ")
                if len(t) > 50: continue # 長すぎる文章は除外
                if "ポイント" in t: continue # ポイント関連の誤爆を防ぐ
                
                # 優先順位付け
                priority = 0
                if "在庫あり" in t or "在庫残少" in t: priority = 3
                elif "お取り寄せ" in t: priority = 2
                elif "予定" in t: priority = 1
                
                if priority > max_priority:
                    max_priority = priority
                    best_stock_text = t
            except: continue

        if best_stock_text:
            stock_text = best_stock_text
        
        # DB保存
        if title != "商品名不明":
            conn = sqlite3.connect("my_app.db")
            conn.cursor().execute("INSERT INTO products (title, price) VALUES (?, ?)", (title, price))
            conn.commit()
            conn.close()
        
        return f"【成功】{title[:10]}... | {price}円 | {stock_text}"

    except Exception as e:
        print(f"詳細エラー: {e}")
        return "エラー発生：もう一度試してください"
    finally:
        driver.quit()

# --- Flet GUI アプリ ---
def main(page: ft.Page):
    page.title = "商品価格監視ツール"
    page.window_width = 700
    page.window_height = 800
    init_db()

    def route_change(route):
        page.views.clear()
        
        if page.route == "/":
            url_input = ft.TextField(
                label="ヨドバシのURLを入力", 
                hint_text="https://www.yodobashi.com/product/...", 
                width=600
            )
            result_text = ft.Text(size=16, weight="bold")
            progress_bar = ft.ProgressBar(width=600, visible=False)

            def start_scrape(e):
                if not url_input.value:
                    result_text.value = "URLを入力してください"
                    page.update()
                    return
                
                e.control.disabled = True
                progress_bar.visible = True
                result_text.value = "取得中... (AIロジックで解析中)"
                result_text.color = ft.Colors.BLUE
                page.update()

                def task():
                    msg = scrape_yodobashi_perfect(url_input.value)
                    result_text.value = msg
                    result_text.color = ft.Colors.GREEN if "成功" in msg else ft.Colors.RED
                    e.control.disabled = False
                    progress_bar.visible = False
                    page.update()

                threading.Thread(target=task, daemon=True).start()

            page.views.append(
                ft.View("/", [
                    ft.AppBar(title=ft.Text("ヨドバシ・価格監視", color=ft.Colors.WHITE), bgcolor=ft.Colors.RED_600),
                    ft.Column([
                        url_input,
                        ft.ElevatedButton("スクレイピング実行", on_click=start_scrape, icon=ft.Icons.PLAY_ARROW),
                        progress_bar,
                        result_text,
                        ft.Divider(),
                        ft.ElevatedButton("保存データ一覧を見る", on_click=lambda _: page.go("/history"), icon=ft.Icons.HISTORY),
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER)
                ])
            )

        elif page.route == "/history":
            data = []
            conn = None
            try:
                conn = sqlite3.connect("my_app.db")
                data = conn.cursor().execute("SELECT title, price, date FROM products ORDER BY id DESC").fetchall()
            except Exception as e:
                print(f"DBエラー: {e}")
            finally:
                if conn: conn.close()

            history_list = ft.ListView(expand=True, spacing=10, padding=20)
            if not data:
                history_list.controls.append(ft.Text("まだ履歴がありません。"))
            else:
                for item in data:
                    history_list.controls.append(
                        ft.Card(
                            content=ft.Container(
                                content=ft.Column([
                                    ft.Text(f"取得日: {item[2]}", size=12, color=ft.Colors.GREY_700),
                                    ft.Text(f"{item[0]}", weight="bold"),
                                    ft.Text(f"{item[1]}円", size=18, color=ft.Colors.RED_ACCENT_400, weight="bold"),
                                ]), padding=10
                            )
                        )
                    )

            page.views.append(
                ft.View("/history", [
                    ft.AppBar(title=ft.Text("保存履歴", color=ft.Colors.BLACK), bgcolor=ft.Colors.BLUE_300),
                    history_list,
                    ft.ElevatedButton("戻る", on_click=lambda _: page.go("/")),
                ])
            )
        page.update()

    page.on_route_change = route_change
    page.go(page.route)

ft.app(target=main)
