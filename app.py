import os
import json
import re
import logging
import requests
from flask import Flask, render_template, request, redirect, url_for, session, send_from_directory, flash, jsonify, Response, stream_with_context, current_app, url_for
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from pytubefix import YouTube
import subprocess
from datetime import datetime, timedelta
import hashlib
import threading
import queue
import time
import stripe

app = Flask(__name__)
app.secret_key = b'_5#y2L"F4Q8z\n\xec]/'


# Add these to your app configuration
app.config['STRIPE_PUBLIC_KEY'] = 'pk_live_51RFAaECAmGynqUgHPCHCTjNbyXz6IobBPjQ6dRGWNvTCkLL1XX8jeZnAXtSY2XlUtD2W5xVhTz2h8uiH2bwDGuJ800vhyQxfmH'
app.config['STRIPE_SECRET_KEY'] = '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'

# Initialize Stripe
stripe.api_key = app.config['STRIPE_SECRET_KEY']

upload_progress = {}
upload_queue = queue.Queue()
progress_updates = []

# Directory setup
current_directory = os.path.dirname(os.path.abspath(__file__))
user_data_folder = os.path.join(current_directory, "user_data")
static_folder = os.path.join(current_directory, 'static')
logos_folder = os.path.join(static_folder, 'logos')
video_info_folder = os.path.join(static_folder, 'video_info')
uploads_folder = os.path.join(current_directory, 'uploads')
thumbnails_folder = os.path.join(static_folder, 'thumbnails')
avatars_folder = os.path.join(static_folder, 'avatars')

# Ensure folders exist
for folder in [user_data_folder, video_info_folder, uploads_folder, thumbnails_folder, avatars_folder]:
    os.makedirs(folder, exist_ok=True)

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def save_user_stripe_customer_id(username, customer_id):
    """Save Stripe customer ID with user data"""
    user_filepath = os.path.join(user_data_folder, f"{username}.json")
    if os.path.exists(user_filepath):
        # Read current user data
        with open(user_filepath, 'r') as file:
            user_data = json.load(file)
        
        # Update Stripe customer ID
        user_data['stripe_customer_id'] = customer_id
        
        # Save updated user data
        with open(user_filepath, 'w') as file:
            json.dump(user_data, file)
        
        return True
    return False

def get_user_by_stripe_customer_id(customer_id):
    """Find username by Stripe customer ID"""
    # List all user files
    user_files = [f for f in os.listdir(user_data_folder) if f.endswith('.json')]
    
    for user_file in user_files:
        user_filepath = os.path.join(user_data_folder, user_file)
        try:
            with open(user_filepath, 'r') as file:
                user_data = json.load(file)
                
                if user_data.get('stripe_customer_id') == customer_id:
                    return user_data['username']
        except:
            continue
    
    return None

def save_user_data(username, password, is_premium=False):
    """Save user data securely"""
    user_data = {
        "username": username, 
        "password": generate_password_hash(password),
        "created_at": datetime.now().isoformat(),
        "is_admin": (username == "Owner"),
        "is_premium": is_premium
    }
    with open(os.path.join(user_data_folder, f"{username}.json"), "w") as file:
        json.dump(user_data, file)
    return user_data

def get_user_data(username):
    """Get user data from file"""
    user_filepath = os.path.join(user_data_folder, f"{username}.json")
    if os.path.exists(user_filepath):
        with open(user_filepath, 'r') as file:
            return json.load(file)
    return None

def sanitize_video_id(video_id):
    """Sanitize video ID for safe filenames"""
    return re.sub(r'[^\w\s.-]', '', video_id).lower()

def get_video_info(video_id):
    """Get video information from file"""
    info_filepath = os.path.join(video_info_folder, f"{video_id}.json")
    if os.path.exists(info_filepath):
        with open(info_filepath, 'r') as file:
            return json.load(file)
    return None

def update_premium_status(username, is_premium):
    """Update a user's premium status"""
    users = load_users()
    if username in users:
        users[username]['is_premium'] = is_premium
        save_users(users)
        return True
    return False

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    if not session.get('logged_in'):
        flash("You need to be logged in to subscribe to premium.", "error")
        return redirect(url_for('login'))
    
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[
                {
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {
                            'name': 'BloodedTube Premium',
                            'description': 'Monthly subscription to BloodedTube Premium'
                        },
                        'unit_amount': 999,  # $9.99
                        'recurring': {
                            'interval': 'month',
                        }
                    },
                    'quantity': 1,
                },
            ],
            mode='subscription',
            success_url=url_for('payment_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('payment_cancel', _external=True),
            client_reference_id=session.get('username'),  # Pass the username for reference
        )
        
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        return str(e), 403

@app.route('/payment-success')
def payment_success():
    session_id = request.args.get('session_id')
    if not session_id:
        return redirect(url_for('home'))
    
    try:
        # Verify the checkout session
        checkout_session = stripe.checkout.Session.retrieve(session_id)
        
        # Get the username from the session
        username = checkout_session.client_reference_id
        
        # Update user's premium status
        if update_premium_status(username, True):
            if session.get('username') == username:
                session['is_premium'] = True
            
            flash("Thank you for subscribing to BloodedTube Premium!", "success")
        else:
            flash("Payment was successful, but we couldn't update your account status. Please contact support.", "error")
        
        return redirect(url_for('premium_panel'))
    except Exception as e:
        flash(f"Error verifying payment: {str(e)}", "error")
        return redirect(url_for('home'))

