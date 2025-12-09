import sys
import threading
import tempfile
import os
import json
import time
import logging
from pathlib import Path
from random import randrange
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, asdict

import requests
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QLabel, QSlider, QMessageBox, QComboBox,
    QFileDialog, QInputDialog, QAbstractItemView, QCompleter, QStatusBar,
    QProgressBar, QMainWindow, QMenu, QSystemTrayIcon
)
from PyQt6.QtCore import Qt, QUrl, QTimer, QSize, pyqtSignal, QObject
from PyQt6.QtGui import QPixmap, QIcon, QKeySequence, QShortcut, QAction
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

# Configuration
LOG_PATH = Path.cwd() / "yt_streamer_plus.log"
PLAYLIST_DB = Path.cwd() / "playlists.json"
SEARCH_HISTORY = Path.cwd() / "search_history.json"
CONFIG_FILE = Path.cwd() / "config.json"
CACHE_DB = Path.cwd() / "cache.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("yt_streamer")

try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except Exception as e:
    logger.error("yt-dlp not available: %s", e)
    YTDLP_AVAILABLE = False

# Constants
SEARCH_BATCH = 10
YDLP_OPTS_EXTRACT = {"quiet": True, "skip_download": True, "no_warnings": True}
YDLP_OPTS_DOWNLOAD = {
    "quiet": True,
    "format": "bestaudio",
    "outtmpl": None,
    "noplaylist": True,
    "no_warnings": True,
}

@dataclass
class Track:
    id: str
    title: str
    uploader: str
    webpage_url: str
    duration: int = 0
    thumbnail: Optional[str] = None
    formats: List = None
    entry: Optional[Dict] = None

    def __post_init__(self):
        if self.formats is None:
            self.formats = []

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "title": self.title,
            "uploader": self.uploader,
            "webpage_url": self.webpage_url,
            "duration": self.duration,
            "thumbnail": self.thumbnail
        }

@dataclass
class AppConfig:
    volume: int = 68
    window_width: int = 1200
    window_height: int = 720
    theme: str = "dark"
    last_playlist: str = ""
    download_quality: str = "bestaudio"
    
    @classmethod
    def load(cls) -> 'AppConfig':
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text())
                return cls(**data)
            except Exception:
                logger.exception("Failed to load config")
        return cls()
    
    def save(self):
        try:
            CONFIG_FILE.write_text(json.dumps(asdict(self), indent=2))
        except Exception:
            logger.exception("Failed to save config")

class WorkerSignals(QObject):
    finished = pyqtSignal(bool, object)

class WorkerThread(threading.Thread):
    def __init__(self, fn, args=(), kwargs=None, callback=None):
        super().__init__(daemon=True)
        self.fn = fn
        self.args = args
        self.kwargs = kwargs or {}
        self.signals = WorkerSignals()
        if callback:
            self.signals.finished.connect(callback)

    def run(self):
        try:
            res = self.fn(*self.args, **self.kwargs)
            self.signals.finished.emit(True, res)
        except Exception as e:
            logger.exception("Worker exception")
            self.signals.finished.emit(False, e)

