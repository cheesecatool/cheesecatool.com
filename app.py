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

app = Flask(__name__, template_folder='templates')
CORS(app)  # 启用CORS支持
socketio = SocketIO(app, cors_allowed_origins="*")  # 初始化SocketIO

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

def get_video_info(url):
    """获取视频信息"""
    try:
        logger.info(f"获取视频信息 - URL: {url}")
        
        # 如果是Shorts，转换URL格式
        video_id = extract_video_id(url)
        if '/shorts/' in url and video_id:
            url = f'https://www.youtube.com/watch?v={video_id}'
            logger.info(f"转换Shorts URL为标准URL: {url}")
        
        # 设置yt-dlp选项
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': '18/best',  # 优先使用format 18，这是一个稳定的格式
            'socket_timeout': 30,
            'retries': 10,
            'fragment_retries': 10,
            'ignoreerrors': True,
            'noplaylist': True,
            'extract_flat': False,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            }
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                logger.error("无法获取视频信息")
                return None
                
            # 获取所有可用格式
            available_formats = info.get('formats', [])
            
            # 记录所有可用格式
            logger.info("所有可用格式:")
            for f in available_formats:
                logger.info(f"格式: {f.get('format_id')} - {f.get('ext')} - {f.get('vcodec')} - {f.get('acodec')} - {f.get('height')}p - {f.get('tbr')}kbps")
            
            # 优先查找format 18
            format_18 = next((f for f in available_formats if f.get('format_id') == '18'), None)
            if format_18:
                selected_formats = [{
                    'format_id': '18',
                    'ext': format_18.get('ext', 'mp4'),
                    'height': format_18.get('height', 360),
                    'width': format_18.get('width', 640),
                    'filesize': format_18.get('filesize', 0) or 0,
                    'vcodec': format_18.get('vcodec', ''),
                    'acodec': format_18.get('acodec', ''),
                    'tbr': format_18.get('tbr', 0) or 0,
                    'type': 'video',
                    'format_note': '360p MP4'
                }]
            else:
                # 如果没有format 18，找出最佳的MP4格式
                mp4_formats = [f for f in available_formats if f.get('ext') == 'mp4' and f.get('acodec') != 'none' and f.get('vcodec') != 'none']
                if mp4_formats:
                    best_mp4 = max(mp4_formats, key=lambda x: (x.get('height', 0) or 0, x.get('tbr', 0) or 0))
                    selected_formats = [{
                        'format_id': best_mp4['format_id'],
                        'ext': 'mp4',
                        'height': best_mp4.get('height', 0),
                        'width': best_mp4.get('width', ''),
                        'filesize': best_mp4.get('filesize', 0) or 0,
                        'vcodec': best_mp4.get('vcodec', ''),
                        'acodec': best_mp4.get('acodec', ''),
                        'tbr': best_mp4.get('tbr', 0) or 0,
                        'type': 'video',
                        'format_note': f"{best_mp4.get('height', 0)}p MP4"
                    }]
                else:
                    # 如果没有合适的MP4格式，使用任何可用的最佳格式
                    selected_formats = [{
                        'format_id': 'best',
                        'ext': 'mp4',
                        'type': 'video',
                        'format_note': 'Best available'
                    }]
            
            return {
                'id': info.get('id'),
                'title': info.get('title'),
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),
                'view_count': info.get('view_count'),
                'uploader': info.get('uploader'),
                'formats': selected_formats
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
def get_video_info_endpoint():
    """获取视频信息的API端点"""
    try:
        data = request.get_json()
        url = data.get('url')
        
        if not url:
            return jsonify({'error': '请提供视频URL'}), 400
            
        video_info = get_video_info(url)
        if not video_info:
            return jsonify({'error': '无法获取视频信息'}), 500
            
        return jsonify(video_info)
        
    except Exception as e:
        logger.error(f"获取视频信息时出错: {str(e)}")
        return jsonify({'error': str(e)}), 500

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
                        # 通过WebSocket发送进度信息
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
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/18/best',  # 更全面的格式选择
            'outtmpl': output_template,
            'merge_output_format': 'mp4',  # 确保输出为MP4格式
            'quiet': False,
            'no_warnings': False,
            'progress_hooks': [progress_hook],  # 添加进度钩子
            'verbose': True,
            'retries': 3,
            'fragment_retries': 3,
            'socket_timeout': 30,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            },
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',  # 使用FFmpeg确保转换为MP4
            }]
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

@app.route('/download/<filename>')
def download_file(filename):
    """处理文件下载请求"""
    try:
        logger.info(f"请求下载文件: {filename}")
        current_dir = os.path.dirname(os.path.abspath(__file__))
        downloads_dir = os.path.join(current_dir, 'downloads')
        return send_from_directory(downloads_dir, filename, as_attachment=True)
    except Exception as e:
        logger.error(f"文件下载失败: {str(e)}")
        return jsonify({'error': '文件下载失败'}), 500

@app.route('/static/<path:filename>')
def serve_static(filename):
    """提供静态文件服务"""
    return send_from_directory('static', filename)

if __name__ == '__main__':
    # 确保下载目录存在
    if not os.path.exists('downloads'):
        os.makedirs('downloads')
    # 确保静态文件目录存在
    if not os.path.exists('static'):
        os.makedirs('static')
    # 使用socketio运行应用
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