@app.route('/payment-cancel')
def payment_cancel():
    flash("Your premium subscription was canceled.", "info")
    return redirect(url_for('account_dashboard'))

# Stripe webhook for handling subscription events
@app.route('/webhook', methods=['POST'])
def webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, app.config['STRIPE_WEBHOOK_SECRET']
        )
    except ValueError as e:
        # Invalid payload
        return '', 400
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        return '', 400

    # Handle the event
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        fulfill_order(session)
    elif event['type'] == 'customer.subscription.deleted':
        subscription = event['data']['object']
        handle_subscription_canceled(subscription)

    return '', 200

def fulfill_order(checkout_session):
    # Get the client_reference_id (username) from the session
    username = checkout_session.get('client_reference_id')
    if username:
        update_premium_status(username, True)

def handle_subscription_canceled(subscription):
    # Get the customer ID
    customer_id = subscription.get('customer')
    
    # Look up the user associated with this customer ID
    # This requires you to store the customer ID with the user when they subscribe
    # For this example, let's assume you have a function to look up a user by Stripe customer ID
    username = get_user_by_stripe_customer_id(customer_id)
    
    if username:
        update_premium_status(username, False)

@app.route('/subscribe-premium', methods=['POST'])
def subscribe_premium():
    """Handle premium subscription requests"""
    if not session.get('logged_in'):
        flash("You need to be logged in to subscribe to premium.", "error")
        return redirect(url_for('login'))
    
    username = session.get('username')
    
    # In a real application, process payment here
    
    # Update user's premium status
    if update_premium_status(username, True):
        session['is_premium'] = True
        flash("You've successfully upgraded to BloodedTube Premium!", "success")
    else:
        flash("Failed to update subscription status.", "error")
        
    return redirect(url_for('account_dashboard'))

def get_all_videos(query=None):
    """Get all videos with optional search query"""
    videos = {}
    for filename in os.listdir(video_info_folder):
        if filename.endswith('.json'):
            try:
                video_id = os.path.splitext(filename)[0]
                video_info = get_video_info(video_id)
                if video_info:
                    if not query or query.lower() in video_info.get('title', '').lower():
                        videos[video_id] = video_info
            except Exception as e:
                logger.error(f"Failed to load video info from {filename}: {e}")
    return videos

def save_video_info(video_id, info):
    """Save video information to file"""
    with open(os.path.join(video_info_folder, f"{video_id}.json"), 'w') as file:
        json.dump(info, file)

# Routes
@app.route('/')
def root():
    return redirect(url_for('home'))

def download_yt_with_fallback_resolution(yt, output_path, filename):
    """
    Attempts to download a YouTube video in the best resolution available,
    prioritizing 1080p. Will not download 4K or 8K.
    
    Args:
        yt: YouTube object
        output_path: Directory to save the video
        filename: Filename for the downloaded video
    
    Returns:
        The path to the downloaded file, or None if download failed
    """
    # Define the resolution preferences in order (highest to lowest)
    preferred_resolutions = ['1080p', '720p', '480p', '360p', '240p', '144p']
    
    # First try to get 1080p specifically
    stream = yt.streams.filter(
        progressive=True,  # Progressive includes audio and video in one file
        file_extension='mp4',
        resolution='1080p'
    ).first()
    
    if stream:
        logger.info("Downloading video in 1080p")
        stream.download(output_path=output_path, filename=filename)
        return os.path.join(output_path, filename)
    
    # If 1080p not available, try other resolutions in order
    for resolution in preferred_resolutions[1:]:  # Skip 1080p as we already tried it
        stream = yt.streams.filter(
            progressive=True,
            file_extension='mp4',
            resolution=resolution
        ).first()
        
        if stream:
            logger.info(f"1080p not available. Downloading in {resolution}")
            stream.download(output_path=output_path, filename=filename)
            return os.path.join(output_path, filename)
    
    # If none of the specific resolutions worked, get the highest available
    # that is not higher than 1080p
    all_streams = yt.streams.filter(
        progressive=True,
        file_extension='mp4'
    ).all()
    
    # Sort streams by resolution (highest first)
    available_streams = []
    for s in all_streams:
        try:
            # Extract the numeric part of the resolution (e.g., '1080p' -> 1080)
            res_value = int(s.resolution[:-1]) if s.resolution else 0
            # Only consider resolutions up to 1080p
            if res_value <= 1080:
                available_streams.append((res_value, s))
        except (ValueError, TypeError, AttributeError):
            pass
    
    # Sort by resolution, highest first
    available_streams.sort(reverse=True)
    
    if available_streams:
        # Get the highest resolution stream that's not higher than 1080p
        best_stream = available_streams[0][1]
        logger.info(f"Downloading highest available resolution: {best_stream.resolution}")
        best_stream.download(output_path=output_path, filename=filename)
        return os.path.join(output_path, filename)
    
    # Last resort: try any progressive stream
    logger.warning("Could not find suitable resolution, trying any available stream")
    stream = yt.streams.filter(
        progressive=True,
        file_extension='mp4'
    ).first()
    
    if stream:
        logger.info(f"Downloading video in {stream.resolution}")
        stream.download(output_path=output_path, filename=filename)
        return os.path.join(output_path, filename)
    else:
        logger.error("No suitable stream found for download")
        return None

