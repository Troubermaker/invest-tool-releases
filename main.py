import webview
import os
from api import Api
import scheduler

def main():
    # Start the background data fetcher scheduling daemon 
    scheduler.start_background_daemon()
    
    api = Api()
    
    # 获取 frontend/dist/index.html 的绝对路径
    current_dir = os.path.dirname(os.path.abspath(__file__))
    dist_path = os.path.join(current_dir, "frontend", "dist", "index.html")
    
    # 如果 frontend/dist 存在，则加载打包后的页面；负责加载本地 vite 开发服务器
    if os.path.exists(dist_path):
        url = f"file:///{dist_path.replace(os.sep, '/')}"
    else:
        url = "http://localhost:5173"

    window = webview.create_window(
        title="量化复盘与盯盘终端", 
        url=url,
        js_api=api,
        width=1440,
        height=900,
        min_size=(1280, 720),
        background_color='#fcfcfc', # 纯净白灰浅色背景
    )
    
    api.set_window(window)
    webview.start(debug=True)

if __name__ == '__main__':
    main()
