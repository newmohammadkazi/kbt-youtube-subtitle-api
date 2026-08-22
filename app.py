import re
import html
import sys
import subprocess
import tempfile

from pathlib import Path
from io import BytesIO

from flask import (
    Flask,
    request,
    jsonify,
    send_file
)

from flask_cors import CORS


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

CORS(app)


# ============================================================
# YES / NO VALUE
# ============================================================

def get_bool(value, default=False):

    if value is None:
        return default

    value = str(
        value
    ).strip().lower()

    return value in [
        "1",
        "true",
        "yes",
        "y",
        "on"
    ]


# ============================================================
# CLEAN CAPTION TEXT
# ============================================================

def clean_caption_text(text):

    text = re.sub(
        r"<[^>]+>",
        "",
        text
    )

    text = html.unescape(
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# CLEAN TIMING
# ============================================================

def clean_timing(line):

    timing_match = re.match(
        r"^(\d{2}:\d{2}:\d{2}\.\d{3})"
        r"\s+-->\s+"
        r"(\d{2}:\d{2}:\d{2}\.\d{3})",
        line
    )

    if timing_match:

        return (
            timing_match.group(1)
            + " --> "
            + timing_match.group(2)
        )

    if "-->" in line:

        parts = line.split()

        if len(parts) >= 3:

            return (
                parts[0]
                + " --> "
                + parts[2]
            )

    return line.strip()


# ============================================================
# PARSE VTT
# ============================================================

def parse_vtt(content):

    lines = content.splitlines()

    cues = []

    current_timing = None
    current_lines = []


    def flush():

        nonlocal current_timing
        nonlocal current_lines

        if not current_lines:
            return

        text = clean_caption_text(
            " ".join(
                current_lines
            )
        )

        if text:

            cues.append({
                "timing": current_timing,
                "text": text
            })

        current_timing = None
        current_lines = []


    for raw_line in lines:

        line = raw_line.strip()


        # ====================================================
        # EMPTY LINE
        # ====================================================

        if not line:

            if current_lines:
                flush()

            continue


        # ====================================================
        # VTT HEADERS
        # ====================================================

        if line == "WEBVTT":
            continue

        if line.startswith(
            "Kind:"
        ):
            continue

        if line.startswith(
            "Language:"
        ):
            continue

        if line.startswith(
            "NOTE"
        ):
            continue


        # ====================================================
        # TIMING
        # ====================================================

        if "-->" in line:

            if current_lines:
                flush()

            current_timing = clean_timing(
                line
            )

            continue


        # ====================================================
        # SEQUENCE NUMBER
        # ====================================================

        if (
            not current_lines
            and line.isdigit()
        ):
            continue


        # ====================================================
        # TEXT
        # ====================================================

        current_lines.append(
            line
        )


    if current_lines:
        flush()


    return cues


# ============================================================
# REMOVE YOUTUBE CAPTION OVERLAP
# ============================================================

def get_new_words(
    previous,
    current
):

    previous_words = (
        previous.split()
    )

    current_words = (
        current.split()
    )

    max_overlap = min(
        len(previous_words),
        len(current_words)
    )


    for size in range(
        max_overlap,
        0,
        -1
    ):

        prev_end = [
            word.lower()
            for word
            in previous_words[-size:]
        ]

        curr_start = [
            word.lower()
            for word
            in current_words[:size]
        ]

        if prev_end == curr_start:

            return " ".join(
                current_words[size:]
            )


    return current


# ============================================================
# BUILD CLEAN CUES
# ============================================================

def build_clean_cues(cues):

    output = []

    accumulated_text = ""


    for cue in cues:

        text = cue["text"]


        if not accumulated_text:

            new_text = text

        else:

            new_text = get_new_words(
                accumulated_text,
                text
            )


        if not new_text:
            continue


        output.append({
            "timing": cue["timing"],
            "text": new_text
        })


        accumulated_text += (
            " "
            + new_text
        )


        accumulated_text = re.sub(
            r"\s+",
            " ",
            accumulated_text
        ).strip()


    return output


# ============================================================
# BUILD TXT
# ============================================================

def build_output(
    cues,
    empty_line_between_cues=False,
    include_cue_timings=False
):

    output = []


    for cue in cues:

        if (
            include_cue_timings
            and cue["timing"]
        ):

            output.append(
                cue["timing"]
            )


        output.append(
            cue["text"]
        )


        if empty_line_between_cues:

            output.append("")


    return "\n".join(
        output
    ).strip()


# ============================================================
# SAFE FILE NAME
# ============================================================

def safe_filename(title):

    title = re.sub(
        r'[<>:"/\\|?*]',
        "_",
        title
    )

    title = title.strip()

    title = title.rstrip(
        ". "
    )

    if not title:

        title = (
            "youtube-subtitle"
        )

    return title


# ============================================================
# VALIDATE YOUTUBE URL
# ============================================================

def is_youtube_url(url):

    pattern = re.compile(
        r"^https?://"
        r"(?:www\.)?"
        r"(?:youtube\.com|youtu\.be)/",
        re.IGNORECASE
    )

    return bool(
        pattern.match(url)
    )


# ============================================================
# GET VIDEO TITLE
# ============================================================

def get_video_title(
    youtube_url
):

    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--skip-download",
        "--print",
        "%(title)s",
        youtube_url
    ]


    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=60
    )


    if result.returncode != 0:

        raise RuntimeError(
            result.stderr.strip()
            or
            "Gagal mengambil info video."
        )


    lines = [
        line.strip()
        for line
        in result.stdout.splitlines()
        if line.strip()
    ]


    if not lines:

        raise RuntimeError(
            "Judul video tidak ditemukan."
        )


    return lines[-1]


