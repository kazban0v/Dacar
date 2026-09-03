from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time

options = Options()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')

try:
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    
    # 1. Login
    driver.get("http://127.0.0.1:8000/users/login/")
    driver.find_element("name", "username").send_keys("admin")
    driver.find_element("name", "password").send_keys("admin123")
    driver.find_element("css selector", "button[type='submit']").click()
    time.sleep(1)
    
    # 2. Check Desktop Dashboard
    driver.get("http://127.0.0.1:8000/")
    time.sleep(2)
    print("--- Desktop Dashboard Console Logs ---")
    for log in driver.get_log('browser'):
        print(f"[{log['level']}] {log['message']}")
        
    # 3. Check Mobile Dashboard
    driver.get("http://127.0.0.1:8000/m/")
    time.sleep(2)
    print("\n--- Mobile Dashboard Console Logs ---")
    for log in driver.get_log('browser'):
        print(f"[{log['level']}] {log['message']}")
        
    driver.quit()
except Exception as e:
    print("Selenium test failed:", e)
