import argparse
import time
import csv
import logging
import json
import os
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from pymongo import MongoClient
from bson import ObjectId

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class XCrawler:
    def __init__(self, query, scrolls, pause, login_wait, output, config_file="config.json", headless=True, mongo_uri="mongodb://localhost:27017/", db_name="twitter_db", collection_name="tweets"):
        self.query = query
        self.scrolls = scrolls
        self.pause = pause
        self.login_wait = login_wait
        self.output = output
        self.tweets = set()
        self.driver = None
        # 获取配置文件的绝对路径
        self.config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), config_file)
        self.account = self._load_account()
        self.headless = headless
        
        # MongoDB 配置
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.collection_name = collection_name
        self.client = None
        self.db = None
        self.collection = None

    def _load_account(self):
        """从配置文件加载账号信息"""
        try:
            if not os.path.exists(self.config_file):
                logger.warning(f"配置文件 {self.config_file} 不存在，将使用手动登录模式")
                return None

            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            if not config.get('accounts'):
                logger.warning("配置文件中没有账号信息，将使用手动登录模式")
                return None

            default_index = config.get('default_account', 0)
            if default_index >= len(config['accounts']):
                logger.warning("默认账号索引超出范围，将使用第一个账号")
                default_index = 0

            account = config['accounts'][default_index]
            if not all(key in account for key in ['username', 'password', 'x_name']):
                logger.warning("账号信息不完整，将使用手动登录模式")
                return None

            logger.info(f"成功加载账号配置: {account['username']}")
            return account

        except Exception as e:
            logger.error(f"加载账号配置失败: {str(e)}")
            return None

    def setup_mongodb(self):
        """设置 MongoDB 连接"""
        max_retries = 3
        retry_delay = 5  # 秒
        
        for attempt in range(max_retries):
            try:
                if self.client:
                    try:
                        self.client.close()
                    except:
                        pass
                
                self.client = MongoClient(
                    self.mongo_uri,
                    maxPoolSize=50,
                    minPoolSize=10,
                    connectTimeoutMS=30000,
                    socketTimeoutMS=45000,
                    serverSelectionTimeoutMS=30000,
                    retryWrites=True,
                    retryReads=True
                )
                
                # 测试连接
                self.client.admin.command('ping')
                
                self.db = self.client[self.db_name]
                self.collection = self.db[self.collection_name]
                
                logger.info(f"成功连接到 MongoDB: {self.mongo_uri}")
                logger.info(f"使用数据库: {self.db_name}")
                logger.info(f"使用集合: {self.collection_name}")
                return
                
            except Exception as e:
                logger.error(f"连接 MongoDB 失败 (尝试 {attempt + 1}/{max_retries}): {str(e)}")
                if attempt < max_retries - 1:
                    logger.info(f"等待 {retry_delay} 秒后重试...")
                    time.sleep(retry_delay)
                else:
                    logger.error("达到最大重试次数，无法连接到 MongoDB")
                    raise

    def setup_driver(self):
        """设置 Chrome WebDriver"""
        try:
            import os
            from webdriver_manager.chrome import ChromeDriverManager
            
            # 获取 Chrome 路径
            chrome_path = None
            possible_paths = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
                "/usr/bin/google-chrome",  # Linux路径
                "/usr/bin/chromium",       # Linux路径
                "/usr/bin/chromium-browser" # Linux路径
            ]
            
            for path in possible_paths:
                if os.path.exists(path):
                    chrome_path = path
                    logger.info(f"找到 Chrome 浏览器: {chrome_path}")
                    break
            
            if not chrome_path:
                logger.warning("未找到 Chrome 浏览器，请确保 Chrome 已正确安装")
            
            # 设置 Chrome 选项
            options = webdriver.ChromeOptions()
            
            # 添加必要的选项
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument('--ignore-certificate-errors')
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-software-rasterizer")
            options.add_argument("--window-size=1920,1080")
            options.add_argument('--start-maximized')
            options.add_argument('--disable-notifications')
            
            # 添加SSL相关选项
            options.add_argument('--ignore-ssl-errors')
            options.add_argument('--ignore-certificate-errors-spki-list')
            options.add_argument('--allow-insecure-localhost')
            options.add_argument('--disable-web-security')
            options.add_argument('--allow-running-insecure-content')
            
            # 禁用自动化标志
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            
            # 设置无头模式
            if self.headless:
                options.add_argument('--headless')
                options.add_argument('--disable-gpu')
                options.add_argument('--no-sandbox')
                options.add_argument('--disable-dev-shm-usage')
                options.add_argument('--remote-debugging-port=9222')
                options.add_argument('--disable-setuid-sandbox')
                logger.info("启用无头模式")
            
            # 设置 Chrome 二进制文件路径
            if chrome_path:
                options.binary_location = chrome_path
            
            # 判断是否需要下载 ChromeDriver
            driver_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chromedriver.exe")
            need_download = False
            
            if not os.path.exists(driver_path):
                logger.info("本地 ChromeDriver 不存在，将自动下载")
                need_download = True
            else:
                try:
                    # 尝试使用本地 ChromeDriver
                    service = Service(
                        executable_path=driver_path,
                        log_path="chromedriver.log"  # 启用日志
                    )
                    test_driver = webdriver.Chrome(service=service, options=options)
                    test_driver.quit()
                    logger.info("本地 ChromeDriver 可用")
                except Exception as e:
                    logger.warning(f"本地 ChromeDriver 不可用: {str(e)}")
                    need_download = True
            
            if need_download:
                logger.info("开始下载 ChromeDriver...")
                try:
                    # 获取 Chrome 版本
                    import subprocess
                    import re
                    
                    chrome_version = None
                    if chrome_path:
                        try:
                            if os.name == 'nt':  # Windows
                                output = subprocess.check_output(
                                    f'"{chrome_path}" --version',
                                    shell=True
                                )
                            else:  # Linux
                                output = subprocess.check_output(
                                    f'{chrome_path} --version',
                                    shell=True
                                )
                            chrome_version = re.search(r'Chrome\s+([\d.]+)', output.decode()).group(1)
                            logger.info(f"检测到 Chrome 版本: {chrome_version}")
                        except Exception as e:
                            logger.warning(f"获取 Chrome 版本失败: {str(e)}")
                    
                    if chrome_version:
                        major_version = chrome_version.split('.')[0]
                        logger.info(f"下载匹配的 ChromeDriver 版本: {major_version}")
                        service = Service(ChromeDriverManager(version=major_version).install())
                    else:
                        logger.info("无法获取 Chrome 版本，下载最新版本的 ChromeDriver")
                        service = Service(ChromeDriverManager().install())
                except Exception as e:
                    logger.error(f"下载 ChromeDriver 失败: {str(e)}")
                    raise
            else:
                service = Service(
                    executable_path=driver_path,
                    log_path="chromedriver.log"  # 启用日志
                )
            
            logger.info("正在初始化 Chrome WebDriver...")
            self.driver = webdriver.Chrome(service=service, options=options)
            logger.info("Chrome WebDriver 初始化成功")
            
            # 设置窗口位置
            self.driver.set_window_position(0, 0)
            # 设置窗口大小
            self.driver.set_window_size(1920, 1080)
            # 隐藏自动化标志
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            logger.info("Chrome WebDriver 设置完成")
            
            # 测试浏览器是否正常工作
            self.driver.get("about:blank")
            logger.info("浏览器测试访问成功")
            
        except Exception as e:
            logger.error(f"设置 WebDriver 失败: {str(e)}")
            logger.error("详细错误信息：", exc_info=True)
            
            # 检查 ChromeDriver 权限
            if os.path.exists(driver_path):
                try:
                    # 尝试修改权限
                    os.chmod(driver_path, 0o755)
                    logger.info(f"已更新 ChromeDriver 权限: {driver_path}")
                except Exception as chmod_err:
                    logger.error(f"无法更新 ChromeDriver 权限: {str(chmod_err)}")
            
            # 检查是否以管理员权限运行
            if not os.environ.get("ADMINISTRATOR"):
                logger.warning("建议：请尝试以管理员权限运行此脚本")
            
            # 如果发生错误，等待用户查看错误信息
            input("按回车键退出...")
            raise

    def login(self):
        """执行登录操作"""
        try:
            # 设置页面加载超时
            self.driver.set_page_load_timeout(30)
            self.driver.set_script_timeout(30)
            
            self.driver.get("https://x.com/login")
            
            if self.account:
                logger.info("尝试自动登录...")
                try:
                    # 等待登录页面加载
                    WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located((By.NAME, "text"))
                    )
                    
                    # 输入用户名（使用 x_name）
                    username_input = self.driver.find_element(By.NAME, "text")
                    username_input.send_keys(self.account['x_name'])
                    self.driver.find_element(By.XPATH, "//span[text()='下一步']").click()
                    
                    # 等待可能的验证步骤
                    time.sleep(2)
                    
                    # 检查是否需要验证用户名或邮箱
                    try:
                        # 尝试查找验证输入框
                        verification_input = WebDriverWait(self.driver, 5).until(
                            EC.presence_of_element_located((By.NAME, "text"))
                        )
                        logger.info("需要验证用户名或邮箱...")
                        # 输入用户名或邮箱
                        verification_input.send_keys(self.account['username'])
                        self.driver.find_element(By.XPATH, "//span[text()='下一步']").click()
                    except TimeoutException:
                        logger.info("无需验证用户名或邮箱")
                    
                    # 等待密码输入框
                    WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located((By.NAME, "password"))
                    )
                    
                    # 输入密码
                    password_input = self.driver.find_element(By.NAME, "password")
                    password_input.send_keys(self.account['password'])
                    self.driver.find_element(By.XPATH, "//span[text()='登录']").click()
                    
                    # 等待验证页面
                    time.sleep(3)
                    
                    # 处理可能的验证码
                    if "验证" in self.driver.page_source:
                        logger.info("需要验证码，请在浏览器中完成验证...")
                        time.sleep(self.login_wait)
                    else:
                        logger.info("登录成功")
                except Exception as e:
                    logger.error(f"自动登录失败: {str(e)}")
                    logger.info("切换到手动登录模式...")
                    time.sleep(self.login_wait)
            else:
                logger.info(f"请在 {self.login_wait} 秒内完成手动登录...")
                time.sleep(self.login_wait)
            
            logger.info("登录等待时间结束")
        except Exception as e:
            logger.error(f"登录过程出错: {str(e)}")
            raise

    def search_and_scroll(self):
        """执行搜索并滚动加载内容"""
        try:
            search_url = f"https://x.com/search?q={self.query}&src=typed_query"
            self.driver.get(search_url)
            time.sleep(5)  # 等待搜索结果加载

            for i in range(self.scrolls):
                logger.info(f"正在执行第 {i+1}/{self.scrolls} 次滚动")
                
                # 滚动前获取当前页面上的推文
                tweets = self.driver.find_elements(By.CSS_SELECTOR, '[data-testid="tweetText"]')
                for tweet in tweets:
                    try:
                        content = tweet.text.strip()
                        if content and content not in self.tweets:
                            self.tweets.add(content)
                            logger.info(f"发现新推文: {content[:50]}...")
                            
                            # 保存到MongoDB
                            tweet_data = {
                                "content": content,
                                "query": self.query,
                                "timestamp": datetime.now(),
                                "source": "x.com",
                                "processed": False
                            }
                            
                            # 使用重试机制保存到MongoDB
                            max_retries = 3
                            for attempt in range(max_retries):
                                try:
                                    self.collection.insert_one(tweet_data)
                                    logger.info(f"推文已保存到MongoDB: {content[:50]}...")
                                    break
                                except Exception as e:
                                    if attempt < max_retries - 1:
                                        logger.warning(f"保存推文失败 (尝试 {attempt + 1}/{max_retries}): {str(e)}")
                                        time.sleep(2)
                                        # 尝试重新连接
                                        self.setup_mongodb()
                                    else:
                                        logger.error(f"保存推文失败，达到最大重试次数: {str(e)}")
                                        raise
                    except Exception as e:
                        logger.warning(f"获取推文内容失败: {str(e)}")
                        continue
                
                # 执行滚动
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(self.pause)

        except Exception as e:
            logger.error(f"搜索和滚动过程出错: {str(e)}")
            raise

    def save_results(self):
        """保存结果到 CSV 文件"""
        try:
            with open(self.output, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['content'])
                for tweet in self.tweets:
                    writer.writerow([tweet])
            logger.info(f"结果已保存到 {self.output}")
        except Exception as e:
            logger.error(f"保存结果失败: {str(e)}")
            raise

    def run(self):
        """运行爬虫"""
        try:
            logger.info("开始运行爬虫...")
            self.setup_mongodb()  # 设置MongoDB连接
            self.setup_driver()
            logger.info("准备登录...")
            self.login()
            logger.info("准备搜索和滚动...")
            self.search_and_scroll()
            logger.info("准备保存结果...")
            self.save_results()
            logger.info("爬虫运行完成")
        except Exception as e:
            logger.error(f"爬虫运行出错: {str(e)}")
            logger.error("详细错误信息：", exc_info=True)
            # 如果发生错误，等待用户查看错误信息
            input("按回车键退出...")
        finally:
            if self.driver:
                try:
                    self.driver.quit()
                    logger.info("浏览器已关闭")
                except Exception as e:
                    logger.error(f"关闭浏览器时出错: {str(e)}")
            if self.client:
                try:
                    self.client.close()
                    logger.info("MongoDB连接已关闭")
                except Exception as e:
                    logger.error(f"关闭MongoDB连接时出错: {str(e)}")

