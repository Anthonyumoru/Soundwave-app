import os
import json
import struct
import zlib
import db
from urllib.parse import unquote
from datetime import datetime
from flask import (Flask, render_template, request, redirect, url_for,
                   send_from_directory, jsonify, session, flash, Response)
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-change-me")

MUSIC_FOLDER  = os.path.join(app.static_folder, "music")
AVATAR_FOLDER = os.path.join(app.static_folder, "avatars")
DATA_DIR      = os.path.join(os.path.dirname(__file__), "data")
METADATA_FILE = os.path.join(DATA_DIR, "metadata.json")
ALLOWED_EXT   = {"mp3"}
ALLOWED_IMG   = {"jpg", "jpeg", "png", "gif", "webp"}
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

PREMIUM_CODE = os.environ.get("PREMIUM_CODE", "SOUNDWAVE-PRO")
PAYMENT_URL  = os.environ.get("PAYMENT_URL", "https://buy.stripe.com/your-link")
PRO_PRICE    = os.environ.get("PRO_PRICE", "$4.99 / month")
APP_NAME     = os.environ.get("APP_NAME", "SoundWave")
SUPPORT_URL  = os.environ.get("SUPPORT_URL", "")

GENRES = ["Hip-Hop", "R&B", "Pop", "Electronic", "Afrobeats",
          "Gospel", "Drill", "Dancehall", "Latin", "Other"]

ART_PALETTE = ["#ff6b1a","#9c27b0","#e91e63","#00bcd4","#4caf50",
               "#f5c518","#ff5722","#3f51b5","#009688","#ff9800"]

