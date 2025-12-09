# Chronosonic YouTube Music Streaming Application

## Overview
YT-Streamer is a comprehensive desktop music player application designed to stream and download audio from YouTube. Developed with Python and PyQt6, it delivers a full-featured music streaming experience with an interface resembling popular music services, complete with playlist management, advanced playback controls, and intelligent music discovery.

## Core Features

### Search and Discovery
- YouTube search functionality powered by yt-dlp
- Thumbnail caching for visual song recognition
- Search history with auto-complete suggestions
- Paginated results with "load more" capability
- Background thumbnail fetching to maintain UI responsiveness

### Playback Engine
- Dual playback modes: direct streaming or local download
- Adaptive format selection for optimal audio quality
- A-B loop point repeat functionality for practice or study
- Playback speed adjustment from 0.5x to 2x
- Volume control with smooth slider interface
- Multiple repeat modes and shuffle functionality

### Queue and Playlist Management
- Drag-and-drop queue reorganization
- Playlist import and export in JSON format
- Persistent local playlist storage
- Automatic addition of related tracks when queue ends
- Visual highlighting of currently playing track

### User Interface
- Clean dual-panel layout separating search results and queue
- Now-playing thumbnail display
- Real-time playback position slider with time display
- Visual feedback for all playback states
- Responsive controls with appropriate enabled/disabled states

## Technical Architecture

### Dependencies
- PyQt6: Primary GUI framework with multimedia capabilities
- yt-dlp: YouTube content extraction and download functionality
- requests: HTTP library for thumbnail fetching
- Python Standard Library: Core modules for threading, file operations, and data management

### Threading Model
- Dedicated worker threads for blocking operations including:
  - YouTube search queries
  - Audio URL extraction and file downloads
  - Thumbnail fetching
  - Related track discovery
- Main thread preserved for responsive UI updates

### Data Persistence
- playlists.json: Structured storage for user playlists
- search_history.json: Record of recent search queries
- Thumbnail cache: Temporary directory for downloaded images
- Logging system: Comprehensive debug logging to file and console

### Playback Pipeline
1. User search and metadata extraction
2. Optimal audio format selection (streaming preferred over download)
3. Download initiation when streaming unavailable
4. URL or file delivery to QMediaPlayer
5. Playback state management and transition handling

## Advanced Features

### Smart Queue Management
- Automatic discovery and addition of related tracks when queue depletes
- Drag-and-drop reordering with real-time queue synchronization
- Visual queue highlighting synchronized with current playback position

### A-B Loop Functionality
- Precise loop point setting during playback
- Automatic restart when reaching endpoint B
- Clear and reset capabilities for loop points

### Playlist Ecosystem
- Playlist creation from current queue
- Standardized JSON format for import and export
- Cross-session persistent storage
- Multiple playlist management with delete capability

### Error Handling and Logging
- Comprehensive exception handling with user-friendly feedback
- Detailed logging to yt_streamer_plus.log file
- Graceful degradation when dependencies are unavailable
- Automatic temporary file cleanup on application exit

## Code Organization

### Main Components
- YTStreamerT12: Primary application window class
- WorkerThread: Generic threading wrapper for asynchronous operations
- DraggableListWidget: Custom QListWidget with drag-drop functionality
- Utility functions: Playlist and search history management routines

### Key Methods
- _extract_or_download(): Core audio acquisition logic
- _prepare_and_play(): Complete playback preparation pipeline
- rebuild_queue_from_widget(): Queue synchronization mechanism
- on_media_status_changed(): Playback state transition management

## Performance Optimizations
- Thumbnail caching to prevent redundant downloads
- Batch searching for efficient result pagination
- Background processing for non-blocking user experience
- Systematic temporary file management with automatic cleanup

## Usage Flow
1. Search for audio content on YouTube
2. Add results to queue via double-click or dedicated button
3. Organize queue through drag-and-drop interface
4. Control playback using transport controls
5. Save and load playlists for future sessions
6. Adjust playback speed, volume, and loop points as needed

## Limitations and Requirements
- Requires yt-dlp installation (pip install yt-dlp)
- Dependent on internet connectivity for search and streaming
- Potential YouTube Terms of Service considerations (educational use recommended)
- Audio quality limited by YouTube source material



YT-Streamer represents a production-ready music streaming application that effectively combines YouTube's extensive content library with the control and features expected from a dedicated music player platform.