def main():
    parser = argparse.ArgumentParser(description='X (Twitter) 爬虫')
    parser.add_argument('--query', default='BTC', help='搜索查询词')
    parser.add_argument('--scrolls', type=int, default=5, help='滚动次数')
    parser.add_argument('--pause', type=float, default=2.0, help='滚动间隔（秒）')
    parser.add_argument('--login-wait', type=int, default=30, help='登录等待时间（秒）')
    parser.add_argument('--output', default='tweets.csv', help='输出文件名')
    parser.add_argument('--config', default='config.json', help='配置文件路径')
    parser.add_argument('--headless', action='store_true', help='启用无头模式')
    parser.add_argument('--mongo-uri', default='mongodb://localhost:27017/', help='MongoDB连接URI')
    parser.add_argument('--db-name', default='twitter_db', help='MongoDB数据库名称')
    parser.add_argument('--collection-name', default='tweets', help='MongoDB集合名称')

    args = parser.parse_args()

    crawler = XCrawler(
        query=args.query,
        scrolls=args.scrolls,
        pause=args.pause,
        login_wait=args.login_wait,
        output=args.output,
        config_file=args.config,
        headless=args.headless,
        mongo_uri=args.mongo_uri,
        db_name=args.db_name,
        collection_name=args.collection_name
    )

    crawler.run()

if __name__ == "__main__":
    main() 