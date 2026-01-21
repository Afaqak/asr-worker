import os
import tempfile
import glob
from flask import Flask, request, jsonify
from google.cloud import storage
import yt_dlp

app = Flask(__name__)

BUCKET_NAME = os.environ.get('BUCKET_NAME')
PROXY_URL = os.environ.get('PROXY_URL')
POT_PROVIDER_URL = os.environ.get('POT_PROVIDER_URL', 'http://127.0.0.1:4416')


def get_storage_client():
    """Get GCS client"""
    return storage.Client()


def get_ydl_opts(tmpdir=None):
    """Get yt-dlp options with POT provider for YouTube"""
    opts = {
        'retries': 5,
        'fragment_retries': 5,
        'ignoreerrors': False,
        'geo_bypass': True,
        'geo_bypass_country': 'US',
        'nocheckcertificate': True,
        'socket_timeout': 60,
        'extractor_retries': 3,
        'noplaylist': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        },
        # Use bgutil POT provider
        'extractor_args': {
            'youtube': {
                'player_client': ['default', 'mweb'],
            },
            'youtubepot-bgutilhttp': {
                'base_url': [POT_PROVIDER_URL],
            }
        },
    }
    
    if tmpdir:
        opts['outtmpl'] = f'{tmpdir}/%(id)s.%(ext)s'
    
    if PROXY_URL:
        opts['proxy'] = PROXY_URL
    
    return opts


@app.route('/download', methods=['POST'])
def download_audio():
    """Download YouTube video as audio and upload to GCS"""
    data = request.json
    video_url = data.get('url')
    
    if not video_url:
        return jsonify({'error': 'No URL provided'}), 400
    
    if not BUCKET_NAME:
        return jsonify({'error': 'BUCKET_NAME not configured'}), 500
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts = get_ydl_opts(tmpdir)
            ydl_opts['format'] = 'bestaudio/best[height<=720]/best'
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                video_id = info['id']
                title = info.get('title', video_id)
                duration = info.get('duration', 0)
                channel = info.get('channel', 'Unknown')
            
            # Find the mp3 file
            audio_files = glob.glob(f'{tmpdir}/{video_id}.mp3')
            if not audio_files:
                audio_files = glob.glob(f'{tmpdir}/*.mp3')
            
            if not audio_files:
                return jsonify({'error': 'Audio extraction failed - no mp3 file created'}), 500
            
            audio_file = audio_files[0]
            file_size = os.path.getsize(audio_file)
            
            storage_client = get_storage_client()
            bucket = storage_client.bucket(BUCKET_NAME)
            blob = bucket.blob(f'audio/{video_id}.mp3')
            
            blob.metadata = {
                'title': title,
                'channel': channel,
                'duration': str(duration),
                'source_url': video_url
            }
            
            blob.upload_from_filename(audio_file, content_type='audio/mpeg')
            
            public_url = f'https://storage.googleapis.com/{BUCKET_NAME}/audio/{video_id}.mp3'
            
            return jsonify({
                'success': True,
                'video_id': video_id,
                'title': title,
                'channel': channel,
                'duration_seconds': duration,
                'file_size_bytes': file_size,
                'gcs_path': f'gs://{BUCKET_NAME}/audio/{video_id}.mp3',
                'url': public_url
            })
            
    except yt_dlp.utils.DownloadError as e:
        return jsonify({'error': f'Download failed: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/info', methods=['POST'])
def get_video_info():
    """Get video info without downloading"""
    data = request.json
    video_url = data.get('url')
    
    if not video_url:
        return jsonify({'error': 'No URL provided'}), 400
    
    try:
        ydl_opts = get_ydl_opts()
        ydl_opts['skip_download'] = True
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            return jsonify({
                'video_id': info['id'],
                'title': info.get('title'),
                'channel': info.get('channel'),
                'duration_seconds': info.get('duration'),
                'view_count': info.get('view_count'),
                'upload_date': info.get('upload_date'),
                'thumbnail': info.get('thumbnail'),
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/formats', methods=['POST'])
def list_formats():
    """List available formats for a video"""
    data = request.json
    video_url = data.get('url')
    
    if not video_url:
        return jsonify({'error': 'No URL provided'}), 400
    
    try:
        ydl_opts = get_ydl_opts()
        ydl_opts['skip_download'] = True
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            formats = []
            for f in info.get('formats', []):
                formats.append({
                    'format_id': f.get('format_id'),
                    'ext': f.get('ext'),
                    'resolution': f.get('resolution'),
                    'filesize': f.get('filesize'),
                    'acodec': f.get('acodec'),
                    'vcodec': f.get('vcodec'),
                })
            return jsonify({
                'video_id': info['id'],
                'title': info.get('title'),
                'formats': formats
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/list', methods=['GET'])
def list_audio_files():
    """List all audio files in the bucket"""
    if not BUCKET_NAME:
        return jsonify({'error': 'BUCKET_NAME not configured'}), 500
    
    try:
        storage_client = get_storage_client()
        bucket = storage_client.bucket(BUCKET_NAME)
        blobs = bucket.list_blobs(prefix='audio/')
        
        files = []
        for blob in blobs:
            files.append({
                'name': blob.name,
                'size_bytes': blob.size,
                'created': blob.time_created.isoformat() if blob.time_created else None,
                'metadata': blob.metadata,
                'url': f'https://storage.googleapis.com/{BUCKET_NAME}/{blob.name}'
            })
        
        return jsonify({'files': files, 'count': len(files)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/delete/<video_id>', methods=['DELETE'])
def delete_audio(video_id):
    """Delete an audio file from the bucket"""
    if not BUCKET_NAME:
        return jsonify({'error': 'BUCKET_NAME not configured'}), 500
    
    try:
        storage_client = get_storage_client()
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(f'audio/{video_id}.mp3')
        
        if blob.exists():
            blob.delete()
            return jsonify({'success': True, 'message': f'Deleted {video_id}.mp3'})
        else:
            return jsonify({'error': 'File not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'bucket_configured': bool(BUCKET_NAME),
        'proxy_configured': bool(PROXY_URL),
        'pot_provider_url': POT_PROVIDER_URL
    })


@app.route('/', methods=['GET'])
def index():
    return jsonify({
        'name': 'YouTube Audio Downloader API',
        'endpoints': {
            'POST /download': 'Download single video as audio',
            'POST /info': 'Get video info without downloading',
            'POST /formats': 'List available formats for a video',
            'GET /list': 'List all audio files in bucket',
            'DELETE /delete/<video_id>': 'Delete an audio file',
            'GET /health': 'Health check'
        }
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
