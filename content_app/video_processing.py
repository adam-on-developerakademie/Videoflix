"""RQ background task for converting uploaded video files via ffmpeg."""

from pathlib import Path
import subprocess

from django.conf import settings


def transcode_video(video_id: int) -> None:
    """Convert one uploaded source video into all output artifacts.

    Workflow:
    1. Rename source to <id>.<original_ext> when needed.
    2. Transcode to 1080p, 720p, and 480p MP4 outputs.
    3. Generate HLS variant playlists and one master playlist.
    4. Generate a thumbnail from the 1080p output.
    5. Remove temporary source files and update model fields.
    """
    from content_app.models import Video  # local import to avoid App Registry issues

    try:
        video = Video.objects.get(pk=video_id)
    except Video.DoesNotExist:
        return

    def cleanup_files(paths: list[Path]) -> None:
        for path in paths:
            if path.exists():
                path.unlink()

    def resolve_source_file() -> Path | None:
        # 1) Primary source from model field.
        if video.video_file and video.video_file.name:
            configured = Path(settings.MEDIA_ROOT) / video.video_file.name
            if configured.exists():
                return configured

        # 2) Fallback to normalized source file at media/videos/<id>.*.
        normalized_candidates = sorted(
            (Path(settings.MEDIA_ROOT) / "videos").glob(f"{video_id}.*")
        )
        if normalized_candidates:
            return normalized_candidates[0]

        # 3) Final fallback: use existing 1080p file as source for remaining renditions.
        fallback_1080 = (
            Path(settings.MEDIA_ROOT)
            / "videos"
            / str(video_id)
            / "1080p"
            / f"{video_id}.mp4"
        )
        if fallback_1080.exists():
            return fallback_1080

        return None

    video.conversion_status = Video.ConversionStatus.PROCESSING
    video.save(update_fields=["conversion_status"])

    source_abs = resolve_source_file()
    if source_abs is None:
        video.conversion_status = Video.ConversionStatus.FAILED
        video.save(update_fields=["conversion_status"])
        return

    media_videos_root = Path(settings.MEDIA_ROOT) / "videos"

    # --- Step 1: rename source to <id>.<original_ext> if needed ---
    if source_abs.parent == media_videos_root and source_abs.stem != str(video_id):
        renamed_abs = source_abs.parent / f"{video_id}{source_abs.suffix}"
        source_abs.rename(renamed_abs)
        source_abs = renamed_abs

    # --- Step 2: define resolution targets ---
    resolutions = {
        "1080p": {
            "vf": "scale=-2:1080",
            "crf": "22",
            "height": 1080,
            "bandwidth": 5500000,
        },
        "720p": {
            "vf": "scale=-2:720",
            "crf": "23",
            "height": 720,
            "bandwidth": 3000000,
        },
        "480p": {
            "vf": "scale=-2:480",
            "crf": "24",
            "height": 480,
            "bandwidth": 1500000,
        },
    }

    output_paths: dict[str, str] = {}
    created_outputs: list[Path] = []
    created_hls_files: list[Path] = []

    for label, opts in resolutions.items():
        out_rel = Path("videos") / str(video_id) / label / f"{video_id}.mp4"
        out_abs = Path(settings.MEDIA_ROOT) / out_rel
        out_abs.parent.mkdir(parents=True, exist_ok=True)

        if source_abs == out_abs:
            output_paths[label] = out_rel.as_posix()
            continue

        command = [
            "ffmpeg", "-y",
            "-i", str(source_abs),
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", opts["crf"],
            "-vf", opts["vf"],
            "-c:a", "aac",
            "-movflags", "+faststart",
            str(out_abs),
        ]

        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError:
            cleanup_files(created_outputs)
            if source_abs.parent == media_videos_root:
                cleanup_files([source_abs])
            video.conversion_status = Video.ConversionStatus.FAILED
            video.save(update_fields=["conversion_status"])
            return

        created_outputs.append(out_abs)
        output_paths[label] = out_rel.as_posix()

    # --- Step 3: create HLS variant playlists and master playlist ---
    hls_variant_paths: dict[str, str] = {}

    for label, opts in resolutions.items():
        variant_dir_rel = Path("videos") / str(video_id) / label
        variant_dir_abs = Path(settings.MEDIA_ROOT) / variant_dir_rel
        variant_playlist_abs = variant_dir_abs / "index.m3u8"
        variant_segment_pattern_abs = variant_dir_abs / "segment_%03d.ts"
        variant_playlist_rel = variant_dir_rel / "index.m3u8"

        variant_command = [
            "ffmpeg", "-y",
            "-i", str(Path(settings.MEDIA_ROOT) / output_paths[label]),
            "-codec", "copy",
            "-start_number", "0",
            "-hls_time", "6",
            "-hls_playlist_type", "vod",
            "-hls_segment_filename", str(variant_segment_pattern_abs),
            str(variant_playlist_abs),
        ]

        try:
            subprocess.run(variant_command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError:
            cleanup_files(created_hls_files)
            cleanup_files(created_outputs)
            if source_abs.parent == media_videos_root:
                cleanup_files([source_abs])
            video.conversion_status = Video.ConversionStatus.FAILED
            video.save(update_fields=["conversion_status"])
            return

        created_hls_files.extend(variant_dir_abs.glob("segment_*.ts"))
        created_hls_files.append(variant_playlist_abs)
        hls_variant_paths[label] = variant_playlist_rel.as_posix()

    master_rel = Path("videos") / str(video_id) / "master.m3u8"
    master_abs = Path(settings.MEDIA_ROOT) / master_rel
    master_lines = ["#EXTM3U", "#EXT-X-VERSION:3"]

    for label, opts in resolutions.items():
        height = int(opts["height"])
        width = int(round(height * 16 / 9 / 2) * 2)
        bandwidth = int(opts["bandwidth"])
        master_lines.append(
            f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={width}x{height}"
        )
        master_lines.append(hls_variant_paths[label])

    try:
        master_abs.write_text("\n".join(master_lines) + "\n", encoding="utf-8")
    except OSError:
        cleanup_files(created_hls_files)
        cleanup_files(created_outputs)
        if source_abs.parent == media_videos_root:
            cleanup_files([source_abs])
        video.conversion_status = Video.ConversionStatus.FAILED
        video.save(update_fields=["conversion_status"])
        return

    created_hls_files.append(master_abs)

    # --- Step 4: generate thumbnail from 1080p output ---
    thumbnail_rel = Path("videos") / str(video_id) / "thumbnail" / f"{video_id}.jpg"
    thumbnail_abs = Path(settings.MEDIA_ROOT) / thumbnail_rel
    thumbnail_abs.parent.mkdir(parents=True, exist_ok=True)

    thumbnail_source = Path(settings.MEDIA_ROOT) / output_paths["1080p"]
    thumbnail_command = [
        "ffmpeg", "-y",
        "-i", str(thumbnail_source),
        "-ss", "00:00:01",
        "-frames:v", "1",
        str(thumbnail_abs),
    ]

    try:
        subprocess.run(thumbnail_command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        cleanup_files(created_hls_files)
        cleanup_files(created_outputs)
        if source_abs.parent == media_videos_root:
            cleanup_files([source_abs])
        video.conversion_status = Video.ConversionStatus.FAILED
        video.save(update_fields=["conversion_status"])
        return

    # --- Step 5: remove renamed source ---
    if source_abs.parent == media_videos_root and source_abs.exists():
        source_abs.unlink()

    # --- Step 6: update record ---
    video.video_file.name = output_paths["1080p"]
    video.file_1080p.name = output_paths["1080p"]
    video.file_720p.name = output_paths["720p"]
    video.file_480p.name = output_paths["480p"]
    video.thumbnail_url = f"{settings.MEDIA_URL}{thumbnail_rel.as_posix()}"
    video.conversion_status = Video.ConversionStatus.DONE
    video.save(
        update_fields=[
            "video_file",
            "file_1080p",
            "file_720p",
            "file_480p",
            "thumbnail_url",
            "conversion_status",
        ]
    )
