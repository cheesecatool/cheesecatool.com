from flask import Flask, request, jsonify, render_template, Response, redirect, send_file, stream_with_context, send_from_directory
import yt_dlp
import logging
import time
import json
import requests
from urllib.parse import urlparse, parse_qs, quote, unquote
from flask_cors import CORS
from flask_socketio import SocketIO
import concurrent.futures
import threading
from functools import partial
import random
import base64
import io
import re
import os
import glob
from flask import after_this_request
from pytube import YouTube
import subprocess
import winreg

app = Flask(__name__, template_folder='templates')
CORS(app)  # 启用CORS支持

# 配置 Socket.IO
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='threading',
    ping_timeout=5,
    ping_interval=25,
    logger=False,
    engineio_logger=False
)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def sanitize_filename(filename):
    """清理文件名，移除非法字符"""
    # 移除非法字符
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # 限制长度
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        filename = name[:196] + ext
    return filename

def extract_video_id(url):
    """从URL中提取视频ID"""
    try:
        logger.info(f"正在提取视频ID，URL: {url}")
        
        # 处理YouTube Shorts URL
        if '/shorts/' in url:
            video_id = url.split('/shorts/')[1].split('?')[0]
            logger.info(f"从Shorts URL提取到视频ID: {video_id}")
            return video_id
            
        # 处理标准YouTube URL
        parsed_url = urlparse(url)
        if parsed_url.hostname in ['www.youtube.com', 'youtube.com', 'm.youtube.com']:
            if parsed_url.path == '/watch':
                query_params = parse_qs(parsed_url.query)
                video_id = query_params.get('v', [None])[0]
            else:
                video_id = None
        else:
            video_id = None
            
        if not video_id:
            logger.error("无法从URL中提取视频ID")
            return None
            
        logger.info(f"成功提取视频ID: {video_id}")
        return video_id
        
    except Exception as e:
        logger.error(f"提取视频ID时出错: {str(e)}")
        return None

def get_system_proxy():
    try:
        reg_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Internet Settings')
        proxy_enable, _ = winreg.QueryValueEx(reg_key, 'ProxyEnable')
        if proxy_enable:
            proxy_server, _ = winreg.QueryValueEx(reg_key, 'ProxyServer')
            app.logger.info(f'系统代理已启用: {proxy_server}')
            return f'http://{proxy_server}'
        else:
            app.logger.info('系统代理未启用')
            return None
    except Exception as e:
        app.logger.error(f'获取系统代理设置失败: {str(e)}')
        return None

def get_video_info(url):
    """获取视频信息"""
    try:
        logger.info(f"获取视频信息 - URL: {url}")
        
        # 如果是Shorts，转换URL格式
        video_id = extract_video_id(url)
        if '/shorts/' in url and video_id:
            url = f'https://www.youtube.com/watch?v={video_id}'
            logger.info(f"转换Shorts URL为标准URL: {url}")
        
        # 获取系统代理设置
        proxy = get_system_proxy()
        if proxy:
            os.environ['HTTP_PROXY'] = proxy
            os.environ['HTTPS_PROXY'] = proxy
            app.logger.info(f'使用系统代理: {proxy}')

        # 设置yt-dlp选项，优化性能
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': 'best[ext=mp4]/best',  # 简化格式选择
            'socket_timeout': 10,  # 减少超时时间
            'retries': 3,
            'fragment_retries': 3,
            'ignoreerrors': True,
            'noplaylist': True,
            'extract_flat': True,  # 只获取基本信息
            'skip_download': True,  # 跳过下载
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            }
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                logger.error("无法获取视频信息")
                return None
            
            # 简化返回的信息
            return {
                'id': info.get('id'),
                'title': info.get('title'),
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),
                'formats': [{
                    'format_id': 'best',
                    'ext': 'mp4',
                    'type': 'video',
                    'format_note': 'Best available'
                }]
            }
            
    except Exception as e:
        logger.error(f"获取视频信息失败: {str(e)}")
        return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/get_video_info', methods=['POST'])
def get_video_info():
    try:
        data = request.get_json()
        url = data.get('url', '')
        app.logger.info(f'获取视频信息 - URL: {url}')

        # 提取视频ID
        app.logger.info(f'正在提取视频ID，URL: {url}')
        video_id = None
        if 'shorts' in url.lower():
            video_id = re.search(r'shorts/([^/?]+)', url)
            if video_id:
                video_id = video_id.group(1)
                app.logger.info(f'从Shorts URL提取到视频ID: {video_id}')
                url = f'https://www.youtube.com/watch?v={video_id}'
                app.logger.info(f'转换Shorts URL为标准URL: {url}')

        # 获取系统代理设置
        proxy = get_system_proxy()
        proxies = {}
        if proxy:
            proxies = {
                'http': proxy,
                'https': proxy
            }
            app.logger.info(f'使用系统代理: {proxy}')

        try:
            app.logger.info('开始提取视频信息...')
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br'
            }

            response = requests.get(url, headers=headers, proxies=proxies, timeout=10)
            app.logger.info(f'获取页面状态码: {response.status_code}')

            if response.status_code == 200:
                html = response.text
                
                # 提取视频标题
                title_match = re.search(r'"title":"([^"]+)"', html)
                title = title_match.group(1) if title_match else '未知标题'

                # 提取视频时长
                duration_match = re.search(r'"lengthSeconds":"(\d+)"', html)
                duration = int(duration_match.group(1)) if duration_match else 0

                # 提取视频缩略图
                thumbnail_match = re.search(r'"thumbnails":\[{"url":"([^"]+)"', html)
                thumbnail = thumbnail_match.group(1) if thumbnail_match else ''

                # 提取视频描述
                description_match = re.search(r'"description":{"simpleText":"([^"]+)"', html)
                description = description_match.group(1) if description_match else ''

                # 提取观看次数
                view_count_match = re.search(r'"viewCount":"(\d+)"', html)
                view_count = int(view_count_match.group(1)) if view_count_match else 0

                # 提取作者信息
                author_match = re.search(r'"author":"([^"]+)"', html)
                author = author_match.group(1) if author_match else '未知作者'

                app.logger.info('成功提取视频信息')
                app.logger.info(f'视频标题: {title}')
                app.logger.info(f'视频时长: {duration}秒')

                return jsonify({
                    'title': title,
                    'duration': duration,
                    'thumbnail': thumbnail,
                    'description': description,
                    'view_count': view_count,
                    'author': author
                })
            else:
                app.logger.error(f'获取视频页面失败: {response.status_code}')
                return jsonify({'error': f'获取视频页面失败: {response.status_code}'}), 500

        except Exception as e:
            app.logger.error(f'获取视频信息失败: {str(e)}')
            app.logger.exception('详细错误信息:')
            return jsonify({'error': f'获取视频信息失败: {str(e)}'}), 500

    except Exception as e:
        app.logger.error(f'请求处理错误: {str(e)}')
        app.logger.exception('详细错误信息:')
        return jsonify({'error': f'请求处理失败: {str(e)}'}), 500