@app.route('/multiple-youtube-upload')
def multiple_youtube_upload():
    if 'logged_in' not in session or not session.get('is_admin'):
        flash("You must be logged in as an admin to access this page.", "error")
        return redirect(url_for('login'))
    return render_template('multiple_youtube_upload.html')

@app.route('/upload-multiple-youtube', methods=['POST'])
def upload_multiple_youtube():
    if 'logged_in' not in session or not session.get('is_admin'):
        return jsonify({"success": False, "message": "Not authorized"}), 401

    youtube_urls = request.form.getlist('youtube_urls[]')
    youtube_urls = [url for url in youtube_urls if url.strip()]

    if not youtube_urls:
        return jsonify({"success": False, "message": "No valid URLs provided"}), 400

    # Get the logged-in username (channel name)
    username = session.get('username', 'Unknown Channel')

    # Reset progress tracking
    global upload_progress, progress_updates
    upload_progress = {}
    progress_updates = []

    for i, url in enumerate(youtube_urls):
        upload_progress[i] = {
            "url": url,
            "progress": 0,
            "status": "queued",
            "title": f"Video {i+1}",
            "error": None
        }
        upload_queue.put((i, url))

    # Start worker threads with username passed in
    max_workers = min(3, len(youtube_urls))
    for _ in range(max_workers):
        t = threading.Thread(target=download_worker, args=(username,))
        t.daemon = True
        t.start()

    return jsonify({
        "success": True,
        "message": f"Processing {len(youtube_urls)} videos",
        "redirect": url_for('home')
    })

def update_premium_status(username, is_premium=True):
    """Update a user's premium status"""
    user_filepath = os.path.join(user_data_folder, f"{username}.json")
    if os.path.exists(user_filepath):
        # Read current user data
        with open(user_filepath, 'r') as file:
            user_data = json.load(file)
        
        # Update premium status
        user_data['is_premium'] = is_premium
        
        # Save updated user data
        with open(user_filepath, 'w') as file:
            json.dump(user_data, file)
        
        return True
    return False

@app.route('/premium-panel')
def premium_panel():
    """Route for the premium features panel"""
    if not session.get('logged_in'):
        flash('You need to log in first.', 'error')
        return redirect(url_for('login'))
    
    if not session.get('is_premium'):
        flash('You need a premium subscription to access this feature.', 'error')
        return redirect(url_for('home'))
    
    # If user is premium, render the premium panel
    return render_template('premium_panel.html')

def download_worker(username):
    while not upload_queue.empty():
        try:
            index, url = upload_queue.get()

            # Update status
            upload_progress[index]["status"] = "downloading"
            progress_updates.append({
                "type": "progress",
                "index": index,
                "progress": 0,
                "title": upload_progress[index]["title"]
            })

            try:
                # Process YouTube URL
                yt = YouTube(url, on_progress_callback=lambda stream, chunk, bytes_remaining: update_progress(
                    index, stream, chunk, bytes_remaining
                ))

                # Update with video title
                upload_progress[index]["title"] = yt.title.replace('#', '')
                progress_updates.append({
                    "type": "progress",
                    "index": index,
                    "progress": 0,
                    "title": upload_progress[index]["title"]
                })

                # Get video ID and prepare filenames
                video_id = sanitize_video_id(yt.video_id)
                video_filename = f"{video_id}.mp4"
                thumbnail_filename = f"{video_id}.jpg"

                # Download video
                video_path = download_yt_with_fallback_resolution(yt, uploads_folder, video_filename)

                if not video_path:
                    raise Exception("Failed to download video at any resolution")

                # Download thumbnail
                thumb_url = yt.thumbnail_url
                try:
                    thumb_resp = requests.get(thumb_url)
                    thumb_resp.raise_for_status()
                    with open(os.path.join(thumbnails_folder, thumbnail_filename), 'wb') as f:
                        f.write(thumb_resp.content)
                except Exception as e:
                    logger.error(f"Thumbnail download failed: {e}")
                    default_thumb = os.path.join(static_folder, 'default_thumbnail.jpg')
                    if os.path.exists(default_thumb):
                        with open(os.path.join(thumbnails_folder, thumbnail_filename), 'wb') as f:
                            with open(default_thumb, 'rb') as df:
                                f.write(df.read())

                # Get video duration
                duration = get_video_duration(video_path)

                # Count subscribers
                subscribers_count = 0
                for filename in os.listdir(user_data_folder):
                    if filename.endswith('.json'):
                        try:
                            with open(os.path.join(user_data_folder, filename), 'r') as file:
                                user_info = json.load(file)
                                if 'subscriptions' in user_info and username in user_info['subscriptions']:
                                    subscribers_count += 1
                        except Exception as e:
                            logger.error(f"Error reading user data: {e}")

                # Save metadata
                video_info = {
                    'title': upload_progress[index]["title"],
                    'description': yt.description or "No description available.",
                    'views': "0",
                    'likes': "0",
                    'dislikes': "0",
                    'date': datetime.now().strftime("%b %d, %Y"),
                    'age': "Just now",
                    'channel': username,
                    'subscribers': str(subscribers_count),
                    'comments_count': "0",
                    'duration': duration,
                    'uploaded_by': username,
                    'views_by': {},
                    'likes_by': [],
                    'dislikes_by': []
                }
                save_video_info(video_id, video_info)

                # Update status
                upload_progress[index]["status"] = "complete"
                upload_progress[index]["progress"] = 100
                progress_updates.append({
                    "type": "complete",
                    "index": index,
                    "title": upload_progress[index]["title"]
                })

            except Exception as e:
                logger.error(f"YouTube download failed for index {index}: {e}")
                upload_progress[index]["status"] = "error"
                upload_progress[index]["error"] = str(e)
                progress_updates.append({
                    "type": "error",
                    "index": index,
                    "error": str(e)
                })

            upload_queue.task_done()

        except Exception as e:
            logger.error(f"Worker thread error: {e}")

    if upload_queue.empty() and all(p["status"] in ["complete", "error"] for p in upload_progress.values()):
        home_url = "/home"
        progress_updates.append({
            "type": "all_complete",
            "redirect": home_url
        })