os.makedirs(MUSIC_FOLDER, exist_ok=True)
os.makedirs(AVATAR_FOLDER, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
db.init_db()


# ── Jinja2 filters ────────────────────────────────────────────────────────────

@app.template_filter("art_color")
def art_color(name):
    h = 0
    for c in str(name or "?"):
        h = ord(c) + ((h << 5) - h)
    return ART_PALETTE[abs(h) % len(ART_PALETTE)]


# ── Session / context processor ───────────────────────────────────────────────

def current_user():
    uid = session.get("user_id")
    if uid:
        u = db.get_user_by_id(uid)
        if u:
            return dict(u)
    return None


@app.context_processor
def inject_globals():
    u = current_user()
    return {
        "current_user": u,
        "app_name":     APP_NAME,
        "support_url":  SUPPORT_URL,
        "payment_url":  PAYMENT_URL,
        "pro_price":    PRO_PRICE,
    }


# ── Metadata helpers ──────────────────────────────────────────────────────────

def load_meta():
    if os.path.exists(METADATA_FILE):
        try:
            with open(METADATA_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"tracks": {}, "artists": {}}


def save_meta(meta):
    meta.setdefault("artists", {})
    with open(METADATA_FILE, "w") as f:
        json.dump(meta, f, indent=2)


def track_meta(meta, filename):
    if filename not in meta["tracks"]:
        meta["tracks"][filename] = {
            "genre":       "Other",
            "artist":      "Unknown Artist",
            "username":    None,
            "play_count":  0,
            "upload_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "comments":    [],
        }
    return meta["tracks"][filename]


def allowed_file(fn, exts):
    return "." in fn and fn.rsplit(".", 1)[1].lower() in exts


def get_user_avatars():
    return {
        u["username"]: ("/static/avatars/" + u["avatar"]) if u["avatar"] else None
        for u in db.get_all_users()
    }


def get_songs_with_meta():
    meta  = load_meta()
    songs = []
    avatars = get_user_avatars()
    if os.path.exists(MUSIC_FOLDER):
        for fn in sorted(os.listdir(MUSIC_FOLDER)):
            if fn.lower().endswith(".mp3"):
                tm   = track_meta(meta, fn)
                name = (os.path.splitext(fn)[0]
                        .replace("-", " ").replace("_", " ").title())
                uname = tm.get("username") or tm.get("artist", "Unknown Artist")
                songs.append({
                    "filename":      fn,
                    "name":          name,
                    "genre":         tm.get("genre", "Other"),
                    "artist":        tm.get("artist", "Unknown Artist"),
                    "username":      uname,
                    "user_avatar":   avatars.get(uname),
                    "play_count":    tm.get("play_count", 0),
                    "upload_date":   tm.get("upload_date", ""),
                    "comment_count": len(tm.get("comments", [])),
                })
    return songs, meta


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/auth", methods=["GET", "POST"])
def auth():
    if current_user():
        return redirect(url_for("index"))

    if request.method == "POST":
        action = request.form.get("action")

        if action == "signin":
            identifier = request.form.get("identifier", "").strip()
            password   = request.form.get("password", "")
            user = db.get_user_by_username(identifier) or db.get_user_by_email(identifier)
            if user and db.check_password(user, password):
                session["user_id"]  = user["id"]
                session["username"] = user["username"]
                return redirect(url_for("index"))
            flash("Invalid username/email or password.", "signin_error")
            return redirect(url_for("auth") + "?tab=signin")

        if action == "signup":
            username = request.form.get("username", "").strip()
            email    = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirm  = request.form.get("confirm", "")
            if len(username) < 3:
                flash("Username must be at least 3 characters.", "signup_error")
                return redirect(url_for("auth") + "?tab=signup")
            if password != confirm:
                flash("Passwords do not match.", "signup_error")
                return redirect(url_for("auth") + "?tab=signup")
            if len(password) < 6:
                flash("Password must be at least 6 characters.", "signup_error")
                return redirect(url_for("auth") + "?tab=signup")
            if db.get_user_by_username(username):
                flash("Username already taken.", "signup_error")
                return redirect(url_for("auth") + "?tab=signup")
            if db.get_user_by_email(email):
                flash("Email already registered.", "signup_error")
                return redirect(url_for("auth") + "?tab=signup")
            try:
                db.create_user(username, email, password)
                user = db.get_user_by_username(username)
                session["user_id"]  = user["id"]
                session["username"] = user["username"]
                return redirect(url_for("index"))
            except Exception as e:
                flash("Registration failed. Please try again.", "signup_error")
                return redirect(url_for("auth") + "?tab=signup")

    return render_template("auth.html", tab=request.args.get("tab", "signin"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ── Profile routes ────────────────────────────────────────────────────────────

@app.route("/profile/<username>")
def profile(username):
    user = db.get_user_by_username(username)
    if not user:
        return redirect(url_for("index"))
    user = dict(user)

    songs, meta = get_songs_with_meta()
    user_songs  = [s for s in songs if (s["username"] or "").lower() == username.lower()]
    total_plays = sum(s["play_count"] for s in user_songs)
    followers   = db.get_followers_count(user["id"])
    following   = db.get_following_count(user["id"])

    cu = current_user()
    i_follow = False
    is_own   = False
    if cu:
        i_follow = db.is_following(cu["id"], user["id"])
        is_own   = cu["id"] == user["id"]

    # Discover other users
    all_users  = [dict(u) for u in db.get_all_users()
                  if u["username"].lower() != username.lower()][:8]
    activities = db.get_user_activities(user["id"])

    return render_template(
        "profile.html",
        profile_user=user,
        songs=user_songs,
        total_plays=total_plays,
        followers=followers,
        following=following,
        i_follow=i_follow,
        is_own=is_own,
        genres=GENRES,
        other_users=all_users,
        activities=activities,
    )


@app.route("/profile/edit", methods=["POST"])
def edit_profile():
    cu = current_user()
    if not cu:
        return redirect(url_for("auth"))
    bio = request.form.get("bio", "").strip()[:300]
    db.update_bio(cu["id"], bio)
    if "avatar" in request.files:
        f = request.files["avatar"]
        if f and f.filename and allowed_file(f.filename, ALLOWED_IMG):
            ext      = f.filename.rsplit(".", 1)[1].lower()
            filename = f"{cu['id']}.{ext}"
            f.save(os.path.join(AVATAR_FOLDER, filename))
            db.update_avatar(cu["id"], filename)
    return redirect(url_for("profile", username=cu["username"]))


# ── Follow API ────────────────────────────────────────────────────────────────

@app.route("/api/user/follow/<username>", methods=["POST"])
def follow_user_route(username):
    cu = current_user()
    if not cu:
        return jsonify({"error": "login required"}), 401
    target = db.get_user_by_username(username)
    if not target or target["id"] == cu["id"]:
        return jsonify({"error": "invalid"}), 400
    if db.is_following(cu["id"], target["id"]):
        db.unfollow_user(cu["id"], target["id"])
        following = False
    else:
        db.follow_user(cu["id"], target["id"])
        following = True
        db.record_activity(cu["id"], cu["username"], "follow",
                           {"target_username": target["username"]})
    return jsonify({"following": following, "followers": db.get_followers_count(target["id"])})


# ── Bio API ───────────────────────────────────────────────────────────────────

@app.route("/api/user/bio", methods=["POST"])
def update_bio_api():
    cu = current_user()
    if not cu:
        return jsonify({"error": "login required"}), 401
    data = request.get_json(silent=True) or {}
    bio  = (data.get("bio") or "").strip()[:300]
    db.update_bio(cu["id"], bio)
    return jsonify({"bio": bio})


# ── Main routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    songs, meta = get_songs_with_meta()
    trending    = sorted(songs, key=lambda s: s["play_count"], reverse=True)[:3]
    total_plays = sum(s["play_count"] for s in songs)
    all_users   = [dict(u) for u in db.get_all_users()]
    return render_template(
        "index.html",
        songs=songs,
        trending=trending,
        total_plays=total_plays,
        genres=GENRES,
        all_users=all_users,
    )


@app.route("/upload", methods=["POST"])
def upload():
    cu = current_user()
    if not cu:
        return redirect(url_for("auth"))
    if "file" not in request.files:
        return redirect(url_for("index"))
    file  = request.files["file"]
    genre = request.form.get("genre", "Other")
    if not file.filename or not allowed_file(file.filename, ALLOWED_EXT):
        return redirect(url_for("index"))
    filename = secure_filename(file.filename)
    file.save(os.path.join(MUSIC_FOLDER, filename))
    track_name = (os.path.splitext(filename)[0]
                  .replace("-", " ").replace("_", " ").title())
    meta = load_meta()
    tm   = track_meta(meta, filename)
    tm["genre"]       = genre if genre in GENRES else "Other"
    tm["artist"]      = cu["username"]
    tm["username"]    = cu["username"]
    tm["upload_date"] = datetime.utcnow().strftime("%Y-%m-%d")
    save_meta(meta)
    db.record_activity(cu["id"], cu["username"], "upload",
                       {"filename": filename, "track_name": track_name, "genre": tm["genre"]})
    return redirect(url_for("index"))


@app.route("/delete/<filename>", methods=["POST"])
def delete_song(filename):
    cu = current_user()
    filename = secure_filename(filename)
    filepath = os.path.join(MUSIC_FOLDER, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    meta = load_meta()
    meta["tracks"].pop(filename, None)
    save_meta(meta)
    return redirect(url_for("index"))


@app.route("/music/<path:filename>")
def serve_music(filename):
    return send_from_directory(MUSIC_FOLDER, filename)


@app.route("/download/<path:filename>")
def download_song(filename):
    return send_from_directory(MUSIC_FOLDER, secure_filename(filename),
                               as_attachment=True)


# ── JSON API ──────────────────────────────────────────────────────────────────

@app.route("/api/play/<filename>", methods=["POST"])
def record_play(filename):
    filename = secure_filename(filename)
    meta = load_meta()
    tm   = track_meta(meta, filename)
    tm["play_count"] = tm.get("play_count", 0) + 1
    save_meta(meta)
    return jsonify({"play_count": tm["play_count"]})


@app.route("/api/comments/<filename>", methods=["GET"])
def get_comments(filename):
    filename = secure_filename(filename)
    meta = load_meta()
    tm   = track_meta(meta, filename)
    return jsonify(tm.get("comments", []))


@app.route("/api/comments/<filename>", methods=["POST"])
def add_comment(filename):
    cu   = current_user()
    filename = secure_filename(filename)
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()[:300]
    name = cu["username"] if cu else (data.get("name") or "Anonymous").strip()[:40]
    if not text:
        return jsonify({"error": "empty"}), 400
    comment = {"name": name, "text": text,
               "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M")}
    meta = load_meta()
    tm   = track_meta(meta, filename)
    tm.setdefault("comments", []).append(comment)
    save_meta(meta)
    return jsonify(comment), 201


@app.route("/api/verify-code", methods=["POST"])
def verify_code():
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip().upper()
    if code == PREMIUM_CODE.strip().upper():
        return jsonify({"valid": True})
    return jsonify({"valid": False}), 400


# ── PWA Icon routes ───────────────────────────────────────────────────────────

def _make_png(size, r, g, b):
    """Generate a minimal solid-colour PNG without PIL."""
    def chunk(tag, data):
        raw = tag + data
        return struct.pack(">I", len(data)) + raw + struct.pack(">I", zlib.crc32(raw) & 0xFFFFFFFF)
    raw_rows = (b"\x00" + bytes([r, g, b] * size)) * size
    idat     = zlib.compress(raw_rows)
    png  = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", idat)
    png += chunk(b"IEND", b"")
    return png

@app.route("/icon.svg")
def icon_svg():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <rect width="100" height="100" rx="22" fill="#ff6b1a"/>
  <path d="M58 28v30.6c-2-1.1-4.3-1.8-6.8-1.8C44.7 56.8 40 61.5 40 67.4S44.7 78 51.2 78s11.2-4.7 11.2-10.6V38h8.4V28H58z" fill="white"/>
</svg>"""
    return Response(svg, mimetype="image/svg+xml")

@app.route("/icon-192.png")
def icon_192():
    return Response(_make_png(192, 255, 107, 26), mimetype="image/png")

@app.route("/icon-512.png")
def icon_512():
    return Response(_make_png(512, 255, 107, 26), mimetype="image/png")


# Keep old artist route for backward compat
@app.route("/artist/<path:artist_name>")
def artist_profile(artist_name):
    artist_name = unquote(artist_name).strip()
    user = db.get_user_by_username(artist_name)
    if user:
        return redirect(url_for("profile", username=user["username"]))
    return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