def load_json_file(path: Path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception(f"Failed to read {path}")
    return default if default is not None else {}

def save_json_file(path: Path, data):
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        logger.exception(f"Failed to save {path}")

class DraggableListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def dropEvent(self, event):
        super().dropEvent(event)
        parent = self.parent()
        while parent and not hasattr(parent, "rebuild_queue_from_widget"):
            parent = parent.parent()
        if parent:
            parent.rebuild_queue_from_widget()

class YTStreamerMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = AppConfig.load()
        self.setWindowTitle("YT-Streamer Pro")
        self.resize(self.config.window_width, self.config.window_height)
        
        # Central widget
        self.central_widget = YTStreamerWidget(self)
        self.setCentralWidget(self.central_widget)
        
        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")
        
        # Progress bar for downloads
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(200)
        self.progress_bar.setVisible(False)
        self.status_bar.addPermanentWidget(self.progress_bar)
        
        # System tray
        self.setup_tray()
        
        # Menu bar
        self.setup_menu()
        
        if not YTDLP_AVAILABLE:
            QMessageBox.critical(self, "Missing Dependency", 
                "yt-dlp is required.\n\nInstall with: pip install yt-dlp")

    def setup_menu(self):
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu("&File")
        
        import_action = QAction("&Import Playlist", self)
        import_action.triggered.connect(self.central_widget.import_playlist)
        file_menu.addAction(import_action)
        
        export_action = QAction("&Export Playlist", self)
        export_action.triggered.connect(self.central_widget.export_selected_playlist)
        file_menu.addAction(export_action)
        
        file_menu.addSeparator()
        
        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # Playback menu
        playback_menu = menubar.addMenu("&Playback")
        
        play_action = QAction("&Play/Pause", self)
        play_action.setShortcut("Space")
        play_action.triggered.connect(self.central_widget.toggle_play)
        playback_menu.addAction(play_action)
        
        next_action = QAction("&Next", self)
        next_action.setShortcut("Ctrl+Right")
        next_action.triggered.connect(self.central_widget.play_next)
        playback_menu.addAction(next_action)
        
        prev_action = QAction("&Previous", self)
        prev_action.setShortcut("Ctrl+Left")
        prev_action.triggered.connect(self.central_widget.play_prev)
        playback_menu.addAction(prev_action)
        
        # Help menu
        help_menu = menubar.addMenu("&Help")
        
        about_action = QAction("&About", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)
        
        shortcuts_action = QAction("&Keyboard Shortcuts", self)
        shortcuts_action.triggered.connect(self.show_shortcuts)
        help_menu.addAction(shortcuts_action)

    def setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        icon = self.style().standardIcon(self.style().StandardPixmap.SP_MediaPlay)
        self.tray_icon.setIcon(icon)
        
        tray_menu = QMenu()
        show_action = tray_menu.addAction("Show")
        show_action.triggered.connect(self.show)
        quit_action = tray_menu.addAction("Quit")
        quit_action.triggered.connect(self.close)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

    def show_about(self):
        QMessageBox.about(self, "About YT-Streamer Pro",
            "YT-Streamer Pro v2.0\n\n"
            "A feature-rich YouTube music streaming application.\n\n"
            "Features:\n"
            "• Search and stream YouTube audio\n"
            "• Playlist management\n"
            "• A-B repeat loops\n"
            "• Playback speed control\n"
            "• Shuffle and repeat modes\n\n"
            "Built with PyQt6 and yt-dlp")

    def show_shortcuts(self):
        shortcuts_text = """
        Keyboard Shortcuts:
        
        Space - Play/Pause
        Ctrl+Right - Next track
        Ctrl+Left - Previous track
        Ctrl+F - Focus search
        Ctrl+Q - Quit
        
        Up/Down - Navigate lists
        Enter - Play selected item
        """
        QMessageBox.information(self, "Keyboard Shortcuts", shortcuts_text)

    def closeEvent(self, event):
        self.config.window_width = self.width()
        self.config.window_height = self.height()
        self.config.volume = self.central_widget.vol_slider.value()
        self.config.save()
        self.central_widget.cleanup()
        super().closeEvent(event)

class YTStreamerWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
        self.setup_player()
        self.setup_data()
        self.setup_shortcuts()
        self.apply_dark_theme()

    def setup_ui(self):
        # Search section
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search YouTube... (Ctrl+F)")
        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self.start_search)
        self.more_btn = QPushButton("Load More")
        self.more_btn.setEnabled(False)
        self.more_btn.clicked.connect(self.load_more_results)

        top_row = QHBoxLayout()
        top_row.addWidget(self.search_input)
        top_row.addWidget(self.search_btn)
        top_row.addWidget(self.more_btn)

        # Setup autocomplete
        history = load_json_file(SEARCH_HISTORY, [])
        completer = QCompleter(history)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.search_input.setCompleter(completer)

        # Lists
        self.results_list = QListWidget()
        self.results_list.itemDoubleClicked.connect(self.queue_and_play_from_results)
        
        self.queue_list = DraggableListWidget(self)
        self.queue_list.itemDoubleClicked.connect(self.play_queue_item)

        middle = QHBoxLayout()
        middle.addWidget(QLabel("Search Results:"))
        middle.addWidget(QLabel("Queue:"))
        
        lists_layout = QHBoxLayout()
        lists_layout.addWidget(self.results_list, 3)
        lists_layout.addWidget(self.queue_list, 2)

        # Now playing thumbnail
        self.now_playing_thumb = QLabel()
        self.now_playing_thumb.setFixedSize(120, 120)
        self.now_playing_thumb.setScaledContents(True)
        self.now_playing_thumb.setStyleSheet("border: 2px solid #444;")

        # Controls
        self.play_pause_btn = QPushButton("Play")
        self.play_pause_btn.setEnabled(False)
        self.play_pause_btn.clicked.connect(self.toggle_play)

        self.prev_btn = QPushButton("◀ Prev")
        self.prev_btn.clicked.connect(self.play_prev)

        self.next_btn = QPushButton("Next ▶")
        self.next_btn.clicked.connect(self.play_next)

        self.add_btn = QPushButton("Add to Queue →")
        self.add_btn.clicked.connect(self.add_selected_to_queue)

        self.shuffle_btn = QPushButton("🔀 Shuffle: Off")
        self.shuffle_btn.setCheckable(True)
        self.shuffle_btn.clicked.connect(self.toggle_shuffle)

        self.repeat_btn = QPushButton("🔁 Repeat: Off")
        self.repeat_btn.clicked.connect(self.toggle_repeat)

        self.speed_combo = QComboBox()
        for s in ("0.5x", "0.75x", "1x", "1.25x", "1.5x", "2x"):
            self.speed_combo.addItem(s)
        self.speed_combo.setCurrentText("1x")
        self.speed_combo.currentTextChanged.connect(self.apply_speed_setting)

        self.a_btn = QPushButton("Set A")
        self.b_btn = QPushButton("Set B")
        self.ab_reset_btn = QPushButton("Clear A-B")
        self.a_btn.clicked.connect(self.set_point_a)
        self.b_btn.clicked.connect(self.set_point_b)
        self.ab_reset_btn.clicked.connect(self.clear_ab)

        # Position slider
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 1000)
        self.position_slider.setEnabled(False)
        self.position_slider.sliderMoved.connect(self.on_seek_slider_moved)

        self.time_label = QLabel("00:00 / 00:00")
        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(68)
        self.vol_slider.valueChanged.connect(self.on_volume_changed)

        controls = QHBoxLayout()
        controls.addWidget(self.prev_btn)
        controls.addWidget(self.play_pause_btn)
        controls.addWidget(self.next_btn)
        controls.addWidget(self.add_btn)
        controls.addWidget(self.shuffle_btn)
        controls.addWidget(self.repeat_btn)
        controls.addWidget(QLabel("Speed:"))
        controls.addWidget(self.speed_combo)
        controls.addWidget(self.a_btn)
        controls.addWidget(self.b_btn)
        controls.addWidget(self.ab_reset_btn)
        controls.addStretch()
        controls.addWidget(QLabel("🔊"))
        controls.addWidget(self.vol_slider)
        controls.addWidget(self.time_label)

        # Playlist controls
        self.playlist_combo = QComboBox()
        self.refresh_playlists()
        self.load_playlist_btn = QPushButton("Load")
        self.save_playlist_btn = QPushButton("Save Queue")
        self.delete_playlist_btn = QPushButton("Delete")
        self.import_playlist_btn = QPushButton("Import")
        self.export_playlist_btn = QPushButton("Export")
        
        self.load_playlist_btn.clicked.connect(self.load_selected_playlist)
        self.save_playlist_btn.clicked.connect(self.save_current_queue_as_playlist)
        self.delete_playlist_btn.clicked.connect(self.delete_selected_playlist)
        self.import_playlist_btn.clicked.connect(self.import_playlist)
        self.export_playlist_btn.clicked.connect(self.export_selected_playlist)

        playlist_row = QHBoxLayout()
        playlist_row.addWidget(QLabel("Playlists:"))
        playlist_row.addWidget(self.playlist_combo)
        playlist_row.addWidget(self.load_playlist_btn)
        playlist_row.addWidget(self.save_playlist_btn)
        playlist_row.addWidget(self.delete_playlist_btn)
        playlist_row.addWidget(self.import_playlist_btn)
        playlist_row.addWidget(self.export_playlist_btn)

        # Main layout
        layout = QVBoxLayout()
        layout.addLayout(top_row)
        layout.addLayout(middle)
        layout.addLayout(lists_layout)
        layout.addWidget(self.now_playing_thumb)
        layout.addWidget(self.position_slider)
        layout.addLayout(controls)
        layout.addLayout(playlist_row)
        self.setLayout(layout)

    def setup_shortcuts(self):
        # Focus search
        focus_search = QShortcut(QKeySequence("Ctrl+F"), self)
        focus_search.activated.connect(lambda: self.search_input.setFocus())

    def apply_dark_theme(self):
        self.setStyleSheet("""
            QWidget {
                background-color: #1e1e1e;
                color: #ffffff;
                font-size: 10pt;
            }
            QLineEdit, QComboBox {
                background-color: #2d2d2d;
                border: 1px solid #444;
                padding: 5px;
                border-radius: 3px;
            }
            QPushButton {
                background-color: #0d7377;
                border: none;
                padding: 6px 12px;
                border-radius: 3px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #14FFEC;
                color: #000;
            }
            QPushButton:disabled {
                background-color: #3d3d3d;
                color: #777;
            }
            QListWidget {
                background-color: #2d2d2d;
                border: 1px solid #444;
                border-radius: 3px;
            }
            QListWidget::item:selected {
                background-color: #0d7377;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #3d3d3d;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #14FFEC;
                width: 14px;
                margin: -4px 0;
                border-radius: 7px;
            }
            QStatusBar {
                background-color: #252525;
                color: #aaa;
            }
        """)

    def setup_player(self):
        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(0.68)
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)
        
        self.player.playbackStateChanged.connect(self.on_playback_state_changed)
        self.player.positionChanged.connect(self.on_position_changed)
        self.player.durationChanged.connect(self.on_duration_changed)
        self.player.mediaStatusChanged.connect(self.on_media_status_changed)
        self.player.errorOccurred.connect(self.on_player_error)

    def setup_data(self):
        self.search_results = []
        self.search_offset = 0
        self.query = ""
        self.queue: List[Track] = []
        self.queue_index = -1
        self.shuffle_on = False
        self.repeat_mode = 0
        self.current_temp_file = None
        self.duration_ms = 0
        self.updating_slider = False
        self.point_a = None
        self.point_b = None
        
        self.thumb_cache = Path(tempfile.gettempdir()) / "yt_streamer_thumbs"
        self.thumb_cache.mkdir(exist_ok=True)
        
        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(400)
        self.ui_timer.timeout.connect(self.refresh_position)
        self.ui_timer.start()

    def start_search(self):
        q = self.search_input.text().strip()
        if not q:
            self.show_status("Enter a search query", 2000)
            return
        
        history = load_json_file(SEARCH_HISTORY, [])
        if q not in history:
            history.append(q)
            save_json_file(SEARCH_HISTORY, history[-200:])
            self.search_input.completer().model().setStringList(history)
        
        self.query = q
        self.search_offset = 0
        self.search_results = []
        self.results_list.clear()
        self.more_btn.setEnabled(False)
        self.search_btn.setEnabled(False)
        self.show_status(f"Searching for: {q}...")
        
        thr = WorkerThread(self._do_search_batch, args=(q, SEARCH_BATCH, 0), 
                          callback=self.on_search_batch)
        thr.start()

    def load_more_results(self):
        thr = WorkerThread(self._do_search_batch, 
                          args=(self.query, SEARCH_BATCH, self.search_offset),
                          callback=self.on_search_batch)
        thr.start()

    def _do_search_batch(self, q: str, batch: int, offset: int) -> List[Dict]:
        if not YTDLP_AVAILABLE:
            raise RuntimeError("yt-dlp not available")
        
        total_n = offset + batch
        with yt_dlp.YoutubeDL(YDLP_OPTS_EXTRACT) as ydl:
            query = f"ytsearch{total_n}:{q}"
            info = ydl.extract_info(query, download=False)
            entries = info.get("entries") or []
            page = entries[offset:offset+batch]
            
            processed = []
            for e in page:
                processed.append({
                    "id": e.get("id"),
                    "title": e.get("title"),
                    "uploader": e.get("uploader") or e.get("channel") or "",
                    "webpage_url": e.get("webpage_url"),
                    "duration": e.get("duration") or 0,
                    "thumbnail": e.get("thumbnail"),
                    "formats": e.get("formats") or [],
                    "entry": e
                })
            return processed

    def on_search_batch(self, ok: bool, result):
        self.search_btn.setEnabled(True)
        if not ok:
            logger.error("Search failed: %s", result)
            self.show_status(f"Search error: {result}", 5000)
            return
        
        new_results = result or []
        self.search_results.extend(new_results)
        
        for r in new_results:
            li = QListWidgetItem(f"{r['title']} — {r['uploader']}")
            li.setData(Qt.ItemDataRole.UserRole, r)
            self.results_list.addItem(li)
            
            if r.get("thumbnail"):
                WorkerThread(self._fetch_thumbnail, 
                           args=(r.get("thumbnail"), li),
                           callback=self._on_thumb_fetched).start()
        
        self.search_offset = len(self.search_results)
        self.more_btn.setEnabled(len(new_results) >= SEARCH_BATCH)
        self.show_status(f"Loaded {len(new_results)} results (total: {len(self.search_results)})", 3000)

    def _fetch_thumbnail(self, url: str, list_item) -> Optional[Tuple[str, int]]:
        try:
            name = str(abs(hash(url))) + ".jpg"
            dest = self.thumb_cache / name
            if not dest.exists():
                r = requests.get(url, timeout=8)
                if r.status_code == 200:
                    dest.write_bytes(r.content)
            return str(dest), id(list_item)
        except Exception:
            logger.exception("Thumbnail fetch failed")
            return None

    def _on_thumb_fetched(self, ok: bool, result):
        if not ok or not result:
            return
        
        try:
            path_str, item_id = result
            pix = QPixmap(path_str).scaled(64, 64, 
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            
            for i in range(self.results_list.count()):
                li = self.results_list.item(i)
                if id(li) == item_id:
                    li.setIcon(QIcon(pix))
                    break
        except Exception:
            logger.exception("Failed to set thumbnail")

    def add_selected_to_queue(self):
        sel = self.results_list.currentItem()
        if not sel:
            self.show_status("No item selected", 2000)
            return
        entry = sel.data(Qt.ItemDataRole.UserRole)
        self._queue_append(entry)
        self.show_status(f"Added: {entry['title'][:50]}...", 2000)

    def queue_and_play_from_results(self, item):
        entry = item.data(Qt.ItemDataRole.UserRole)
        self._queue_append(entry)
        self.play_index(len(self.queue)-1)

    def _queue_append(self, entry: Dict):
        track = Track(
            id=entry.get("id", ""),
            title=entry.get("title", ""),
            uploader=entry.get("uploader", ""),
            webpage_url=entry.get("webpage_url", ""),
            duration=entry.get("duration", 0),
            thumbnail=entry.get("thumbnail"),
            formats=entry.get("formats", []),
            entry=entry.get("entry")
        )
        
        title = f"{track.title} — {track.uploader}"
        li = QListWidgetItem(title)
        li.setData(Qt.ItemDataRole.UserRole, entry)
        
        if track.thumbnail:
            name = str(abs(hash(track.thumbnail))) + ".jpg"
            fp = self.thumb_cache / name
            if fp.exists():
                try:
                    pix = QPixmap(str(fp)).scaled(48, 48,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
                    li.setIcon(QIcon(pix))
                except Exception:
                    pass
        
        self.queue_list.addItem(li)
        self.queue.append(track)
        
        if self.queue_index == -1:
            self.queue_index = 0
        self.play_pause_btn.setEnabled(True)

    def play_queue_item(self, item):
        entry = item.data(Qt.ItemDataRole.UserRole)
        for i, track in enumerate(self.queue):
            if track.id == entry.get("id"):
                self.play_index(i)
                break

    def rebuild_queue_from_widget(self):
        newq = []
        for i in range(self.queue_list.count()):
            entry = self.queue_list.item(i).data(Qt.ItemDataRole.UserRole)
            track = Track(
                id=entry.get("id", ""),
                title=entry.get("title", ""),
                uploader=entry.get("uploader", ""),
                webpage_url=entry.get("webpage_url", ""),
                duration=entry.get("duration", 0),
                thumbnail=entry.get("thumbnail"),
                formats=entry.get("formats", [])
            )
            newq.append(track)
        self.queue = newq
        
        cur = self.queue_list.currentRow()
        if cur >= 0:
            self.queue_index = cur

    def play_index(self, idx: int):
        if idx < 0 or idx >= len(self.queue):
            return
        self.queue_index = idx
        track = self.queue[idx]
        self._prepare_and_play(track, idx)

    def _prepare_and_play(self, track: Track, idx: int):
        logger.info("Preparing: %s", track.title)
        self.play_pause_btn.setEnabled(False)
        self.position_slider.setEnabled(False)
        self.show_status(f"Loading: {track.title[:50]}...")
        
        WorkerThread(self._extract_or_download, args=(track,),
                    callback=lambda ok, res: self._on_url_ready(ok, res, idx)).start()

    def _extract_or_download(self, track: Track) -> Tuple:
        if not YTDLP_AVAILABLE:
            raise RuntimeError("yt-dlp not available")
        
        # Try to get direct stream URL first
        formats = track.formats or []
        if not formats:
            with yt_dlp.YoutubeDL(YDLP_OPTS_EXTRACT) as ydl:
                info = ydl.extract_info(track.webpage_url, download=False)
                formats = info.get("formats") or []
        
        # Find best audio-only format
        audio_only = [f for f in formats 
                     if f.get("acodec") and f.get("acodec") != "none" 
                     and f.get("vcodec") in (None, "none")]
        
        if not audio_only:
            audio_only = [f for f in formats if f.get("acodec") and f.get("acodec") != "none"]
        
        audio_only.sort(key=lambda f: int(f.get("abr") or f.get("tbr") or 0), reverse=True)
        
        for f in audio_only:
            if f.get("url"):
                with yt_dlp.YoutubeDL(YDLP_OPTS_EXTRACT) as ydl:
                    info = ydl.extract_info(track.webpage_url, download=False)
                duration = int((info.get("duration") or 0) * 1000)
                return False, f.get("url"), info.get("title") or track.title, duration, info
        
        # Fallback: download
        logger.info("Downloading: %s", track.title)
        tmp_dir = tempfile.gettempdir()
        tmpfile = Path(tmp_dir) / f"ytp_{int(time.time())}.%(ext)s"
        
        opts = dict(YDLP_OPTS_DOWNLOAD)
        opts["outtmpl"] = str(tmpfile)
        
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(track.webpage_url, download=True)
            candidates = sorted(Path(tmp_dir).glob("ytp_*"), 
                              key=lambda p: p.stat().st_mtime, reverse=True)
            if candidates:
                duration = int((info.get("duration") or 0) * 1000)
                return True, str(candidates[0]), info.get("title") or track.title, duration, info
        
        raise RuntimeError("Failed to get playable URL")

    def _on_url_ready(self, ok: bool, result, idx: int):
        if not ok:
            logger.error("Prepare failed: %s", result)
            self.show_status(f"Failed to load track: {result}", 5000)
            return
        
        is_local, path_or_url, title, duration_ms, info = result
        self.duration_ms = duration_ms or 0
        
        try:
            if is_local:
                qurl = QUrl.fromLocalFile(str(path_or_url))
                self.current_temp_file = str(path_or_url)
            else:
                qurl = QUrl(path_or_url)
                self.current_temp_file = None
            
            # Update now playing thumbnail
            thumb = None
            if isinstance(info, dict):
                thumb = info.get("thumbnail")
                if not thumb and info.get("thumbnails"):
                    thumb = info.get("thumbnails")[-1].get("url")
            
            if thumb:
                self._load_now_playing_thumb(thumb)
            
            self.player.setSource(qurl)
            self.apply_speed_setting()
            self.player.play()
            self.play_pause_btn.setEnabled(True)
            self.play_pause_btn.setText("⏸ Pause")
            self.position_slider.setEnabled(True)
            self.highlight_playing_in_queue(idx)
            self.show_status(f"Playing: {title[:50]}", 3000)
            
            # Update window title
            if self.window():
                self.window().setWindowTitle(f"YT-Streamer Pro - {title[:60]}")
        
        except Exception:
            logger.exception("Playback start failed")
            self.show_status("Playback failed", 5000)

    def _load_now_playing_thumb(self, thumb_url: str):
        try:
            name = str(abs(hash(thumb_url))) + ".jpg"
            fp = self.thumb_cache / name
            if fp.exists():
                pix = QPixmap(str(fp)).scaled(
                    self.now_playing_thumb.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                self.now_playing_thumb.setPixmap(pix)
            else:
                WorkerThread(self._fetch_thumbnail, args=(thumb_url, None),
                           callback=self._on_now_thumb).start()
        except Exception:
            logger.exception("Load thumbnail failed")

    def _on_now_thumb(self, ok: bool, result):
        if not ok or not result:
            return
        try:
            path_str, _ = result
            if path_str and Path(path_str).exists():
                pix = QPixmap(path_str).scaled(
                    self.now_playing_thumb.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                self.now_playing_thumb.setPixmap(pix)
        except Exception:
            logger.exception("Set now playing thumb failed")

    def highlight_playing_in_queue(self, idx: int):
        if 0 <= idx < self.queue_list.count():
            self.queue_list.setCurrentRow(idx)

    def play_next(self):
        if not self.queue:
            return
        
        if self.shuffle_on:
            next_idx = randrange(0, len(self.queue))
        else:
            next_idx = self.queue_index + 1
        
        if next_idx >= len(self.queue):
            if self.repeat_mode == 1:  # Repeat all
                next_idx = 0
            else:
                self.show_status("End of queue", 2000)
                return
        
        self.play_index(next_idx)

    def play_prev(self):
        if not self.queue:
            return
        
        prev_idx = self.queue_index - 1
        if prev_idx < 0:
            if self.repeat_mode == 1:  # Repeat all
                prev_idx = len(self.queue) - 1
            else:
                prev_idx = 0
        
        self.play_index(prev_idx)

    def toggle_shuffle(self):
        self.shuffle_on = not self.shuffle_on
        self.shuffle_btn.setText(f"🔀 Shuffle: {'On' if self.shuffle_on else 'Off'}")
        self.show_status(f"Shuffle {'enabled' if self.shuffle_on else 'disabled'}", 2000)

    def toggle_repeat(self):
        self.repeat_mode = (self.repeat_mode + 1) % 3
        names = {0: "Off", 1: "All", 2: "One"}
        self.repeat_btn.setText(f"🔁 Repeat: {names[self.repeat_mode]}")
        self.show_status(f"Repeat mode: {names[self.repeat_mode]}", 2000)

    def set_point_a(self):
        pos = self.player.position()
        self.point_a = pos
        self.show_status(f"Point A set at {self._fmt_ms(pos)}", 2000)

    def set_point_b(self):
        pos = self.player.position()
        if self.point_a and pos <= self.point_a:
            self.show_status("Point B must be after Point A", 3000)
            return
        self.point_b = pos
        self.show_status(f"Point B set at {self._fmt_ms(pos)}", 2000)

    def clear_ab(self):
        self.point_a = None
        self.point_b = None
        self.show_status("A-B loop cleared", 2000)

    def on_playback_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_pause_btn.setText("⏸ Pause")
        else:
            self.play_pause_btn.setText("▶ Play")

    def on_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            logger.info("Track ended")
            if self.repeat_mode == 2:  # Repeat one
                self.play_index(self.queue_index)
            else:
                self.play_next()

    def on_position_changed(self, pos_ms: int):
        # Handle A-B loop
        if self.point_a is not None and self.point_b is not None:
            if pos_ms >= self.point_b:
                try:
                    self.player.setPosition(self.point_a)
                except Exception:
                    pass
                return
        
        if self.updating_slider:
            return
        
        dur = self.duration_ms or self.player.duration()
        if dur and dur > 0:
            self.updating_slider = True
            val = int((pos_ms / max(1, dur)) * 1000)
            self.position_slider.setValue(val)
            self.updating_slider = False
        
        self.update_time_label(pos_ms, dur)
        
        # Update window title with progress
        if dur > 0 and self.window():
            pct = int((pos_ms / dur) * 100)
            if self.queue and 0 <= self.queue_index < len(self.queue):
                title = self.queue[self.queue_index].title
                self.window().setWindowTitle(f"YT-Streamer [{pct}%] - {title[:50]}")

    def on_duration_changed(self, dur_ms: int):
        self.duration_ms = dur_ms
        self.update_time_label(self.player.position(), dur_ms)

    def on_player_error(self, error, err_str: str):
        logger.error("Player error: %s %s", error, err_str)
        self.show_status(f"Playback error: {err_str}", 5000)

    def on_seek_slider_moved(self, val: int):
        dur = self.duration_ms or self.player.duration()
        if not dur:
            return
        pos = int((val / 1000.0) * dur)
        try:
            self.player.setPosition(pos)
        except Exception:
            logger.exception("Seek failed")

    def refresh_position(self):
        try:
            pos = self.player.position()
            self.on_position_changed(pos)
        except Exception:
            pass

    def update_time_label(self, pos_ms: int, dur_ms: int):
        self.time_label.setText(f"{self._fmt_ms(pos_ms)} / {self._fmt_ms(dur_ms)}")

    def toggle_play(self):
        try:
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
            else:
                self.player.play()
        except Exception:
            logger.exception("Toggle play failed")

    def apply_speed_setting(self):
        txt = self.speed_combo.currentText().rstrip("x")
        try:
            rate = float(txt)
            self.player.setPlaybackRate(rate)
        except Exception:
            logger.exception("Set playback rate failed")

    def on_volume_changed(self, v: int):
        try:
            self.audio_output.setVolume(v / 100.0)
        except Exception:
            logger.exception("Set volume failed")

    def _fmt_ms(self, ms: int) -> str:
        if not ms or ms <= 0:
            return "00:00"
        s = int(ms / 1000)
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def refresh_playlists(self):
        db = load_json_file(PLAYLIST_DB, {})
        self.playlist_combo.clear()
        self.playlist_combo.addItem("Select playlist...")
        for k in sorted(db.keys()):
            self.playlist_combo.addItem(k)

    def save_current_queue_as_playlist(self):
        if not self.queue:
            self.show_status("Queue is empty", 2000)
            return
        
        name, ok = QInputDialog.getText(self, "Save Playlist", "Enter playlist name:")
        if not ok or not name.strip():
            return
        
        db = load_json_file(PLAYLIST_DB, {})
        db[name] = [track.to_dict() for track in self.queue]
        save_json_file(PLAYLIST_DB, db)
        self.refresh_playlists()
        self.show_status(f"Saved playlist: {name}", 3000)

    def load_selected_playlist(self):
        sel = self.playlist_combo.currentText()
        if not sel or sel == "Select playlist...":
            self.show_status("Select a playlist first", 2000)
            return
        
        db = load_json_file(PLAYLIST_DB, {})
        items = db.get(sel, [])
        if not items:
            self.show_status("Playlist is empty", 2000)
            return
        
        self.queue.clear()
        self.queue_list.clear()
        
        for it in items:
            entry = {
                "id": it.get("id"),
                "title": it.get("title"),
                "uploader": it.get("uploader"),
                "webpage_url": it.get("webpage_url"),
                "duration": it.get("duration", 0),
                "thumbnail": it.get("thumbnail"),
                "formats": [],
                "entry": None
            }
            self._queue_append(entry)
        
        self.show_status(f"Loaded {len(items)} tracks from '{sel}'", 3000)

    def delete_selected_playlist(self):
        sel = self.playlist_combo.currentText()
        if not sel or sel == "Select playlist...":
            self.show_status("Select a playlist to delete", 2000)
            return
        
        reply = QMessageBox.question(self, "Delete Playlist",
            f"Delete playlist '{sel}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            db = load_json_file(PLAYLIST_DB, {})
            if sel in db:
                del db[sel]
                save_json_file(PLAYLIST_DB, db)
                self.refresh_playlists()
                self.show_status(f"Deleted playlist: {sel}", 3000)

    def import_playlist(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Playlist",
            str(Path.cwd()), "JSON Files (*.json);;All Files (*)")
        if not path:
            return
        
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            name = payload.get("name") if isinstance(payload, dict) else f"Imported_{int(time.time())}"
            tracks = payload.get("tracks") if isinstance(payload, dict) else payload
            
            if not isinstance(tracks, list):
                self.show_status("Invalid playlist format", 3000)
                return
            
            db = load_json_file(PLAYLIST_DB, {})
            db[name] = tracks
            save_json_file(PLAYLIST_DB, db)
            self.refresh_playlists()
            self.show_status(f"Imported: {name}", 3000)
        except Exception:
            logger.exception("Import failed")
            self.show_status("Import failed", 3000)

    def export_selected_playlist(self):
        sel = self.playlist_combo.currentText()
        if not sel or sel == "Select playlist...":
            self.show_status("Select a playlist to export", 2000)
            return
        
        db = load_json_file(PLAYLIST_DB, {})
        payload = db.get(sel)
        if payload is None:
            self.show_status("Playlist not found", 2000)
            return
        
        path, _ = QFileDialog.getSaveFileName(self, "Export Playlist",
            f"{sel}.json", "JSON Files (*.json);;All Files (*)")
        if not path:
            return
        
        try:
            Path(path).write_text(
                json.dumps({"name": sel, "tracks": payload}, indent=2),
                encoding="utf-8")
            self.show_status(f"Exported to: {path}", 3000)
        except Exception:
            logger.exception("Export failed")
            self.show_status("Export failed", 3000)

    def show_status(self, msg: str, duration: int = 0):
        if self.window() and hasattr(self.window(), 'status_bar'):
            self.window().status_bar.showMessage(msg, duration)

    def cleanup(self):
        try:
            if self.current_temp_file and Path(self.current_temp_file).exists():
                Path(self.current_temp_file).unlink()
                logger.info("Cleaned up temp file")
        except Exception:
            logger.exception("Cleanup failed")

def main():
    try:
        app = QApplication(sys.argv)
        app.setApplicationName("YT-Streamer Pro")
        app.setOrganizationName("YTStreamer")
        
        window = YTStreamerMainWindow()
        window.show()
        
        sys.exit(app.exec())
    except Exception:
        logger.exception("Fatal error")
        raise

if __name__ == "__main__":
    main()