# ============================================================
# DOWNLOAD SUBTITLE
# ============================================================

def download_subtitle(
    youtube_url,
    temp_directory
):

    output_template = str(
        Path(
            temp_directory
        )
        /
        "subtitle"
    )


    command = [
        sys.executable,
        "-m",
        "yt_dlp",

        "--skip-download",

        "--write-auto-subs",

        "--write-subs",

        "--sub-langs",
        "id-orig,id",

        "--sub-format",
        "vtt",

        "-o",
        output_template,

        youtube_url
    ]


    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=120
    )


    if result.returncode != 0:

        raise RuntimeError(
            result.stderr.strip()
            or
            "Gagal mengambil subtitle."
        )


    temp_path = Path(
        temp_directory
    )


    vtt_files = list(
        temp_path.glob(
            "subtitle.*.vtt"
        )
    )


    if not vtt_files:

        raise RuntimeError(
            "Subtitle Indonesia tidak ditemukan."
        )


    # ========================================================
    # PRIORITY: id-orig
    # ========================================================

    for file in vtt_files:

        if ".id-orig.vtt" in file.name:

            return file


    # ========================================================
    # SECOND PRIORITY: id
    # ========================================================

    for file in vtt_files:

        if ".id.vtt" in file.name:

            return file


    return vtt_files[0]


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    return jsonify({
        "status": "ok",
        "name": "YouTube Subtitle API",
        "message": "API aktif"
    })


# ============================================================
# DOWNLOAD API
# ============================================================

@app.route(
    "/api/subtitle",
    methods=[
        "GET",
        "POST"
    ]
)
def subtitle():

    # ========================================================
    # GET INPUT
    # ========================================================

    if request.method == "POST":

        data = (
            request.get_json(
                silent=True
            )
            or
            request.form
        )

    else:

        data = request.args


    youtube_url = str(
        data.get(
            "url",
            ""
        )
    ).strip()


    if not youtube_url:

        return jsonify({
            "error": "URL YouTube belum diisi."
        }), 400


    if not is_youtube_url(
        youtube_url
    ):

        return jsonify({
            "error": "URL harus berasal dari YouTube."
        }), 400


    # ========================================================
    # OPTIONS
    # ========================================================

    empty_line_between_cues = get_bool(
        data.get(
            "empty_line"
        ),
        default=True
    )


    include_cue_timings = get_bool(
        data.get(
            "timings"
        ),
        default=False
    )


    include_file_name = get_bool(
        data.get(
            "file_name"
        ),
        default=True
    )


    try:

        # ====================================================
        # VIDEO TITLE
        # ====================================================

        video_title = get_video_title(
            youtube_url
        )


        # ====================================================
        # TEMP DIRECTORY
        # ====================================================

        with tempfile.TemporaryDirectory() as temp_directory:


            # ================================================
            # DOWNLOAD VTT
            # ================================================

            subtitle_file = download_subtitle(
                youtube_url,
                temp_directory
            )


            # ================================================
            # READ VTT
            # ================================================

            content = (
                subtitle_file
                .read_text(
                    encoding="utf-8"
                )
            )


            # ================================================
            # PARSE
            # ================================================

            cues = parse_vtt(
                content
            )


            if not cues:

                return jsonify({
                    "error": (
                        "Subtitle ditemukan, "
                        "tetapi tidak dapat dibaca."
                    )
                }), 500


            # ================================================
            # CLEAN
            # ================================================

            clean_cues = build_clean_cues(
                cues
            )


            # ================================================
            # BUILD TXT
            # ================================================

            result = build_output(
                clean_cues,

                empty_line_between_cues=
                    empty_line_between_cues,

                include_cue_timings=
                    include_cue_timings
            )


            # ================================================
            # INCLUDE VIDEO TITLE
            # ================================================

            if include_file_name:

                result = (
                    video_title
                    + "\n\n"
                    + result
                )


            # ================================================
            # PREPARE DOWNLOAD IN MEMORY
            # ================================================

            filename = (
                safe_filename(
                    video_title
                )
                + ".txt"
            )


            file_bytes = BytesIO(
                result.encode(
                    "utf-8"
                )
            )


            file_bytes.seek(0)


        # ====================================================
        # TEMP DIRECTORY SUDAH BOLEH DIHAPUS
        # HASIL TXT SEKARANG ADA DI MEMORY
        # ====================================================

        return send_file(
            file_bytes,

            mimetype=(
                "text/plain; "
                "charset=utf-8"
            ),

            as_attachment=True,

            download_name=filename
        )


    # ========================================================
    # TIMEOUT
    # ========================================================

    except subprocess.TimeoutExpired:

        return jsonify({
            "error": (
                "Proses YouTube terlalu lama. "
                "Silakan coba lagi."
            )
        }), 504


    # ========================================================
    # OTHER ERROR
    # ========================================================

    except Exception as e:

        return jsonify({
            "error": "Gagal memproses subtitle.",
            "detail": str(e)
        }), 500


# ============================================================
# RUN LOCAL
# ============================================================

if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )

# trigger deploy