# Progress callback function for YouTube downloads
def update_progress(index, stream, chunk, bytes_remaining):
    if stream.filesize is None:
        return
    
    bytes_downloaded = stream.filesize - bytes_remaining
    progress = int(bytes_downloaded / stream.filesize * 100)
    
    # Only update if progress has changed significantly
    if abs(progress - upload_progress[index]["progress"]) >= 5:
        upload_progress[index]["progress"] = progress
        progress_updates.append({
            "type": "progress",
            "index": index,
            "progress": progress,
            "title": upload_progress[index]["title"]
        })

# Add route for progress updates using server-sent events
@app.route('/upload-progress')
def upload_progress_stream():
    def generate():
        last_id = 0
        while True:
            # Check for new updates
            if last_id < len(progress_updates):
                for i in range(last_id, len(progress_updates)):
                    yield f"data: {json.dumps(progress_updates[i])}\n\n"
                last_id = len(progress_updates)
                
            # Check if all downloads are complete
            if all(p["status"] in ["complete", "error"] for p in upload_progress.values()) and not upload_queue.empty():
                yield f"data: {json.dumps({'type': 'all_complete', 'redirect': url_for('home')})}\n\n"  # Changed from dashboard to home
                break
                
            time.sleep(0.5)
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

# Add this route to the Flask app to ensure the template is found
@app.route('/template/multiple_youtube_upload.html')
def multiple_youtube_upload_template():
    return send_from_directory('templates', 'multiple_youtube_upload.html')

@app.route('/home')
def home():
    query = request.args.get('query')
    videos = get_all_videos(query)
    return render_template('home.html', videos=videos)

# Add this to the imports at the top
import subprocess
from datetime import datetime, timedelta

