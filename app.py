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
COOKIES_FILE = os.environ.get('COOKIES_FILE', 'cookies.txt')

# Global cookies path
COOKIES_PATH = None


def get_storage_client():
    return storage.Client()


def download_cookies():
    """Download cookies file from GCS bucket"""
    global COOKIES_PATH

    if not BUCKET_NAME:
        return None

    try:
        storage_client = get_storage_client()
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(COOKIES_FILE)

        if blob.exists():
            COOKIES_PATH = '/tmp/cookies.txt'
            blob.download_to_filename(COOKIES_PATH)
            print(f"Downloaded cookies from gs://{BUCKET_NAME}/{COOKIES_FILE}")
            return COOKIES_PATH
        else:
            print(f"No cookies file found at gs://{BUCKET_NAME}/{COOKIES_FILE}")
            return None
    except Exception as e:
        print(f"Error downloading cookies: {e}")
        return None


def get_ydl_opts(tmpdir=None):
    """Get yt-dlp options (cookies-first, POT fallback)"""
    global COOKIES_PATH

    # Always ensure cookies exist (Cloud Run safe)
    if not COOKIES_PATH or not os.path.exists(COOKIES_PATH):
        download_cookies()

    opts = {
        'retries': 5,
        'fragment_retries': 5,
        'socket_timeout': 60,
        'extractor_retries': 3,
        'noplaylist': True,
        'verbose': True,
        'geo_bypass': True,
        'nocheckcertificate': True,
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/122.0.0.0 Safari/537.36'
            ),
        },
    }

    if tmpdir:
        opts['outtmpl'] = f'{tmpdir}/%(id)s.%(ext)s'

    if PROXY_URL:
        opts['proxy'] = PROXY_URL

    extractor_args = {
        'youtube': {
            'player_client': ['web', 'android'],
        }
    }

    # Only use POT provider if cookies are NOT available
    if not (COOKIES_PATH and os.path.exists(COOKIES_PATH)):
        extractor_args['youtubepot-bgutilhttp'] = {
            'base_url': [POT_PROVIDER_URL],
        }

    opts['extractor_args'] = extractor_args

    if COOKIES_PATH and os.path.exists(COOKIES_PATH):
        opts['cookiefile'] = COOKIES_PATH

    return opts


@app.route('/download', methods=['POST'])
def download_audio():
    data = request.json
    video_url = data.get('url')

    if not video_url:
        return jsonify({'error': 'No URL provided'}), 400

    if not BUCKET_NAME:
        return jsonify({'error': 'BUCKET_NAME not configured'}), 500

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts = get_ydl_opts(tmpdir)
            ydl_opts['format'] = 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best'
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

            audio_files = glob.glob(f'{tmpdir}/{video_id}.mp3') or glob.glob(f'{tmpdir}/*.mp3')

            if not audio_files:
                return jsonify({'error': 'Audio extraction failed'}), 500

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

            return jsonify({
                'success': True,
                'video_id': video_id,
                'title': title,
                'channel': channel,
                'duration_seconds': duration,
                'file_size_bytes': file_size,
                'gcs_path': f'gs://{BUCKET_NAME}/audio/{video_id}.mp3',
                'url': f'https://storage.googleapis.com/{BUCKET_NAME}/audio/{video_id}.mp3'
            })

    except yt_dlp.utils.DownloadError as e:
        return jsonify({'error': f'Download failed: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/refresh-cookies', methods=['POST'])
def refresh_cookies():
    if download_cookies():
        return jsonify({'success': True, 'message': 'Cookies refreshed'})
    return jsonify({'success': False, 'message': 'No cookies file found'}), 404


@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'bucket_configured': bool(BUCKET_NAME),
        'proxy_configured': bool(PROXY_URL),
        'cookies_available': bool(COOKIES_PATH and os.path.exists(COOKIES_PATH))
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