@app.route('/download', methods=['POST'])
def download_video():
    """处理视频下载请求"""
    try:
        data = request.get_json()
        url = data.get('url')
        
        if not url:
            return jsonify({'error': '缺少必要参数'}), 400
            
        logger.info(f"开始下载 - URL: {url}")
        
        # 设置临时下载目录
        temp_dir = os.path.join(app.root_path, 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        # 生成临时文件名
        temp_filename = f"temp_{random.randint(1000000000, 9999999999)}"
        output_template = os.path.join(temp_dir, f"{temp_filename}.mp4")

        def progress_hook(d):
            if d['status'] == 'downloading':
                try:
                    total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                    downloaded = d.get('downloaded_bytes', 0)
                    if total > 0:
                        percentage = (downloaded / total) * 100
                        socketio.emit('download_progress', {
                            'percentage': round(percentage, 1),
                            'downloaded': downloaded,
                            'total': total,
                            'speed': d.get('speed', 0),
                            'eta': d.get('eta', 0)
                        })
                        logger.info(f"下载进度: {percentage:.1f}%")
                except Exception as e:
                    logger.error(f"处理进度信息出错: {str(e)}")
        
        # 使用yt-dlp下载
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': output_template,
            'merge_output_format': 'mp4',
            'quiet': False,
            'no_warnings': False,
            'progress_hooks': [progress_hook],
            'verbose': True,
            'retries': 3,
            'fragment_retries': 3,
            'socket_timeout': 30,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            }
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                logger.info("开始使用yt-dlp下载...")
                info = ydl.extract_info(url, download=True)
                if not info:
                    logger.error("下载失败：无法获取视频信息")
                    return jsonify({'error': '下载失败：无法获取视频信息'}), 500
                
                logger.info(f"视频信息: {info.get('title', '未知标题')}")
                
                # 查找下载的文件
                downloaded_files = glob.glob(os.path.join(temp_dir, f"{temp_filename}.*"))
                if not downloaded_files:
                    return jsonify({'error': '下载失败，文件未找到'}), 400
                
                downloaded_file = downloaded_files[0]
                logger.info(f"下载完成: {downloaded_file}")
                
                # 构建响应文件名
                video_title = sanitize_filename(info['title'])
                filename = f"{video_title}.mp4"
                
                # 发送文件
                response = send_file(
                    downloaded_file,
                    as_attachment=True,
                    download_name=filename,
                    mimetype='video/mp4'
                )
                
                # 删除临时文件
                @after_this_request
                def remove_file(response):
                    try:
                        if os.path.exists(downloaded_file):
                            os.remove(downloaded_file)
                            logger.info("临时文件已删除")
                    except Exception as e:
                        logger.error(f"删除临时文件失败: {str(e)}")
                    return response
                
                return response
                
            except Exception as e:
                logger.error(f"下载失败: {str(e)}")
                return jsonify({'error': str(e)}), 500
            
    except Exception as e:
        logger.error(f"请求处理失败: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/static/<path:filename>')
def serve_static(filename):
    """提供静态文件服务"""
    return send_from_directory('static', filename)

@app.route('/test_connection', methods=['GET'])
def test_connection():
    try:
        # 测试与 YouTube 的连接
        response = requests.get('https://www.youtube.com', timeout=10)
        app.logger.info(f'YouTube 连接状态码: {response.status_code}')
        return jsonify({
            'status': 'success',
            'youtube_status': response.status_code
        })
    except Exception as e:
        app.logger.error(f'连接测试失败: {str(e)}')
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

if __name__ == '__main__':
    # 确保下载目录存在
    if not os.path.exists('downloads'):
        os.makedirs('downloads')
    # 确保静态文件目录存在
    if not os.path.exists('static'):
        os.makedirs('static')
    
    # 设置环境变量禁用代理
    os.environ['NO_PROXY'] = 'localhost,127.0.0.1'
    os.environ['no_proxy'] = 'localhost,127.0.0.1'
    
    # 禁用所有代理设置
    os.environ.pop('HTTP_PROXY', None)
    os.environ.pop('HTTPS_PROXY', None)
    os.environ.pop('http_proxy', None)
    os.environ.pop('https_proxy', None)
    
    # 使用普通的 Flask 运行方式
    app.run(host='localhost', port=3000, debug=True, threaded=True)