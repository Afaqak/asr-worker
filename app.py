import os
import tempfile
from flask import Flask, request, jsonify
from google.cloud import storage
import yt_dlp

app = Flask(__name__)

BUCKET_NAME = os.environ.get('BUCKET_NAME')
PROXY_URL = os.environ.get('PROXY_URL')

def get_storage_client():
    """Get GCS client"""
    return storage.Client()

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
            # Configure yt-dlp for audio extraction
            ydl_opts = {
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'outtmpl': f'{tmpdir}/%(id)s.%(ext)s',
                # Reliability options
                'retries': 5,
                'fragment_retries': 5,
                'ignoreerrors': False,
                'geo_bypass': True,
                'nocheckcertificate': True,
                'socket_timeout': 60,
                'extractor_retries': 3,
            }
            
            # Add proxy if configured
            if PROXY_URL:
                ydl_opts['proxy'] = PROXY_URL
            
            # Download and extract audio
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                video_id = info['id']
                title = info.get('title', video_id)
                duration = info.get('duration', 0)
                channel = info.get('channel', 'Unknown')
            
            # Find the converted file
            audio_file = f'{tmpdir}/{video_id}.mp3'
            
            # Check if file exists
            if not os.path.exists(audio_file):
                return jsonify({'error': 'Audio extraction failed'}), 500
            
            file_size = os.path.getsize(audio_file)
            
            # Upload to GCS
            storage_client = get_storage_client()
            bucket = storage_client.bucket(BUCKET_NAME)
            blob = bucket.blob(f'audio/{video_id}.mp3')
            
            # Set metadata
            blob.metadata = {
                'title': title,
                'channel': channel,
                'duration': str(duration),
                'source_url': video_url
            }
            
            blob.upload_from_filename(audio_file, content_type='audio/mpeg')
            
            # Generate signed URL (valid for 7 days)
            from datetime import timedelta
            signed_url = blob.generate_signed_url(expiration=timedelta(days=7))
            
            return jsonify({
                'success': True,
                'video_id': video_id,
                'title': title,
                'channel': channel,
                'duration_seconds': duration,
                'file_size_bytes': file_size,
                'gcs_path': f'gs://{BUCKET_NAME}/audio/{video_id}.mp3',
                'signed_url': signed_url
            })
            
    except yt_dlp.utils.DownloadError as e:
        return jsonify({'error': f'Download failed: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/batch', methods=['POST'])
def batch_download():
    """Download multiple YouTube videos as audio"""
    data = request.json
    urls = data.get('urls', [])
    
    if not urls:
        return jsonify({'error': 'No URLs provided'}), 400
    
    results = []
    for url in urls:
        try:
            # Reuse the download logic
            with app.test_request_context(json={'url': url}):
                response = download_audio()
                if hasattr(response, 'get_json'):
                    results.append(response.get_json())
                else:
                    results.append(response[0].get_json())
        except Exception as e:
            results.append({'url': url, 'error': str(e)})
    
    return jsonify({'results': results})


@app.route('/info', methods=['POST'])
def get_video_info():
    """Get video info without downloading"""
    data = request.json
    video_url = data.get('url')
    
    if not video_url:
        return jsonify({'error': 'No URL provided'}), 400
    
    try:
        ydl_opts = {
            'skip_download': True,
            'geo_bypass': True,
        }
        
        if PROXY_URL:
            ydl_opts['proxy'] = PROXY_URL
        
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
            from datetime import timedelta
            files.append({
                'name': blob.name,
                'size_bytes': blob.size,
                'created': blob.time_created.isoformat() if blob.time_created else None,
                'metadata': blob.metadata,
                'signed_url': blob.generate_signed_url(expiration=timedelta(days=1))
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
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'bucket_configured': bool(BUCKET_NAME),
        'proxy_configured': bool(PROXY_URL)
    })


@app.route('/', methods=['GET'])
def index():
    """API documentation"""
    return jsonify({
        'name': 'YouTube Audio Downloader API',
        'endpoints': {
            'POST /download': 'Download single video as audio',
            'POST /batch': 'Download multiple videos as audio',
            'POST /info': 'Get video info without downloading',
            'GET /list': 'List all audio files in bucket',
            'DELETE /delete/<video_id>': 'Delete an audio file',
            'GET /health': 'Health check'
        },
        'example': {
            'download': {'url': 'https://www.youtube.com/watch?v=VIDEO_ID'},
            'batch': {'urls': ['https://www.youtube.com/watch?v=ID1', 'https://www.youtube.com/watch?v=ID2']},
            'info': {'url': 'https://www.youtube.com/watch?v=VIDEO_ID'}
        }
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