# Function to get video duration using ffprobe
def get_video_duration(video_path):
    try:
        # Use ffprobe to get video duration in seconds
        cmd = [
            'ffprobe', 
            '-v', 'error', 
            '-show_entries', 'format=duration', 
            '-of', 'default=noprint_wrappers=1:nokey=1', 
            video_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        duration_seconds = float(result.stdout.strip())
        
        # Convert to minutes:seconds format
        minutes = int(duration_seconds // 60)
        seconds = int(duration_seconds % 60)
        return f"{minutes}:{seconds:02d}"
    except Exception as e:
        logger.error(f"Failed to get video duration: {e}")
        return "0:00"  # Default duration if extraction fails

# Update the upload route to get video duration and set channel name
@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if 'logged_in' not in session:
        flash("You must be logged in to upload videos.", "error")
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        video = request.files.get('video')
        thumbnail = request.files.get('thumbnail')
        title = request.form.get('title', '').replace('#', '')
        description = request.form.get('description', 'No description available.')

        if not video or not title:
            flash("Video and title are required.", "error")
            return redirect(url_for('upload'))

        # Generate unique ID and sanitize filename
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        video_filename = secure_filename(video.filename)
        video_id = sanitize_video_id(f"{timestamp}-{os.path.splitext(video_filename)[0]}")
        
        # Save video file
        video_path = os.path.join(uploads_folder, f"{video_id}.mp4")
        video.save(video_path)
        
        # Handle thumbnail
        if thumbnail and thumbnail.filename:
            thumbnail_extension = os.path.splitext(thumbnail.filename)[1].lower()
            if thumbnail_extension in ['.jpg', '.jpeg', '.png']:
                thumbnail_filename = f"{video_id}.jpg"
                thumbnail.save(os.path.join(thumbnails_folder, thumbnail_filename))
            else:
                flash("Invalid thumbnail format. Using default thumbnail.", "warning")
        
        # Get video duration
        duration = get_video_duration(video_path)
        
        # Get channel name from user session
        username = session.get('username', "Unknown Channel")
        
        # Get subscriber count for this channel
        subscribers_count = 0
        for filename in os.listdir(user_data_folder):
            if filename.endswith('.json'):
                try:
                    with open(os.path.join(user_data_folder, filename), 'r') as file:
                        user_info = json.load(file)
                        if 'subscriptions' in user_info and username in user_info['subscriptions']:
                            subscribers_count += 1
                except Exception as e:
                    logger.error(f"Error reading user data: {e}")
        
        # Save video info
        video_info = {
            'title': title,
            'description': description,
            'views': "0",
            'likes': "0",
            'dislikes': "0",
            'date': datetime.now().strftime("%b %d, %Y"),
            'age': "Just now",
            'channel': username,
            'subscribers': str(subscribers_count),
            'comments_count': "0",
            'duration': duration,
            'uploaded_by': username,
            'views_by': {},  # To track unique viewers
            'likes_by': [],  # To track who liked
            'dislikes_by': []  # To track who disliked
        }
        save_video_info(video_id, video_info)

        flash("Video uploaded successfully!", "success")
        return redirect(url_for('home'))  # Changed from dashboard to home
        
    return render_template('upload.html')

@app.route('/admin-upload', methods=['GET', 'POST'])
def admin_upload():
    if 'logged_in' not in session or not session.get('is_admin', False):
        flash("You must be logged in as an admin to access this page.", "error")
        return redirect(url_for('login'))

    if request.method == 'POST':
        youtube_url = request.form.get('youtube_url')
        if not youtube_url:
            flash("You must provide a YouTube URL.", "error")
            return redirect(url_for('admin_upload'))

        try:
            yt = YouTube(youtube_url)
            title = yt.title.replace('#', '')
            video_id = sanitize_video_id(yt.video_id)
            video_filename = f"{video_id}.mp4"
            thumbnail_filename = f"{video_id}.jpg"

            # Download video with resolution fallback
            video_path = download_yt_with_fallback_resolution(yt, uploads_folder, video_filename)
            
            if not video_path:
                raise Exception("Failed to download video at any resolution")
            
            # Download thumbnail
            thumb_url = yt.thumbnail_url
            try:
                thumb_resp = requests.get(thumb_url)
                thumb_resp.raise_for_status()
                with open(os.path.join(thumbnails_folder, thumbnail_filename), 'wb') as f:
                    f.write(thumb_resp.content)
            except Exception as e:
                logger.error(f"Thumbnail download failed: {e}")
                # Use a default thumbnail if download fails
                default_thumb = os.path.join(static_folder, 'default_thumbnail.jpg')
                if os.path.exists(default_thumb):
                    with open(os.path.join(thumbnails_folder, thumbnail_filename), 'wb') as f:
                        with open(default_thumb, 'rb') as df:
                            f.write(df.read())

            # Get video duration
            duration = get_video_duration(video_path)
            
            # Get channel name from user session
            username = session.get('username', "Unknown Channel")
            
            # Get subscriber count for this channel
            subscribers_count = 0
            for filename in os.listdir(user_data_folder):
                if filename.endswith('.json'):
                    try:
                        with open(os.path.join(user_data_folder, filename), 'r') as file:
                            user_info = json.load(file)
                            if 'subscriptions' in user_info and username in user_info['subscriptions']:
                                subscribers_count += 1
                    except Exception as e:
                        logger.error(f"Error reading user data: {e}")
            
            # Save metadata with more details
            video_info = {
                'title': title,
                'description': yt.description or "No description available.",
                'views': "0",
                'likes': "0",
                'dislikes': "0",
                'date': datetime.now().strftime("%b %d, %Y"),
                'age': "Just now",
                'channel': username,
                'subscribers': str(subscribers_count),
                'comments_count': "0",
                'duration': duration,
                'uploaded_by': username,
                'views_by': {},  # To track unique viewers
                'likes_by': [],  # To track who liked
                'dislikes_by': []  # To track who disliked
            }
            save_video_info(video_id, video_info)

            flash("Video uploaded successfully!", "success")
            return redirect(url_for('home'))

        except Exception as e:
            logger.error(f"YouTube download failed: {e}")
            flash(f"Failed to download YouTube video: {e}", "error")
            return redirect(url_for('admin_upload'))

    return render_template("admin_upload.html")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        user_data = get_user_data(username)
        if user_data and check_password_hash(user_data['password'], password):
            session['logged_in'] = True
            session['username'] = username
            session['is_admin'] = user_data.get('is_admin', False)
            session['is_premium'] = user_data.get('is_premium', False)  # Add premium status to session
            flash("Login successful!", "success")
            return redirect(url_for('home'))
        else:
            flash("Invalid credentials. Please try again.", "error")
    return render_template('login.html')

@app.route('/account', methods=['GET', 'POST'])
def account_dashboard():
    if 'logged_in' not in session:
        flash("You must be logged in to access your account settings.", "error")
        return redirect(url_for('login'))
        
    username = session.get('username')
    user_data = get_user_data(username)
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'update_profile':
            display_name = request.form.get('display_name')
            handle = request.form.get('handle', '').lower()
            
            # Validate handle (alphanumeric and underscores only)
            if not re.match(r'^[a-z0-9_]+$', handle):
                flash("Handle can only contain lowercase letters, numbers, and underscores.", "error")
                return redirect(url_for('account_dashboard'))
            
            # Check if handle already exists (except for current user)
            for filename in os.listdir(user_data_folder):
                if filename.endswith('.json') and filename != f"{username}.json":
                    with open(os.path.join(user_data_folder, filename), 'r') as file:
                        other_user = json.load(file)
                        if other_user.get('handle') == handle:
                            flash("This handle is already taken. Please choose another.", "error")
                            return redirect(url_for('account_dashboard'))
            
            # Update user data
            user_data['display_name'] = display_name
            user_data['handle'] = handle
            
            # Save profile picture if uploaded
            if 'profile_picture' in request.files:
                profile_pic = request.files['profile_picture']
                if profile_pic and profile_pic.filename:
                    # Secure the filename and ensure it's an image
                    filename = secure_filename(profile_pic.filename)
                    if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                        # Save with username as filename
                        avatar_path = os.path.join(avatars_folder, f"{username}.jpg")
                        profile_pic.save(avatar_path)
                        user_data['has_avatar'] = True
                    else:
                        flash("Invalid image format. Please use JPG, JPEG or PNG.", "error")
            
            # Save updated user data
            with open(os.path.join(user_data_folder, f"{username}.json"), 'w') as file:
                json.dump(user_data, file)
                
            flash("Profile updated successfully!", "success")
            return redirect(url_for('account_dashboard'))
            
        elif action == 'delete_account':
            password = request.form.get('password')
            
            # Verify password
            if not check_password_hash(user_data['password'], password):
                flash("Incorrect password. Account deletion cancelled.", "error")
                return redirect(url_for('account_dashboard'))
                
            try:
                # Delete user data
                user_filepath = os.path.join(user_data_folder, f"{username}.json")
                if os.path.exists(user_filepath):
                    os.remove(user_filepath)
                
                # Delete avatar if exists
                avatar_path = os.path.join(avatars_folder, f"{username}.jpg")
                if os.path.exists(avatar_path):
                    os.remove(avatar_path)
                
                # Log out
                session.clear()
                flash("Your account has been deleted.", "info")
                return redirect(url_for('home'))
            except Exception as e:
                logger.error(f"Failed to delete account: {e}")
                flash("An error occurred while deleting your account.", "error")
    
    # Get subscribers
    subscribers = []
    for filename in os.listdir(user_data_folder):
        if filename.endswith('.json'):
            try:
                with open(os.path.join(user_data_folder, filename), 'r') as file:
                    sub_user = json.load(file)
                    if 'subscriptions' in sub_user and username in sub_user['subscriptions']:
                        subscribers.append(sub_user['username'])
            except Exception as e:
                logger.error(f"Error reading subscriber data: {e}")
    
    # Get user videos
    user_videos = {}
    all_videos = get_all_videos()
    for video_id, info in all_videos.items():
        if info.get('uploaded_by') == username:
            user_videos[video_id] = info
            
    return render_template('account_dashboard.html', 
                          user_data=user_data, 
                          subscribers=subscribers,
                          videos=user_videos)

@app.route('/signup', methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        confirm_password = request.form.get("confirm_password")
        
        # Validation
        if get_user_data(username):
            flash("Username already exists. Please choose another.", "error")
            return redirect(url_for("signup"))
            
        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return redirect(url_for("signup"))
            
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return redirect(url_for("signup"))
            
        # Save user and log them in
        save_user_data(username, password)
        session['logged_in'] = True
        session['username'] = username
        session['is_admin'] = (username == "Owner")
        session['is_premium'] = False  # Default to non-premium for new users
        
        flash("Account created successfully!", "success")
        return redirect(url_for("home"))
        
    return render_template("signup.html")

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for('home'))

@app.route('/dashboard')
def dashboard():
    if 'logged_in' not in session:
        flash("You must be logged in to access the dashboard.", "error")
        return redirect(url_for('login'))
        
    # Get user's videos
    username = session.get('username')
    user_videos = {}
    all_videos = get_all_videos()
    
    for video_id, info in all_videos.items():
        if info.get('uploaded_by') == username:
            user_videos[video_id] = info
            
    return render_template('dashboard.html', videos=user_videos, username=username)

@app.route('/download/<video_id>')
def download_video(video_id):
    """Allow users to download the video"""
    video_path = os.path.join(uploads_folder, f"{video_id}.mp4")
    if not os.path.exists(video_path):
        flash("Video not found.", "error")
        return redirect(url_for('home'))
    
    # Get the video info for the filename
    video_info = get_video_info(video_id)
    filename = f"{video_info.get('title', 'video')}.mp4"
    
    # Send the file as an attachment
    return send_file(video_path, as_attachment=True, download_name=filename)

@app.route('/dislike-video/<video_id>', methods=['POST'])
def dislike_video(video_id):
    """Handle dislike action"""
    if 'logged_in' not in session:
        return jsonify({"success": False, "message": "Not logged in"}), 401
        
    video_info = get_video_info(video_id)
    if not video_info:
        return jsonify({"success": False, "message": "Video not found"}), 404
        
    try:
        # Track who disliked the video
        if 'dislikes_by' not in video_info:
            video_info['dislikes_by'] = []
            
        username = session.get('username')
        
        # Check if user already disliked
        if username in video_info['dislikes_by']:
            # User already disliked, so remove the dislike
            video_info['dislikes_by'].remove(username)
            disliked = False
        else:
            # Add user to dislikes
            video_info['dislikes_by'].append(username)
            disliked = True
            
            # If user previously liked, remove the like
            if 'likes_by' in video_info and username in video_info['likes_by']:
                video_info['likes_by'].remove(username)
                video_info['likes'] = str(len(video_info['likes_by']))
                
        # Update dislike count
        video_info['dislikes'] = str(len(video_info['dislikes_by']))
        
        save_video_info(video_id, video_info)
        return jsonify({
            "success": True, 
            "dislikes": video_info['dislikes'],
            "disliked": disliked
        })
    except Exception as e:
        logger.error(f"Failed to dislike video: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/subscribe/<channel_name>', methods=['POST'])
def subscribe_to_channel(channel_name):
    """Handle subscribe/unsubscribe actions"""
    if 'logged_in' not in session:
        return jsonify({"success": False, "message": "Not logged in"}), 401
    
    try:
        # Get user data
        username = session.get('username')
        user_filepath = os.path.join(user_data_folder, f"{username}.json")
        
        if os.path.exists(user_filepath):
            with open(user_filepath, 'r') as file:
                user_data = json.load(file)
        else:
            user_data = {
                "username": username,
                "subscriptions": []
            }
        
        # Initialize subscriptions list if it doesn't exist
        if 'subscriptions' not in user_data:
            user_data['subscriptions'] = []
            
        # Check if user is already subscribed
        is_subscribed = channel_name in user_data['subscriptions']
        
        if is_subscribed:
            # Unsubscribe
            user_data['subscriptions'].remove(channel_name)
            is_subscribed = False
        else:
            # Subscribe
            user_data['subscriptions'].append(channel_name)
            is_subscribed = True
            
        # Save user data
        with open(user_filepath, 'w') as file:
            json.dump(user_data, file)
            
        # Update channel subscriber count
        # Get all videos by this channel
        all_videos = get_all_videos()
        subscribers_count = 0
        
        # Count how many unique users are subscribed to this channel
        for filename in os.listdir(user_data_folder):
            if filename.endswith('.json'):
                try:
                    with open(os.path.join(user_data_folder, filename), 'r') as file:
                        user_info = json.load(file)
                        if 'subscriptions' in user_info and channel_name in user_info['subscriptions']:
                            subscribers_count += 1
                except Exception as e:
                    logger.error(f"Error reading user data: {e}")
        
        # Update subscriber count in all videos by this channel
        for video_id, info in all_videos.items():
            if info.get('channel') == channel_name:
                info['subscribers'] = str(subscribers_count)
                save_video_info(video_id, info)
        
        return jsonify({
            "success": True,
            "isSubscribed": is_subscribed,
            "subscribers": subscribers_count
        })
    except Exception as e:
        logger.error(f"Failed to subscribe: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

# Update the like video route to track who liked it
@app.route('/like-video/<video_id>', methods=['POST'])
def like_video(video_id):
    if 'logged_in' not in session:
        return jsonify({"success": False, "message": "Not logged in"}), 401
        
    video_info = get_video_info(video_id)
    if not video_info:
        return jsonify({"success": False, "message": "Video not found"}), 404
        
    try:
        # Track who liked the video
        if 'likes_by' not in video_info:
            video_info['likes_by'] = []
            
        username = session.get('username')
        
        # Check if user already liked
        if username in video_info['likes_by']:
            # User already liked, so remove the like
            video_info['likes_by'].remove(username)
            liked = False
        else:
            # Add user to likes
            video_info['likes_by'].append(username)
            liked = True
            
            # If user previously disliked, remove the dislike
            if 'dislikes_by' in video_info and username in video_info['dislikes_by']:
                video_info['dislikes_by'].remove(username)
                video_info['dislikes'] = str(len(video_info['dislikes_by']))
                
        # Update like count
        video_info['likes'] = str(len(video_info['likes_by']))
        
        save_video_info(video_id, video_info)
        return jsonify({
            "success": True, 
            "likes": video_info['likes'],
            "liked": liked
        })
    except Exception as e:
        logger.error(f"Failed to like video: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

# Update the watch page route to prevent view spamming
@app.route('/watch/<video_id>')
def watch_page(video_id):
    video_info = get_video_info(video_id)
    if not video_info:
        flash("Video not found.", "error")
        return redirect(url_for('home'))
        
    # Anti-spam view counting
    # Use a combination of session ID and IP to identify viewers
    viewer_id = hashlib.md5((session.get('username', '') + request.remote_addr).encode()).hexdigest()
    
    # Initialize or get view history
    if 'views_by' not in video_info:
        video_info['views_by'] = {}
    
    current_time = datetime.now().isoformat()
    
    # Check if this viewer has viewed this video recently
    if viewer_id in video_info['views_by']:
        last_view_time = datetime.fromisoformat(video_info['views_by'][viewer_id])
        time_since_last_view = datetime.now() - last_view_time
        
        # Only count a new view if it's been more than 30 minutes
        if time_since_last_view > timedelta(minutes=30):
            video_info['views_by'][viewer_id] = current_time
            current_views = int(video_info.get('views', 0))
            video_info['views'] = str(current_views + 1)
    else:
        # First time viewing
        video_info['views_by'][viewer_id] = current_time
        current_views = int(video_info.get('views', 0))
        video_info['views'] = str(current_views + 1)
    
    # Clean up old view records (older than 7 days)
    cleanup_time = datetime.now() - timedelta(days=7)
    for viewer, view_time in list(video_info['views_by'].items()):
        if datetime.fromisoformat(view_time) < cleanup_time:
            del video_info['views_by'][viewer]
    
    save_video_info(video_id, video_info)
    
    # Get subscribed status if user is logged in
    is_subscribed = False
    if 'logged_in' in session:
        username = session.get('username')
        user_data = get_user_data(username)
        if user_data and 'subscriptions' in user_data:
            is_subscribed = video_info.get('channel', '') in user_data['subscriptions']
    
    # Get recommended videos
    recommended_videos = get_all_videos()
    
    # Get comments if they exist
    comments = video_info.get('comments', [])
    
    return render_template('watch.html', 
                          video_id=video_id, 
                          video_title=video_info.get('title', 'Unknown Video'),
                          video_info=video_info,
                          videos=recommended_videos,
                          is_subscribed=is_subscribed,
                          comments=comments)

@app.route('/video/<video_id>')
def watch_video(video_id):
    """Stream the video file"""
    video_path = os.path.join(uploads_folder, f"{video_id}.mp4")
    if not os.path.exists(video_path):
        return "Video not found", 404
        
    def generate():
        with open(video_path, 'rb') as video_file:
            data = video_file.read(1024 * 1024)  # Read 1MB at a time
            while data:
                yield data
                data = video_file.read(1024 * 1024)
                
    return Response(generate(), mimetype='video/mp4')

@app.route('/search')
def search():
    query = request.args.get('query')
    return redirect(url_for('home', query=query)) if query else redirect(url_for('home'))

@app.route('/@<handle>')
def user_profile(handle):
    # Find user by handle
    user_data = None
    username = None
    
    for filename in os.listdir(user_data_folder):
        if filename.endswith('.json'):
            with open(os.path.join(user_data_folder, filename), 'r') as file:
                user_info = json.load(file)
                if user_info.get('handle') == handle:
                    user_data = user_info
                    username = user_info['username']
                    break
    
    if not user_data:
        flash("User not found.", "error")
        return redirect(url_for('home'))
    
    # Get user videos
    user_videos = {}
    all_videos = get_all_videos()
    for video_id, info in all_videos.items():
        if info.get('uploaded_by') == username:
            user_videos[video_id] = info
    
    # Check if current user is subscribed to this user
    is_subscribed = False
    if 'logged_in' in session:
        current_user = get_user_data(session['username'])
        if current_user and 'subscriptions' in current_user:
            is_subscribed = username in current_user['subscriptions']
    
    # Get subscriber count
    subscribers_count = 0
    for filename in os.listdir(user_data_folder):
        if filename.endswith('.json'):
            try:
                with open(os.path.join(user_data_folder, filename), 'r') as file:
                    sub_user = json.load(file)
                    if 'subscriptions' in sub_user and username in sub_user['subscriptions']:
                        subscribers_count += 1
            except Exception as e:
                logger.error(f"Error reading subscriber data: {e}")
    
    return render_template('user_profile.html', 
                          user_data=user_data,
                          videos=user_videos,
                          is_subscribed=is_subscribed,
                          subscribers_count=subscribers_count)

@app.route('/avatar/<username>')
def get_avatar(username):
    # Check if user has a custom avatar
    user_data = get_user_data(username)
    if user_data and user_data.get('has_avatar', False):
        avatar_path = os.path.join(avatars_folder, f"{username}.jpg")
        if os.path.exists(avatar_path):
            return send_file(avatar_path)
    
    # Return default avatar
    default_avatar = os.path.join(static_folder, 'default_avatar.jpg')
    if os.path.exists(default_avatar):
        return send_file(default_avatar)
    else:
        # If even the default avatar doesn't exist, return a 404
        return "Avatar not found", 404

@app.route('/delete-video/<video_id>', methods=['POST'])
def delete_video(video_id):
    if 'logged_in' not in session:
        return jsonify({"success": False, "message": "Not logged in"}), 401
        
    video_info = get_video_info(video_id)
    if not video_info:
        return jsonify({"success": False, "message": "Video not found"}), 404
        
    # Check if user owns the video or is admin
    if video_info.get('uploaded_by') != session.get('username') and not session.get('is_admin'):
        return jsonify({"success": False, "message": "Permission denied"}), 403
        
    try:
        # Delete video file
        video_path = os.path.join(uploads_folder, f"{video_id}.mp4")
        if os.path.exists(video_path):
            os.remove(video_path)
            
        # Delete thumbnail
        thumb_path = os.path.join(thumbnails_folder, f"{video_id}.jpg")
        if os.path.exists(thumb_path):
            os.remove(thumb_path)
            
        # Delete info file
        info_path = os.path.join(video_info_folder, f"{video_id}.json")
        if os.path.exists(info_path):
            os.remove(info_path)
            
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Failed to delete video: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/add-comment/<video_id>', methods=['POST'])
def add_comment(video_id):
    if 'logged_in' not in session:
        return jsonify({"success": False, "message": "Not logged in"}), 401
        
    video_info = get_video_info(video_id)
    if not video_info:
        return jsonify({"success": False, "message": "Video not found"}), 404
        
    comment_text = request.form.get('comment')
    if not comment_text:
        return jsonify({"success": False, "message": "Comment text required"}), 400
        
    try:
        # Initialize comments if not present
        if 'comments' not in video_info:
            video_info['comments'] = []
            
        # Add comment
        comment = {
            'id': len(video_info['comments']) + 1,
            'user': session.get('username'),
            'text': comment_text,
            'timestamp': datetime.now().isoformat(),
            'likes': 0
        }
        video_info['comments'].append(comment)
        
        # Update comment count
        video_info['comments_count'] = str(len(video_info['comments']))
        
        save_video_info(video_id, video_info)
        return jsonify({"success": True, "comment": comment})
    except Exception as e:
        logger.error(f"Failed to add comment: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

if __name__ == "__main__":
    # Create default admin account if it doesn't exist
    if not get_user_data("Owner"):
        save_user_data("Owner", "admin1234")
        logger.info("Created default admin account (Owner:admin1234)")
        
    # Start the application
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host='0.0.0.0', port=port)